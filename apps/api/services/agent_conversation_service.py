"""Durable single-workspace storage for shared Agent conversations."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apps.api.services.agent_lifecycle import (
    RUN_RECORD_LOST,
    RUN_RECORD_LOST_MESSAGE,
    TERMINAL_RUN_STATES,
    status_rank,
    trace_entry,
)

MAX_SHARED_CONVERSATIONS = 100
MAX_MESSAGES_PER_CONVERSATION = 80
MAX_CONVERSATION_BYTES = 4_000_000
MAX_TRACE_ENTRIES_PER_RUN = 2_000
# Longer than the proxy's upstream request timeout, so a live forward is never reclassified.
SUBMISSION_STALE_SECONDS = 180
# Reads that must agree before a Run is declared lost: a terminal state cannot be undone.
LOST_RUN_READS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_conversations (
    id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_conversations_updated
ON agent_conversations(updated_at DESC);
CREATE TABLE IF NOT EXISTS agent_conversation_runs (
    scope TEXT NOT NULL,
    run_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    access_token TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, run_id)
);
CREATE TABLE IF NOT EXISTS agent_run_submissions (
    scope TEXT NOT NULL,
    submission_key TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    state TEXT NOT NULL,
    run_id TEXT,
    conversation_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, submission_key)
);
CREATE TABLE IF NOT EXISTS agent_run_trace (
    scope TEXT NOT NULL,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    dedupe TEXT NOT NULL,
    upstream_sequence INTEGER,
    at TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    PRIMARY KEY (scope, run_id, seq),
    UNIQUE (scope, run_id, dedupe)
);
CREATE TABLE IF NOT EXISTS agent_run_jobs (
    scope TEXT NOT NULL,
    run_id TEXT NOT NULL,
    job_kind TEXT NOT NULL,
    job_id TEXT NOT NULL,
    owned INTEGER NOT NULL,
    linked_at TEXT NOT NULL,
    PRIMARY KEY (scope, run_id, job_kind, job_id)
);
"""

# Added to agent_conversation_runs after its first release. Runs from before these
# columns keep trace_open = 0, so recovery does not chase records it never traced.
_RUN_COLUMNS = {
    "missing_reads": "INTEGER NOT NULL DEFAULT 0",
    "event_cursor": "INTEGER NOT NULL DEFAULT 0",
    "trace_open": "INTEGER NOT NULL DEFAULT 0",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_trace(
    connection: sqlite3.Connection,
    scope: str,
    run_id: str,
    kind: str,
    detail: dict[str, Any] | None = None,
    *,
    dedupe: str | None = None,
    at: str | None = None,
    upstream_sequence: int | None = None,
) -> bool:
    """Append once per dedupe key, so replays and duplicate callbacks change nothing."""

    key = dedupe or kind
    if connection.execute(
        "SELECT 1 FROM agent_run_trace WHERE scope = ? AND run_id = ? AND dedupe = ?",
        (scope, run_id, key),
    ).fetchone():
        return False
    count, last = connection.execute(
        "SELECT COUNT(*), COALESCE(MAX(seq), 0) FROM agent_run_trace WHERE scope = ? AND run_id = ?",
        (scope, run_id),
    ).fetchone()
    # A terminal event must survive the cap, or a long Run could never show how it ended.
    if count >= MAX_TRACE_ENTRIES_PER_RUN and kind.split(".")[-1] not in TERMINAL_RUN_STATES:
        if kind != "trace.truncated":
            _append_trace(connection, scope, run_id, "trace.truncated", {"limit": MAX_TRACE_ENTRIES_PER_RUN})
        return False
    connection.execute(
        """INSERT INTO agent_run_trace
           (scope, run_id, seq, kind, dedupe, upstream_sequence, at, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (scope, run_id, last + 1, kind, key, upstream_sequence, at or _utc_now(), _encoded(detail or {})),
    )
    return True


def _encoded(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _before(stamp: str, cutoff: str) -> bool:
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return moment < datetime.fromisoformat(cutoff)
    except ValueError:
        return False


def _merge_messages(
    stored: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    messages: dict[str, dict[str, Any]] = {}
    for message in [*stored, *incoming]:
        message_id = message.get("id")
        if not isinstance(message_id, str):
            continue
        previous = messages.get(message_id)
        if previous is None:
            messages[message_id] = message
            continue
        merged = {**previous, **message}
        if "artifacts" not in message and "artifacts" in previous:
            merged["artifacts"] = previous["artifacts"]
        messages[message_id] = merged
    return sorted(
        messages.values(),
        key=lambda item: (str(item.get("createdAt", "")), str(item.get("id", ""))),
    )[-MAX_MESSAGES_PER_CONVERSATION:]


def _newer_checkpoint(
    stored: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    candidates = [item for item in (stored, incoming) if isinstance(item, dict)]
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item.get("updatedAt", "")))


def merge_conversation(
    stored: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge a stale browser snapshot without dropping messages from another browser."""

    if stored is None:
        merged = dict(incoming)
        merged["messages"] = list(incoming.get("messages", []))[-MAX_MESSAGES_PER_CONVERSATION:]
    else:
        newer = incoming if incoming["updatedAt"] >= stored["updatedAt"] else stored
        messages = _merge_messages(
            list(stored.get("messages", [])),
            list(incoming.get("messages", [])),
        )
        updated_at = max(
            str(stored["updatedAt"]),
            str(incoming["updatedAt"]),
            *(str(item.get("createdAt", "")) for item in messages),
        )
        checkpoint = _newer_checkpoint(
            stored.get("researchCheckpoint"),
            incoming.get("researchCheckpoint"),
        )
        merged = {
            "id": incoming["id"],
            "title": newer["title"],
            "createdAt": min(str(stored["createdAt"]), str(incoming["createdAt"])),
            "updatedAt": updated_at,
            "messages": messages,
            **({"researchCheckpoint": checkpoint} if checkpoint else {}),
        }

    while len(_encoded(merged).encode("utf-8")) > MAX_CONVERSATION_BYTES:
        messages = list(merged.get("messages", []))
        if len(messages) <= 1:
            raise ValueError("Agent conversation exceeds the shared storage limit")
        merged["messages"] = messages[1:]
    return merged


class AgentConversationStore:
    """SQLite-backed shared history for the one AlphaLab publication workspace."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            present = {row["name"] for row in connection.execute("PRAGMA table_info(agent_conversation_runs)")}
            for name, definition in _RUN_COLUMNS.items():
                if name not in present:
                    connection.execute(f"ALTER TABLE agent_conversation_runs ADD COLUMN {name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM agent_conversations
                ORDER BY updated_at DESC, id ASC
                LIMIT ?
                """,
                (MAX_SHARED_CONVERSATIONS,),
            ).fetchall()
        conversations: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict):
                conversations.append(value)
        return conversations

    def upsert(self, incoming: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            merged = self._upsert(connection, incoming)
        return merged

    @staticmethod
    def _upsert(connection: sqlite3.Connection, incoming: dict[str, Any]) -> dict[str, Any]:
        row = connection.execute(
            "SELECT payload_json FROM agent_conversations WHERE id = ?", (incoming["id"],),
        ).fetchone()
        try:
            stored = json.loads(row["payload_json"]) if row else None
            if not isinstance(stored, dict):
                stored = None
        except (json.JSONDecodeError, TypeError):
            stored = None
        merged = merge_conversation(stored, incoming)
        connection.execute(
            """INSERT INTO agent_conversations (id, payload_json, created_at, updated_at)
               VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET
               payload_json = excluded.payload_json, created_at = excluded.created_at,
               updated_at = excluded.updated_at""",
            (merged["id"], _encoded(merged), merged["createdAt"], merged["updatedAt"]),
        )
        # Active conversations remain recoverable even when history reaches its cap.
        connection.execute(
            """DELETE FROM agent_conversations WHERE id NOT IN
               (SELECT id FROM agent_conversations ORDER BY updated_at DESC, id ASC LIMIT ?)
               AND id NOT IN (SELECT conversation_id FROM agent_conversation_runs WHERE delivered = 0)""",
            (MAX_SHARED_CONVERSATIONS,),
        )
        return merged

    def attach_run(
        self, scope: str, run: dict[str, Any], access_token: str, conversation: dict[str, Any],
        submission_key: str | None = None,
    ) -> dict[str, Any]:
        """Commit the user turn and recovery credential before acknowledging submission."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = self._upsert(connection, conversation)
            # A lost acknowledgement can be resolved by more than one path; one Run stays one row.
            connection.execute(
                """INSERT OR IGNORE INTO agent_conversation_runs
                   (scope, run_id, conversation_id, access_token, snapshot_json, trace_open)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                (scope, run["id"], saved["id"], access_token, _encoded(run)),
            )
            detail: dict[str, Any] = {"conversationId": saved["id"]}
            if submission_key:
                row = connection.execute(
                    "SELECT message_id FROM agent_run_submissions WHERE scope = ? AND submission_key = ?",
                    (scope, submission_key),
                ).fetchone()
                if row:
                    detail["messageId"] = row["message_id"]
                connection.execute(
                    """UPDATE agent_run_submissions SET state = 'accepted', run_id = ?,
                       conversation_json = NULL, updated_at = ?
                       WHERE scope = ? AND submission_key = ?""",
                    (run["id"], _utc_now(), scope, submission_key),
                )
            _append_trace(connection, scope, run["id"], "submission.accepted", detail)
        return saved

    def claim_submission(
        self, scope: str, key: str, conversation: dict[str, Any], *, message_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Reserve one model-billed submission before it is forwarded.

        ``claimed`` means this caller owns the forward. Otherwise the returned state
        says what is already known about the earlier attempt.
        """
        moment = now or datetime.now(timezone.utc)
        stamp = moment.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_run_submissions WHERE scope = ? AND submission_key = ?",
                (scope, key),
            ).fetchone()
            if row is None or row["state"] == "not_started":
                connection.execute(
                    """INSERT INTO agent_run_submissions
                       (scope, submission_key, conversation_id, message_id, state, conversation_json,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'submitting', ?, ?, ?)
                       ON CONFLICT(scope, submission_key) DO UPDATE SET
                       state = 'submitting', conversation_json = excluded.conversation_json,
                       updated_at = excluded.updated_at""",
                    (scope, key, conversation["id"], message_id, _encoded(conversation), stamp, stamp),
                )
                return {"claimed": True, "state": "submitting", "run_id": None}
            state = row["state"]
            if state == "submitting" and self._submission_is_stale(row, moment):
                state = "unconfirmed"
                connection.execute(
                    "UPDATE agent_run_submissions SET state = ?, updated_at = ? WHERE scope = ? AND submission_key = ?",
                    (state, stamp, scope, key),
                )
            return {"claimed": False, "state": state, "run_id": row["run_id"]}

    @staticmethod
    def _submission_is_stale(row: sqlite3.Row, moment: datetime) -> bool:
        return datetime.fromisoformat(row["updated_at"]) <= moment - timedelta(seconds=SUBMISSION_STALE_SECONDS)

    def release_submission(self, scope: str, key: str) -> None:
        """Forget an attempt the Host definitely did not accept, so it may be retried."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM agent_run_submissions WHERE scope = ? AND submission_key = ? AND state != 'accepted'",
                (scope, key),
            )

    def mark_submission(self, scope: str, key: str, state: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE agent_run_submissions SET state = ?, updated_at = ?
                   WHERE scope = ? AND submission_key = ? AND state != 'accepted'""",
                (state, _utc_now(), scope, key),
            )

    def get_submission(self, scope: str, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_run_submissions WHERE scope = ? AND submission_key = ?",
                (scope, key),
            ).fetchone()
        return dict(row) if row else None

    def unresolved_submissions(self, scope: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Attempts whose outcome is unknown: unconfirmed, or abandoned mid-forward by a crash."""
        moment = now or datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM agent_run_submissions WHERE scope = ? AND state IN ('submitting', 'unconfirmed')",
                (scope,),
            ).fetchall()
            unresolved = []
            for row in rows:
                if row["state"] == "submitting" and not self._submission_is_stale(row, moment):
                    continue
                if row["state"] == "submitting":
                    connection.execute(
                        "UPDATE agent_run_submissions SET state = 'unconfirmed', updated_at = ? "
                        "WHERE scope = ? AND submission_key = ?",
                        (moment.isoformat(), scope, row["submission_key"]),
                    )
                unresolved.append({**dict(row), "state": "unconfirmed"})
        return unresolved

    def open_submissions(self, scope: str) -> list[dict[str, Any]]:
        """Sends a reloaded browser must still treat as undecided.

        A send known not to have started is safe to repeat under any identity, and one
        the user has since moved past in that conversation is no longer theirs to retry.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT conversation_id, message_id, state, conversation_json, updated_at
                   FROM agent_run_submissions AS open
                   WHERE scope = ? AND state IN ('submitting', 'unconfirmed')
                     AND NOT EXISTS (
                         SELECT 1 FROM agent_run_submissions AS later
                         WHERE later.scope = open.scope AND later.conversation_id = open.conversation_id
                           AND later.state = 'accepted' AND later.created_at > open.created_at
                     )
                   ORDER BY updated_at DESC LIMIT 20""",
                (scope,),
            ).fetchall()
        return [
            {
                "conversationId": row["conversation_id"], "messageId": row["message_id"], "state": row["state"],
                "message": next(
                    (item.get("content") for item in json.loads(row["conversation_json"] or "{}").get("messages", [])),
                    None,
                ),
                "title": json.loads(row["conversation_json"] or "{}").get("title"),
                "createdAt": json.loads(row["conversation_json"] or "{}").get("createdAt"),
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def get_run(self, scope: str, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_conversation_runs WHERE scope = ? AND run_id = ?",
                (scope, run_id),
            ).fetchone()
        return dict(row) if row else None

    def pending_runs(self, scope: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_conversation_runs WHERE scope = ? AND delivered = 0",
                (scope,),
            ).fetchall()
        return [dict(row) for row in rows]

    def runs_to_reconcile(self, scope: str) -> list[dict[str, Any]]:
        """Runs still owed a reply, artifacts or the rest of their event trace."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_conversation_runs WHERE scope = ? AND (delivered = 0 OR trace_open = 1)",
                (scope,),
            ).fetchall()
        return [dict(row) for row in rows]

    def note_missing_read(self, scope: str, run_id: str) -> int:
        with self._connect() as connection:
            connection.execute(
                "UPDATE agent_conversation_runs SET missing_reads = missing_reads + 1 WHERE scope = ? AND run_id = ?",
                (scope, run_id),
            )
            row = connection.execute(
                "SELECT missing_reads FROM agent_conversation_runs WHERE scope = ? AND run_id = ?",
                (scope, run_id),
            ).fetchone()
        return int(row["missing_reads"]) if row else 0

    def reset_missing_reads(self, scope: str, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE agent_conversation_runs SET missing_reads = 0 "
                "WHERE scope = ? AND run_id = ? AND missing_reads != 0",
                (scope, run_id),
            )

    def lost_run_snapshot(self, scope: str, run_id: str) -> dict[str, Any] | None:
        """A terminal record for a Run its Host no longer knows. Never claims it succeeded."""
        row = self.get_run(scope, run_id)
        if row is None:
            return None
        snapshot = json.loads(row["snapshot_json"])
        if snapshot.get("status") in TERMINAL_RUN_STATES:
            return snapshot
        snapshot.pop("pendingInteraction", None)
        return {
            **snapshot, "status": "failed", "completedAt": _utc_now(),
            "error": {"code": RUN_RECORD_LOST, "message": RUN_RECORD_LOST_MESSAGE},
        }

    def record_events(self, scope: str, run_id: str, events: list[dict[str, Any]]) -> bool:
        """Apply Host events idempotently. Returns whether the terminal event was seen."""
        finished = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT event_cursor FROM agent_conversation_runs WHERE scope = ? AND run_id = ?",
                (scope, run_id),
            ).fetchone()
            if row is None:
                return False
            cursor = int(row["event_cursor"])
            for event in events:
                sequence = event.get("sequence")
                if not isinstance(sequence, int) or isinstance(sequence, bool) or event.get("runId") != run_id:
                    continue
                if event.get("status") in TERMINAL_RUN_STATES:
                    finished = True
                if sequence <= cursor:
                    continue
                if sequence > cursor + 1:
                    # The Host keeps the newest 1000 events per Run; say so rather than imply completeness.
                    _append_trace(
                        connection, scope, run_id, "events.gap",
                        {"afterSequence": cursor, "resumedAtSequence": sequence},
                        dedupe=f"gap:{cursor}:{sequence}",
                    )
                cursor = sequence
                entry = trace_entry(event)
                if entry:
                    kind, detail = entry
                    _append_trace(
                        connection, scope, run_id, kind, detail, dedupe=f"event:{sequence}",
                        at=str(event.get("at") or _utc_now()), upstream_sequence=sequence,
                    )
            connection.execute(
                "UPDATE agent_conversation_runs SET event_cursor = ?, trace_open = trace_open * ? "
                "WHERE scope = ? AND run_id = ?",
                (cursor, 0 if finished else 1, scope, run_id),
            )
        return finished

    def close_trace(self, scope: str, run_id: str, *, incomplete_reason: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if incomplete_reason:
                _append_trace(connection, scope, run_id, "trace.incomplete", {"reason": incomplete_reason})
            connection.execute(
                "UPDATE agent_conversation_runs SET trace_open = 0 WHERE scope = ? AND run_id = ?",
                (scope, run_id),
            )

    def add_trace(
        self, scope: str, run_id: str, kind: str, detail: dict[str, Any] | None = None, *, dedupe: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _append_trace(connection, scope, run_id, kind, detail, dedupe=dedupe)

    def trace_entries(self, scope: str, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT seq, kind, upstream_sequence, at, detail_json FROM agent_run_trace
                   WHERE scope = ? AND run_id = ? ORDER BY seq""",
                (scope, run_id),
            ).fetchall()
        return [
            {"seq": row["seq"], "kind": row["kind"], "upstreamSequence": row["upstream_sequence"],
             "at": row["at"], "detail": json.loads(row["detail_json"])}
            for row in rows
        ]

    def submission_for_run(self, scope: str, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT message_id, state FROM agent_run_submissions WHERE scope = ? AND run_id = ?",
                (scope, run_id),
            ).fetchone()
        return dict(row) if row else None

    def link_job(self, scope: str, run_id: str, job_kind: str, job_id: str, *, owned: bool) -> bool:
        """Tie background work to the Run that asked for it. Unknown or finished Runs get no link."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT snapshot_json FROM agent_conversation_runs WHERE scope = ? AND run_id = ?",
                (scope, run_id),
            ).fetchone()
            if row is None or json.loads(row["snapshot_json"]).get("status") in TERMINAL_RUN_STATES:
                return False
            connection.execute(
                """INSERT OR IGNORE INTO agent_run_jobs (scope, run_id, job_kind, job_id, owned, linked_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (scope, run_id, job_kind, job_id, int(owned), _utc_now()),
            )
            _append_trace(
                connection, scope, run_id, "job.linked", {"kind": job_kind, "jobId": job_id, "owned": owned},
                dedupe=f"job:{job_kind}:{job_id}",
            )
        return True

    def run_jobs(self, scope: str, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_kind, job_id, owned FROM agent_run_jobs WHERE scope = ? AND run_id = ? ORDER BY linked_at, job_id",
                (scope, run_id),
            ).fetchall()
        return [{"kind": row["job_kind"], "id": row["job_id"], "owned": bool(row["owned"])} for row in rows]

    def prune_records(self, scope: str, *, retention_days: int, now: datetime | None = None) -> int:
        """Apply local retention to finished traces and settled submissions, never to open work."""
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=retention_days)).isoformat()
        removed = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for row in connection.execute(
                "SELECT run_id, snapshot_json FROM agent_conversation_runs WHERE scope = ? AND delivered = 1 AND trace_open = 0",
                (scope,),
            ).fetchall():
                snapshot = json.loads(row["snapshot_json"])
                finished = str(snapshot.get("completedAt") or snapshot.get("createdAt") or "")
                if snapshot.get("status") in TERMINAL_RUN_STATES and finished and _before(finished, cutoff):
                    removed += connection.execute(
                        "DELETE FROM agent_run_trace WHERE scope = ? AND run_id = ?", (scope, row["run_id"]),
                    ).rowcount
                    connection.execute(
                        "DELETE FROM agent_run_jobs WHERE scope = ? AND run_id = ?", (scope, row["run_id"]),
                    )
            connection.execute(
                "DELETE FROM agent_run_submissions WHERE scope = ? AND state IN ('accepted', 'not_started') AND updated_at < ?",
                (scope, cutoff),
            )
        return removed

    def conversation_runs(self, scope: str) -> list[dict[str, Any]]:
        """Return the latest snapshot per conversation, without any credentials."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT conversation_id, snapshot_json FROM agent_conversation_runs
                   WHERE rowid IN (SELECT MAX(rowid) FROM agent_conversation_runs WHERE scope = ?
                                   GROUP BY conversation_id)
                   AND conversation_id IN (SELECT id FROM agent_conversations)
                   ORDER BY rowid DESC LIMIT ?""", (scope, MAX_SHARED_CONVERSATIONS),
            ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest.setdefault(row["conversation_id"], {
                "conversationId": row["conversation_id"], "run": json.loads(row["snapshot_json"]),
            })
        return list(latest.values())

    def record_run(
        self, scope: str, run: dict[str, Any], *, artifacts: list[dict] | None = None,
        checkpoint: dict | None = None, delivered: bool = False,
        reconciliation: dict[str, Any] | None = None,
    ) -> None:
        """Monotonic snapshots and one deterministic terminal reply share a transaction."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_conversation_runs WHERE scope = ? AND run_id = ?",
                (scope, run["id"]),
            ).fetchone()
            if row is None:
                return
            previous = json.loads(row["snapshot_json"])
            # A slow read cannot move a task backwards, and a terminal state is final.
            if previous["status"] in TERMINAL_RUN_STATES or status_rank(run.get("status")) < status_rank(previous["status"]):
                run = previous
            terminal = run["status"] in TERMINAL_RUN_STATES
            connection.execute(
                """UPDATE agent_conversation_runs SET snapshot_json = ?, delivered = MAX(delivered, ?)
                   WHERE scope = ? AND run_id = ?""",
                (_encoded(run), int(terminal and delivered), scope, run["id"]),
            )
            if not terminal:
                return
            if reconciliation is not None:
                _append_trace(connection, scope, run["id"], "delivery.reconciled", reconciliation)
            elif delivered:
                _append_trace(connection, scope, run["id"], "delivery.reconciled", {"expected": [], "delivered": []})
            conversation = connection.execute(
                "SELECT payload_json FROM agent_conversations WHERE id = ?", (row["conversation_id"],),
            ).fetchone()
            if conversation is None:
                return
            value = json.loads(conversation["payload_json"])
            created_at = run.get("completedAt") or run["createdAt"]
            content = str(run.get("summary") or (run.get("error") or {}).get("message") or {
                "cancelled": "The response was cancelled.",
                "blocked": "The Agent could not complete this request.",
            }.get(run["status"], "The Agent completed without a text response."))[:40_000]
            value["messages"] = [{
                "id": f"assistant:{run['id']}", "role": "assistant", "content": content,
                "createdAt": created_at, "runId": run["id"],
                **({"error": True} if run["status"] in {"failed", "cancelled"} else {}),
                **({"artifacts": artifacts} if artifacts else {}),
            }]
            value["updatedAt"] = max(value["updatedAt"], created_at)
            if checkpoint:
                value["researchCheckpoint"] = checkpoint
            self._upsert(connection, value)
