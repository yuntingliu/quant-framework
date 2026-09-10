"""Durable single-workspace storage for shared Agent conversations."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

MAX_SHARED_CONVERSATIONS = 100
MAX_MESSAGES_PER_CONVERSATION = 80
MAX_CONVERSATION_BYTES = 4_000_000
TERMINAL_RUN_STATES = frozenset({"completed", "blocked", "failed", "cancelled"})

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
"""


def _encoded(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
    ) -> dict[str, Any]:
        """Commit the user turn and recovery credential before acknowledging submission."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = self._upsert(connection, conversation)
            connection.execute(
                """INSERT INTO agent_conversation_runs
                   (scope, run_id, conversation_id, access_token, snapshot_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (scope, run["id"], saved["id"], access_token, _encoded(run)),
            )
        return saved

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
            if previous["status"] in TERMINAL_RUN_STATES:
                run = previous
            terminal = run["status"] in TERMINAL_RUN_STATES
            connection.execute(
                """UPDATE agent_conversation_runs SET snapshot_json = ?, delivered = MAX(delivered, ?)
                   WHERE scope = ? AND run_id = ?""",
                (_encoded(run), int(terminal and delivered), scope, run["id"]),
            )
            if not terminal:
                return
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
