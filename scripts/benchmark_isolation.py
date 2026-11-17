"""Isolated benchmark runs (issue #22): a clean starting state for every task.

Every task must start from the same state, or later runs see what earlier runs
left behind: data the Agent synchronized, its report Documents, background
jobs from a cancelled run, and the history of failed sync jobs. This module
keeps one snapshot of the fixture state and restores it before each task,
with a fresh ``alphalab dev serve`` so no job or connection survives.

Create the snapshot once, with no workstation running:

    python scripts/benchmark_isolation.py snapshot

scripts/agent_benchmark.py then restores it and starts its own workstation for
every task.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402

RUNTIME = ROOT / "data/runtime"
SNAPSHOT = ROOT / "build/benchmarks/snapshot"
ORIGIN = "http://127.0.0.1:8000"
# F0: the market data every task may rely on. Anything else the Agent syncs.
F0_DATASETS = ("rq.instruments", "rq.bars", "rq.paused", "rq.is_st", "rq.daily_factors")
F0_DIRS = ("rq/instruments", "rq/bars", "rq/market_state/paused", "rq/market_state/is_st",
           "rq/market_state/daily_factors")
DATABASES = ("app/alphalab.db", "app/dataio.db")
# A write-ahead log left by a killed workstation is replayed onto the restored
# database and corrupts it ("database disk image is malformed").
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
# Rebuilt from the snapshot on every restore; conexus/project holds the Agent
# workspace (report Documents, runs), conexus/secrets is kept.
VOLATILE = ("rq", "canonical", "cache", "conexus/project")
BASE_PROJECTS = ("sdk-v1-default", "bench-momentum", "bench-quality")


def _port_open(port: int = 8000) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _remove(path: Path) -> None:
    for attempt in range(20):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return
        except OSError:
            # Windows releases file handles shortly after a process exits.
            time.sleep(0.5)
    raise SystemExit(f"Could not remove {path}; is another AlphaLab process still running?")


class Workstation:
    """One ``alphalab dev serve`` process owned by the benchmark."""

    def __init__(self, env: dict[str, str], log_path: Path):
        self.env = {**os.environ, **env}
        self.log_path = log_path
        self.process: subprocess.Popen | None = None

    def start(self, timeout: float = 240.0) -> None:
        if _port_open():
            raise SystemExit("Port 8000 is in use. Stop your own 'alphalab dev serve'; the benchmark starts its own.")
        executable = Path(sys.executable).with_name("alphalab.exe" if os.name == "nt" else "alphalab")
        command = [str(executable), "dev", "serve"] if executable.exists() else [sys.executable, "-m", "alphalab.cli", "dev", "serve"]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("a", encoding="utf-8")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.process = subprocess.Popen(command, cwd=ROOT, env=self.env, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=flags)
        deadline = time.monotonic() + timeout
        with httpx.Client(base_url=ORIGIN, timeout=5.0, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise SystemExit(f"alphalab dev serve exited during startup; see {self.log_path}")
                try:
                    if client.get("/api/conexus/manifest").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(1)
        self.stop()
        raise SystemExit(f"alphalab dev serve did not become ready; see {self.log_path}")

    def stop(self, timeout: float = 60.0) -> None:
        if self.process is None or self.process.poll() is not None:
            self.process = None
            return
        # Ctrl+Break lets uvicorn shut down and close the Conexus host's pipe.
        self.process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.process.pid)], capture_output=True)
            else:
                self.process.kill()
            self.process.wait(timeout=30)
        deadline = time.monotonic() + 30
        while _port_open() and time.monotonic() < deadline:
            time.sleep(0.5)
        self.process = None


def clean_databases(alphalab_db: Path, dataio_db: Path, keep_backtest: str) -> None:
    """Reduce copied databases to the fixture state."""

    with sqlite3.connect(dataio_db) as conn:
        marks = ",".join("?" * len(F0_DATASETS))
        for table, column in (("partitions", "dataset_id"), ("quality_runs", "dataset_id"), ("datasets", "id")):
            conn.execute(f"DELETE FROM {table} WHERE {column} NOT IN ({marks})", F0_DATASETS)
        conn.execute("DELETE FROM sync_checkpoints")
        conn.execute("DELETE FROM sync_jobs")
    with sqlite3.connect(alphalab_db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        marks = ",".join("?" * len(BASE_PROJECTS))
        conn.execute(f"DELETE FROM strategy_projects WHERE id NOT IN ({marks})", BASE_PROJECTS)
        conn.execute("DELETE FROM backtests WHERE id <> ?", (keep_backtest,))
        conn.execute("DELETE FROM backtest_jobs WHERE COALESCE(result_id, '') <> ?", (keep_backtest,))
    for path in (alphalab_db, dataio_db):
        with sqlite3.connect(path) as conn:
            conn.execute("VACUUM")


def make_snapshot(env: dict[str, str]) -> None:
    state = json.loads(fixtures.FIXTURE_STATE.read_text(encoding="utf-8"))
    workstation = Workstation(env, ROOT / "build/benchmarks/snapshot-serve.log")
    workstation.start()
    try:
        with httpx.Client(base_url=ORIGIN, timeout=120.0, trust_env=False) as client:
            for project in client.get("/api/agent/context").json()["projects"]:
                if project["id"] not in BASE_PROJECTS:
                    client.delete(f"/api/strategy/projects/{project['id']}", params={"confirm_delete": "true"})
            for fixture in fixtures.PROJECTS:
                fixtures.create_project(client, fixture, reset=True)
    finally:
        workstation.stop()
    _remove(SNAPSHOT)
    for relative in F0_DIRS:
        shutil.copytree(RUNTIME / relative, SNAPSHOT / relative)
    for relative in DATABASES:
        (SNAPSHOT / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RUNTIME / relative, SNAPSHOT / relative)
    clean_databases(SNAPSHOT / DATABASES[0], SNAPSHOT / DATABASES[1], state["F2_BACKTEST"])
    (SNAPSHOT / "snapshot.json").write_text(json.dumps({
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "F2_BACKTEST": state["F2_BACKTEST"],
        "datasets": F0_DATASETS, "projects": BASE_PROJECTS,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Snapshot written to {SNAPSHOT} (F0 data, fixture projects, F2 backtest {state['F2_BACKTEST']}).")


def restore_snapshot() -> None:
    """Replace the runtime state with the snapshot. The workstation must be stopped."""

    if not (SNAPSHOT / "snapshot.json").exists():
        raise SystemExit("No benchmark snapshot. Run: python scripts/benchmark_isolation.py snapshot")
    if _port_open():
        raise SystemExit("Stop alphalab dev serve before restoring the benchmark snapshot.")
    for relative in VOLATILE:
        _remove(RUNTIME / relative)
    for relative in DATABASES:
        _remove(RUNTIME / relative)
        for suffix in SQLITE_SIDECARS:
            _remove(RUNTIME / f"{relative}{suffix}")
    for relative in F0_DIRS:
        shutil.copytree(SNAPSHOT / relative, RUNTIME / relative)
    for relative in DATABASES:
        shutil.copy2(SNAPSHOT / relative, RUNTIME / relative)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["snapshot", "restore"])
    args = parser.parse_args()
    if args.action == "snapshot":
        make_snapshot({})
    else:
        restore_snapshot()
        print("Runtime state restored from the benchmark snapshot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
