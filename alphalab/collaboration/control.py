"""Host-only serial delegation ledger; never a business-output publisher.

One SQLite transaction owns each transition. Python entry points are trusted Host
APIs, not model tools or an OS sandbox. Evidence remains in its immutable store.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import budget as budgets
from .contracts import (
    CollaborationError,
    decode_json,
    encode_json,
    require,
    sha256,
    validate_contract,
)
from .evidence import EvidenceStore

REVIEW_SCOPES = {"model.invoke", "evidence.read", "review.submit"}
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_PUBLICATION_HISTORY = 100


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()

def _publication_state(state):
    """Fingerprint fields that affect finalization eligibility."""
    return encode_json({
        "status": state.get("status"),
        "reason": state.get("reason"),
        "candidate": state.get("candidate"),
        "manifest": state.get("manifest"),
        "children": state.get("children", []),
        "corrections": state.get("corrections", []),
        "correction": state.get("correction"),
        "deadline": state.get("deadline"),
    })


class ControlStore:
    """Small durable state machine, scoped to a trusted evidence-store root."""

    def __init__(self, root: Path, evidence_root: Path):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "control.sqlite3"
        self.evidence = EvidenceStore(evidence_root)
        self._safe_path()
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS controls(task_id TEXT PRIMARY KEY, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY, task_id TEXT NOT NULL, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(
                    sequence INTEGER PRIMARY KEY, task_id TEXT NOT NULL, call_id TEXT NOT NULL,
                    payload BLOB NOT NULL, UNIQUE(task_id, call_id));
            """)
            db.execute("BEGIN IMMEDIATE")
            if "event_id" not in {row[1] for row in db.execute("PRAGMA table_info(events)")}:
                db.execute("ALTER TABLE events ADD COLUMN event_id TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS event_identity ON events(task_id,event_id)")
            db.commit()

    def _safe_path(self):
        require(self.root.resolve() == self.root and not self.path.is_symlink(), "ACCESS_DENIED")
        for suffix in ("-journal", "-wal", "-shm"):
            require(not Path(str(self.path) + suffix).is_symlink(), "ACCESS_DENIED")

    def _connect(self):
        self._safe_path()
        db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def context(self, task_id, candidate):
        return self.evidence.candidate_context(task_id, candidate)

    def register(self, task_id, candidate, policy, budget=None, model=None):
        context = self.context(task_id, candidate)
        task = context["task"]
        require(isinstance(policy, dict) and set(policy) == {"scopes", "operations"})
        require(isinstance(policy["scopes"], list) and all(isinstance(s, str) for s in policy["scopes"]))
        ledger = budgets.configuration(budget, model)
        fingerprint = sha256(encode_json({"task": task, "policy": policy,
                                         "budget": ledger["limits"], "model": ledger["model"]}))
        state = {"task": task, "policy": policy, "fingerprint": fingerprint,
            "candidate": candidate, "manifest": context["manifest_ref"],
            "status": "registered", "reason": None, "children": [], "receipts": {},
            "version": 0,
            "deadline": _time(task["deadline"]), "ledger": ledger}
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT payload FROM controls WHERE task_id=?", (task_id,)).fetchone()
            if previous:
                old = decode_json(previous[0])
                require("ledger" in old, "BUDGET_UNVERIFIABLE")
                require(old["fingerprint"] == fingerprint and old["candidate"] == candidate, "DUPLICATE_CONFLICT")
            else:
                db.execute("INSERT INTO controls VALUES (?,?)", (task_id, encode_json(state, maximum=MAX_STATE_BYTES)))
            db.commit()
        return context

    def _publication_context(self, state, task_id, destination):
        """Build trusted finalization context from the current approved task."""
        require(state.get("status") == "completed", "FINALIZATION_BLOCKED")
        require(self._approved(state), "FINALIZATION_BLOCKED")

        latest = state["children"][-1]
        context = self.context(task_id, state["candidate"])
        require(context["manifest_ref"] == state["manifest"], "STALE_CANDIDATE")
        context.update(
            delegation=latest["delegation"],
            review=latest["review"],
            correction=state.get("correction"),
            task_state={
                "status": "ready",
                "version": state.get("version", 0),
            },
            destination=destination,
        )
        return context

    def prepare_publication(self, task_id, request, *, destination):
        """Persist one immutable, idempotent publication intent.

        ``destination`` must be obtained by the trusted coordinator from the
        authoritative Local Host workspace snapshot. The Local Host must still
        re-check its expected versions inside its own protected commit.
        """
        require(isinstance(request, dict), "INVALID_CONTRACT")
        require(request.get("task_id") == task_id, "TASK_MISMATCH")
        idempotency_key = request.get("idempotency_key")
        require(
            isinstance(idempotency_key, str)
            and 1 <= len(idempotency_key) <= 200
            and bool(idempotency_key.strip()),
            "INVALID_CONTRACT",
        )
        request_bytes = encode_json(request, maximum=MAX_STATE_BYTES)
        request_digest = sha256(request_bytes)

        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT payload FROM controls WHERE task_id=?", (task_id,)
                ).fetchone()
                require(row is not None, "TASK_MISMATCH")
                state = decode_json(row[0])

                history = state.setdefault("publication_history", [])
                require(isinstance(history, list), "INTEGRITY_ERROR")
                current = state.get("publication")
                records = [*history, *([current] if current else [])]

                # Idempotency lookup precedes fresh preflight. A replay of a
                # committed request must return its receipt even if the live
                # workspace has subsequently advanced to a newer revision.
                for previous in records:
                    if previous.get("idempotency_key") == idempotency_key:
                        require(
                            previous.get("request_sha256") == request_digest,
                            "DUPLICATE_CONFLICT",
                        )
                        db.commit()
                        return decode_json(encode_json(previous))

                if current is not None:
                    require(current.get("status") == "rejected", "INVALID_STATE")

                require(
                    not any(item.get("status") == "committed" for item in history),
                    "FINALIZATION_BLOCKED",
                )
                if current is not None:
                    require(len(history) < MAX_PUBLICATION_HISTORY, "EVIDENCE_LIMIT")
                    history.append(current)

                context = self._publication_context(state, task_id, destination)
                validate_contract("final_output_request", request, context)

                intent = {
                    "intent_id": str(uuid4()),
                    "status": "pending",
                    "task_id": task_id,
                    "request_id": request["request_id"],
                    "idempotency_key": idempotency_key,
                    "request_sha256": request_digest,
                    "request": decode_json(request_bytes),
                    "created_at": budgets.stamp(),
                    "receipt": None,
                    "failure": None,
                }
                state["publication"] = intent
                budgets.append_event(
                    db,
                    state,
                    {
                        "kind": "publication_intent_prepared",
                        "outcome": "pending",
                        "intent_id": intent["intent_id"],
                        "idempotency_key": idempotency_key,
                        "request_sha256": request_digest,
                    },
                )
                db.execute(
                    "UPDATE controls SET payload=? WHERE task_id=?",
                    (encode_json(state, maximum=MAX_STATE_BYTES), task_id),
                )
                db.commit()
                return decode_json(encode_json(intent))
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise

    def get_publication(self, task_id, *, intent_id=None, idempotency_key=None):
        """Return a publication intent/receipt to trusted Host code only."""
        require((intent_id is None) != (idempotency_key is None), "INVALID_CONTRACT")
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT payload FROM controls WHERE task_id=?", (task_id,)
            ).fetchone()
            require(row is not None, "TASK_MISMATCH")
            state = decode_json(row[0])

        records = [
            *state.get("publication_history", []),
            *([state["publication"]] if state.get("publication") else []),
        ]
        field, expected = (
            ("intent_id", intent_id)
            if intent_id is not None
            else ("idempotency_key", idempotency_key)
        )
        matches = [item for item in records if item.get(field) == expected]
        require(len(matches) == 1, "TASK_MISMATCH")
        return decode_json(encode_json(matches[0]))

    def record_publication_receipt(self, task_id, intent_id, receipt):
        """Durably reconcile a Local Host commit receipt into ControlStore."""
        required = {
            "task_id", "intent_id", "request_id", "idempotency_key",
            "request_sha256", "payload_sha256", "candidate", "manifest",
            "review_submission_id", "destination", "workspace_revision",
            "report_revision", "committed_at",
        }
        require(isinstance(receipt, dict) and set(receipt) == required, "INVALID_CONTRACT")

        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT payload FROM controls WHERE task_id=?", (task_id,)
                ).fetchone()
                require(row is not None, "TASK_MISMATCH")
                state = decode_json(row[0])
                intent = state.get("publication")
                require(intent is not None and intent.get("intent_id") == intent_id, "TASK_MISMATCH")

                if intent.get("status") == "committed":
                    require(intent.get("receipt") == receipt, "DUPLICATE_CONFLICT")
                    db.commit()
                    return decode_json(encode_json(intent["receipt"]))

                require(intent.get("status") == "pending", "INVALID_STATE")
                request = intent["request"]
                require(state.get("status") == "completed" and self._approved(state), "FINALIZATION_BLOCKED")
                require(state.get("version", 0) == request["expected_task_version"], "FINALIZATION_BLOCKED")
                require(state["candidate"] == request["candidate"], "STALE_CANDIDATE")
                require(state["manifest"] == request["manifest"], "STALE_CANDIDATE")

                destination = request["destination"]
                expected_workspace_revision = destination["expected_workspace_revision"] + 1
                expected_report_revision = (
                    1 if destination["mode"] == "create"
                    else destination["expected_report_revision"] + 1
                )
                require(
                    receipt["task_id"] == task_id
                    and receipt["intent_id"] == intent_id
                    and receipt["request_id"] == request["request_id"]
                    and receipt["idempotency_key"] == intent["idempotency_key"]
                    and receipt["request_sha256"] == intent["request_sha256"]
                    and receipt["payload_sha256"] == request["payload_sha256"]
                    and receipt["candidate"] == request["candidate"]
                    and receipt["manifest"] == request["manifest"]
                    and receipt["review_submission_id"] == request["review_submission_id"]
                    and receipt["destination"] == destination,
                    "TASK_MISMATCH",
                )
                require(
                    type(receipt["workspace_revision"]) is int
                    and receipt["workspace_revision"] == expected_workspace_revision
                    and type(receipt["report_revision"]) is int
                    and receipt["report_revision"] == expected_report_revision,
                    "VERSION_CONFLICT",
                )
                require(isinstance(receipt["committed_at"], str), "INVALID_CONTRACT")
                try:
                    committed_at = datetime.fromisoformat(
                        receipt["committed_at"].replace("Z", "+00:00")
                    )
                except ValueError as exc:
                    raise CollaborationError("INVALID_CONTRACT") from exc
                require(committed_at.tzinfo is not None, "INVALID_CONTRACT")

                intent["status"] = "committed"
                intent["receipt"] = decode_json(encode_json(receipt))
                intent["committed_at"] = receipt["committed_at"]
                budgets.append_event(
                    db,
                    state,
                    {
                        "kind": "publication_receipt_recorded",
                        "outcome": "committed",
                        "intent_id": intent_id,
                        "idempotency_key": intent["idempotency_key"],
                        "request_sha256": intent["request_sha256"],
                        "workspace_revision": receipt["workspace_revision"],
                        "report_revision": receipt["report_revision"],
                    },
                )
                db.execute(
                    "UPDATE controls SET payload=? WHERE task_id=?",
                    (encode_json(state, maximum=MAX_STATE_BYTES), task_id),
                )
                db.commit()
                return decode_json(encode_json(receipt))
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise

    def abort_publication(
        self, task_id, intent_id, *, error_code, reason, confirmed_not_committed=False
    ):
        """Record a definite pre-commit rejection; never use for an unknown outcome.

        A timeout or lost HTTP response is not proof that the Local Host failed
        to commit. In that case, query the Local Host by idempotency key instead.
        """
        require(confirmed_not_committed is True, "INVALID_CONTRACT")
        require(isinstance(error_code, str) and re.fullmatch(r"[A-Z_]{1,80}", error_code), "INVALID_CONTRACT")
        require(isinstance(reason, str) and 1 <= len(reason.strip()) <= 4000, "INVALID_CONTRACT")

        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT payload FROM controls WHERE task_id=?", (task_id,)
                ).fetchone()
                require(row is not None, "TASK_MISMATCH")
                state = decode_json(row[0])
                intent = state.get("publication")
                require(intent is not None and intent.get("intent_id") == intent_id, "TASK_MISMATCH")
                failure = {
                    "error_code": error_code,
                    "reason": reason.strip(),
                }
                if intent.get("status") == "rejected":
                    require(intent.get("failure") == failure, "DUPLICATE_CONFLICT")
                    db.commit()
                    return decode_json(encode_json(intent))
                require(intent.get("status") == "pending", "INVALID_STATE")
                intent["status"] = "rejected"
                intent["failure"] = failure
                intent["rejected_at"] = budgets.stamp()
                budgets.append_event(
                    db,
                    state,
                    {
                        "kind": "publication_intent_rejected",
                        "outcome": "rejected",
                        "intent_id": intent_id,
                        "idempotency_key": intent["idempotency_key"],
                        "error_code": error_code,
                    },
                )
                db.execute(
                    "UPDATE controls SET payload=? WHERE task_id=?",
                    (encode_json(state, maximum=MAX_STATE_BYTES), task_id),
                )
                db.commit()
                return decode_json(encode_json(intent))
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise

    def request(self, task_id, operation, **args):
        """Host-only operations. Model-facing routing lives in the Node Host."""
        context = None
        if operation == "candidate":
            context = self.context(task_id, args["candidate"])
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM controls WHERE task_id=?", (task_id,)).fetchone()
            require(row is not None, "TASK_MISMATCH")
            state = decode_json(row[0])
            now = datetime.now(timezone.utc).timestamp()
            before_expiry = _publication_state(state)

            self._expire(state, now)

            if _publication_state(state) != before_expiry:
                state["version"] = state.get("version", 0) + 1
                budgets.append_event(
                    db,
                    state,
                    {
                        "kind": "deadline_expired",
                        "outcome": state["status"],
                    },
                )
            # Preserve expiry even if the following transition is rejected,
            # without releasing the writer lock between budget decisions.
            db.execute("UPDATE controls SET payload=? WHERE task_id=?", (encode_json(state, maximum=MAX_STATE_BYTES), task_id))
            db.execute("SAVEPOINT transition")
            before_transition = _publication_state(state)
            try:
                if operation in {"reserve", "dispatch", "settle", "reconcile", "accounting", "requests", "human"}:
                    require("ledger" in state, "BUDGET_UNVERIFIABLE")
                    result = budgets.operate(db, state, operation, args)
                elif operation == "event":
                    result = self._transition(state, operation, args, context)
                    result = budgets.append_event(db, state, result)
                elif operation == "events":
                    after, limit = args.get("after", 0), args.get("limit", 500)
                    require(budgets.integer(after) and budgets.integer(limit, 1) and limit <= 500)
                    result = [{"sequence": row[0], **decode_json(row[1])} for row in db.execute(
                        "SELECT sequence,payload FROM events WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                        (task_id, after, limit))]
                else:
                    before = state["status"]
                    if operation == "start" and "ledger" in state:
                        count = budgets.totals(budgets.rows(db, state))
                        limits = state["ledger"]["limits"]
                        require(count["model_calls"] < limits["max_model_calls"] and count["tool_calls"] < limits["max_tool_calls"] and
                                all(limits[limit] is None or count[charged] < limits[limit] for limit, charged in
                                    (("max_total_tokens", "charged_tokens"), ("max_cost_micros", "charged_cost_micros"))), "BUDGET_EXHAUSTED")
                    result = self._transition(state, operation, args, context)

                    if operation != "view":
                        child = args.get("delegation") or next((c["delegation"] for c in state["children"] if c["delegation"]["child_task_id"] == args.get("child_task_id")), None)
                        identity = {"task_id": task_id, "role": "researcher", "node_id": state["task"]["researcher_node_id"]} if child is None else {
                            "task_id": task_id, "role": "reviewer", "node_id": child["node_id"], "child_task_id": child["child_task_id"], "attempt_id": child["attempt_id"]}
                        budgets.append_event(db, state, {**budgets.owner(state, identity), "kind": operation, "outcome": state["status"],
                                                         "previous_status": before})

                    if _publication_state(state) != before_transition:
                        state["version"] = state.get("version", 0) + 1
            except CollaborationError as error:
                if error.code in budgets.LIMIT_ERRORS:
                    budgets.block(state, error.code)
                    if _publication_state(state) != before_transition:
                        state["version"] = state.get("version", 0) + 1
                    budgets.append_event(db, state, {"kind": "budget_denied", "error_code": error.code,
                                                     "call_id": args.get("call_id"),
                                                     **budgets.owner(state, args.get("identity", {"task_id": task_id, "role": "researcher", "node_id": state["task"]["researcher_node_id"]}))})
                    db.execute("UPDATE controls SET payload=? WHERE task_id=?", (encode_json(state, maximum=MAX_STATE_BYTES), task_id))
                    db.commit()
                else:
                    db.execute("ROLLBACK TO transition")
                    db.commit()
                raise
            db.execute("UPDATE controls SET payload=? WHERE task_id=?", (encode_json(state, maximum=MAX_STATE_BYTES), task_id))
            db.commit()
            return result

    @staticmethod
    def _expire(state, now):
        if state["status"] in {"registered", "running"} and now >= state["deadline"]:
            state.update(status="timed_out", reason="deadline")
            if "ledger" in state:
                state["ledger"]["finished_at"] = budgets.stamp()
        for child in state["children"]:
            if child["status"] == "running" and (state["status"] != "running" or now >= _time(child["delegation"]["deadline"])):
                child.update(status="timed_out" if state["status"] == "timed_out" or state["status"] == "running" else "cancelled")

    def _transition(self, state, operation, args, context):
        children = state["children"]
        task = state["task"]
        if operation == "view":
            return {
                key: state[key]
                for key in (
                    "status",
                    "reason",
                    "candidate",
                    "manifest",
                    "children",
                    "deadline",
                )
            } | {
                "version": state.get("version", 0),
                "corrections": state.get("corrections", []),
                "correction": state.get("correction"),
            }
        if operation == "event":
            event = args["event"]
            require(set(event) <= {"event_id", "kind", "role", "root_run_id", "node_id", "child_task_id", "attempt_id", "usage", "late", "error_code"})
            require(event.get("role") in {"researcher", "reviewer"} and
                    event.get("kind") in {"model_started", "model_settled", "denied", "late_result"})
            require(event.get("error_code") is None or re.fullmatch(r"[A-Z_]{1,80}", event["error_code"]))
            if event.get("usage") is not None:
                require(isinstance(event["usage"], dict) and set(event["usage"]) <= {
                    "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_output_tokens", "cost_micros", "currency"})
            require(len(encode_json(event)) <= 4096, "EVIDENCE_LIMIT")
            identity = {"task_id": task["task_id"], "role": event["role"], "node_id": event.get("node_id", task[event["role"] + "_node_id"])}
            if event["role"] == "reviewer":
                child = next((c for c in children if c["delegation"]["child_task_id"] == event.get("child_task_id")), children[-1] if children else None)
                require(child is not None, "TASK_MISMATCH")
                identity.update(child_task_id=child["delegation"]["child_task_id"], attempt_id=event.get("attempt_id", child["delegation"]["attempt_id"]))
            require(event.get("root_run_id", task["root_run_id"]) == task["root_run_id"], "TASK_MISMATCH")
            return {**event, **budgets.owner(state, identity)}
        if operation == "begin":
            require(state["status"] != "timed_out", "INVALID_STATE")
            require(state["status"] == "registered", "DUPLICATE_CHILD_TASK")
            require(datetime.now(timezone.utc).timestamp() < state["deadline"], "INVALID_STATE")
            state["status"] = "running"
            if "ledger" in state:
                state["ledger"]["started_at"] = budgets.stamp()
                state["ledger"]["queue_ms"] = max(0, round((_time(state["ledger"]["started_at"]) - _time(state["ledger"]["registered_at"])) * 1000))
                state["deadline"] = min(state["deadline"], datetime.now(timezone.utc).timestamp() +
                                        state["ledger"]["limits"]["wall_time_ms"] / 1000)
            return {"status": "running", "deadline": state["deadline"]}
        if operation in {"cancel", "timeout", "recover", "finish_root"}:
            if state["status"] in {"registered", "running"}:
                status = "cancelled" if operation == "cancel" else "blocked"
                if operation == "timeout":
                    status = "timed_out"
                if operation == "finish_root" and args["status"] == "done":
                    require(datetime.now(timezone.utc).timestamp() < state["deadline"], "INVALID_STATE")
                    require(self._approved(state), "FINALIZATION_BLOCKED")
                    status = "completed"
                state.update(status=status, reason=args.get("reason", operation))
                if "ledger" in state:
                    state["ledger"]["finished_at"] = budgets.stamp()
                for child in children:
                    if child["status"] == "running":
                        child["status"] = "timed_out" if status == "timed_out" else "cancelled"
            if operation == "finish_root" and "ledger" in state and state["ledger"]["elapsed_ms"] is None:
                require(args.get("elapsed_ms") is None or budgets.integer(args["elapsed_ms"]))
                state["ledger"]["elapsed_ms"] = args.get("elapsed_ms")
            return {"status": state["status"], "reason": state["reason"]}
        if operation == "submit":
            raw = args["payload_json"].encode("utf-8")
            require(sha256(raw) == args["sha256"], "INTEGRITY_ERROR")
            review = decode_json(raw)
            require(review.get("submission_id") == args["submission_id"], "TASK_MISMATCH")
            receipt = state["receipts"].get(args["submission_id"])
            if receipt:
                require(receipt["sha256"] == args["sha256"] and receipt["identity"] == args["identity"], "DUPLICATE_CONFLICT")
                return {"submission_id": args["submission_id"], "sha256": args["sha256"]}
        require(state["status"] == "running", "INVALID_STATE")
        if operation == "candidate":
            require(
                not any(c["status"] == "running" for c in children),
                "CHILD_LIMIT",
            )
            require(context["task"] == task, "TASK_MISMATCH")

            previous_candidate = state["candidate"]
            new_candidate = args["candidate"]

            require(
                new_candidate["candidate_id"]
                == previous_candidate["candidate_id"]
                and new_candidate["revision"]
                > previous_candidate["revision"],
                "STALE_CANDIDATE",
            )

            correction = args.get("correction")
            previous_child = children[-1] if children else None
            previous_review = (
                previous_child.get("review")
                if previous_child else None
            )

            if previous_child is not None:
                require(
                    previous_child["status"] == "completed"
                    and previous_review is not None
                    and previous_review["status"] == "findings",
                    "INVALID_STATE",
                )

                require(
                    correction is not None,
                    "INVALID_CORRECTION",
                )

                previous_context = self.context(
                    task["task_id"],
                    previous_candidate,
                )

                correction_context = {
                    **context,
                    "review": previous_review,
                    "next_candidate": new_candidate,
                    "previous_manifest": previous_context["manifest"],
                }

                validate_contract(
                    "correction_response",
                    correction,
                    correction_context,
                )

                require(
                    not correction["remaining_disagreements"],
                    "INVALID_CORRECTION",
                )

                # Preserve every validated correction response.
                state.setdefault("corrections", []).append(correction)

                # Retain the latest correction for compatibility with
                # downstream consumers.
                state["correction"] = correction

            else:
                require(
                    correction is None,
                    "INVALID_CORRECTION",
                )

            state.update(
                candidate=new_candidate,
                manifest=context["manifest_ref"],
            )

            return {
                "candidate": state["candidate"],
                "manifest": state["manifest"],
            }
        if operation == "start":
            delegation = args["delegation"]
            require(not any(c["delegation"]["child_task_id"] == delegation.get("child_task_id") or
                            c["delegation"]["attempt_id"] == delegation.get("attempt_id") for c in children), "DUPLICATE_CHILD_TASK")
            require(not any(c["status"] == "running" for c in children) and len(children) < task["max_review_rounds"], "CHILD_LIMIT")
            require(delegation.get("round") == len(children) + 1, "CHILD_LIMIT")
            validate_contract("delegation_request", delegation, self.context(task["task_id"], state["candidate"]))
            require(_time(delegation["deadline"]) > datetime.now(timezone.utc).timestamp(), "INVALID_STATE")
            scopes = sorted(REVIEW_SCOPES.intersection(state["policy"]["scopes"]))
            require(set(scopes) == REVIEW_SCOPES, "ACCESS_DENIED")
            child = {"delegation": delegation, "scopes": scopes, "status": "running", "review": None, "needs_parent_escalation": False,
                     "budget_id": task["budget_id"]}
            children.append(child)
            return child
        child = next((c for c in children if c["delegation"]["child_task_id"] == args.get("child_task_id")), None)
        require(child is not None, "TASK_MISMATCH")
        if operation == "cancel_child":
            if child["status"] == "running":
                child["status"] = "cancelled"
            return child
        require(child["status"] == "running", "INVALID_STATE")
        if operation == "escalate":
            child["needs_parent_escalation"] = True
            return {"status": "needs_parent_escalation"}
        if operation == "finish_child":
            child["status"] = "completed" if args["status"] == "done" and child["review"] else "blocked"
            return child
        require(operation == "submit")
        d = child["delegation"]
        expected = {"role": "reviewer", "task_id": task["task_id"], "node_id": d["node_id"],
                    "child_task_id": d["child_task_id"], "attempt_id": d["attempt_id"]}
        require(args["identity"] == expected, "TASK_MISMATCH")
        require(child["review"] is None, "DUPLICATE_CONFLICT")
        context = self.context(task["task_id"], state["candidate"])
        context["delegation"] = d
        validate_contract("review_result", review, context)
        require(not child["needs_parent_escalation"] or review["status"] in {"blocked", "incomplete"}, "FINALIZATION_BLOCKED")
        require(datetime.now(timezone.utc).timestamp() < min(state["deadline"], _time(d["deadline"])), "INVALID_STATE")
        child["review"] = review
        state["receipts"][args["submission_id"]] = {"sha256": args["sha256"], "identity": expected}
        return {"submission_id": args["submission_id"], "sha256": args["sha256"]}

    @staticmethod
    def _approved(state):
        children = state["children"]
        return bool(children and children[-1]["status"] == "completed" and
                    not children[-1]["needs_parent_escalation"] and children[-1]["review"] and
                    children[-1]["review"]["status"] == "pass" and
                    children[-1]["review"]["candidate"] == state["candidate"] and
                    children[-1]["review"]["manifest"] == state["manifest"])
