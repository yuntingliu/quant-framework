"""Index the benchmark task set into benchmark/manifest.json.

The manifest is the published, machine-readable inventory of the task set: one
entry per task, the fixtures each task restores, and a rolled-up digest of
every frozen input. Regenerate it whenever a task or fixture changes.

Reference answers are deliberately absent. The manifest is safe to read in the
same process that drives an agent; benchmark/reference is not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = REPO_ROOT / "benchmark"
SCHEMA_VERSION = 1
TASK_SET_VERSION = "0.1.0"

CATEGORIES = (
    "data_diagnostics",
    "factor_implementation",
    "strategy_backtest",
    "risk_execution",
    "result_audit",
    "failure_recovery",
)


def _aggregate_sha256(digests: list[str]) -> str:
    """Hash the sorted member digests, matching alphalab.provenance._aggregate."""

    rolling = hashlib.sha256()
    for digest in sorted(digests):
        rolling.update(digest.encode("ascii"))
    return rolling.hexdigest()


def _fixture_index() -> dict:
    fixtures = {}
    for path in sorted(BENCHMARK.glob("data/*/fixture.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        files = fixture["files"]
        fixtures[fixture["fixture"]] = {
            "profile": fixture["profile"],
            "seed": fixture["seed"],
            "calendar": fixture["calendar"],
            "universe_size": len(fixture["universe"]),
            "rows": fixture["rows"],
            "planted_defects": [
                {"kind": item["kind"], "dataset": item["dataset"]}
                for item in fixture["planted_defects"]
            ],
            "file_count": len(files),
            "bytes": sum(item["bytes"] for item in files.values()),
            "aggregate_sha256": _aggregate_sha256([item["sha256"] for item in files.values()]),
        }
    return fixtures


def _task_index() -> list[dict]:
    tasks = []
    for path in sorted(BENCHMARK.glob("tasks/*/*/task.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        tasks.append(
            {
                "task_id": task["task_id"],
                "split": task["split"],
                "category": task["category"],
                "difficulty": task["difficulty"],
                "title": task["title"],
                "directory": path.parent.relative_to(REPO_ROOT).as_posix(),
                "as_of_date": task["request"]["as_of_date"],
                "profile": task["environment"]["profile"],
                "fixture": task["environment"]["fixture"],
                "expected_outcome": task["expected_outcome"],
                "negative_case": (
                    task["negative_case"]["kind"] if task.get("negative_case") else None
                ),
                "budget": task["budget"],
                "blocking_conditions": sum(
                    1 for item in task["pass_conditions"] if item["weight"] == "blocking"
                ),
                "scored_conditions": sum(
                    1 for item in task["pass_conditions"] if item["weight"] == "scored"
                ),
            }
        )
    return tasks


def build() -> dict:
    tasks = _task_index()
    fixtures = _fixture_index()
    by = lambda key: {  # noqa: E731
        value: sum(1 for task in tasks if task[key] == value)
        for value in sorted({task[key] for task in tasks})
    }
    return {
        "task_set": "alphalab-financial-agent-benchmark",
        "version": TASK_SET_VERSION,
        "schema_version": SCHEMA_VERSION,
        "task_schema": "benchmark/schema/task.schema.json",
        "reference_root": "benchmark/reference",
        "reference_warning": (
            "benchmark/reference holds expected answers. Never mount it into an agent "
            "workspace and never load it in the process that drives the agent under test."
        ),
        "data_sources": {
            "benchmark/data": {
                "kind": "synthetic",
                "generator": "scripts/build_benchmark_data.py",
                "license": "same terms as this repository",
                "contains_vendor_data": False,
                "notes": (
                    "Seeded pseudo-random price paths and statements. No economic meaning; "
                    "used only to plant and detect defects deterministically."
                ),
            },
            "data/": {
                "kind": "tracked development bundle",
                "generator": "scripts/build_example_data.py",
                "manifest": "data/manifest.json",
                "contains_vendor_data": True,
                "notes": (
                    "300 A-share symbols over five years, normalized from licensed QMT and RQ "
                    "caches. Already frozen and digested by data/manifest.json. A development "
                    "demonstration sample, not an unbiased investable benchmark."
                ),
            },
        },
        "counts": {
            "tasks": len(tasks),
            "by_split": by("split"),
            "by_category": by("category"),
            "by_difficulty": by("difficulty"),
            "by_expected_outcome": by("expected_outcome"),
            "negative_cases": {
                value: sum(1 for task in tasks if task["negative_case"] == value)
                for value in sorted(
                    {task["negative_case"] for task in tasks if task["negative_case"]}
                )
            },
        },
        "categories": list(CATEGORIES),
        "fixtures": fixtures,
        "tasks": tasks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero if benchmark/manifest.json is stale instead of rewriting it.",
    )
    args = parser.parse_args()
    manifest = build()
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    path = BENCHMARK / "manifest.json"
    if args.check:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != serialized:
            raise SystemExit(
                "benchmark/manifest.json is stale; run python -m scripts.build_benchmark_manifest"
            )
        print(json.dumps({"status": "current", "tasks": manifest["counts"]["tasks"]}, indent=2))
        return
    path.write_text(serialized, encoding="utf-8")
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
