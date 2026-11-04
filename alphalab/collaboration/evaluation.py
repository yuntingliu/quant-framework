"""Independent checks for synthetic development cases and benchmark records.

These helpers do not evaluate arbitrary financial reports or execute strategies.
The oracle is test-only and must never be included in an Agent's input.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any


def _finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite JSON numbers are not allowed.")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def _json(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    _finite(value)
    return value


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if path == root.resolve() or not path.is_relative_to(root.resolve()):
        raise ValueError("Fixture path must remain inside its corpus.")
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_development_corpus(root: Path) -> dict:
    """Verify all declared bytes, IDs and input boundaries; never rewrite hashes."""
    root = Path(root)
    manifest = _json(root / "manifest.json")
    if manifest["schema_version"] != 1 or manifest["set_kind"] != "development":
        raise ValueError("Unsupported development corpus.")
    seen = set()
    for entry in manifest["cases"]:
        case_id = entry["fixture_id"]
        if case_id in seen:
            raise ValueError(f"Duplicate fixture ID: {case_id}")
        seen.add(case_id)
        path = _inside(root, entry["input_file"])
        if _digest(path) != entry["sha256"]:
            raise ValueError(f"Fixture integrity mismatch: {case_id}")
        payload = _json(path)
        if set(payload) != {"fixture_id", "question", "candidate", "evidence", "required_evidence_ids"}:
            raise ValueError(f"Unexpected Agent input fields: {case_id}")
        if payload["fixture_id"] != case_id:
            raise ValueError(f"Fixture identity mismatch: {case_id}")
        if sorted(payload["evidence"]) != sorted(entry["allowed_evidence_ids"]):
            raise ValueError(f"Evidence identity mismatch: {case_id}")
    for name in ("oracle", "protocol_scenarios"):
        entry = manifest[name]
        path = _inside(root, entry["file"])
        if _digest(path) != entry["sha256"]:
            raise ValueError(f"Fixture integrity mismatch: {name}")
        _json(path)
    oracle = _json(_inside(root, manifest["oracle"]["file"]))
    if set(oracle["cases"]) != seen:
        raise ValueError("Oracle and input fixture IDs must match exactly.")
    return {
        "fixture_set_id": manifest["fixture_set_id"],
        "version": manifest["version"],
        "manifest_sha256": _digest(root / "manifest.json"),
        "case_count": len(seen),
    }


def load_agent_input(root: Path, fixture_id: str) -> dict:
    """Return only the allowlisted input file, never the manifest or oracle."""
    verify_development_corpus(root)
    manifest = _json(Path(root) / "manifest.json")
    entry = next((item for item in manifest["cases"] if item["fixture_id"] == fixture_id), None)
    if entry is None:
        raise ValueError(f"Unknown development fixture: {fixture_id}")
    return _json(_inside(Path(root), entry["input_file"]))


def _timestamp(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timing evidence requires an explicit timezone.")
    return value


def fixture_facts(payload: dict) -> dict:
    """Calculate only the small corpus's declared arithmetic/timing facts.

    This is an independent fixture oracle, not a replacement financial engine or
    a production reviewer. Qualitative conclusions use the declared oracle
    rationale; no string classifier pretends to establish them.
    """
    evidence = payload["evidence"]
    facts: dict[str, Any] = {
        "missing_evidence_ids": sorted(set(payload["required_evidence_ids"]) - evidence.keys()),
    }
    if "returns" in evidence:
        nav = Decimal(1)
        for row in evidence["returns"]["rows"]:
            nav *= Decimal(1) + Decimal(row["return"])
        facts["compound_return"] = str(nav - 1)
    if "timing" in evidence:
        timing = evidence["timing"]
        facts["available_after_decision"] = (
            _timestamp(timing["available_at"]) > _timestamp(timing["decision_at"])
        )
    if "result" in evidence:
        result = evidence["result"]
        facts["net_total_return"] = result["net_total_return"]
        facts["fees_included"] = result["definition"]["fees_included"]
        reported = payload["candidate"].get("reported_net_return")
        if reported is not None:
            facts["report_minus_result"] = str(Decimal(reported) - Decimal(result["net_total_return"]))
    return facts


def _pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ValueError("Oracle citations must be JSON pointers.")
    value = document
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def check_development_oracle(root: Path) -> dict:
    """Check independently derived numbers and the oracle's resolvable citations."""
    verified = verify_development_corpus(root)
    manifest = _json(Path(root) / "manifest.json")
    oracle = _json(_inside(Path(root), manifest["oracle"]["file"]))
    for case_id, expected in oracle["cases"].items():
        payload = load_agent_input(root, case_id)
        actual = fixture_facts(payload)
        for key, value in expected["facts"].items():
            observed = actual.get(key)
            if key in {"compound_return", "net_total_return", "report_minus_result"}:
                equal = observed is not None and Decimal(observed) == Decimal(value)
            else:
                equal = observed == value
            if not equal:
                raise ValueError(f"Independent oracle mismatch: {case_id}/{key}")
        for citation in expected["citations"]:
            _pointer(payload, citation)
    return {**verified, "oracle_checks": "passed", "qualitative_scoring": "declared_development_expectations"}


def validate_run_record(record: dict) -> None:
    """Validate a versioned measurement record, including totals and missingness."""
    from jsonschema import Draft7Validator, FormatChecker

    _finite(record)
    schema = json.loads(files("alphalab.collaboration").joinpath("schemas/run-record.v1.json").read_text())
    Draft7Validator(schema, format_checker=FormatChecker()).validate(record)
    start, end = (_timestamp(record[field]) for field in ("started_at", "finished_at"))
    if end < start:
        raise ValueError("Run finish precedes start.")
    roles = record["roles"]
    names = [role["role"] for role in roles]
    if len(names) != len(set(names)):
        raise ValueError("Role entries must be unique.")
    expected_roles = {"researcher", "reviewer"} if record["arm"] == "two_agent" else {"researcher"}
    if set(names) != expected_roles:
        raise ValueError("Role entries must match the benchmark arm.")
    for field in ("model_calls", "tool_calls", "retries", "searches"):
        if record["totals"][field] != sum(role[field] for role in roles):
            raise ValueError(f"Role/total mismatch: {field}")
    for row in [record["totals"], *roles]:
        usage = row["usage"]
        if usage["status"] == "unavailable" and any(
            usage[key] is not None for key in ("input_tokens", "output_tokens")
        ):
            raise ValueError("Unavailable token usage cannot contain invented numbers.")
        if usage["status"] == "unavailable" and not usage["reason"]:
            raise ValueError("Unavailable usage requires an explanation.")
        if usage["status"] != "unavailable" and any(
            usage[key] is None for key in ("input_tokens", "output_tokens")
        ):
            raise ValueError("Known token usage requires both token counts.")
        cost = row["cost"]
        if cost["status"] == "unavailable" and cost["amount"] is not None:
            raise ValueError("Unavailable cost must be null.")
        if cost["status"] == "unavailable" and not cost["reason"]:
            raise ValueError("Unavailable cost requires an explanation.")
        if cost["status"] != "unavailable" and cost["amount"] is None:
            raise ValueError("Known cost requires an amount.")
        if cost["status"] == "scripted_no_external_provider" and Decimal(cost["amount"]) != 0:
            raise ValueError("Scripted external provider cost must be zero.")
        if cost["status"] in {"actual", "estimated"} and not cost["currency"]:
            raise ValueError("Provider cost requires a currency.")
    for field in ("input_tokens", "output_tokens"):
        values = [role["usage"][field] for role in roles]
        expected_total = None if any(value is None for value in values) else sum(values)
        if record["totals"]["usage"][field] != expected_total:
            raise ValueError(f"Role/total mismatch: {field}")
    total_cost = record["totals"]["cost"]
    role_costs = [role["cost"] for role in roles]
    if any(cost["amount"] is None for cost in role_costs):
        if total_cost["amount"] is not None:
            raise ValueError("Total cost must remain unknown when a role's cost is unknown.")
    else:
        if len({cost["currency"] for cost in role_costs}) > 1:
            raise ValueError("Cannot sum costs in different currencies.")
        if total_cost["currency"] != role_costs[0]["currency"] or total_cost["amount"] is None:
            raise ValueError("Total cost currency/amount mismatch.")
        if Decimal(total_cost["amount"]) != sum(Decimal(cost["amount"]) for cost in role_costs):
            raise ValueError("Role/total cost mismatch.")
    attempts = record["attempts"]
    if len(attempts) != record["totals"]["model_calls"]:
        raise ValueError("Every model call must have an attempt record.")
    ids = [attempt["id"] for attempt in attempts]
    if len(ids) != len(set(ids)) or any(attempt["role"] not in names for attempt in attempts):
        raise ValueError("Attempt identity/role mismatch.")
    for role in roles:
        role_attempts = [attempt for attempt in attempts if attempt["role"] == role["role"]]
        if len(role_attempts) != role["model_calls"]:
            raise ValueError("Every role's model calls require matching attempts.")
        if sum(len(attempt["tool_names"]) for attempt in role_attempts) != role["tool_calls"]:
            raise ValueError("Tool-call totals must match recorded requested tool calls.")
    if record["outcome"]["status"] != "completed" and not record["outcome"]["reason"]:
        raise ValueError("Non-completed runs require a reason.")
    if record["mode"] == "live" and total_cost["status"] == "scripted_no_external_provider":
        raise ValueError("Live calls cannot claim scripted zero cost.")
