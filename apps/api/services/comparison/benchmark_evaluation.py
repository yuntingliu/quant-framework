"""Drive the offline scoring path used by the benchmark evaluation CLI."""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .evaluation_input import bind_observation, validate_trial_for_plan
from .evaluation_metrics import build_report
from .evaluation_protocol import (
    EVALUATOR_VERSION,
    PUBLIC_TASK_SET,
    SYNTHETIC_TASK_SET,
    _id,
    verify_protocol,
)
from .grading import grade_trial
from .regression import make_regression_snapshot


def evaluate_records(
    lock: dict,
    root: Path,
    records: Iterable[dict],
    *,
    observations: Mapping[str, dict] | None = None,
    reviews: Mapping[str, list] | None = None,
    expected_protocol_sha256: str | None = None,
    regression_system_version: str | None = None,
) -> tuple[dict, dict | None]:
    """Score all observed trials, and retain missing design cells in the report."""
    tasks = verify_protocol(lock, root)
    plan = lock["plan"]
    formal = plan["split"] == "holdout" and plan["source_class"] in {"private_replay", "live"}
    private_confirmation = formal and lock["task_set"] not in {PUBLIC_TASK_SET, SYNTHETIC_TASK_SET}
    if (formal or expected_protocol_sha256 is not None) and expected_protocol_sha256 != lock["protocol_sha256"]:
        raise ValueError("Use the separately registered protocol digest to confirm the design")
    if regression_system_version is not None and (
        plan["split"] != "dev" or plan["source_class"] not in {"synthetic", "public_replay"}
    ):
        raise ValueError("Public regression snapshots support redistributable development tasks only")
    if formal and plan["thresholds"]["minimum_human_score"] <= 0:
        raise ValueError("A confirmation design requires a positive human-review threshold")
    observations = observations or {}
    reviews = reviews or {}
    schedule = [
        (task_id, variant, repetition)
        for repetition in range(plan["repetitions"])
        for variant in sorted(plan["configurations"])
        for task_id in plan["task_ids"]
    ]
    random.Random(plan["seed"]).shuffle(schedule)
    order = {cell: position for position, cell in enumerate(schedule)}
    evaluated = []
    grades = {}
    seen_cells = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("A neutral trial record must be an object")
        trial_id = _id(raw.get("trial_id"), "trial_id")
        if trial_id in grades:
            raise ValueError("Duplicate neutral trial record")
        task_id = _id(raw.get("task_id"), "task_id")
        if task_id not in tasks:
            raise ValueError("Observed task is outside the frozen design")
        frozen = tasks[task_id]
        record = bind_observation(raw, observations[trial_id]) if trial_id in observations else raw
        record = validate_trial_for_plan(record, frozen, plan, order=order)
        cell = (task_id, record["conditions"]["variant"], record["reproducibility"]["repetition_index"])
        if cell in seen_cells:
            raise ValueError("Repeated design cell. Keep retries within its single neutral record and billing ledger")
        seen_cells.add(cell)
        evaluated.append(record)
        grade = grade_trial(
            frozen.task, record, reference=frozen.reference, reviews=reviews.get(trial_id, ()),
            minimum_human_score=plan["thresholds"]["minimum_human_score"] if formal else None,
        )
        if formal:
            matched = not record["conditions"].get("conditions_not_matched")
            grade["checks"].append({
                "id": "protocol:observed-controls", "domain": "policy", "blocking": True,
                "status": "passed" if matched else "failed",
            })
            grade["qualified"] = grade["qualified"] and matched
        grades[trial_id] = grade
    if (observations.keys() | reviews.keys()) - grades.keys():
        raise ValueError("Observation or review references a missing trial")
    expected_cells = order.keys()
    metrics_plan = {
        **plan, "mode": "human" if plan["execution_mode"] == "real_user" else "automated",
        "source": {"live": "live", "synthetic": "double"}.get(plan["source_class"], "recorded"),
        "stage": "confirmation" if plan["split"] == "holdout" else "development",
        "protocol_verified": private_confirmation,
    }
    report = build_report(evaluated, grades, plan=metrics_plan)
    report["grading"] = [
        {
            "trial_id": trial_id, "checks": grade["checks"],
            "human_argument_score": grade["human_argument_score"],
            "argument_quality": grade["argument_quality"],
        }
        for trial_id, grade in sorted(grades.items())
    ]
    report["protocol"] = {
        "sha256": lock["protocol_sha256"], "evaluator_version": EVALUATOR_VERSION,
        "task_set": lock["task_set"], "task_set_version": lock["task_set_version"],
        "experiment_id": plan["experiment_id"],
        "split": plan["split"], "source_class": plan["source_class"],
        "scope": "private" if plan["split"] == "holdout" or plan["source_class"] == "private_replay" else "development",
        "claim_status": "registered_confirmation_design" if private_confirmation else "development_evidence_only",
        "thresholds": plan["thresholds"],
        "expected_trials": len(expected_cells), "observed_trials": len(seen_cells),
        "missing_trials": [
            {"task_id": task_id, "config": variant, "repetition_index": repetition}
            for task_id, variant, repetition in sorted(expected_cells - seen_cells)
        ],
    }
    snapshot = None
    if regression_system_version is not None:
        snapshot = make_regression_snapshot(
            ((record, grades[record["trial_id"]]) for record in evaluated),
            suite_version=lock["task_set_version"], evaluator_version=EVALUATOR_VERSION,
            system_version=regression_system_version, public_task_versions=lock["inputs"],
        )
    return report, snapshot


def record_objects(values: Iterable[Any]) -> Iterable[dict]:
    """Accept one #8 JSON record per file or an array of such records."""
    for value in values:
        if isinstance(value, list):
            yield from value
        else:
            yield value
