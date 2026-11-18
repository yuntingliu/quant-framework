
"""Prepare frozen synthetic evidence for the Member 2 correction Host acceptance test.

No model calls, ControlStore registration, backtest execution or publication.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

# Add the repository root to Python's import path.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alphalab.collaboration import EvidenceStore, validate_contract
from scripts.prepare_collaboration_acceptance import prepare
from tests.collaboration.correction_fixture import (
    make_correction,
    prepare_corrected_candidate,
)


def build_fixture(output: Path) -> Path:
    output = output.resolve()

    if output.exists():
        raise FileExistsError(
            "Choose a fresh output directory."
        )

    # Creates the original frozen candidate and findings review.
    fixture = prepare(output, "seeded-formula")

    # The correction helpers only need access to the evidence store.
    holder = SimpleNamespace(
        evidence=EvidenceStore(output / "evidence")
    )

    new_candidate, new_manifest = prepare_corrected_candidate(
        holder, fixture
    )

    correction = make_correction(
        fixture, new_candidate, new_manifest, holder
    )

    first = fixture["versions"][0]

    second_delegation = copy.deepcopy(first["delegation"])
    second_delegation.update({
        "child_task_id": "review-2",
        "attempt_id": "attempt-2",
        "round": 2,
        "candidate": new_candidate,
        "manifest": new_manifest,
    })

    second_review = copy.deepcopy(first["review"])
    second_review.update({
        "submission_id": "submission-2",
        "child_task_id": "review-2",
        "attempt_id": "attempt-2",
        "round": 2,
        "candidate": new_candidate,
        "manifest": new_manifest,
        "status": "pass",
        "findings": [],
        "missing_evidence": [],
        "reason": None,
    })

    context = holder.evidence.candidate_context(
        fixture["task_id"], new_candidate
    )

    descriptors = {
        item["artifact_id"]: item
        for item in context["manifest"]["artifacts"]
    }

    for coverage in second_review["coverage"]:
        for citation in coverage["evidence"]:
            citation["sha256"] = descriptors[
                citation["artifact_id"]
            ]["sha256"]

    validate_contract(
        "delegation_request",
        second_delegation,
        context,
    )

    validate_contract(
        "review_result",
        second_review,
        {
            **context,
            "delegation": second_delegation,
        },
    )

    result = {
        "scenario": "correction-loop",
        "task_id": fixture["task_id"],
        "task": fixture["task"],
        "versions": [
            first,
            {
                "candidate": new_candidate,
                "manifest": new_manifest,
                "delegation": second_delegation,
                "review": second_review,
            },
        ],
        "correction": correction,
        "publication_performed": False,
    }

    path = output / "fixture.json"
    path.write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )

    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    args = parser.parse_args()

    print(build_fixture(args.output))


if __name__ == "__main__":
    main()
