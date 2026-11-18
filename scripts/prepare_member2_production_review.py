"""Prepare an authorised, archived AlphaLab backtest for an opt-in review.

No backtest execution, model calls or publication. The invoking operator must
already be authorised to inspect the selected backtest and source material.
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alphalab import ResultStore
from alphalab.collaboration import EvidenceStore, validate_contract
from apps.api.services.agent_collaboration_service import prepare_collaboration_candidate

CATEGORIES = ["formulas", "pit", "returns", "execution", "risk", "report_consistency", "support"]


def build_initial_delegation(task, candidate, manifest, *, child_task_id, attempt_id):
    deadline = datetime.fromisoformat(task["deadline"].replace("Z", "+00:00"))
    if deadline <= datetime.now(timezone.utc):
        raise ValueError("Task deadline has expired")
    return {
        "schema_version": 1, "task_id": task["task_id"],
        "root_run_id": task["root_run_id"], "parent_run_id": task["root_run_id"],
        "child_task_id": child_task_id, "attempt_id": attempt_id,
        "node_id": task["reviewer_node_id"], "role": "reviewer", "round": 1,
        "candidate": candidate, "manifest": manifest, "result_type": "review_result",
        "policy_id": task["policy_id"], "grant_id": task["grant_id"],
        "budget_id": task["budget_id"], "deadline": task["deadline"],
    }


def prepare(*, backtest_id, output_dir, request_id, question, report_markdown, outputs, claim_text):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError("Choose a fresh output directory; existing review state must not be overwritten")
    if not question.strip() or not claim_text.strip() or not report_markdown.strip():
        raise ValueError("Question, claim and report must be nonempty")
    suffix = uuid.uuid4().hex[:16]
    task = {
        "schema_version": 1, "task_id": f"task-{suffix}",
        "root_run_id": f"root-{suffix}",
        "researcher_node_id": "researcher-1", "reviewer_node_id": "reviewer-1",
        "question": question, "policy_id": f"policy-{suffix}",
        "grant_id": f"grant-{suffix}", "budget_id": f"budget-{suffix}",
        "deadline": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "max_review_rounds": 1, "required_categories": CATEGORIES,
        "not_applicable_categories": [],
    }
    validate_contract("task_envelope", task)
    # Do not persist until validation and candidate preparation have succeeded.
    output_dir.mkdir(parents=True)
    result_store = ResultStore()
    try:
        prepared = prepare_collaboration_candidate(
            task=task, backtest_id=backtest_id, request_id=request_id,
            report_markdown=report_markdown, outputs=outputs, claim_text=claim_text,
            evidence_store=EvidenceStore(output_dir / "evidence"),
            result_store=result_store,
        )
    finally:
        result_store.close()
    delegation = build_initial_delegation(
        task, prepared["candidate"], prepared["manifest"],
        child_task_id=f"review-{suffix}", attempt_id=f"attempt-{suffix}",
    )
    context = EvidenceStore(output_dir / "evidence").candidate_context(task["task_id"], prepared["candidate"])
    validate_contract("delegation_request", delegation, context)
    # This file holds references and a draft, not a reviewer answer or oracle.
    bundle = {
        "scenario": "archived-backtest", "backtest_id": backtest_id,
        "task_id": task["task_id"], "task": task,
        "versions": [{"candidate": prepared["candidate"], "manifest": prepared["manifest"],
                      "delegation": delegation}],
        "coverage": prepared["coverage"], "omissions": prepared["omissions"],
        "publication_performed": False,
    }
    (output_dir / "fixture.json").write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_dir / "fixture.json"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backtest-id", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--claim", required=True, help="Literal draft claim, not a verified conclusion")
    p.add_argument("--report-file", type=Path, required=True)
    p.add_argument("--outputs-file", type=Path, required=True, help="Proposed schema-valid workspace outputs, NOT published")
    args = p.parse_args()
    path = prepare(backtest_id=args.backtest_id, output_dir=args.output,
                   request_id=args.request_id, question=args.question, claim_text=args.claim,
                   report_markdown=args.report_file.read_text(encoding="utf-8"),
                   outputs=json.loads(args.outputs_file.read_text(encoding="utf-8")))
    print(f"Prepared immutable candidate: {path}")


if __name__ == "__main__":
    main()
