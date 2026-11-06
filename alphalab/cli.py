"""Command-line entry point for local AlphaLab data operations."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.quality import validate_all, validate_dataset
from alphalab.dataio.rq_templates import (
    DEFAULT_RQ_SYNC_TEMPLATE_ID,
    list_rq_sync_templates,
)
from alphalab.dataio.runtime import OperationsStore
from alphalab.dataio.sync import SyncJobManager, SyncRequest, build_sync_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alphalab")
    commands = parser.add_subparsers(dest="command", required=True)
    data = commands.add_parser("data", help="Manage local runtime data")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    data_commands.add_parser("catalog", help="List runtime datasets")
    data_commands.add_parser("status", help="Show runtime readiness")
    data_commands.add_parser("templates", help="List built-in RQ sync templates")

    plan = data_commands.add_parser("plan", help="Preview an RQ sync")
    _add_sync_arguments(plan)
    sync = data_commands.add_parser("sync", help="Run an RQ sync")
    _add_sync_arguments(sync)

    validate = data_commands.add_parser("validate", help="Validate runtime datasets")
    validate.add_argument("dataset", nargs="?", help="Dataset id; omit to validate all")
    validate.add_argument("--datasets", help="Comma-separated dataset ids")
    validate.add_argument("--start", help="Required coverage start date")
    validate.add_argument("--as-of", help="Required coverage end date")
    validate.add_argument("--fail-on-gap", action="store_true")
    jobs = data_commands.add_parser("jobs", help="List local sync jobs")
    jobs.add_argument("--limit", type=int, default=20)
    dev = commands.add_parser("dev", help="Inspect the local development runtime")
    dev_commands = dev.add_subparsers(dest="dev_command", required=True)
    dev_commands.add_parser("doctor", help="Report local runtime readiness")
    serve = dev_commands.add_parser("serve", help="Serve the built workbench and API locally")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--agent", choices=["local", "remote", "off"], default="local")
    serve.add_argument("--agent-port", type=int, default=0, help="Local Conexus port; 0 selects an available port")
    _add_release_commands(commands)
    return parser


def _add_release_commands(commands: Any) -> None:
    release = commands.add_parser("release", help="Inspect and verify deployable releases")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    manifest = release_commands.add_parser("manifest", help="Write the release manifest")
    manifest.add_argument("--release-dir", default=".")
    manifest.add_argument("--commit", required=True)
    manifest.add_argument("--ref")
    verify = release_commands.add_parser(
        "verify", help="Verify a release against its manifest and the current release"
    )
    verify.add_argument("--release-dir", default=".")
    verify.add_argument("--current", help="Currently active release directory, if any")
    verify.add_argument("--allow-layout-change", action="store_true")
    version = release_commands.add_parser("version", help="Show local or running instance versions")
    version.add_argument("--url", help="Loopback origin or health URL of a running instance")
    version.add_argument("--release-dir", default=None)
    tasks = release_commands.add_parser("tasks", help="List active tasks that block a release switch")
    tasks.add_argument("--runtime-dir", default=None)


def _add_sync_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", choices=["rq"])
    parser.add_argument(
        "--template",
        default=DEFAULT_RQ_SYNC_TEMPLATE_ID,
        choices=[item.id for item in list_rq_sync_templates()],
        help="Acquisition template; datasets default to the template contract",
    )
    parser.add_argument(
        "--datasets",
        default=None,
        help="Comma-separated: instruments,bars,fundamentals,factors",
    )
    parser.add_argument(
        "--symbols",
        help="Comma-separated framework symbols; omit to resolve the full template scope",
    )
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--force", action="store_true")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "dev" and args.dev_command == "serve":
        from alphalab.devtools import serve_workbench

        try:
            serve_workbench(host=args.host, port=args.port, agent=args.agent, agent_port=args.agent_port)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    if args.command == "dev" and args.dev_command == "doctor":
        from alphalab.devtools import doctor_report

        result = doctor_report()
        print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
        return 0 if result.get("status") == "ready" else 1
    if args.command == "release":
        return _release(args)
    if args.command != "data":
        return 2
    result: Any
    if args.data_command == "catalog":
        result = DataCatalog().list()
    elif args.data_command == "status":
        result = DataCatalog().summary()
    elif args.data_command == "templates":
        result = [item.to_dict() for item in list_rq_sync_templates()]
    elif args.data_command == "jobs":
        result = OperationsStore().list_jobs(limit=max(1, args.limit))
    elif args.data_command == "validate":
        selected = _split(args.datasets) if args.datasets else None
        if args.dataset and selected:
            raise SystemExit("dataset and --datasets cannot be used together")
        result = (
            validate_dataset(
                args.dataset,
                start_date=args.start,
                as_of_date=args.as_of,
                fail_on_gap=args.fail_on_gap,
            )
            if args.dataset
            else validate_all(
                datasets=selected,
                start_date=args.start,
                as_of_date=args.as_of,
                fail_on_gap=args.fail_on_gap,
            )
        )
    elif args.data_command in {"plan", "sync"}:
        request = SyncRequest(
            source=args.source,
            template_id=args.template,
            datasets=_split(args.datasets) if args.datasets else None,
            symbols=_split(args.symbols) if args.symbols else None,
            start=args.start,
            end=args.end,
            force=args.force,
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
    if isinstance(result, list) and any(
        isinstance(item, dict) and item.get("status") in {"failed", "invalid"}
        for item in result
    ):
        return 1
    return 0


def _release(args: argparse.Namespace) -> int:
    import os
    from pathlib import Path

    from alphalab import release

    def emit(value: Any) -> None:
        print(json.dumps(value, indent=2, ensure_ascii=True, default=str))

    try:
        if args.release_command == "manifest":
            manifest = release.build_manifest(args.release_dir, commit_sha=args.commit, ref=args.ref)
            path = release.write_manifest(args.release_dir, manifest)
            emit({"status": "ok", "path": str(path), "manifest": manifest})
            return 0
        if args.release_command == "verify":
            manifest = release.read_manifest(args.release_dir)
            problems = release.verify_manifest(args.release_dir, manifest)
            current = None
            if args.current and (Path(args.current) / release.MANIFEST_NAME).is_file():
                current = release.read_manifest(args.current)
            notes: list[str] = []
            if not problems:
                notes = release.check_compatibility(
                    current, manifest, allow_layout_change=args.allow_layout_change
                )
            emit({"status": "failed" if problems else "ok", "problems": problems, "notes": notes})
            return 1 if problems else 0
        if args.release_command == "version":
            if args.url:
                result = release.query_instance_version(
                    args.url,
                    username=os.getenv("ALPHALAB_WEB_USERNAME"),
                    password=os.getenv("ALPHALAB_WEB_PASSWORD"),
                )
            else:
                result = release.current_versions(args.release_dir or release.REPO_ROOT)
            emit(result)
            return 0
        if args.release_command == "tasks":
            from alphalab.utils.paths import RUNTIME_DIR

            runtime = Path(args.runtime_dir) if args.runtime_dir else RUNTIME_DIR
            active = release.find_active_tasks(runtime)
            emit({
                "status": "busy" if active else "idle",
                "runtime_dir": str(runtime),
                "active": active,
                "host_lock_pid": release.host_lock_owner(runtime),
            })
            return 1 if active else 0
    except release.ReleaseError as exc:
        emit({"status": "error", "error_type": type(exc).__name__, "error": str(exc)})
        return 2
    return 2


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
