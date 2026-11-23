"""Freeze, score and compare AlphaLab Agent trial records without provider access."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from apps.api.services.comparison.evaluation_protocol import (
    freeze_protocol,
    normalize_plan,
    read_json,
    verify_plan_tasks,
)

_REPOSITORY = Path(__file__).resolve().parents[1]


def _object_file(path: Path | None) -> dict:
    if path is None:
        return {}
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
            handle.write("\n")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _paid_trial_cost_caps(plan: dict) -> list[float]:
    """Return one cost cap per planned trial for an explicitly paid plan."""
    plan = normalize_plan(plan)
    if plan["source_class"] != "live":
        raise ValueError("A paid extension plan must declare source_class 'live'")
    task_ids = plan["task_ids"]
    repetitions = plan["repetitions"]
    configurations = plan["configurations"]
    caps = []
    for variant, config in configurations.items():
        cost = config["limits"]["cost_usd"]
        if type(cost) not in (int, float) or cost <= 0:
            raise ValueError(f"Configuration {variant} needs a positive cost_usd limit")
        caps.extend([float(cost)] * len(task_ids) * repetitions)
    return caps


def _parameters() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--suite-root", type=Path, default=_REPOSITORY,
        help="Root containing benchmark/manifest.json. Use tests/fixtures/agent_benchmark for the offline demo.",
    )
    actions = cli.add_subparsers(dest="action", required=True)
    freeze = actions.add_parser("freeze", help="Pin a task set, design and scoring thresholds")
    freeze.add_argument("--plan", type=Path, required=True)
    freeze.add_argument("--out", type=Path, required=True)

    evaluate = actions.add_parser("evaluate", help="Score already-recorded neutral #8 trials offline")
    evaluate.add_argument("--lock", type=Path, required=True)
    evaluate.add_argument("--records", nargs="+", type=Path, required=True)
    evaluate.add_argument("--observations", type=Path)
    evaluate.add_argument("--reviews", type=Path)
    evaluate.add_argument("--expected-protocol-sha256")
    evaluate.add_argument("--out", type=Path, required=True)
    evaluate.add_argument("--regression-out", type=Path)
    evaluate.add_argument("--system-version")

    regress = actions.add_parser("regress", help="Compare two publication-safe regression snapshots")
    regress.add_argument("--baseline", type=Path, required=True)
    regress.add_argument("--candidate", type=Path, required=True)
    regress.add_argument("--out", type=Path, required=True)

    preflight = actions.add_parser(
        "preflight-paid", help="Preflight an explicitly enabled paid extension without provider access"
    )
    preflight.add_argument("--plan", type=Path)
    preflight.add_argument("--total-budget-usd", type=float)
    preflight.add_argument("--runner-supports-paid", action="store_true")
    preflight.add_argument("--enable", action="store_true")
    return cli


def main(argv: list[str] | None = None) -> int:
    cli = _parameters()
    args = cli.parse_args(argv)
    try:
        if args.action == "freeze":
            lock, _ = freeze_protocol(_object_file(args.plan), args.suite_root)
            _write_json(args.out, lock)
            print(lock["protocol_sha256"])
            return 0
        if args.action == "regress":
            from apps.api.services.comparison.regression import compare_regression_snapshots

            result = compare_regression_snapshots(_object_file(args.baseline), _object_file(args.candidate))
            _write_json(args.out, result)
            return int(result["has_regression"] or result["requires_rebaseline"])
        if args.action == "preflight-paid":
            if not args.enable:
                print("Paid extension disabled. Pass --enable to preflight a requested paid plan.")
                return 0
            from apps.api.services.comparison.regression import check_paid_extension

            if args.plan is None:
                raise ValueError("--plan is required with --enable")
            if args.total_budget_usd is None:
                raise ValueError("--total-budget-usd is required with --enable")
            plan = _object_file(args.plan)
            verify_plan_tasks(plan, args.suite_root)
            caps = _paid_trial_cost_caps(plan)
            try:
                approved = check_paid_extension(
                    enabled=True, total_budget_usd=args.total_budget_usd,
                    trial_cost_caps_usd=caps, runner_supports_paid=args.runner_supports_paid,
                )
            except RuntimeError as exc:
                raise ValueError(str(exc)) from exc
            if not approved:
                raise ValueError("Paid extension preflight was not approved")
            print(
                f"Paid extension preflight approved: {len(caps)} trials, "
                f"{sum(caps):.2f} USD of caps within {args.total_budget_usd:.2f} USD"
            )
            return 0

        from apps.api.services.comparison.benchmark_evaluation import (
            evaluate_records,
            record_objects,
        )

        if bool(args.regression_out) != bool(args.system_version):
            raise ValueError("Provide --regression-out and --system-version together")
        if args.regression_out is not None and args.regression_out.resolve() == args.out.resolve():
            raise ValueError("Specify distinct paths for the report and regression snapshot")
        report, snapshot = evaluate_records(
            _object_file(args.lock), args.suite_root,
            record_objects(read_json(path) for path in args.records),
            observations=_object_file(args.observations), reviews=_object_file(args.reviews),
            expected_protocol_sha256=args.expected_protocol_sha256,
            regression_system_version=args.system_version,
        )
        _write_json(args.out, report)
        if snapshot is not None:
            _write_json(args.regression_out, snapshot)
        return 0
    except (OSError, TypeError, KeyError, ValueError) as exc:
        print(f"Benchmark evaluation error ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
