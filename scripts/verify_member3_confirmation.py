"""Verify the frozen Member 3 confirmation corpus without exposing its oracle.

The command checks fixture and oracle hashes, evidence allowlists, JSON-pointer
citations, and independently calculated arithmetic/timing facts. It never
constructs or sends an Agent request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "tests/fixtures/collaboration/confirmation"


def load_json(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    ensure_finite(value)
    return value


def ensure_finite(value: Any) -> None:
    if isinstance(value, float):
        import math
        if not math.isfinite(value):
            raise ValueError("Non-finite JSON numbers are not allowed.")
    elif isinstance(value, dict):
        for item in value.values():
            ensure_finite(item)
    elif isinstance(value, list):
        for item in value:
            ensure_finite(item)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root: Path, relative: str) -> Path:
    base = root.resolve()
    candidate = (root / relative).resolve()
    if candidate == base or not candidate.is_relative_to(base):
        raise ValueError(f"Corpus path escapes its root: {relative}")
    return candidate


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timing evidence requires an explicit timezone.")
    return parsed


def pointer(document: Any, path: str) -> Any:
    if not path.startswith("/"):
        raise ValueError(f"Invalid JSON pointer: {path}")
    value = document
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def facts(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = payload["evidence"]
    result: dict[str, Any] = {
        "missing_evidence_ids": sorted(set(payload["required_evidence_ids"]) - evidence.keys())
    }
    if "returns" in evidence:
        nav = Decimal("1")
        for row in evidence["returns"]["rows"]:
            nav *= Decimal("1") + Decimal(row["return"])
        result["compound_return"] = str(nav - Decimal("1"))
    if "timing" in evidence:
        timing = evidence["timing"]
        result["available_after_decision"] = (
            timestamp(timing["available_at"]) > timestamp(timing["decision_at"])
        )
    if "result" in evidence:
        result_data = evidence["result"]
        result["net_total_return"] = result_data["net_total_return"]
        result["fees_included"] = result_data["definition"]["fees_included"]
        reported = payload["candidate"].get("reported_net_return")
        if reported is not None:
            result["report_minus_result"] = str(
                Decimal(reported) - Decimal(result_data["net_total_return"])
            )
    return result


def verify(corpus: Path) -> dict[str, Any]:
    corpus = corpus.resolve()
    manifest = load_json(corpus / "manifest.json")
    if manifest.get("schema_version") != 1 or manifest.get("set_kind") != "confirmation":
        raise ValueError("Expected a version 1 confirmation corpus.")
    seen: set[str] = set()
    inputs: dict[str, dict[str, Any]] = {}
    for entry in manifest["cases"]:
        fixture_id = entry["fixture_id"]
        if fixture_id in seen:
            raise ValueError(f"Duplicate fixture ID: {fixture_id}")
        seen.add(fixture_id)
        path = inside(corpus, entry["input_file"])
        if sha256(path) != entry["sha256"]:
            raise ValueError(f"Input hash mismatch: {fixture_id}")
        payload = load_json(path)
        if set(payload) != {"fixture_id", "question", "candidate", "evidence", "required_evidence_ids"}:
            raise ValueError(f"Unexpected Agent input fields: {fixture_id}")
        if payload["fixture_id"] != fixture_id:
            raise ValueError(f"Fixture identity mismatch: {fixture_id}")
        if sorted(payload["evidence"]) != sorted(entry["allowed_evidence_ids"]):
            raise ValueError(f"Evidence allowlist mismatch: {fixture_id}")
        inputs[fixture_id] = payload
    oracle_entry = manifest["oracle"]
    oracle_path = inside(corpus, oracle_entry["file"])
    if sha256(oracle_path) != oracle_entry["sha256"]:
        raise ValueError("Oracle hash mismatch.")
    oracle = load_json(oracle_path)
    if set(oracle["cases"]) != seen:
        raise ValueError("Oracle and input fixture IDs must match exactly.")
    for fixture_id, expectation in oracle["cases"].items():
        payload = inputs[fixture_id]
        actual = facts(payload)
        for key, expected in expectation["facts"].items():
            observed = actual.get(key)
            if key in {"compound_return", "net_total_return", "report_minus_result"}:
                match = observed is not None and Decimal(observed) == Decimal(expected)
            else:
                match = observed == expected
            if not match:
                raise ValueError(f"Independent oracle mismatch: {fixture_id}/{key}")
        for citation in expectation["citations"]:
            pointer(payload, citation)
    return {
        "status": "passed",
        "fixture_set_id": manifest["fixture_set_id"],
        "version": manifest["version"],
        "case_count": len(seen),
        "manifest_sha256": sha256(corpus / "manifest.json"),
        "oracle_sha256": sha256(oracle_path),
        "oracle_checks": "passed",
        "agent_input_files": len(inputs),
        "oracle_sent_to_agent": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = parser.parse_args()
    print(json.dumps(verify(args.corpus), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
