"""Root budget operations inside ControlStore's existing SQLite transaction.

Only trusted Host adapters supply bounds and usage. Reservations are conservative
until reconciled; neither a missing bill nor a process restart makes work free.
Money is integer millionths of the pinned currency. Token detail subsets are
retained for inspection, never added a second time to total tokens.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from .contracts import decode_json, encode_json, require, sha256

DEFAULT_BUDGET = {
    "version": 1, "max_model_calls": 64, "max_tool_calls": 256,
    "wall_time_ms": 180_000, "max_output_tokens": 4096,
    "max_total_tokens": None, "max_cost_micros": None, "currency": None,
}
DEFAULT_MODEL = {
    "provider_id": "host-callback", "model_id": "unspecified", "profile_id": "unspecified",
    "configuration_id": "host-callback-v1", "bounds_id": None,
    "supports_output_cap": False, "pricing": None,
}
LIMIT_ERRORS = {"BUDGET_EXHAUSTED", "BUDGET_UNVERIFIABLE", "CURRENCY_MISMATCH", "BOUND_EXCEEDED"}


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 9_007_199_254_740_991


def stamp():
    return datetime.now(timezone.utc).isoformat()


def configuration(budget=None, model=None):
    budget = {**DEFAULT_BUDGET, **(budget or {})}
    model = model or DEFAULT_MODEL.copy()
    require(set(budget) == set(DEFAULT_BUDGET) and budget["version"] == 1)
    for key in ("max_model_calls", "max_tool_calls", "wall_time_ms", "max_output_tokens"):
        require(integer(budget[key], 1))
    for key in ("max_total_tokens", "max_cost_micros"):
        require(budget[key] is None or integer(budget[key]))
    require(budget["currency"] is None or re.fullmatch(r"[A-Z]{3}", budget["currency"]))
    require(budget["max_cost_micros"] is None or budget["currency"] is not None)
    require(set(model) == set(DEFAULT_MODEL))
    for key in ("provider_id", "model_id", "profile_id", "configuration_id"):
        require(isinstance(model[key], str) and 0 < len(model[key]) <= 200)
    require(model["bounds_id"] is None or isinstance(model["bounds_id"], str) and 0 < len(model["bounds_id"]) <= 200)
    require(type(model["supports_output_cap"]) is bool)
    price = model["pricing"]
    if price is not None:
        require(set(price) == {"id", "currency", "input_micros_per_million", "output_micros_per_million"})
        require(isinstance(price["id"], str) and 0 < len(price["id"]) <= 200)
        require(isinstance(price["currency"], str) and re.fullmatch(r"[A-Z]{3}", price["currency"]))
        require(all(integer(price[k]) for k in ("input_micros_per_million", "output_micros_per_million")))
    return {"limits": budget, "model": model, "registered_at": stamp(), "started_at": None,
            "finished_at": None, "queue_ms": None, "elapsed_ms": None}


def owner(state, identity, *, active=False):
    task = state["task"]
    require(isinstance(identity, dict) and identity.get("task_id") == task["task_id"], "TASK_MISMATCH")
    if identity.get("role") == "researcher":
        expected = {"role": "researcher", "task_id": task["task_id"], "node_id": task["researcher_node_id"]}
        child = None
    else:
        child = next((c for c in state["children"] if c["delegation"]["child_task_id"] == identity.get("child_task_id")), None)
        require(child is not None, "TASK_MISMATCH")
        d = child["delegation"]
        expected = {"role": "reviewer", "task_id": task["task_id"], "node_id": d["node_id"],
                    "child_task_id": d["child_task_id"], "attempt_id": d["attempt_id"]}
    require(identity == expected, "TASK_MISMATCH")
    if active:
        require(state["status"] == "running" and (child is None or child["status"] == "running"), "INVALID_STATE")
    return {**expected, "parent_run_id": task["root_run_id"] if child else None,
            "round": child["delegation"]["round"] if child else 0}


def append_event(db, state, event):
    """One durable event stream, paged separately from the bounded UI buffer."""
    event = dict(event)
    event_id = event.pop("event_id", str(uuid4()))
    require(isinstance(event_id, str) and 0 < len(event_id) <= 200)
    digest = sha256(encode_json(event))
    old = db.execute("SELECT payload FROM events WHERE task_id=? AND event_id=?",
                     (state["task"]["task_id"], event_id)).fetchone()
    if old:
        previous = decode_json(old[0])
        require(previous["event_digest"] == digest, "DUPLICATE_CONFLICT")
        return previous
    task = state["task"]
    payload = {"event_id": event_id, "recorded_at": stamp(), "task_id": task["task_id"],
               "root_run_id": task["root_run_id"], "role": "host", "node_id": None,
               "parent_run_id": None, "child_task_id": None, "attempt_id": None, "round": 0,
               "call_id": None, "candidate": state["candidate"], "manifest": state["manifest"],
               "policy_id": task["policy_id"], "policy_fingerprint": state["fingerprint"],
               "budget_id": task["budget_id"], "budget_version": 1,
               **event, "event_digest": digest}
    require(len(encode_json(payload)) <= 16_384, "EVIDENCE_LIMIT")
    db.execute("INSERT INTO events(task_id,payload,event_id) VALUES (?,?,?)",
               (task["task_id"], encode_json(payload), event_id))
    return payload


def rows(db, state):
    return [decode_json(row[0]) for row in db.execute(
        "SELECT payload FROM requests WHERE task_id=? ORDER BY sequence", (state["task"]["task_id"],))]


def totals(requests):
    result = {"model_calls": 0, "tool_calls": 0, "dispatched_calls": 0, "pending": 0,
              "known_tokens": 0, "held_tokens": 0, "unknown_token_calls": 0,
              "known_cost_micros": 0, "held_cost_micros": 0, "unknown_cost_calls": 0}
    for row in requests:
        result[row["kind"] + "_calls"] += 1
        result["dispatched_calls"] += row["dispatched_at"] is not None
        result["pending"] += row["settlement"] is None
        usage = row["settlement"]["usage"] if row["settlement"] else {}
        tokens, cost = usage.get("total_tokens"), usage.get("cost_micros")
        result["known_tokens"] += tokens or 0
        result["known_cost_micros"] += cost or 0
        result["unknown_token_calls"] += tokens is None
        result["unknown_cost_calls"] += cost is None
        result["held_tokens"] += (row["reserved_tokens"] or 0) if tokens is None else 0
        result["held_cost_micros"] += (row["reserved_cost_micros"] or 0) if cost is None else 0
    result["total_tokens"] = None if result["unknown_token_calls"] else result["known_tokens"]
    result["cost_micros"] = None if result["unknown_cost_calls"] else result["known_cost_micros"]
    result["charged_tokens"] = result["known_tokens"] + result["held_tokens"]
    result["charged_cost_micros"] = result["known_cost_micros"] + result["held_cost_micros"]
    result["unbounded_token_calls"] = sum(r["reserved_tokens"] is None and (r["settlement"] is None or r["settlement"]["usage"]["total_tokens"] is None) for r in requests)
    result["unbounded_cost_calls"] = sum(r["reserved_cost_micros"] is None and (r["settlement"] is None or r["settlement"]["usage"]["cost_micros"] is None) for r in requests)
    result["retries"] = sum(r["purpose"] == "retry" for r in requests)
    result["schema_repairs"] = sum(r["purpose"] == "schema_repair" for r in requests)
    return result


def priced(price, input_tokens, output_tokens):
    if price is None or input_tokens is None or output_tokens is None:
        return None
    # Round up once per request, preserving an integer upper bound in micro-units.
    return (input_tokens * price["input_micros_per_million"] +
            output_tokens * price["output_micros_per_million"] + 999_999) // 1_000_000


def usage_record(raw, kind, model, currency):
    usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None,
             "cached_input_tokens": None, "reasoning_output_tokens": None,
             "cost_micros": None, "currency": currency, "tokens_status": "unavailable",
             "cost_status": "unavailable", "reason": "missing_usage"}
    if kind == "tool":
        usage.update(input_tokens=0, output_tokens=0, total_tokens=0, tokens_status="not_applicable")
    if not isinstance(raw, dict):
        if raw is not None:
            usage["reason"] = "malformed_usage"
        return usage
    token_keys = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_output_tokens")
    malformed = any(raw.get(k) is not None and not integer(raw[k]) for k in token_keys)
    if not malformed and kind == "model":
        for key in token_keys:
            usage[key] = raw.get(key)
        i, o, t = (usage[k] for k in token_keys[:3])
        malformed = (i is not None and o is not None and t is not None and i + o != t or
                     i is not None and raw.get("cached_input_tokens", 0) is not None and raw.get("cached_input_tokens", 0) > i or
                     o is not None and raw.get("reasoning_output_tokens", 0) is not None and raw.get("reasoning_output_tokens", 0) > o)
        if i is not None and o is not None and t is None:
            usage["total_tokens"] = i + o
        if usage["total_tokens"] is not None:
            usage.update(tokens_status="reported", reason=None)
    if malformed:
        usage.update({k: None for k in token_keys})
        usage.update(tokens_status="unavailable", reason="malformed_usage")
    mismatch = raw.get("currency") is not None and raw["currency"] != currency
    if mismatch:
        usage.update(reason="currency_mismatch", cost_status="currency_mismatch")
    elif raw.get("cost_micros") is not None:
        if integer(raw["cost_micros"]):
            usage.update(cost_micros=raw["cost_micros"], cost_status="reported")
            if kind == "tool":
                usage["reason"] = None
        else:
            usage.update(reason="malformed_cost")
    elif kind == "model":
        cost = priced(model["pricing"], usage["input_tokens"], usage["output_tokens"])
        if cost is not None:
            usage.update(cost_micros=cost, cost_status="calculated")
        elif usage["reason"] is None:
            usage["reason"] = "unknown_price"
    return usage


def block(state, reason):
    if state["status"] == "running":
        state.update(status="blocked", reason=reason)
        if "ledger" in state:
            state["ledger"]["finished_at"] = stamp()
        for child in state["children"]:
            if child["status"] == "running":
                child["status"] = "cancelled"


def operate(db, state, operation, args):
    ledger = state["ledger"]
    limits, model = ledger["limits"], ledger["model"]
    task_id = state["task"]["task_id"]
    if operation == "accounting":
        requests = rows(db, state)
        human = [decode_json(row[0]) for row in db.execute("SELECT payload FROM events WHERE task_id=?", (task_id,))]
        human = [event for event in human if event["kind"] == "human_intervention" and not event["synthetic"]]
        return {**ledger, "budget_id": state["task"]["budget_id"], "deadline": state["deadline"],
                "totals": totals(requests), "roles": {role: totals([r for r in requests if r["identity"]["role"] == role])
                                                     for role in ("researcher", "reviewer")},
                "human_interventions": {"count": len(human), "known_active_time_ms": sum(e["active_time_ms"] or 0 for e in human),
                                        "active_time_ms": None if any(e["active_time_ms"] is None for e in human) else sum(e["active_time_ms"] for e in human)}}
    if operation == "requests":
        after, limit = args.get("after", 0), args.get("limit", 200)
        require(integer(after) and integer(limit, 1) and limit <= 500)
        return [{"sequence": row[0], **decode_json(row[1])} for row in db.execute(
            "SELECT sequence,payload FROM requests WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (task_id, after, limit))]
    if operation == "human":
        identity = owner(state, args["identity"])
        require(args["kind"] in {"approval", "clarification", "manual_repair", "rerun"})
        require(args.get("active_time_ms") is None or integer(args["active_time_ms"]))
        require(type(args.get("synthetic", False)) is bool)
        return append_event(db, state, {**identity, "kind": "human_intervention", "event_id": args["event_id"],
                                       "intervention_type": args["kind"], "active_time_ms": args.get("active_time_ms"),
                                       "synthetic": args.get("synthetic", False)})
    call_id = args["call_id"]
    require(isinstance(call_id, str) and 0 < len(call_id) <= 200)
    old = db.execute("SELECT payload FROM requests WHERE task_id=? AND call_id=?", (task_id, call_id)).fetchone()
    if operation == "reserve":
        identity = owner(state, args["identity"], active=True)
        require(args["kind"] in {"model", "tool"})
        require(isinstance(args["name"], str) and 0 < len(args["name"]) <= 100)
        require(args.get("purpose", "normal") in {"normal", "retry", "schema_repair"})
        fingerprint = sha256(encode_json(args))
        if old:
            row = decode_json(old[0])
            require(row["fingerprint"] == fingerprint, "DUPLICATE_CONFLICT")
            return {**row, "dispatch": False}
        count = totals(rows(db, state))
        kind = args["kind"]
        if model["pricing"] and limits["currency"]:
            require(model["pricing"]["currency"] == limits["currency"], "CURRENCY_MISMATCH")
        require(count[kind + "_calls"] < limits["max_" + kind + "_calls"], "BUDGET_EXHAUSTED")
        bound, output, cost = 0, 0, args.get("cost_bound_micros")
        if kind == "model":
            bound, output = args.get("input_tokens_bound"), limits["max_output_tokens"]
            require(bound is None or integer(bound))
            hard = limits["max_total_tokens"] is not None or limits["max_cost_micros"] is not None
            require(not hard or bound is not None and model["bounds_id"] and model["supports_output_cap"], "BUDGET_UNVERIFIABLE")
            if limits["max_total_tokens"] is not None:
                output = min(output, limits["max_total_tokens"] - count["charged_tokens"] - bound)
            if limits["max_cost_micros"] is not None:
                price = model["pricing"]
                require(price is not None, "BUDGET_UNVERIFIABLE")
                require(price["currency"] == limits["currency"], "CURRENCY_MISMATCH")
                remaining = limits["max_cost_micros"] - count["charged_cost_micros"]
                available = remaining * 1_000_000 - bound * price["input_micros_per_million"]
                require(available >= 0, "BUDGET_EXHAUSTED")
                if price["output_micros_per_million"]:
                    output = min(output, available // price["output_micros_per_million"])
            require(output >= 1, "BUDGET_EXHAUSTED")
            cost = priced(model["pricing"], bound, output)
        else:
            require(cost is None or integer(cost))
            require(limits["max_cost_micros"] is None or cost is not None, "BUDGET_UNVERIFIABLE")
        if limits["max_cost_micros"] is not None:
            require(cost + count["charged_cost_micros"] <= limits["max_cost_micros"], "BUDGET_EXHAUSTED")
        require(cost is None or integer(cost), "BUDGET_UNVERIFIABLE")
        row = {"call_id": call_id, "kind": kind, "name": args["name"], "identity": identity,
               "purpose": args.get("purpose", "normal"), "fingerprint": fingerprint,
               "input_tokens_bound": bound, "max_output_tokens": output,
               "reserved_tokens": None if bound is None else bound + output,
               "reserved_cost_micros": cost, "reserved_at": stamp(), "dispatched_at": None,
               "settlement": None}
        db.execute("INSERT INTO requests(task_id,call_id,payload) VALUES (?,?,?)", (task_id, call_id, encode_json(row)))
        append_event(db, state, {**identity, "kind": kind + "_reserved", "call_id": call_id,
                                 "reserved_tokens": row["reserved_tokens"], "reserved_cost_micros": cost,
                                 "purpose": row["purpose"]})
        return {**row, "dispatch": True}
    require(old is not None, "TASK_MISMATCH")
    row = decode_json(old[0])
    require(owner(state, args["identity"]) == row["identity"], "TASK_MISMATCH")
    if operation == "dispatch":
        owner(state, args["identity"], active=True)
        require(row["dispatched_at"] is None and row["settlement"] is None, "DUPLICATE_CONFLICT")
        row["dispatched_at"] = stamp()
        append_event(db, state, {**row["identity"], "kind": row["kind"] + "_started", "call_id": call_id})
    else:
        require(operation in {"settle", "reconcile"})
        require(args["outcome"] in {"succeeded", "failed", "denied", "cancelled", "invalid"})
        require(args.get("duration_ms") is None or integer(args["duration_ms"]))
        require(args.get("error_code") is None or re.fullmatch(r"[A-Z_]{1,80}", args["error_code"]))
        currency = limits["currency"] or (model["pricing"] or {}).get("currency")
        usage = usage_record(args.get("usage"), row["kind"], model, currency)
        settlement = {"outcome": args["outcome"], "usage": usage,
                      "duration_ms": args.get("duration_ms"), "error_code": args.get("error_code")}
        if row["settlement"]:
            if row["settlement"] == settlement:
                return row
            require(operation == "reconcile", "DUPLICATE_CONFLICT")
            previous = row["settlement"]
            require(previous["outcome"] == settlement["outcome"] and previous["duration_ms"] == settlement["duration_ms"] and
                    previous["error_code"] == settlement["error_code"], "DUPLICATE_CONFLICT")
            for key in ("input_tokens", "output_tokens", "total_tokens", "cost_micros", "cached_input_tokens", "reasoning_output_tokens"):
                require(previous["usage"][key] is None or previous["usage"][key] == usage[key], "DUPLICATE_CONFLICT")
        row["settlement"] = settlement
        row["settled_at"] = stamp()
        late = state["status"] != "running" or (row["identity"]["role"] == "reviewer" and not any(
            c["status"] == "running" and c["delegation"]["child_task_id"] == row["identity"]["child_task_id"] for c in state["children"]))
        violated = any(usage[k] is not None and row[b] is not None and usage[k] > row[b]
                       for k, b in (("total_tokens", "reserved_tokens"), ("input_tokens", "input_tokens_bound"),
                                    ("output_tokens", "max_output_tokens"), ("cost_micros", "reserved_cost_micros")))
        if violated or usage["cost_status"] == "currency_mismatch" and limits["max_cost_micros"] is not None:
            block(state, "BOUND_EXCEEDED" if violated else "CURRENCY_MISMATCH")
        append_event(db, state, {**row["identity"], "kind": row["kind"] + ("_reconciled" if operation == "reconcile" else "_settled"), "call_id": call_id,
                                 **settlement, "late": late, "bound_exceeded": violated})
    db.execute("UPDATE requests SET payload=? WHERE task_id=? AND call_id=?", (encode_json(row), task_id, call_id))
    return row
