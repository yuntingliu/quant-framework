"""Check the shared valid/invalid contract corpus, optionally in both runtimes."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphalab.collaboration import CollaborationError, validate_contract  # noqa: E402


def load_contract_cases(root: Path) -> list[dict]:
    examples = json.loads((root / "examples.json").read_text(encoding="utf-8"))
    cases = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    result = []
    for case in cases:
        kind = case["kind"]
        context = examples["correction_context" if kind == "correction_response" else "context"]
        expanded = {**case, "payload": copy.deepcopy(examples["messages"][kind]), "context": copy.deepcopy(context)}
        for patch in case["patches"]:
            current = expanded[patch["scope"]]
            for key in patch["path"][:-1]:
                current = current[key]
            key = patch["path"][-1]
            if patch["operation"] == "delete":
                del current[key]
            else:
                current[key] = copy.deepcopy(patch["value"])
        result.append(expanded)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=ROOT / "tests/fixtures/collaboration/contracts")
    parser.add_argument("--cross-language", action="store_true")
    parser.add_argument("--node", default=shutil.which("node"))
    args = parser.parse_args()
    cases = load_contract_cases(args.fixtures)
    outcomes = []
    for case in cases:
        try:
            validate_contract(case["kind"], case["payload"], case["context"])
            outcome = {"valid": True}
        except CollaborationError as error:
            outcome = {"valid": False, "code": error.code}
        expected = {"valid": False, "code": case["expected_code"]} if case["expected_code"] else {"valid": True}
        if outcome != expected:
            raise SystemExit(f"Contract case failed: {case['name']} ({outcome})")
        outcomes.append(outcome)
    if args.cross_language:
        if not args.node:
            parser.error("Node is required for cross-language validation.")
        completed = subprocess.run([args.node, str(ROOT / "integrations/conexus/collaboration/contracts.mjs")],
                                   input=json.dumps(cases), capture_output=True, text=True, check=True, timeout=30, cwd=ROOT)
        if json.loads(completed.stdout) != outcomes:
            raise SystemExit("Node/Python contract decisions differ.")
    print(json.dumps({"cases": len(cases), "python": "passed",
                      "node": "passed" if args.cross_language else "not_requested",
                      "publication_performed": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
