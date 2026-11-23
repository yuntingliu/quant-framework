"""Aggregate neutral comparison records without dropping failures or exposing logs."""

from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from statistics import fmean, median
from typing import Any

REPORT_VERSION = 1
_FEATURES = ("financial_tools", "skills", "context_compression", "recovery", "multi_agent")
_SAFETY = ("unauthorized_actions", "fabrication_events", "duplicate_executions")
_STATES = {
    "pending",
    "running",
    "completed",
    "blocked",
    "failed",
    "timeout",
    "cancelled",
    "interrupted",
    "refused",
    "clarification_required",
    "unknown",
}
_SEMANTIC_OUTCOMES = {"completed", "refused", "clarification_required"}
_STAGE_BY_SPLIT = {"dev": "development", "holdout": "confirmation"}
_STRATUM_FIELDS = ("cache", "mode", "source", "stage")
_STRATUM_VALUES = (
    {"cold", "warm"},
    {"automated", "human"},
    {"double", "recorded", "live"},
    {"development", "confirmation"},
)
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./+@#-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MATCHED_CONTROLS = ("hardware", "permissions_sha256", "assets_sha256", "search_policy")
_MODEL_CONTROLS = ("model_id", "provider", "reasoning")


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        return None
    return float(value) if valid else None


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _tag(value: Any) -> str | None:
    return value if isinstance(value, str) and _TAG.fullmatch(value) else None


def _identifier(value: Any, field: str) -> str:
    tag = _tag(value)
    if tag is None:
        raise ValueError(f"Invalid or missing {field}")
    return tag


def _state(value: Any) -> str:
    return value if isinstance(value, str) and value in _STATES else "unknown"


def _percentile(values: list[float], proportion: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = proportion * (len(ordered) - 1)
    position = math.floor(index)
    fraction = index - position
    return ordered[position] + fraction * (ordered[math.ceil(index)] - ordered[position])


def _describe(values: Iterable[float | None]) -> dict[str, Any]:
    observed = [value for value in values if value is not None]
    return {
        "observations": len(observed),
        "median": median(observed) if observed else None,
        "p90": _percentile(observed, 0.9),
    }


def trial_accounting(record: Mapping, source: str = "unknown") -> dict[str, Any]:
    """Ledger lines are billable units, not a second copy of runner totals."""
    resources = _mapping(record.get("resources"))
    results = _mapping(record.get("results"))
    budget = _mapping(record.get("budget"))
    ledger = resources.get("cost_ledger")
    lines: dict[str, Mapping] = {}
    conflicting: set[str] = set()
    invalid_line = ledger is not None and not isinstance(ledger, list)
    if isinstance(ledger, list):
        for item in ledger:
            line = _mapping(item)
            charge_id = _tag(line.get("charge_id"))
            if charge_id is None:
                invalid_line = True
                continue
            if charge_id in lines and lines[charge_id] != line:
                conflicting.add(charge_id)
            lines[charge_id] = line

    by_kind: dict[str, list[float]] = defaultdict(list)
    attempts: dict[str, list[Mapping]] = defaultdict(list)
    roles: set[str] = set()
    for charge_id, line in lines.items():
        cost = _number(line.get("cost_usd"))
        if cost is None or charge_id in conflicting:
            invalid_line = True
        else:
            kind = line.get("kind")
            by_kind[kind if kind in ("model", "search", "tool", "other") else "other"].append(cost)
        attempt_id = _tag(line.get("attempt_id"))
        if attempt_id is not None:
            attempts[attempt_id].append(line)
        role = _tag(line.get("role"))
        if role is not None:
            roles.add(role)

    costs_by_kind = {kind: math.fsum(values) for kind, values in sorted(by_kind.items())}
    ledger_subtotal = math.fsum(costs_by_kind.values())
    runner_costs = [
        value
        for value in (_number(resources.get("cost_usd")), _number(budget.get("spent_usd")))
        if value is not None
    ]
    events = _mapping(record.get("execution")).get("events")
    if isinstance(events, list):
        emitted_costs = [_number(_mapping(event).get("cost_usd")) for event in events]
        # The runner appends an event before testing its token and step caps.
        # Its aggregate can therefore omit the final, already incurred charge.
        if any(value is not None for value in emitted_costs):
            runner_costs.append(math.fsum(value for value in emitted_costs if value is not None))
    runner_total = max(runner_costs, default=None)
    ledger_complete = (
        isinstance(ledger, list)
        and not invalid_line
        and (resources.get("cost_ledger_complete") is True)
    )
    if ledger_complete and runner_total is not None and runner_total > ledger_subtotal:
        ledger_complete = math.isclose(ledger_subtotal, runner_total, rel_tol=1e-9, abs_tol=1e-12)

    paid_possible = source != "double" or budget.get("enabled") is True
    if ledger_complete:
        total = ledger_subtotal
    elif ledger is None and not paid_possible and runner_total is not None:
        total = runner_total
    else:
        total = None

    attempt_rows = []
    for index, attempt_lines in enumerate(attempts.values(), start=1):
        amounts = [_number(line.get("cost_usd")) for line in attempt_lines]
        statuses = sorted({_state(line.get("status")) for line in attempt_lines})
        attempt_rows.append(
            {
                "index": index,
                "recorded_charge_count": len(attempt_lines),
                "recorded_cost_usd": (
                    math.fsum(amount for amount in amounts if amount is not None)
                    if all(x is not None for x in amounts)
                    and all(line["charge_id"] not in conflicting for line in attempt_lines)
                    else None
                ),
                "recorded_charge_statuses": statuses,
            }
        )
    retries = _count(results.get("retries"))
    return {
        "cost_usd": total,
        "recorded_cost_usd": max(ledger_subtotal, runner_total or 0.0),
        "cost_status": (
            "complete"
            if total is not None
            else ("partial" if ledger is not None or runner_total is not None else "unrecorded")
        ),
        "cost_by_kind_usd": costs_by_kind,
        "recorded_role_count": len(roles),
        "recorded_charge_count": len(lines),
        "recorded_attempt_count": max(1 + (retries or 0), len(attempts)),
        "recorded_retries": retries,
        "attempts_with_recorded_charges": attempt_rows,
    }


def _stratum(conditions: Mapping, budget: Mapping, plan: Mapping) -> tuple[str, ...]:
    cache = conditions.get("cache")
    if cache is None:
        cold, warm = conditions.get("cold_start"), conditions.get("warm_cache")
        if cold is True and warm is not True:
            cache = "cold"
        elif warm is True and cold is not True:
            cache = "warm"
    mode = conditions.get("mode", conditions.get("execution_mode", "automated"))
    if mode == "real_user":
        mode = "human"
    source = conditions.get("source", conditions.get("execution_source"))
    if source is None and budget.get("enabled") is False:
        source = "double"
    if source == "offline":
        source = "double"
    if source == "double" and budget.get("enabled") is True:
        source = "unknown"
    stage = conditions.get("stage") or _STAGE_BY_SPLIT.get(conditions.get("split"))
    if stage is None:
        stage = plan.get("stage") or _STAGE_BY_SPLIT.get(plan.get("split"))
    candidates = (cache, mode, source, stage)
    return tuple(
        candidate if isinstance(candidate, str) and candidate in options else "unknown"
        for candidate, options in zip(candidates, _STRATUM_VALUES)
    )


@dataclass(frozen=True)
class _Trial:
    outcome: dict[str, Any]
    stratum: tuple[str, ...]
    controls: Mapping
    limits: Mapping
    pair_set: str | int | None
    data_hash: str | None
    evidence_hash: str | None


def _trial(record: Any, grades: Mapping, plan: Mapping) -> _Trial:
    if not isinstance(record, Mapping):
        record = record.to_dict()
    conditions = _mapping(record.get("conditions"))
    budget = _mapping(record.get("budget"))
    reproducibility = _mapping(record.get("reproducibility"))
    execution = _mapping(record.get("execution"))
    resources = _mapping(record.get("resources"))
    trial_id = _identifier(record.get("trial_id"), "trial ID")
    task_id = _identifier(record.get("task_id"), "task ID")
    config = _identifier(conditions.get("variant", record.get("config")), "configuration ID")
    grade = _mapping(grades.get(trial_id))
    safety = _mapping(grade.get("safety"))
    runtime_status = _state(execution.get("status"))
    runtime_may_complete = runtime_status in {"completed", "blocked"}
    outcome = _state(grade.get("outcome")) if runtime_may_complete else runtime_status
    evidence_hash = reproducibility.get("evidence_sha256", grade.get("evidence_sha256"))
    stratum = _stratum(conditions, budget, plan)
    version = _mapping(_mapping(plan.get("configurations")).get(config))
    completed = record.get("is_complete") is True
    infrastructure_failure = grade.get("infrastructure_failure") is True
    qualified = (
        completed
        and grade.get("qualified") is True
        and runtime_may_complete
        and outcome in _SEMANTIC_OUTCOMES
        and not infrastructure_failure
        and not any(_count(safety.get(name)) for name in _SAFETY)
    )
    runner_elapsed = _number(_mapping(record.get("timestamps")).get("duration_s"))
    end_to_end_elapsed = _number(resources.get("end_to_end_seconds"))
    elapsed_source = (
        "observer_end_to_end"
        if end_to_end_elapsed is not None
        else "runner_window" if runner_elapsed is not None else "unrecorded"
    )
    row = {
        "trial_id": trial_id,
        "task_id": task_id,
        "task_category": _tag(record.get("task_category")),
        "config": config,
        "repetition_index": _count(reproducibility.get("repetition_index")),
        "framework_version": _tag(reproducibility.get("framework_version"))
        or _tag(conditions.get("framework_version")),
        "declared_framework_version": _tag(version.get("framework_version")),
        "task_version": _tag(reproducibility.get("task_version")),
        **dict(zip(_STRATUM_FIELDS, stratum)),
        "record_closed": completed,
        "graded": bool(grade),
        "runtime_status": runtime_status,
        "semantic_outcome": _state(grade.get("semantic_outcome")),
        "outcome": outcome,
        "qualified": qualified,
        "infrastructure_failure": infrastructure_failure,
        "elapsed_seconds": end_to_end_elapsed if end_to_end_elapsed is not None else runner_elapsed,
        "elapsed_source": elapsed_source,
        "end_to_end_seconds": end_to_end_elapsed,
        "runner_window_seconds": runner_elapsed,
        "human_intervention_count": _count(resources.get("human_intervention_count")),
        "human_active_minutes": _number(resources.get("human_active_minutes")),
        "safety": {name: _count(safety.get(name)) for name in _SAFETY},
        **trial_accounting(record, stratum[2]),
    }
    return _Trial(
        row,
        stratum,
        conditions,
        _mapping(conditions.get("limits")),
        _tag(reproducibility.get("experiment_id"))
        or _count(reproducibility.get("interleave_seed")),
        _tag(_mapping(record.get("initial_state")).get("initial_data_hash")),
        (
            evidence_hash
            if isinstance(evidence_hash, str) and _SHA256.fullmatch(evidence_hash)
            else None
        ),
    )


def _reproducibility(trials: list[_Trial]) -> dict[str, Any]:
    groups: dict[tuple, list[_Trial]] = defaultdict(list)
    for trial in trials:
        key = (
            trial.outcome["task_id"],
            trial.outcome["task_version"],
            trial.pair_set,
            trial.data_hash,
        )
        groups[key].append(trial)
    repeated_groups = 0
    outcome_groups_matching = 0
    evidence_groups_comparable = 0
    evidence_groups_matching = 0
    for group in groups.values():
        repetitions = [x.outcome["repetition_index"] for x in group]
        if len(group) < 2 or None in repetitions or len(set(repetitions)) != len(repetitions):
            continue
        repeated_groups += 1
        outcomes = {(x.outcome["outcome"], x.outcome["qualified"]) for x in group}
        outcome_groups_matching += len(outcomes) == 1
        if any(x.evidence_hash is None for x in group):
            continue
        evidence_groups_comparable += 1
        evidence_groups_matching += (
            len(outcomes) == 1 and len({x.evidence_hash for x in group}) == 1
        )
    return {
        "repeated_task_groups": repeated_groups,
        "outcome_groups_matching": outcome_groups_matching,
        "evidence_groups_comparable": evidence_groups_comparable,
        "evidence_groups_matching": evidence_groups_matching,
        "evidence_reproducibility_rate": (
            evidence_groups_matching / evidence_groups_comparable
            if evidence_groups_comparable
            else None
        ),
    }


def _summary(trials: list[_Trial]) -> dict[str, Any]:
    rows = [trial.outcome for trial in trials]
    n = len(rows)
    qualified = sum(row["qualified"] for row in rows)
    costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
    total = math.fsum(costs) if n and len(costs) == n else None
    return {
        "trial_count": n,
        "attempt_count": sum(row["recorded_attempt_count"] for row in rows),
        "graded_count": sum(row["graded"] for row in rows),
        "qualified_count": qualified,
        "qualified_completion_rate": qualified / n if n else None,
        "outcome_counts": dict(sorted(Counter(row["outcome"] for row in rows).items())),
        "runtime_status_counts": dict(
            sorted(Counter(row["runtime_status"] for row in rows).items())
        ),
        "infrastructure_failure_count": sum(row["infrastructure_failure"] for row in rows),
        "infrastructure_failure_rate": (
            sum(row["infrastructure_failure"] for row in rows) / n if n else None
        ),
        "cost_observations": len(costs),
        "total_cost_usd": total,
        "recorded_cost_usd": math.fsum(row["recorded_cost_usd"] for row in rows),
        "cost_per_qualified_task_usd": (
            total / qualified if total is not None and qualified else None
        ),
        "cost_per_qualified_task_status": (
            "no_qualified_tasks"
            if not qualified
            else "incomplete_cost_capture" if total is None else "defined"
        ),
        "elapsed_seconds": _describe(row["elapsed_seconds"] for row in rows),
        "end_to_end_seconds": _describe(row["end_to_end_seconds"] for row in rows),
        "runner_window_seconds": _describe(row["runner_window_seconds"] for row in rows),
        "human_intervention_count": _describe(row["human_intervention_count"] for row in rows),
        "human_active_minutes": _describe(row["human_active_minutes"] for row in rows),
        "safety": {
            key: {
                "count": sum(row["safety"][key] or 0 for row in rows),
                "observations": sum(row["safety"][key] is not None for row in rows),
            }
            for key in _SAFETY
        },
        "reproducibility": _reproducibility(trials),
    }


def _thresholds(plan: Mapping) -> dict[str, float | int]:
    supplied = _mapping(plan.get("thresholds"))
    thresholds = {}
    for key, default in (
        ("quality_floor", 0.8),
        ("equivalence_margin", 0.05),
        ("confidence", 0.95),
    ):
        value = _number(supplied.get(key, default))
        if value is None or value > 1 or (key == "confidence" and not 0 < value < 1):
            raise ValueError(f"Invalid {key}")
        thresholds[key] = value
    samples = _count(supplied.get("bootstrap_samples", 2000))
    seed = _count(plan.get("bootstrap_seed", plan.get("seed", 2026)))
    if samples is None or not 100 <= samples <= 100_000 or seed is None:
        raise ValueError("Invalid bootstrap sample count or seed")
    return {**thresholds, "bootstrap_samples": samples, "bootstrap_seed": seed}


def _difference(
    paired: list[tuple[_Trial, _Trial]], field: str, settings: Mapping
) -> dict[str, Any]:
    by_task: dict[str, list[float]] = defaultdict(list)
    for left, right in paired:
        a, b = left.outcome[field], right.outcome[field]
        if a is not None and b is not None:
            by_task[left.outcome["task_id"]].append(b - a)
    task_means = [fmean(values) for values in by_task.values()]
    pair_count = sum(map(len, by_task.values()))
    result = {
        "task_count": len(task_means),
        "observed_pairs": pair_count,
        "right_minus_left": fmean(task_means) if task_means else None,
        "confidence_interval": None,
        "interval_method": None,
    }
    n = len(task_means)
    if n < 2:
        return result

    alpha = 1 - settings["confidence"]
    if field == "qualified" and len(set(task_means)) == 1:
        # Resampling only observed zeros gives a spuriously narrow [0, 0]. Bound the
        # chance of sampling a task with a discordant repetition when none was seen.
        if all(all(x == 0 for x in values) for values in by_task.values()):
            bound = 1 - alpha ** (1 / n)
            return {
                **result,
                "confidence_interval": [-bound, bound],
                "interval_method": "zero_discordance_binomial_bound",
            }
        center = task_means[0]
        radius = math.sqrt(2 * math.log(2 / alpha) / n)
        return {
            **result,
            "confidence_interval": [max(-1.0, center - radius), min(1.0, center + radius)],
            "interval_method": "bounded_task_mean_hoeffding_interval",
        }
    rng = random.Random(settings["bootstrap_seed"])
    draws = [fmean(rng.choices(task_means, k=n)) for _ in range(settings["bootstrap_samples"])]
    return {
        **result,
        "confidence_interval": [_percentile(draws, alpha / 2), _percentile(draws, 1 - alpha / 2)],
        "interval_method": "task_cluster_percentile_bootstrap",
    }


def _usable_limits(limits: Mapping) -> bool:
    return (
        bool(limits)
        and all(value is None or _number(value) is not None for value in limits.values())
        and any(
            _number(limits.get(key)) is not None
            for key in ("time_s", "tokens", "cost_usd", "steps")
        )
    )


def _equal_control(left: _Trial, right: _Trial, name: str) -> bool:
    a, b = left.controls.get(name), right.controls.get(name)
    return a is not None and a != "" and type(a) is type(b) and a == b


def _match_reasons(
    paired: list[tuple[_Trial, _Trial]],
    rows: list[dict[str, Any]],
    kind: str,
    feature: str | None,
    stratum: tuple[str, ...],
) -> set[str]:
    reasons: set[str] = set()
    if not paired or any(row["pair_status"] != "paired" for row in rows):
        reasons.add("incomplete_pair_coverage")
    if "unknown" in stratum:
        reasons.add("unverified_stratum")
    for left, right in paired:
        a, b = left.outcome, right.outcome
        if not a["graded"] or not b["graded"]:
            reasons.add("missing_grades")
        if not a["record_closed"] or not b["record_closed"]:
            reasons.add("incomplete_trial_records")
        if left.pair_set is None or right.pair_set is None:
            reasons.add("unverified_trial_batch")
        if not left.data_hash or left.data_hash != right.data_hash:
            reasons.add("unmatched_initial_data")
        if not a["task_version"] or a["task_version"] != b["task_version"]:
            reasons.add("unmatched_task_version")
        for row in (a, b):
            if not row["framework_version"] or (
                row["declared_framework_version"]
                and row["framework_version"] != row["declared_framework_version"]
            ):
                reasons.add("unverified_framework_versions")
        if left.controls.get("conditions_not_matched") or right.controls.get(
            "conditions_not_matched"
        ):
            reasons.add("runner_reported_unmatched_conditions")
        if not all(_equal_control(left, right, field) for field in _MATCHED_CONTROLS):
            reasons.add("unmatched_operating_conditions")
        if kind in {"same_model", "ablation"} and not all(
            _equal_control(left, right, field) for field in _MODEL_CONTROLS
        ):
            reasons.add("unmatched_model_conditions")
        if kind == "ablation":
            a_features = _mapping(left.controls.get("features"))
            b_features = _mapping(right.controls.get("features"))
            if (
                any(
                    type(a_features.get(name)) is not bool or type(b_features.get(name)) is not bool
                    for name in _FEATURES
                )
                or set(a_features) != set(b_features)
                or {key for key in a_features if a_features[key] != b_features[key]} != {feature}
            ):
                reasons.add("ablation_changes_unisolated")
    for side in range(2):
        if len({pair[side].outcome["framework_version"] for pair in paired}) > 1:
            reasons.add("mixed_framework_versions")
    return reasons


def _same_quality(
    quality: dict[str, Any],
    paired: list[tuple[_Trial, _Trial]],
    side_summaries: tuple[dict, dict],
    settings: Mapping,
    reasons: set[str],
    status: str,
) -> dict[str, Any]:
    left_summary, right_summary = side_summaries
    more_reasons = set(reasons)
    floor = settings["quality_floor"]
    margin = settings["equivalence_margin"]
    if any(
        side["qualified_completion_rate"] is None or side["qualified_completion_rate"] < floor
        for side in side_summaries
    ):
        more_reasons.add("quality_floor_not_met")
    interval = quality["confidence_interval"]
    if interval is None or not -margin <= interval[0] <= interval[1] <= margin:
        more_reasons.add("quality_equivalence_not_established")
    cost_reasons = set(more_reasons)
    time_reasons = set(more_reasons)
    if any(side["cost_per_qualified_task_usd"] is None for side in side_summaries):
        cost_reasons.add("incomplete_cost_per_qualified_task")
    if any(
        side["elapsed_seconds"]["observations"] != side["trial_count"] or not side["trial_count"]
        for side in side_summaries
    ):
        time_reasons.add("incomplete_latency_capture")
    clocks = {trial.outcome["elapsed_source"] for pair in paired for trial in pair}
    if len(clocks) != 1:
        time_reasons.add("inconsistent_latency_clocks")
    if status == "confirmation" and clocks != {"observer_end_to_end"}:
        time_reasons.add("end_to_end_latency_unverified")
    claims = (not cost_reasons, not time_reasons)
    return {
        "status": status if all(claims) else "partial" if any(claims) else "not_established",
        "reasons": sorted(cost_reasons | time_reasons),
        "cost_status": "not_established" if cost_reasons else status,
        "elapsed_status": "not_established" if time_reasons else status,
        "elapsed_source": next(iter(clocks)) if len(clocks) == 1 else None,
        "right_minus_left_cost_per_qualified_task_usd": (
            right_summary["cost_per_qualified_task_usd"]
            - left_summary["cost_per_qualified_task_usd"]
            if not cost_reasons
            else None
        ),
        "all_pair_elapsed_seconds": (
            _difference(paired, "elapsed_seconds", settings) if not time_reasons else None
        ),
        "all_pair_cost_usd": (
            _difference(paired, "cost_usd", settings) if not cost_reasons else None
        ),
    }


def _planned_stratum(plan: Mapping) -> tuple[str, ...]:
    planned = {
        "cache": plan.get("cache"),
        "mode": plan.get("mode", plan.get("execution_mode")),
        "source": plan.get("source", plan.get("execution_source")),
        "stage": plan.get("stage") or _STAGE_BY_SPLIT.get(plan.get("split")),
    }
    # Do not substitute the runner's implicit automated mode for an undeclared plan stratum.
    return _stratum(planned, {}, {})


def _observed_feature(side: list[_Trial], feature: str | None) -> bool | None:
    values = [_mapping(trial.controls.get("features")).get(feature) for trial in side]
    if values and type(values[0]) is bool and all(value is values[0] for value in values):
        return values[0]
    return None


def _comparison(
    spec: Mapping,
    trials: list[_Trial],
    stratum: tuple[str, ...],
    plan: Mapping,
    settings: Mapping,
) -> dict[str, Any]:
    comparison_id = _identifier(spec.get("id"), "comparison ID")
    left_config = _identifier(spec.get("left"), "left configuration")
    right_config = _identifier(spec.get("right"), "right configuration")
    kind = spec.get("kind")
    feature = spec.get("feature")
    if left_config == right_config or kind not in {"same_model", "product", "ablation"}:
        raise ValueError("Invalid comparison design")
    if (kind == "ablation" and feature not in _FEATURES) or (
        kind != "ablation" and feature is not None
    ):
        raise ValueError("Invalid comparison feature")

    # Records outside the planned cell set remain in trial outcomes and summaries,
    # but cannot enter a prespecified paired experiment.
    sides = (
        [t for t in trials if t.outcome["config"] == left_config],
        [t for t in trials if t.outcome["config"] == right_config],
    )
    indexed: tuple[dict, dict] = (defaultdict(list), defaultdict(list))
    for side, index in zip(sides, indexed):
        for trial in side:
            index[(trial.outcome["task_id"], trial.outcome["repetition_index"])].append(trial)
    observed = set(indexed[0]) | set(indexed[1])
    task_ids, repetitions = plan.get("task_ids"), _count(plan.get("repetitions"))
    expected = (
        {
            (_identifier(task_id, "plan task ID"), n)
            for task_id in task_ids
            for n in range(repetitions)
        }
        if isinstance(task_ids, list) and repetitions
        else observed
    )
    paired = []
    pair_rows = []
    for task_id, repetition in sorted(
        expected | observed, key=lambda k: (k[0], -1 if k[1] is None else k[1])
    ):
        key = (task_id, repetition)
        left, right = indexed[0].get(key, []), indexed[1].get(key, [])
        if repetition is None or key not in expected:
            pair_status = "unplanned_cell"
        elif len(left) > 1 or len(right) > 1:
            pair_status = "duplicate_cell"
        elif not left and not right:
            pair_status = "missing_both"
        elif not left or not right:
            pair_status = "missing_left" if not left else "missing_right"
        elif left[0].pair_set != right[0].pair_set:
            pair_status = "different_trial_batches"
        else:
            pair_status = "paired"
            paired.append((left[0], right[0]))
        pair_rows.append(
            {
                "task_id": task_id,
                "repetition_index": repetition,
                "pair_status": pair_status,
                "left_trial_ids": [trial.outcome["trial_id"] for trial in left],
                "right_trial_ids": [trial.outcome["trial_id"] for trial in right],
                "left_outcome": left[0].outcome["outcome"] if len(left) == 1 else None,
                "right_outcome": right[0].outcome["outcome"] if len(right) == 1 else None,
                "left_qualified": left[0].outcome["qualified"] if len(left) == 1 else None,
                "right_qualified": right[0].outcome["qualified"] if len(right) == 1 else None,
            }
        )
    side_summaries = (_summary(sides[0]), _summary(sides[1]))
    settings_reason = _match_reasons(paired, pair_rows, kind, feature, stratum)
    if not isinstance(task_ids, list) or not repetitions:
        settings_reason.add("undeclared_task_or_repetition_set")
    quality = _difference(paired, "qualified", settings)
    matched_budget = bool(paired) and all(
        _usable_limits(left.limits) and left.limits == right.limits for left, right in paired
    )
    budget_reasons = set(settings_reason)
    if not matched_budget:
        budget_reasons.add("unmatched_or_undeclared_limits")
    _, _, source, stage = stratum
    formal_evidence = (
        plan.get("protocol_verified") is True
        and source == "live"
        and stage == "confirmation"
        and repetitions is not None
        and repetitions >= 2
    )
    status = "confirmation" if formal_evidence else "descriptive"
    return {
        "id": comparison_id,
        "kind": kind,
        "feature": feature,
        "left": {
            "config": left_config,
            "feature_enabled": _observed_feature(sides[0], feature),
            **side_summaries[0],
        },
        "right": {
            "config": right_config,
            "feature_enabled": _observed_feature(sides[1], feature),
            **side_summaries[1],
        },
        **dict(zip(_STRATUM_FIELDS, stratum)),
        "pair_status_counts": dict(
            sorted(Counter(row["pair_status"] for row in pair_rows).items())
        ),
        "paired_outcomes": pair_rows,
        "paired_qualified_rate_difference": quality,
        "quality_at_same_budget": {
            "status": "not_established" if budget_reasons else status,
            "reasons": sorted(budget_reasons),
            "paired_qualified_rate_difference": quality if not budget_reasons else None,
        },
        "cost_time_at_same_quality": _same_quality(
            quality, paired, side_summaries, settings, settings_reason, status
        ),
    }


def build_report(
    records: Iterable[Any], grades: Mapping, *, plan: Mapping | None = None
) -> dict[str, Any]:
    """Return an allowlisted report for PR #39 records and independent trial grades.

    Plan comparisons are within cache, user-interaction, source and evaluation stage
    strata. The paid runner's retired counters do not attest to complete billing;
    real/replayed runs need an explicitly complete itemized charge ledger.
    """
    plan = plan or {}
    settings = _thresholds(plan)
    trials = sorted(
        (_trial(record, grades, plan) for record in records), key=lambda t: t.outcome["trial_id"]
    )
    if len({trial.outcome["trial_id"] for trial in trials}) != len(trials):
        raise ValueError("Duplicate trial ID in report input")

    grouped: dict[tuple, list[_Trial]] = defaultdict(list)
    strata: dict[tuple, list[_Trial]] = defaultdict(list)
    for trial in trials:
        grouped[
            (trial.stratum, trial.outcome["config"], trial.outcome["framework_version"])
        ].append(trial)
        strata[trial.stratum].append(trial)
    summaries = [
        {
            **dict(zip(_STRATUM_FIELDS, stratum)),
            "config": config,
            "framework_version": version,
            **_summary(group),
        }
        for (stratum, config, version), group in sorted(
            grouped.items(), key=lambda x: (x[0][0], x[0][1], x[0][2] or "")
        )
    ]

    planned = _planned_stratum(plan)
    eligible_strata = {
        key
        for key in strata
        if all(
            requested == "unknown" or requested == actual for requested, actual in zip(planned, key)
        )
    } or {planned}
    comparisons = [
        _comparison(spec, strata[key], key, plan, settings)
        for spec in plan.get("comparisons", [])
        for key in sorted(eligible_strata)
    ]
    return {
        "report_version": REPORT_VERSION,
        "methods": {
            "qualification_denominator": "all_recorded_trials_in_stratum",
            "cost_per_qualified_denominator": "qualified_trials_in_stratum",
            "cost_per_qualified_numerator": "all_observed_costs_including_failed_attempts_and_retries",
            "elapsed_time_population": "all_recorded_trials_with_measured_runner_or_end_to_end_elapsed_time",
            "paired_estimate": "equally_weighted_task_means_of_right_minus_left_repetition_differences",
            "paired_interval": "task_cluster_bootstrap_with_conservative_bounds_for_constant_qualification_differences",
            **settings,
        },
        "trial_outcomes": [trial.outcome for trial in trials],
        "summaries": summaries,
        "comparisons": comparisons,
    }
