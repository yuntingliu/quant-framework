"""Analyse paired Issue #24 benchmark records from all recorded attempts.

Quality labels are supplied in a separate, independently prepared adjudication
file. This script never asks an LLM to judge another LLM and never assigns zero
when token/cost data is unavailable.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "alphalab/collaboration/schemas/run-record.v1.json"
ARMS = ("single", "single_self_check", "two_agent")
SCORE_FIELDS = (
    "seeded_error_opportunities",
    "errors_detected",
    "errors_corrected",
    "false_alarms",
    "unsupported_conclusions_remaining",
)
BUDGET_PARITY_FIELDS = ("wall_seconds", "model_calls", "tokens", "cost", "currency")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def record_paths(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob("record.json"))


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def bootstrap_interval(values: list[float], *, seed: int = 24, samples: int = 10_000) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        draw = [values[rng.randrange(len(values))] for _ in values]
        means.append(statistics.fmean(draw))
    lower = percentile(means, 0.025)
    upper = percentile(means, 0.975)
    return [round(lower, 6), round(upper, 6)] if lower is not None and upper is not None else None


def load_adjudications(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("adjudications"), dict):
        raise ValueError("Adjudications file must have schema_version 1 and an adjudications object.")
    result = payload["adjudications"]
    for record_id, row in result.items():
        if not isinstance(row, dict) or type(row.get("qualified")) is not bool:
            raise ValueError(f"Adjudication {record_id} requires a Boolean qualified field.")
        scores = row.get("quality_scores")
        if not isinstance(scores, dict) or any(type(scores.get(name)) is not int for name in SCORE_FIELDS):
            raise ValueError(f"Adjudication {record_id} must provide integer quality_scores: {SCORE_FIELDS}.")
        if any(scores[name] < 0 for name in SCORE_FIELDS):
            raise ValueError(f"Adjudication {record_id} has a negative quality count.")
        if scores["errors_detected"] > scores["seeded_error_opportunities"]:
            raise ValueError(f"Adjudication {record_id}: detected errors exceed seeded opportunities.")
        if scores["errors_corrected"] > scores["errors_detected"]:
            raise ValueError(f"Adjudication {record_id}: corrected errors exceed detected errors.")
        if not isinstance(row.get("basis"), str) or not row["basis"].strip():
            raise ValueError(f"Adjudication {record_id} requires an audit basis.")
    return result


def load_records(paths: list[Path], adjudications: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not paths:
        raise ValueError("No record.json files were found.")
    schema = read_json(SCHEMA_PATH)
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    records = []
    seen = set()
    for path in paths:
        row = read_json(path)
        record_id = row.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"Record at {path} has no record_id.")
        if record_id in seen:
            raise ValueError(f"Duplicate record_id: {record_id}")
        seen.add(record_id)
        if record_id not in adjudications:
            raise ValueError(f"Missing independent quality adjudication for record: {record_id}")
        adjudication = adjudications[record_id]
        row["outcome"]["qualified"] = adjudication["qualified"]
        row["outcome"]["quality_scores"] = adjudication["quality_scores"]
        errors = sorted(validator.iter_errors(row), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.path)
            raise ValueError(f"Invalid run record {record_id} at {location}: {first.message}")
        validate_accounting(row)
        records.append(row)
    unused = set(adjudications) - seen
    if unused:
        raise ValueError(f"Adjudications contain unknown record IDs: {sorted(unused)}")
    return records



def validate_accounting(row: dict[str, Any]) -> None:
    """Check cross-field accounting invariants in addition to the JSON schema."""
    record_id = row["record_id"]
    roles = row["roles"]
    names = [item["role"] for item in roles]
    expected_roles = {"researcher", "reviewer"} if row["arm"] == "two_agent" else {"researcher"}
    if len(names) != len(set(names)) or set(names) != expected_roles:
        raise ValueError(f"Run {record_id} has duplicate or unexpected role entries")
    for field in ("model_calls", "tool_calls", "retries", "searches"):
        if row["totals"][field] != sum(item[field] for item in roles):
            raise ValueError(f"Run {record_id} has inconsistent role/total accounting for {field}")
    attempts = row["attempts"]
    attempt_ids = [item["id"] for item in attempts]
    if len(attempts) != row["totals"]["model_calls"] or len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError(f"Run {record_id} does not preserve unique records for every model-call attempt")
    for role in roles:
        role_attempts = [item for item in attempts if item["role"] == role["role"]]
        if len(role_attempts) != role["model_calls"]:
            raise ValueError(f"Run {record_id} has a role/model-call attempt mismatch")
        if sum(len(item["tool_names"]) for item in role_attempts) != role["tool_calls"]:
            raise ValueError(f"Run {record_id} has a role/tool-call attempt mismatch")
    for token_field in ("input_tokens", "output_tokens"):
        values = [role["usage"][token_field] for role in roles]
        expected = None if any(value is None for value in values) else sum(values)
        if row["totals"]["usage"][token_field] != expected:
            raise ValueError(f"Run {record_id} has inconsistent token totals")
    role_costs = [role["cost"] for role in roles]
    total_cost = row["totals"]["cost"]
    if any(cost["amount"] is None for cost in role_costs):
        if total_cost["amount"] is not None or total_cost["status"] != "unavailable":
            raise ValueError(f"Run {record_id} must preserve unknown role cost in the total")
    else:
        currencies = {cost["currency"] for cost in role_costs}
        if len(currencies) != 1 or total_cost["currency"] != next(iter(currencies)):
            raise ValueError(f"Run {record_id} cannot sum role costs with inconsistent currencies")
        expected_cost = sum((Decimal(cost["amount"]) for cost in role_costs), Decimal("0"))
        if total_cost["amount"] is None or Decimal(total_cost["amount"]) != expected_cost:
            raise ValueError(f"Run {record_id} has inconsistent cost totals")
    if row["outcome"]["status"] != "completed" and not row["outcome"]["reason"]:
        raise ValueError(f"Run {record_id} has a non-completed outcome without a reason")
    if row["mode"] == "live" and total_cost["status"] == "scripted_no_external_provider":
        raise ValueError(f"Run {record_id} cannot claim scripted zero cost for live calls")

def validate_pairs(records: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, dict[str, Any]]]:
    pairs: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in records:
        arm = row["arm"]
        if arm not in ARMS:
            raise ValueError(f"Unsupported benchmark arm: {arm}")
        key = (row["paired_case_id"], row["repetition"])
        if arm in pairs[key]:
            raise ValueError(f"Duplicate arm for paired task/repetition: {key}/{arm}")
        pairs[key][arm] = row
    for key, rows in pairs.items():
        if set(rows) != set(ARMS):
            raise ValueError(f"Incomplete paired task {key}: found {sorted(rows)}, expected {list(ARMS)}")
        fingerprints = {(r["fixture"]["id"], r["fixture"]["sha256"], r["mode"]) for r in rows.values()}
        if len(fingerprints) != 1:
            raise ValueError(f"Paired task inputs/modes differ for {key}")
        model_settings = {(r["configuration"]["model"], r["configuration"]["provider"]) for r in rows.values()}
        if len(model_settings) != 1:
            raise ValueError(f"Model/provider settings differ across arms for {key}")
        a, b = rows["single_self_check"]["budget"], rows["two_agent"]["budget"]
        if any(a.get(field) != b.get(field) for field in BUDGET_PARITY_FIELDS):
            raise ValueError(f"Self-check and two-agent total allowances are not matched for {key}")
    return pairs


def sum_if_known(values: list[int | None]) -> int | None:
    return None if any(value is None for value in values) else sum(values)


def role_cost_total(records: list[dict[str, Any]]) -> tuple[Decimal | None, str | None, str | None]:
    costs = [row["totals"]["cost"] for row in records]
    if any(cost["amount"] is None or cost["status"] == "unavailable" for cost in costs):
        return None, None, "At least one attempt has unavailable cost; no zero-cost imputation was made."
    currencies = {cost["currency"] for cost in costs if cost["currency"] is not None}
    statuses = {cost["status"] for cost in costs}
    if len(currencies) > 1:
        return None, None, "Mixed currencies cannot be summed."
    if "scripted_no_external_provider" in statuses and len(statuses) > 1:
        return None, None, "Scripted and provider-billed records cannot be combined as one currency total."
    currency = next(iter(currencies), None)
    return sum((Decimal(cost["amount"]) for cost in costs), Decimal("0")), currency, None


def arm_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    qualified = sum(row["outcome"]["qualified"] is True for row in rows)
    all_scores = [row["outcome"]["quality_scores"] for row in rows]
    totals = {}
    for field in SCORE_FIELDS:
        totals[field] = sum(score[field] for score in all_scores)
    calls = sum(row["totals"]["model_calls"] for row in rows)
    retries = sum(row["totals"]["retries"] for row in rows)
    tokens_in = sum_if_known([row["totals"]["usage"]["input_tokens"] for row in rows])
    tokens_out = sum_if_known([row["totals"]["usage"]["output_tokens"] for row in rows])
    seconds = [float(row["timings"]["total_seconds"]) for row in rows]
    costs, currency, cost_reason = role_cost_total(rows)
    human_rows = [item for row in rows for item in row["human_interventions"]]
    human_seconds = [item["active_seconds"] for item in human_rows]
    human_seconds_total = None if any(value is None for value in human_seconds) else sum(human_seconds)
    false_alarm_opportunities = sum(
        1 for row in rows
        if any(tag in str(row["paired_case_id"]).upper() for tag in ("CLEAN", "FALSE-ALARM", "DISPUTE"))
    )
    score_denominator = totals["seeded_error_opportunities"]
    detected_rate = (totals["errors_detected"] / score_denominator) if score_denominator else None
    correction_rate = (totals["errors_corrected"] / totals["errors_detected"]) if totals["errors_detected"] else None
    return {
        "runs": len(rows),
        "qualified_tasks": qualified,
        "qualified_completion_rate": qualified / len(rows) if rows else None,
        "outcomes": dict(Counter(row["outcome"]["status"] for row in rows)),
        "quality": {
            **totals,
            "error_detection_rate": detected_rate,
            "error_correction_rate_given_detection": correction_rate,
            "false_alarms_per_clean_or_false_alarm_task": (
                totals["false_alarms"] / false_alarm_opportunities if false_alarm_opportunities else None
            ),
        },
        "effort": {
            "total_elapsed_seconds": round(sum(seconds), 4),
            "mean_elapsed_seconds": round(statistics.fmean(seconds), 4),
            "median_elapsed_seconds": round(statistics.median(seconds), 4),
            "p95_elapsed_seconds": round(percentile(seconds, 0.95) or 0, 4),
            "model_calls_including_failed_attempts": calls,
            "retries": retries,
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "human_interventions": len(human_rows),
            "human_active_seconds": human_seconds_total,
        },
        "cost": {
            "total_cost": str(costs) if costs is not None else None,
            "currency": currency,
            "cost_per_qualified_task": str(costs / qualified) if costs is not None and qualified else None,
            "status": "known" if costs is not None else "unavailable",
            "reason": cost_reason or ("No qualified tasks; cost per qualified task is undefined." if not qualified else None),
        },
    }


def paired_differences(pairs: dict[tuple[str, int], dict[str, dict[str, Any]]], left: str, right: str, metric: str) -> dict[str, Any]:
    diffs = []
    for rows in pairs.values():
        a = rows[left]
        b = rows[right]
        if metric == "qualified":
            diffs.append(float(a["outcome"]["qualified"]) - float(b["outcome"]["qualified"]))
        elif metric == "elapsed_seconds":
            diffs.append(float(a["timings"]["total_seconds"]) - float(b["timings"]["total_seconds"]))
    return {
        "left_minus_right_mean": round(statistics.fmean(diffs), 6) if diffs else None,
        "bootstrap_95_percent_ci": bootstrap_interval(diffs),
        "paired_observations": len(diffs),
        "positive_means_left_higher": True,
    }


def analyse(records: list[dict[str, Any]], pairs: dict[tuple[str, int], dict[str, dict[str, Any]]]) -> dict[str, Any]:
    grouped = {arm: [row for row in records if row["arm"] == arm] for arm in ARMS}
    return {
        "schema_version": 1,
        "method": "paired three-arm analysis; independent adjudication; 10,000-resample percentile bootstrap (seed 24)",
        "paired_trials": len(pairs),
        "arms": {arm: arm_summary(rows) for arm, rows in grouped.items()},
        "paired_comparisons": {
            "two_agent_vs_single": {
                "qualified_completion": paired_differences(pairs, "two_agent", "single", "qualified"),
                "elapsed_seconds": paired_differences(pairs, "two_agent", "single", "elapsed_seconds"),
            },
            "two_agent_vs_single_self_check": {
                "qualified_completion": paired_differences(pairs, "two_agent", "single_self_check", "qualified"),
                "elapsed_seconds": paired_differences(pairs, "two_agent", "single_self_check", "elapsed_seconds"),
            },
        },
        "caveats": [
            "Quality labels must be assigned independently using the frozen scoring rubric; this script does not use an LLM judge.",
            "All supplied run records, including failed, blocked, timed-out and retried attempts, are included.",
            "Unavailable token or cost data remains null and is not converted to zero.",
            "A small paired sample produces wide, unstable confidence intervals; interpret them descriptively.",
        ],
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Issue #24 benchmark summary",
        "",
        f"Paired trials: **{summary['paired_trials']}**",
        "",
        "| Configuration | Qualified tasks | Completion rate | Mean seconds | Model calls | Tokens in/out | Cost per qualified task |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm, row in summary["arms"].items():
        effort, cost = row["effort"], row["cost"]
        tokens = f"{effort['input_tokens']}/{effort['output_tokens']}" if effort['input_tokens'] is not None and effort['output_tokens'] is not None else "Unavailable"
        cpq = f"{cost['cost_per_qualified_task']} {cost['currency'] or ''}".strip() if cost["cost_per_qualified_task"] is not None else "Undefined / unavailable"
        lines.append(f"| {arm} | {row['qualified_tasks']}/{row['runs']} | {row['qualified_completion_rate']:.1%} | {effort['mean_elapsed_seconds']:.2f} | {effort['model_calls_including_failed_attempts']} | {tokens} | {cpq} |")
    lines.extend(["", "## Paired comparisons", ""])
    for name, comparisons in summary["paired_comparisons"].items():
        lines.append(f"### {name}")
        for metric, result in comparisons.items():
            lines.append(f"- {metric}: mean left-minus-right = {result['left_minus_right_mean']}; 95% bootstrap CI = {result['bootstrap_95_percent_ci']}; n = {result['paired_observations']}")
        lines.append("")
    lines.extend(["## Caveats", ""])
    lines.extend(f"- {item}" for item in summary["caveats"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True, help="Directory containing per-run record.json files, or one record.json.")
    parser.add_argument("--adjudications", type=Path, required=True, help="Independent manual quality labels keyed by record_id.")
    parser.add_argument("--output", type=Path, required=True, help="Output directory for summary.json and summary.md.")
    args = parser.parse_args()
    adjudications = load_adjudications(args.adjudications)
    records = load_records(record_paths(args.records), adjudications)
    pairs = validate_pairs(records)
    summary = analyse(records, pairs)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (args.output / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    print(json.dumps({"status": "passed", "records": len(records), "paired_trials": len(pairs), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
