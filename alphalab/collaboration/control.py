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


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


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
            before_expiry = (state["status"], [c["status"] for c in state["children"]])
            self._expire(state, now)
            if before_expiry != (state["status"], [c["status"] for c in state["children"]]):
                budgets.append_event(db, state, {"kind": "deadline_expired", "outcome": state["status"]})
            # Preserve expiry even if the following transition is rejected,
            # without releasing the writer lock between budget decisions.
            db.execute("UPDATE controls SET payload=? WHERE task_id=?", (encode_json(state, maximum=MAX_STATE_BYTES), task_id))
            db.execute("SAVEPOINT transition")
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
            except CollaborationError as error:
                if error.code in budgets.LIMIT_ERRORS:
                    budgets.block(state, error.code)
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
            return {key: state[key] for key in ("status", "reason", "candidate", "manifest", "children", "deadline")}
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
            require(not any(c["status"] == "running" for c in children), "CHILD_LIMIT")
            require(context["task"] == task, "TASK_MISMATCH")
            require(args["candidate"]["candidate_id"] == state["candidate"]["candidate_id"] and
                    args["candidate"]["revision"] > state["candidate"]["revision"], "STALE_CANDIDATE")
            state.update(candidate=args["candidate"], manifest=context["manifest_ref"])
            return {"candidate": state["candidate"], "manifest": state["manifest"]}
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
