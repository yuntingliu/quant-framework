from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from alphalab import __version__
from alphalab.cli import main
from alphalab.release import (
    MANIFEST_NAME,
    ActiveTaskError,
    CompatibilityError,
    ReleaseError,
    build_manifest,
    check_compatibility,
    deployment_matches_manifest,
    ensure_no_active_tasks,
    find_active_tasks,
    host_lock_owner,
    query_instance_version,
    read_manifest,
    verify_manifest,
    write_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
RELEASE_FILES = (
    "pyproject.toml",
    "alphalab/__init__.py",
    "integrations/conexus/runtime.lock.json",
    "vendor/conexus/EXPORT.json",
    "apps/desktop/package.json",
    "apps/desktop/package-lock.json",
)
COMMIT = "c" * 40


@pytest.fixture
def release(tmp_path: Path) -> Path:
    """A release directory holding copies of the real pinned inputs only."""
    root = tmp_path / "release"
    for name in RELEASE_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    upstream = root / "build/conexus/UPSTREAM.json"
    upstream.parent.mkdir(parents=True)
    shutil.copy2(root / "integrations/conexus/runtime.lock.json", upstream)
    return root


def _lock(root: Path) -> dict:
    return json.loads((root / "integrations/conexus/runtime.lock.json").read_text(encoding="utf-8"))


def _database(path: Path, sql: str, rows: list[tuple] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        table = sql.split()[2]
        connection.execute(sql)
        if rows:
            marks = ",".join("?" for _ in rows[0])
            connection.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
        connection.commit()
    finally:
        connection.close()


def test_manifest_records_versions_core_and_pinned_inputs(release):
    manifest = build_manifest(release, commit_sha=COMMIT, ref="origin/main")
    assert manifest["schema"] == "alphalab.release-manifest.v1"
    assert manifest["commit_sha"] == COMMIT
    assert manifest["ref"] == "origin/main"
    assert manifest["app"]["version"] == __version__
    assert manifest["core"]["revision"] == _lock(release)["revision"]
    assert manifest["core"]["layout_version"] == _lock(release)["layoutVersion"]
    assert manifest["core"]["runtime_built"] is True
    assert set(manifest["inputs"]) == set(RELEASE_FILES) - {"alphalab/__init__.py"}

    write_manifest(release, manifest)
    assert read_manifest(release) == manifest
    assert verify_manifest(release, manifest) == []


def test_unreadable_or_foreign_manifest_is_rejected(release):
    with pytest.raises(ReleaseError, match="unreadable"):
        read_manifest(release)
    (release / MANIFEST_NAME).write_text('{"schema": "other"}', encoding="utf-8")
    with pytest.raises(ReleaseError, match="unsupported schema"):
        read_manifest(release)


def test_verify_detects_changed_inputs_and_mismatched_runtime(release):
    manifest = build_manifest(release, commit_sha=COMMIT)
    lock = _lock(release)
    lock["revision"] = "d" * 40
    (release / "integrations/conexus/runtime.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    problems = verify_manifest(release, manifest)
    assert "pinned input changed: integrations/conexus/runtime.lock.json" in problems
    assert "core lock revision differs from the manifest" in problems
    assert "built Conexus runtime does not match the pinned core lock" in problems

    (release / "build/conexus/UPSTREAM.json").unlink()
    assert "built Conexus runtime is missing: build/conexus/UPSTREAM.json" in verify_manifest(
        release, manifest
    )


def test_verify_detects_inconsistent_application_versions(release):
    manifest = build_manifest(release, commit_sha=COMMIT)
    package = release / "alphalab/__init__.py"
    package.write_text(
        package.read_text(encoding="utf-8").replace(__version__, "9.9.9", 1), encoding="utf-8"
    )
    assert any(p.startswith("package version 9.9.9") for p in verify_manifest(release, manifest))


def test_compatibility_blocks_layout_changes_unless_approved(release):
    current = build_manifest(release, commit_sha="a" * 40)
    candidate = json.loads(json.dumps(current))
    candidate["commit_sha"] = COMMIT
    candidate["core"]["revision"] = "e" * 40
    assert check_compatibility(current, candidate) == [
        f"Conexus core revision changes to {'e' * 40}"
    ]
    candidate["core"]["layout_version"] = 99
    with pytest.raises(CompatibilityError, match="layout"):
        check_compatibility(current, candidate)
    notes = check_compatibility(current, candidate, allow_layout_change=True)
    assert any("(approved)" in note for note in notes)
    assert "first managed activation" in check_compatibility(None, candidate)[0]


def test_compatibility_rejects_an_unsupported_python(release):
    candidate = build_manifest(release, commit_sha=COMMIT)
    with pytest.raises(CompatibilityError, match="requires Python"):
        check_compatibility(None, candidate, python_version=(3, 8))


def test_deployment_must_match_commit_version_and_core(release):
    manifest = build_manifest(release, commit_sha=COMMIT)
    healthy = {
        "status": "ok",
        "commit_sha": COMMIT,
        "version": __version__,
        "core_revision": _lock(release)["revision"],
    }
    assert deployment_matches_manifest(healthy, manifest) == []
    stale = dict(healthy, commit_sha="b" * 40, core_revision="f" * 40)
    problems = deployment_matches_manifest(stale, manifest)
    assert [p.split()[0] for p in problems] == ["commit_sha", "core_revision"]


def test_idle_runtime_has_no_active_tasks(tmp_path):
    runtime = tmp_path / "runtime"
    _database(
        runtime / "app/alphalab.db",
        "CREATE TABLE backtest_jobs (id TEXT, status TEXT)",
        [("1", "succeeded"), ("2", "interrupted")],
    )
    assert find_active_tasks(runtime) == []
    ensure_no_active_tasks(runtime)
    assert find_active_tasks(tmp_path / "missing") == []


@pytest.mark.parametrize(
    ("database", "sql", "rows", "source"),
    [
        ("alphalab.db", "CREATE TABLE backtest_jobs (id TEXT, status TEXT)",
         [("1", "running"), ("2", "failed")], "alphalab.db:backtest_jobs"),
        ("alphalab.db", "CREATE TABLE research_runs (id TEXT, status TEXT)",
         [("1", "queued")], "alphalab.db:research_runs"),
        ("dataio.db", "CREATE TABLE sync_jobs (id TEXT, status TEXT)",
         [("1", "pending")], "dataio.db:sync_jobs"),
        ("agent-conversations.sqlite3",
         "CREATE TABLE agent_conversation_runs (run_id TEXT, snapshot_json TEXT, delivered INTEGER)",
         [("1", '{"status": "running"}', 0), ("2", '{"status": "completed"}', 0),
          ("3", '{"status": "running"}', 1)],
         "agent-conversations.sqlite3:agent_conversation_runs"),
    ],
)
def test_each_active_task_source_blocks_a_release_switch(tmp_path, database, sql, rows, source):
    runtime = tmp_path / "runtime"
    _database(runtime / "app" / database, sql, rows)
    assert find_active_tasks(runtime) == [{"source": source, "count": 1}]
    with pytest.raises(ActiveTaskError, match=source):
        ensure_no_active_tasks(runtime)


def test_local_agent_runs_and_host_lock_are_reported(tmp_path):
    runtime = tmp_path / "runtime"
    state = runtime / "conexus/project/state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"runs": [{"status": "running"}, {"status": "completed"}]}))
    (state.parent / "host.lock").write_text("4242\n")
    assert find_active_tasks(runtime) == [{"source": "conexus/project/state.json:runs", "count": 1}]
    assert host_lock_owner(runtime) == 4242

    state.write_text("not json")
    with pytest.raises(ActiveTaskError, match="cannot read"):
        find_active_tasks(runtime)


def test_instance_version_query_is_loopback_only():
    with pytest.raises(ReleaseError, match="loopback"):
        query_instance_version("http://example.com/api/health")
    with pytest.raises(ReleaseError, match="loopback"):
        query_instance_version("file:///etc/passwd")


def test_instance_version_query_reads_health_with_basic_auth():
    seen: dict[str, str | None] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            seen["path"] = self.path
            seen["auth"] = self.headers.get("Authorization")
            body = json.dumps({
                "status": "ok", "version": __version__, "commit_sha": COMMIT,
                "core_revision": "r", "store": {"backtests": 3},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = query_instance_version(
            f"http://127.0.0.1:{server.server_port}", username="dev", password="pw"
        )
    finally:
        server.shutdown()
        server.server_close()
    assert seen["path"] == "/api/health"
    assert seen["auth"] == "Basic ZGV2OnB3"
    assert result["commit_sha"] == COMMIT
    assert "store" not in result


def test_release_cli_writes_verifies_and_guards(release, tmp_path, capsys):
    assert main(["release", "manifest", "--release-dir", str(release), "--commit", COMMIT]) == 0
    assert (release / MANIFEST_NAME).is_file()
    capsys.readouterr()

    assert main(["release", "verify", "--release-dir", str(release)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"

    (release / "pyproject.toml").write_text("changed", encoding="utf-8")
    assert main(["release", "verify", "--release-dir", str(release)]) == 1
    assert "pinned input changed: pyproject.toml" in json.loads(capsys.readouterr().out)["problems"]

    assert main(["release", "verify", "--release-dir", str(tmp_path / "missing")]) == 2
    capsys.readouterr()

    runtime = tmp_path / "runtime"
    assert main(["release", "tasks", "--runtime-dir", str(runtime)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "idle"
    _database(runtime / "app/dataio.db", "CREATE TABLE sync_jobs (id TEXT, status TEXT)", [("1", "running")])
    assert main(["release", "tasks", "--runtime-dir", str(runtime)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "busy"


def test_release_cli_refuses_an_incompatible_candidate(release, tmp_path, capsys):
    current = tmp_path / "current"
    shutil.copytree(release, current)
    assert main(["release", "manifest", "--release-dir", str(current), "--commit", "a" * 40]) == 0
    lock = _lock(release)
    lock["layoutVersion"] = 99
    for name in ("integrations/conexus/runtime.lock.json", "build/conexus/UPSTREAM.json"):
        (release / name).write_text(json.dumps(lock), encoding="utf-8")
    assert main(["release", "manifest", "--release-dir", str(release), "--commit", COMMIT]) == 0
    capsys.readouterr()

    args = ["release", "verify", "--release-dir", str(release), "--current", str(current)]
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["error_type"] == "CompatibilityError"
    assert main([*args, "--allow-layout-change"]) == 0


def test_application_version_is_declared_consistently():
    from apps.api.main import app

    declared = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in declared
    assert app.version == __version__


def test_health_reports_the_pinned_core_revision():
    from fastapi.testclient import TestClient

    from apps.api.main import app

    body = TestClient(app).get("/api/health").json()
    assert body["core_revision"] == _lock(ROOT)["revision"]
    assert "manifest" in body
