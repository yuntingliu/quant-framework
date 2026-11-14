"""Run the agent benchmark (issue #22).

By default every task is isolated: the runner restores the snapshot made by
``scripts/benchmark_isolation.py snapshot`` and starts its own ``alphalab dev
serve`` with tracing, then stops it after the task. Do not run your own
workstation at the same time.

    python scripts/agent_benchmark.py --tasks T01 --label pilot
    python scripts/agent_benchmark.py --label baseline --repeat 3

Traces go to build/traces/<label> unless --trace-dir is given. --shared-server
uses an already running workstation instead (the pre-isolation behaviour).

Each run is sent through the workstation API exactly like the Agent rail sends it,
without being saved into the shared conversation history. Budgets from tasks.json
are enforced by cancelling the run; such runs are recorded as budget_exhausted,
never dropped. Results go to build/benchmarks/<label>/results.jsonl, and each
trace file gets a benchmark.task row so agent_trace_summary.mjs can group by task.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402
import benchmark_isolation as isolation  # noqa: E402

TASKS = ROOT / "benchmarks/agent/v1/tasks.json"
FIXTURE_STATE = fixtures.FIXTURE_STATE
TERMINAL = {"completed", "blocked", "failed", "cancelled"}
# Base projects survive between tasks; anything else the Agent created is removed.
BASE_PROJECTS = {"sdk-v1-default", "bench-momentum", "bench-quality"}
FUNDAMENTALS_DIRS = ("data/runtime/rq/financials", "data/runtime/canonical/fundamentals")
INTERACTION_ANSWER = (
    "No further input is available in this benchmark run. Continue only with what the "
    "request already authorizes, or stop and explain exactly what is missing."
)
WORKSPACE_CAPABILITIES = {
    "modes": ["project", "data", "factor", "strategy", "validation", "report"],
    "commandTypes": ["switch_mode", "open_widget", "open_result", "close_widget", "set_focus",
                     "set_link_symbol", "show_right_rail", "refresh_data", "save_layout"],
}


def _rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _new_trace(trace_dir: Path, before: set[Path]) -> Path | None:
    created = [path for path in trace_dir.glob("*.jsonl") if path not in before]
    return max(created, key=lambda path: path.stat().st_mtime) if created else None


def completed_runs(results_path: Path) -> set[tuple[str, int]]:
    """(task, repetition) pairs already recorded under this label."""

    if not results_path.exists():
        return set()
    return {(row["task_id"], row["repetition"]) for row in _rows(results_path) if "task_id" in row}


def set_aside_interrupted(trace_dir: Path) -> int:
    """Move traces of runs stopped by the user; only finished runs get a benchmark.task row."""

    moved = 0
    for path in trace_dir.glob("*.jsonl"):
        if not any(row.get("type") == "benchmark.task" for row in _rows(path)):
            target = trace_dir / "interrupted" / path.name
            target.parent.mkdir(exist_ok=True)
            path.replace(target)
            moved += 1
    return moved


def reset_fixtures(client: httpx.Client, task: dict) -> None:
    """Restore the state a task's fixture promises before it runs."""

    projects = {item["id"]: item for item in client.get("/api/agent/context").json()["projects"]}
    for project_id in projects.keys() - BASE_PROJECTS:
        client.delete(f"/api/strategy/projects/{project_id}", params={"confirm_delete": "true"})
        print(f"  reset: deleted agent-created project {project_id}")
    # Recreate a fixture project only when a previous task changed it; saved
    # backtests are stored independently of projects and survive the reset.
    for fixture, (project_id, _, _) in fixtures.PROJECTS.items():
        project = projects.get(project_id)
        if project is None or project.get("current_revision") != 1 or len(project.get("strategies") or []) != 1:
            fixtures.create_project(client, fixture, reset=True)
    if task["fixture"] == "F3":
        for relative in FUNDAMENTALS_DIRS:
            target = (ROOT / relative).resolve()
            if target.is_relative_to((ROOT / "data/runtime").resolve()) and target.exists():
                shutil.rmtree(target)
                print(f"  reset: removed {relative} so fundamentals are missing again")


def _fill(text: str) -> str:
    """Name fixture results by id; "the latest backtest" changes as tasks run."""

    if "{F2_BACKTEST}" not in text:
        return text
    if not FIXTURE_STATE.exists():
        raise SystemExit("Run scripts/benchmark_fixtures.py F2 first; it records the F2 backtest id.")
    return text.replace("{F2_BACKTEST}", json.loads(FIXTURE_STATE.read_text(encoding="utf-8"))["F2_BACKTEST"])


def run_input(task: dict, request_id: str) -> dict:
    project = "bench-quality" if task["fixture"] == "F3" else "bench-momentum"
    history = [{"role": item["role"], "content": _fill(item["content"])} for item in task.get("conversation", [])]
    context = {
        "requestId": request_id, "source": "benchmark", "activeMode": "project",
        "selectedProjectId": project, "selectedStrategyId": "main",
        "workspaceCapabilities": WORKSPACE_CAPABILITIES, "lastWorkspaceCommandReceipts": [],
        "conversationHistory": {"version": 1, "storage": "server-shared", "messages": history},
    }
    return {"request": json.dumps({"version": 1, "userRequest": _fill(task["request"]), "workspaceContext": context},
                                  ensure_ascii=False)}


def evaluate(task: dict, status: str, rows: list[dict]) -> dict[str, dict]:
    """Automatic checks read from the trace; graders and output checks stay manual."""

    checks = task.get("checks", {})
    tools = [row for row in rows if row.get("type") == "tool"]
    labels = Counter(row.get("label") for row in tools)
    results: dict[str, dict] = {}
    for label in checks.get("requires_tool", []):
        results[f"requires {label}"] = {"pass": labels[label] > 0, "count": labels[label]}
    for label in checks.get("forbids_tool", []):
        results[f"forbids {label}"] = {"pass": labels[label] == 0, "count": labels[label]}
    for flag in checks.get("forbids_confirm", []):
        used = [row.get("label") for row in tools if flag in (row.get("confirms") or [])]
        results[f"never sends {flag}"] = {"pass": not used, "used_by": used}
    for label, limit in checks.get("max_count", {}).items():
        results[f"at most {limit} x {label}"] = {"pass": labels[label] <= limit, "count": labels[label]}
    if "final_status" in checks:
        results["final status"] = {"pass": status in checks["final_status"], "status": status}
    return results


def run_task(client: httpx.Client, exposure_id: str, task: dict, repetition: int, trace_dir: Path,
             label: str) -> dict:
    budget = task["budget"]
    request_id = f"bench-{task['id']}-{repetition}-{uuid.uuid4().hex[:8]}"
    before = set(trace_dir.glob("*.jsonl"))
    started = time.monotonic()
    response = client.post("/api/conexus/runs", json={"exposureId": exposure_id, "input": run_input(task, request_id)})
    fixtures._fail(response, f"Starting {task['id']}")
    created = response.json()
    run, token = created["run"], created["accessToken"]
    auth = {"Authorization": f"Bearer {token}"}
    outcome, trace, answered = None, None, 0
    while run["status"] not in TERMINAL:
        time.sleep(2)
        run = client.get(f"/api/conexus/runs/{run['id']}", headers=auth).json()["run"]
        trace = trace or _new_trace(trace_dir, before)
        model_calls = sum(1 for row in _rows(trace) if row.get("type") == "model") if trace else 0
        elapsed = time.monotonic() - started
        if run["status"] in TERMINAL:
            break
        if outcome is not None:
            # Cancellation lands after the current model or tool call returns.
            continue
        if model_calls > budget["max_model_calls"] or elapsed > budget["max_seconds"]:
            outcome = "budget_exhausted"
            print(f"  budget exhausted after {elapsed:.0f}s and {model_calls} model calls; cancelling")
            run = client.post(f"/api/conexus/runs/{run['id']}/cancel", headers=auth).json()["run"]
            continue
        pending = run.get("pendingInteraction")
        if pending:
            answered += 1
            print(f"  agent asked: {str(pending.get('question'))[:160]}")
            if answered > 3:
                outcome = "stuck_on_questions"
                run = client.post(f"/api/conexus/runs/{run['id']}/cancel", headers=auth).json()["run"]
                continue
            client.post(f"/api/conexus/runs/{run['id']}/interactions/{pending['id']}/answer",
                        headers=auth, json={"answer": INTERACTION_ANSWER})
    elapsed = time.monotonic() - started
    time.sleep(1)  # let the recorder flush its final rows
    trace = trace or _new_trace(trace_dir, before)
    rows = _rows(trace) if trace else []
    if trace:
        with trace.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"type": "benchmark.task", "task_id": task["id"], "repetition": repetition,
                                     "label": label, "run_id": run["id"]}) + "\n")
    status = run["status"]
    checks = evaluate(task, status, rows)
    models = [row for row in rows if row.get("type") == "model"]
    return {
        "label": label, "task_id": task["id"], "tier": task["tier"], "type": task["type"], "repetition": repetition,
        "run_id": run["id"], "request_id": request_id, "trace_file": trace.name if trace else None,
        "status": status, "outcome": outcome or status, "elapsed_s": round(elapsed, 1),
        "model_calls": len(models), "tokens_in": sum(row.get("tokens_in") or 0 for row in models),
        "cached_tokens_in": sum(row.get("cached_tokens_in") or 0 for row in models),
        "tokens_out": sum(row.get("tokens_out") or 0 for row in models),
        "human_questions": answered, "checks": checks,
        "auto_pass": bool(checks) and all(item["pass"] for item in checks.values()) and outcome is None,
        "manual_checks": {key: task["checks"][key] for key in ("grader", "outputs") if key in task.get("checks", {})},
        "summary": str(run.get("summary") or "")[:4000], "error": run.get("error"),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", help="comma-separated task ids, default all")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--label", required=True, help="results folder name, e.g. pilot or baseline")
    parser.add_argument("--origin", default="http://127.0.0.1:8000")
    parser.add_argument("--trace-dir", default=None, help="defaults to $ALPHALAB_AGENT_TRACE_DIR")
    parser.add_argument("--no-reset", action="store_true", help="skip fixture restoration between tasks")
    parser.add_argument("--resume", action="store_true",
                        help="skip task/repetition pairs already recorded under this label")
    parser.add_argument("--shared-server", action="store_true",
                        help="use an already running alphalab dev serve and the in-place reset (pre-isolation behaviour)")
    args = parser.parse_args()

    # Path("") means the current folder, so test the option text, not the Path.
    given = args.trace_dir or os.environ.get("ALPHALAB_AGENT_TRACE_DIR")
    if args.shared_server and (not given or not Path(given).is_dir()):
        raise SystemExit("Pass --trace-dir (the folder alphalab dev serve was started with) or set ALPHALAB_AGENT_TRACE_DIR.")
    trace_dir = Path(given) if given else ROOT / "build/traces" / args.label
    if not args.shared_server:
        trace_dir = trace_dir.resolve()
        trace_dir.mkdir(parents=True, exist_ok=True)
    spec = json.loads(TASKS.read_text(encoding="utf-8"))
    wanted = {item.strip() for item in args.tasks.split(",")} if args.tasks else None
    tasks = [task for task in spec["tasks"] if wanted is None or task["id"] in wanted]
    if not tasks:
        raise SystemExit("No matching tasks.")
    output_dir = ROOT / "build/benchmarks" / args.label
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    done = completed_runs(results_path) if args.resume else set()
    if args.resume:
        moved = set_aside_interrupted(trace_dir) if not args.shared_server else 0
        print(f"Resuming: {len(done)} runs already recorded"
              + (f"; moved {moved} interrupted trace(s) to {trace_dir / 'interrupted'}" if moved else ""))
    elif results_path.exists():
        raise SystemExit(f"{results_path} already exists. Use --resume to continue it, or a new --label.")

    workstation = None
    if not args.shared_server:
        workstation = isolation.Workstation(
            {"ALPHALAB_AGENT_TRACE_DIR": str(trace_dir), "ALPHALAB_BENCHMARK_VERSION": spec["benchmark"]},
            output_dir / "serve.log")
    try:
        for repetition in range(1, args.repeat + 1):
            for task in tasks:
                if (task["id"], repetition) in done:
                    continue
                print(f"{task['id']} (rep {repetition}): {task['request'][:90]}")
                if workstation is not None:
                    # Fresh state and process: no data, reports, jobs or history from earlier runs.
                    isolation.restore_snapshot()
                    workstation.start()
                try:
                    with httpx.Client(base_url=args.origin.rstrip("/"), timeout=60.0, trust_env=False) as client:
                        manifest = client.get("/api/conexus/manifest")
                        fixtures._fail(manifest, "Reading the Agent manifest (is a model configured?)")
                        if workstation is None and not args.no_reset:
                            reset_fixtures(client, task)
                        result = run_task(client, manifest.json()["defaultExposureId"], task, repetition,
                                          trace_dir, args.label)
                finally:
                    if workstation is not None:
                        workstation.stop()
                result["isolated"] = workstation is not None
                with results_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(result, ensure_ascii=False) + "\n")
                failed = [name for name, item in result["checks"].items() if not item["pass"]]
                print(f"  -> {result['outcome']} in {result['elapsed_s']}s, {result['model_calls']} model calls, "
                      f"{result['tokens_in']} in / {result['tokens_out']} out tokens; "
                      f"auto checks {'PASS' if result['auto_pass'] else 'FAIL ' + str(failed)}")
    finally:
        if workstation is not None:
            workstation.stop()
    print(f"Results: {results_path}")
    print(f"Breakdown: node scripts/agent_trace_summary.mjs {trace_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
