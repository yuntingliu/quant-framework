"""Versioned, publication-safe regression records for the benchmark evaluator."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

_SNAPSHOT_VERSION = 1
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}\Z")
_CHECK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}\Z")
_CACHE_STATES = {"cold", "warm", "unknown"}
_MODES = {"automated", "human", "unknown"}
_SOURCES = {"double", "recorded", "live", "unknown"}
_CHECK_STATUSES = {"passed", "failed", "unassessed"}
_SAFETY_FIELDS = ("unauthorized_actions", "fabrication_events", "duplicate_executions")
_VERSION_FIELDS = ("suite_version", "evaluator_version", "system_version")
_CASE_FIELDS = ("task_id", "config", "repetition_index", "cache", "mode", "source", "stage")


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Expected a stable identifier for {field}")
    return value


def _choice(value: Any, options: set[str], field: str) -> str:
    if not isinstance(value, str) or value not in options:
        raise ValueError(f"Invalid {field}")
    return value


def _count(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"Expected a nonnegative integer for {field}")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"Expected a boolean for {field}")
    return value


def _trial_cache_state(conditions: Mapping[str, Any]) -> str:
    if "cache" in conditions:
        return _choice(conditions["cache"], _CACHE_STATES, "cache")
    cold, warm = conditions.get("cold_start"), conditions.get("warm_cache")
    if cold is True and warm is not True:
        return "cold"
    if warm is True and cold is not True:
        return "warm"
    return "unknown"


def _case_summary(case: Mapping[str, Any]) -> dict[str, Any]:
    """Select the only fields that may appear in snapshots and diffs."""
    checks: dict[str, str] = {}
    for check_id, status in case["blocking_checks"].items():
        if not isinstance(check_id, str) or not _CHECK_ID.fullmatch(check_id):
            raise ValueError("Expected a stable identifier for check_id")
        checks[check_id] = _choice(status, _CHECK_STATUSES, "check status")
    return {
        "task_id": _identifier(case["task_id"], "task_id"),
        "config": _identifier(case["config"], "config"),
        "repetition_index": _count(case["repetition_index"], "repetition_index"),
        "cache": _choice(case["cache"], _CACHE_STATES, "cache"),
        "mode": _choice(case["mode"], _MODES, "mode"),
        "source": _choice(case["source"], _SOURCES, "source"),
        "stage": _identifier(case["stage"], "stage"),
        "task_version": _identifier(case["task_version"], "task_version"),
        "framework_version": _identifier(case["framework_version"], "framework_version"),
        "qualified": _boolean(case["qualified"], "qualified"),
        "outcome": _identifier(case["outcome"], "outcome"),
        "runtime_status": _identifier(case["runtime_status"], "runtime_status"),
        "infrastructure_failure": _boolean(
            case["infrastructure_failure"], "infrastructure_failure"
        ),
        "blocking_checks": dict(sorted(checks.items())),
        "safety": {field: _count(case["safety"][field], field) for field in _SAFETY_FIELDS},
    }


def _case_key(case: Mapping[str, Any]) -> tuple:
    return tuple(case[field] for field in _CASE_FIELDS)


def make_regression_snapshot(
    evaluated_trials: Iterable[tuple[Mapping[str, Any], Mapping[str, Any]]],
    *,
    suite_version: str,
    evaluator_version: str,
    system_version: str,
    public_task_versions: Mapping[str, str],
) -> dict[str, Any]:
    """Project neutral runner and grader records onto a public, stable manifest.

    Only tasks in the evaluator-owned allowlist can appear. Trial IDs, raw events,
    artifact paths/content, grader diagnostics, reviews, and actual values are omitted.
    """
    versions = {
        "suite_version": _identifier(suite_version, "suite_version"),
        "evaluator_version": _identifier(evaluator_version, "evaluator_version"),
        "system_version": _identifier(system_version, "system_version"),
    }
    cases = []
    for trial, grade in evaluated_trials:
        task_id = trial["task_id"]
        if task_id not in public_task_versions:
            continue
        if grade["task_id"] != task_id or grade["trial_id"] != trial["trial_id"]:
            raise ValueError("Grade does not belong to its paired trial")
        if grade["grader_version"] != evaluator_version:
            raise ValueError("Grade version differs from the declared evaluator version")
        conditions = trial.get("conditions", {})
        reproducibility = trial.get("reproducibility", {})
        cases.append(
            {
                "task_id": task_id,
                "config": conditions.get("variant", trial["config"]),
                "repetition_index": reproducibility.get("repetition_index", 0),
                "cache": _trial_cache_state(conditions),
                "mode": conditions.get("mode", "automated"),
                "source": conditions.get(
                    "source",
                    "double" if trial.get("budget", {}).get("enabled") is False else "unknown",
                ),
                "stage": conditions.get("stage", "unknown"),
                "task_version": public_task_versions[task_id],
                "framework_version": reproducibility.get(
                    "framework_version",
                    conditions.get("framework_version", system_version),
                ),
                "qualified": grade["qualified"],
                "outcome": grade["outcome"],
                "runtime_status": grade["runtime_status"],
                "infrastructure_failure": grade["infrastructure_failure"],
                "blocking_checks": {
                    check["id"]: check["status"] for check in grade["checks"] if check["blocking"]
                },
                "safety": grade["safety"],
            }
        )
    if not cases:
        raise ValueError("The snapshot has no trials from the public task allowlist")
    return {
        "schema_version": _SNAPSHOT_VERSION,
        "versions": versions,
        "cases": list(_index_cases(cases).values()),
    }


def _index_cases(cases: Iterable[Mapping[str, Any]]) -> dict[tuple, dict[str, Any]]:
    index = {}
    for case in cases:
        summary = _case_summary(case)
        key = _case_key(summary)
        if key in index:
            raise ValueError(
                "Duplicate task, configuration, repetition, and conditions in snapshot"
            )
        index[key] = summary
    return dict(sorted(index.items()))


def _case_change(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    if baseline is None:
        return "added", []
    if candidate is None:
        return "missing", ["case_missing"]
    if baseline["task_version"] != candidate["task_version"]:
        return "contract_changed", ["task_version_changed"]

    reasons = []
    if baseline["qualified"] and not candidate["qualified"]:
        reasons.append("qualification_lost")
    if not baseline["infrastructure_failure"] and candidate["infrastructure_failure"]:
        reasons.append("infrastructure_failure")
    for check_id, status in baseline["blocking_checks"].items():
        if check_id not in candidate["blocking_checks"]:
            reasons.append(f"blocking_check_missing:{check_id}")
        elif status == "passed" and candidate["blocking_checks"][check_id] != "passed":
            reasons.append(f"blocking_check_lost:{check_id}")
    for field in _SAFETY_FIELDS:
        if candidate["safety"][field] > baseline["safety"][field]:
            reasons.append(f"safety_count_increased:{field}")
    if reasons:
        return "regressed", reasons
    if not baseline["qualified"] and candidate["qualified"]:
        return "improved", []
    if any(
        baseline[field] != candidate[field] for field in baseline if field != "framework_version"
    ):
        return "changed", []
    return "unchanged", []


def compare_regression_snapshots(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare stable keys, keeping failed, missing and incompatibly changed cases visible."""
    if any(item.get("schema_version") != _SNAPSHOT_VERSION for item in (baseline, candidate)):
        raise ValueError("Unsupported regression snapshot schema version")
    baseline_cases = _index_cases(baseline["cases"])
    candidate_cases = _index_cases(candidate["cases"])
    version_diff = {
        label: {field: _identifier(snapshot["versions"][field], field) for field in _VERSION_FIELDS}
        for label, snapshot in (("baseline", baseline), ("candidate", candidate))
    }
    cases = []
    unchanged = 0
    for key in sorted(baseline_cases.keys() | candidate_cases.keys()):
        before = baseline_cases.get(key)
        after = candidate_cases.get(key)
        state, reasons = _case_change(before, after)
        if state == "unchanged":
            unchanged += 1
            continue
        cases.append(
            {
                "case": dict(zip(_CASE_FIELDS, key, strict=True)),
                "state": state,
                "reasons": reasons,
                "baseline": before,
                "candidate": after,
            }
        )
    return {
        "schema_version": _SNAPSHOT_VERSION,
        "versions": version_diff,
        "has_regression": any(case["state"] in {"missing", "regressed"} for case in cases),
        "requires_rebaseline": any(case["state"] == "contract_changed" for case in cases),
        "unchanged_cases": unchanged,
        "changed_cases": cases,
    }


def _positive_budget(value: Any) -> Decimal:
    if type(value) not in (int, float, Decimal):
        raise ValueError("Paid extension requires finite positive USD budget limits")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Paid extension requires finite positive USD budget limits") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Paid extension requires finite positive USD budget limits")
    return amount


def check_paid_extension(
    *,
    enabled: bool = False,
    total_budget_usd: float | None = None,
    trial_cost_caps_usd: Iterable[float] = (),
    runner_supports_paid: bool = False,
) -> bool:
    """Preflight an explicitly requested paid extension without running a provider.

    Caps must cover all possible attempts, roles and search for each planned trial.
    The caller must verify real adapters and runner-side enforcement before setting
    ``runner_supports_paid``. The Issue #8 PR currently supplies stub adapters.
    """
    if enabled is not True:
        return False
    total = _positive_budget(total_budget_usd)
    costs = tuple(_positive_budget(cap) for cap in trial_cost_caps_usd)
    if not costs:
        raise ValueError("Paid extension requires a cost cap for each planned trial")
    if sum(costs) > total:
        raise ValueError("Sum of the planned trial caps exceeds the paid extension budget")
    if runner_supports_paid is not True:
        raise RuntimeError(
            "Paid extension requires verified real adapters and total-budget enforcement"
        )
    return True
