"""Build and validate a pinned dev3 release without touching the running service."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
SOURCE = HOME / "src/quant-framework-dev3"
STATE = HOME / ".local/state/alphalab-dev3"
RELEASES = HOME / "releases/alphalab-dev3"
STATE.mkdir(parents=True, exist_ok=True)
os.umask(0o077)
lock = (STATE / "build-local.lock").open("w")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
ref = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
start_step = int(sys.argv[2]) if len(sys.argv) > 2 else 1
reuse = sys.argv[3] if len(sys.argv) > 3 else None
frontend_only = len(sys.argv) > 4 and sys.argv[4] == "frontend"
app_only = len(sys.argv) > 4 and sys.argv[4] == "app"
commit = subprocess.check_output(["git", "rev-parse", ref + "^{commit}"], cwd=SOURCE, text=True).strip()
if subprocess.check_output(["git", "status", "--porcelain"], cwd=SOURCE, text=True).strip():
    raise SystemExit("Source checkout has uncommitted changes.")
subprocess.run(["git", "checkout", "--detach", commit], cwd=SOURCE, check=True)
release = RELEASES / commit
release.mkdir(parents=True, exist_ok=True)
existing_validation = release / ".mac-local-build-validated.json"
if existing_validation.is_file() and json.loads(existing_validation.read_text()).get("commit_sha") == commit:
    print("Using validated local build: " + commit, flush=True)
    raise SystemExit(0)
if not (release / "pyproject.toml").exists():
    with subprocess.Popen(["git", "archive", commit], cwd=SOURCE, stdout=subprocess.PIPE) as archive:
        with tarfile.open(fileobj=archive.stdout, mode="r|") as source_archive:
            source_archive.extractall(release, filter="data")
        if archive.wait():
            raise SystemExit("Git archive failed.")
if reuse:
    base = RELEASES / reuse
    if not (base / ".mac-local-build-validated.json").is_file():
        raise SystemExit("Reuse requires a validated base build.")
    changed = subprocess.check_output(["git", "diff", "--name-only", reuse, commit], cwd=SOURCE, text=True).splitlines()
    runtime_config = {"integrations/conexus/alphalab-research-agent/agents/AlphaLab-Research-Agent.agent.json", "integrations/conexus/model-providers.mjs", "integrations/conexus/web-search.mjs", "integrations/conexus/local-host.mjs"}
    frontend_paths = ("apps/desktop/src/", "apps/desktop/tests/")
    app_paths = ("apps/api/", "tests/", *frontend_paths) if app_only else ("apps/api/", "tests/")
    compatible = all(name.startswith(frontend_paths) for name in changed) if frontend_only else all(name.startswith(app_paths) or name in runtime_config for name in changed)
    if not changed or not compatible:
        raise SystemExit("Only Python API/test changes may reuse the identical frontend and Conexus programs.")
    for name in (["conexus", "conexus-source"] if frontend_only or app_only else ["web", "conexus", "conexus-source"]):
        target = release / "build" / name
        if not target.exists():
            shutil.copytree(base / "build" / name, target, symlinks=True)

log_path = STATE / ("build-local-" + commit + ".log")
status_path = STATE / "build-local-status.json"
started = time.monotonic()
python = release / ".venv/bin/python"
environment = dict(
    os.environ,
    PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:" + str(HOME / ".local/bin") + ":/usr/bin:/bin:/usr/sbin:/sbin",
    ELECTRON_SKIP_BINARY_DOWNLOAD="1",
    NODE_OPTIONS="--max-old-space-size=4096",
    ALPHALAB_RUNTIME_DIR=str(release / "build/test-runtime"),
    ALPHALAB_CACHE_DIR=str(release / "build/test-cache"),
    ALPHALAB_ENV_FILE=str(release / "build/test.env"),
    ALPHALAB_WEB_AUTH_ENABLED="0",
    PYTHONDONTWRITEBYTECODE="1",
    TMPDIR=str(Path(os.getenv("TMPDIR", "/tmp")).resolve()),
)
commands = [
    ([sys._base_executable, "-m", "venv", str(release / ".venv")], release),
    ([str(python), "-m", "pip", "install", "-e", ".[app,dev,rq]"], release),
    ([str(python), "-m", "pip", "check"], release),
    (["npm", "--prefix", "apps/desktop", "ci", "--no-fund"], release),
    ([str(python), "scripts/build_conexus_runtime.py"], release),
    (["npm", "--prefix", "apps/desktop", "run", "lint"], release),
    (["npm", "--prefix", "apps/desktop", "test"], release),
    (["npm", "--prefix", "apps/desktop", "run", "build"], release),
    ([str(python), "-m", "pytest", "tests", "-q", "-k", "not test_failed_release_restores_only_an_existing_previous_link"], release),
    ([str(python), "scripts/check_facade_imports.py"], release),
    ([str(python), str(SOURCE / "scripts/check_repository_hygiene.py")], SOURCE),
    ([str(python), "-m", "ruff", "check", "alphalab", "apps/api", "tests", "scripts"], release),
    ([str(python), "scripts/check_conexus_runtime.py"], release),
    (["git", "diff", "--check"], SOURCE),
]
if frontend_only:
    commands[8] = ([str(python), "-m", "pytest", "tests/contracts", "-q", "-k", "not test_failed_release_restores_only_an_existing_previous_link"], release)


def status(**data):
    status_path.write_text(json.dumps({"commit": commit, "log": str(log_path), **data}, indent=2))


try:
    with log_path.open("a") as output:
        for index, (command, cwd) in enumerate(commands, 1):
            if index < start_step:
                continue
            if reuse and index in ({5, 13} if frontend_only or app_only else {4, 5, 6, 7, 8, 13}):
                output.write(f"\nValidated unchanged frontend/Conexus check inherited from {reuse}: {' '.join(command)}\n")
                continue
            label = " ".join(command)
            status(state="running", step=index, total=len(commands), command=label)
            output.write("\n$ " + label + "\n")
            output.flush()
            print(f"[{index}/{len(commands)}] {label}", flush=True)
            result = subprocess.run(command, cwd=cwd, env=environment, stdout=output, stderr=subprocess.STDOUT)
            if result.returncode:
                status(state="failed", step=index, command=label, returncode=result.returncode)
                raise SystemExit(f"Build failed at step {index}; inspect {log_path}")
    subprocess.run(
        [str(python), "-m", "alphalab.cli", "release", "manifest", "--release-dir", str(release), "--commit", commit, "--ref", ref],
        cwd=release, env=environment, stdout=subprocess.DEVNULL, check=True,
    )
    manifest = json.loads((release / "release-manifest.json").read_text())
    metadata = {
        "commit_sha": commit,
        "app_version": manifest["app"]["version"],
        "core_revision": manifest["core"]["revision"],
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "build_seconds": round(time.monotonic() - started, 2),
        "platform": "darwin-arm64",
        "agent_mode": "local",
        "platform_exclusions": "Two Linux flock/systemctl deployment-script cases; Mac deployment uses launchd and Python fcntl.",
        "unchanged_frontend_and_core_from": None if frontend_only or app_only else reuse,
        "unchanged_core_from": reuse if frontend_only or app_only else None,
        "validation_scope": "frontend tests, lint, build, contracts and facade" if frontend_only else "full application with unchanged core" if app_only else "full release",
        "checks": [" ".join(cmd) for cmd, _ in commands[2:]],
    }
    (release / ".mac-local-build-validated.json").write_text(json.dumps(metadata, indent=2))
    status(state="passed", metadata=metadata)
    print("Build and checks passed: " + commit, flush=True)
except BaseException:
    if not status_path.exists():
        status(state="failed", command="initialization")
    raise
