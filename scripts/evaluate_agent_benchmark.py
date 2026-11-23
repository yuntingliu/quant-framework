"""Freeze, score and compare AlphaLab Agent trial records without provider access."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from apps.api.services.comparison.evaluation_protocol import freeze_protocol, read_json

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
