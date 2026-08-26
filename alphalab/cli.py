"""Command-line entry point for local AlphaLab data operations."""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.quality import validate_all, validate_dataset
from alphalab.dataio.runtime import OperationsStore
from alphalab.dataio.sync import SyncJobManager, SyncRequest, build_sync_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alphalab")
    commands = parser.add_subparsers(dest="command", required=True)
    data = commands.add_parser("data", help="Manage local runtime data")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    data_commands.add_parser("catalog", help="List runtime datasets")
    data_commands.add_parser("status", help="Show runtime readiness")

    plan = data_commands.add_parser("plan", help="Preview an RQ sync")
    _add_sync_arguments(plan)
    sync = data_commands.add_parser("sync", help="Run an RQ sync")
    _add_sync_arguments(sync)

    validate = data_commands.add_parser("validate", help="Validate runtime datasets")
    validation_target = validate.add_mutually_exclusive_group()
    validation_target.add_argument(
        "dataset",
        nargs="?",
        help="Dataset id; omit to validate all",
    )
    validation_target.add_argument("--datasets", help="Comma-separated dataset ids to validate")
    validate.add_argument("--start", help="Required earliest daily-bar date (YYYY-MM-DD)")
    validate.add_argument("--as-of", help="Required latest date (YYYY-MM-DD)")
    validate.add_argument(
        "--fail-on-gap",
        action="store_true",
        help="Fail for stale bounds or incomplete market-state/factor coverage",
    )
    jobs = data_commands.add_parser("jobs", help="List local sync jobs")
    jobs.add_argument("--limit", type=int, default=20)
    return parser


def _add_sync_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", choices=["rq"])
    parser.add_argument(
        "--datasets",
        default="instruments,bars,market-state,fundamentals,factors",
        help="Comma-separated: instruments,bars,market-state,fundamentals,factors",
    )
    parser.add_argument("--symbols", help="Comma-separated framework symbols")
    parser.add_argument(
        "--universe",
        choices=["sample", "all"],
        default="sample",
        help="Use the bundled sample or resolve every historical A-share from RQ instruments",
    )
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--bar-chunk-days", type=int, default=366)
    parser.add_argument("--market-state-chunk-days", type=int, default=366)
    parser.add_argument("--daily-factors", default="market_cap,roe")
    parser.add_argument("--index-symbols", default="000300.SH,000905.SH,000852.SH")
    parser.add_argument(
        "--component-frequency",
        choices=["ME", "W-FRI", "B"],
        default="ME",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "data":
        return 2
    result: Any
    if args.data_command == "catalog":
        result = DataCatalog().list()
    elif args.data_command == "status":
        result = DataCatalog().summary()
    elif args.data_command == "jobs":
        result = OperationsStore().list_jobs(limit=max(1, args.limit))
    elif args.data_command == "validate":
        result = (
            validate_dataset(
                args.dataset,
                start_date=args.start,
                as_of_date=args.as_of,
                fail_on_gap=args.fail_on_gap,
            )
            if args.dataset
            else validate_all(
                datasets=_split(args.datasets) if args.datasets else None,
                start_date=args.start,
                as_of_date=args.as_of,
                fail_on_gap=args.fail_on_gap,
            )
        )
    elif args.data_command in {"plan", "sync"}:
        request = SyncRequest(
            source=args.source,
            datasets=_split(args.datasets),
            symbols=_split(args.symbols) if args.symbols else None,
            universe=args.universe,
            start=args.start,
            end=args.end,
            force=args.force,
            bar_chunk_days=args.bar_chunk_days,
            market_state_chunk_days=args.market_state_chunk_days,
            daily_factors=_split(args.daily_factors),
            index_symbols=_split(args.index_symbols),
            component_frequency=args.component_frequency,
        )
        if args.data_command == "plan":
            result = build_sync_plan(request)
        else:
            result = SyncJobManager().run_now(request)
    else:
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
    if isinstance(result, dict) and result.get("status") in {"failed", "invalid"}:
        return 1
    if isinstance(result, list) and any(item.get("status") == "failed" for item in result):
        return 1
    return 0


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
