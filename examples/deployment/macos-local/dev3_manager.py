"""Private Mac mini operations for the authenticated dev3 local workstation."""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CONFIG = HOME / ".config/alphalab-dev3"
STATE = HOME / ".local/state/alphalab-dev3"
RUNTIME = HOME / ".local/share/alphalab-dev3/runtime"
CURRENT = HOME / "apps/alphalab-dev3"
RELEASES = HOME / "releases/alphalab-dev3"
DOMAIN = "gui/" + str(os.getuid())
WEB = "net.acetoken.alphalab-dev3"
PRIVATE = WEB + "-agent-api"
PUBLIC = WEB + "-tunnel"
OLD_SSH = WEB + "-agent-tunnel"
DEPLOYMENT_BRANCH = "deploy/dev3-local-20261019"
PLISTS = HOME / "Library/LaunchAgents"
os.umask(0o077)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    temp.chmod(0o600)
    os.replace(temp, path)


def values():
    result = {}
    for line in (CONFIG / "alphalab.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = json.loads(value)
    return result


def launch(*args, check=True):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def running(label):
    return launch("print", DOMAIN + "/" + label, check=False).returncode == 0


def stop(label):
    if running(label):
        launch("bootout", DOMAIN + "/" + label)
    deadline = time.monotonic() + 40
    while running(label) and time.monotonic() < deadline:
        time.sleep(0.2)
    if running(label):
        raise RuntimeError("Service did not stop: " + label)


def start(label):
    launch("enable", DOMAIN + "/" + label)
    if not running(label):
        launch("bootstrap", DOMAIN, str(PLISTS / (label + ".plist")))


def api(path, data=None, method=None, timeout=20):
    conf = values()
    credential = base64.b64encode((conf["ALPHALAB_WEB_USERNAME"] + ":" + conf["ALPHALAB_WEB_PASSWORD"]).encode()).decode()
    request = urllib.request.Request(
        "http://127.0.0.1:8300" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Basic " + credential, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def health(expected=None):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            result = api("/api/health", timeout=2)
            if result.get("status") == "ok" and (expected is None or result.get("commit_sha") == expected):
                return result
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Authenticated service health timed out.")


def release_cli(release, *args):
    """Run the release's own ``alphalab release`` command and return its JSON output."""
    result = subprocess.run(
        [str(release / ".venv/bin/python"), "-m", "alphalab.cli", "release", *args],
        cwd=release, capture_output=True, text=True,
    )
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        payload = {"status": "error", "error": result.stderr.strip()}
    return result.returncode, payload


def deployment_mismatch(release, health_payload):
    # Mirrors alphalab.release.deployment_matches_manifest; this script runs
    # outside the release virtual environment.
    manifest = json.loads((release / "release-manifest.json").read_text())
    expected = {
        "commit_sha": manifest["commit_sha"],
        "version": manifest["app"]["version"],
        "core_revision": manifest["core"]["revision"],
    }
    return [f"{key} is {health_payload.get(key)!r}, expected {value!r}" for key, value in expected.items() if health_payload.get(key) != value]


def check_jobs(release=None):
    """Refuse to interrupt research work; the release's own guard covers every task source."""
    release = (release or CURRENT).resolve()
    if not (release / "alphalab/release.py").is_file():
        return legacy_check_jobs()
    code, result = release_cli(release, "tasks", "--runtime-dir", str(RUNTIME))
    if code:
        raise RuntimeError("Active tasks block the release change: " + json.dumps(result.get("active") or result))


def legacy_check_jobs():
    # Releases built before alphalab.release existed cannot run the shared guard.
    for name, table in [("alphalab.db", "backtest_jobs"), ("dataio.db", "sync_jobs")]:
        database = RUNTIME / "app" / name
        if not database.exists():
            continue
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            columns = [row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')]
            if "status" in columns:
                count = connection.execute(
                    'SELECT COUNT(*) FROM "' + table + '" WHERE status IN (\'queued\',\'running\',\'pending\')'
                ).fetchone()[0]
                if count:
                    raise RuntimeError(f"{count} active {table}; wait before changing the release.")
    local = RUNTIME / "conexus/project/state.json"
    if local.exists():
        runs = json.loads(local.read_text()).get("runs", [])
        if any(run.get("status") in {"queued", "running"} for run in runs):
            raise RuntimeError("An Agent run is active; wait before changing the release.")


def check_release(release):
    args = ["verify", "--release-dir", str(release)]
    if CURRENT.exists() and CURRENT.resolve() != release.resolve():
        args += ["--current", str(CURRENT.resolve())]
    code, result = release_cli(release, *args)
    if code:
        raise RuntimeError("Release manifest or compatibility check failed: " + json.dumps(result))
    return result


def snapshot(tag="manual", offline=False):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = STATE / "backups" / (stamp + "-" + tag)
    target.mkdir(parents=True, mode=0o700)
    shutil.copytree(CONFIG, target / "config", ignore=shutil.ignore_patterns("__pycache__"))
    (target / "plists").mkdir()
    for plist in PLISTS.glob("net.acetoken.alphalab-dev3*.plist"):
        shutil.copy2(plist, target / "plists" / plist.name)
    shutil.copytree(RUNTIME, target / "runtime", ignore=shutil.ignore_patterns("*-wal", "*-shm", "host.lock"))
    # Online snapshots use SQLite's own backup rather than a live .db copy.
    for source in (RUNTIME / "app").iterdir():
        if source.is_file() and source.suffix in {".db", ".sqlite3"}:
            destination = target / "runtime/app" / source.name
            destination.unlink(missing_ok=True)
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as origin, sqlite3.connect(destination) as saved:
                origin.backup(saved)
                if saved.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup database verification failed: " + source.name)
    write_json(target / "snapshot.json", {
        "created_at": datetime.now(timezone.utc).isoformat(), "tag": tag, "offline": offline,
        "release": str(CURRENT.resolve()), "old_ssh_registered": running(OLD_SSH),
    })
    print("Backup verified: " + str(target), flush=True)
    return target


def write_runner():
    # The native Host owns Agent execution. The existing loopback forwarder
    # authenticates tool requests to the same protected Python backend.
    runner = '''import json,os,sys,threading
from pathlib import Path
config=Path.home()/'.config/alphalab-dev3/alphalab.env'
for line in config.read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key,value=line.split('=',1);os.environ[key.strip()]=json.loads(value)
os.environ['PATH']=str(Path(sys.executable).parent)+':/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
os.environ['ALPHALAB_AGENT_MODE']='local'
from alphalab.local_agent import local_agent
import uvicorn
root=Path.home()/'apps/alphalab-dev3'
with local_agent(root.resolve(),api_port=8301,agent_port=8387) as child:
    server=uvicorn.Server(uvicorn.Config('apps.api.main:app',host='127.0.0.1',port=8300,workers=1))
    done=threading.Event()
    def monitor():
        while not done.wait(.5):
            if child.poll() is not None:server.should_exit=True;return
    watcher=threading.Thread(target=monitor,daemon=True);watcher.start()
    try:
        server.run()
        if child.poll() is not None:raise RuntimeError('Local Conexus stopped; inspect runtime/conexus/host.log')
    finally:
        done.set();watcher.join(timeout=2)
'''
    path = CONFIG / "run_server.py"
    path.write_text(runner)
    path.chmod(0o600)
    plist = {
        "Label": WEB, "ProgramArguments": [str(CURRENT / ".venv/bin/python"), str(path)],
        "WorkingDirectory": str(CURRENT), "RunAtLoad": True, "KeepAlive": True,
        "ThrottleInterval": 5, "ExitTimeOut": 40,
        "StandardOutPath": str(STATE / "server.log"), "StandardErrorPath": str(STATE / "server.log"),
        "EnvironmentVariables": {"PYTHONDONTWRITEBYTECODE": "1"},
    }
    path = PLISTS / (WEB + ".plist")
    path.write_bytes(plistlib.dumps(plist))
    path.chmod(0o600)


def switch(release):
    release = release.resolve(strict=True)
    if release.parent != RELEASES.resolve():
        raise ValueError("Release path is outside the dev3 release directory.")
    next_link = CURRENT.with_name(CURRENT.name + ".next")
    next_link.unlink(missing_ok=True)
    next_link.symlink_to(release, target_is_directory=True)
    os.replace(next_link, CURRENT)


def restore(backup):
    backup = backup.resolve(strict=True)
    if not backup.is_relative_to((STATE / "backups").resolve()):
        raise ValueError("Rollback requires a verified dev3 snapshot.")
    metadata = json.loads((backup / "snapshot.json").read_text())
    stop(WEB)
    stop(PRIVATE)
    stop(OLD_SSH)
    if RUNTIME.resolve() != HOME / ".local/share/alphalab-dev3/runtime":
        raise ValueError("Unexpected runtime path.")
    preserved = STATE / ("runtime-before-rollback-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    RUNTIME.rename(preserved)
    shutil.copytree(backup / "runtime", RUNTIME)
    shutil.copytree(backup / "config", CONFIG, dirs_exist_ok=True)
    for plist in (backup / "plists").iterdir():
        shutil.copy2(plist, PLISTS / plist.name)
    switch(Path(metadata["release"]))
    start(PRIVATE)
    start(WEB)
    if metadata.get("old_ssh_registered"):
        start(OLD_SSH)
    health()
    print("Previous release and matching data restored. Newer state retained: " + str(preserved))


def deploy(ref):
    source = HOME / "src/quant-framework-dev3"
    commit = subprocess.check_output(["git", "rev-parse", ref + "^{commit}"], cwd=source, text=True).strip()
    release = RELEASES / commit
    validation = release / ".mac-local-build-validated.json"
    if not validation.is_file() or json.loads(validation.read_text())["commit_sha"] != commit:
        raise RuntimeError("Build and validation must finish before activation.")
    if not (release / "release-manifest.json").is_file():
        release_cli(release, "manifest", "--release-dir", str(release), "--commit", commit, "--ref", ref)
    check_release(release)
    check_jobs(release)
    stop(WEB)
    stop(PRIVATE)
    backup = None
    try:
        backup = snapshot("before-local-" + commit[:8], offline=True)
        stop(OLD_SSH)
        launch("disable", DOMAIN + "/" + OLD_SSH)
        conf = values()
        for key in ["CONEXUS_WEB_ORIGIN", "CONEXUS_PUBLICATION_SLUG", "CONEXUS_PUBLICATION_WORKSPACE_TOKEN"]:
            conf.pop(key, None)
        conf["ALPHALAB_AGENT_MODE"] = "local"
        conf["ALPHALAB_INSTANCE_ID"] = "dev3"
        conf["ALPHALAB_CACHE_DIR"] = str(RUNTIME / "cache")
        temp = CONFIG / "alphalab.env.tmp"
        temp.write_text("".join(key + "=" + json.dumps(str(value)) + "\n" for key, value in sorted(conf.items())))
        temp.chmod(0o600)
        os.replace(temp, CONFIG / "alphalab.env")
        pending = CONFIG / "model-profile.pending.json"
        if pending.exists():
            profile = json.loads(pending.read_text())
            profile.pop("activate", None)
            write_json(RUNTIME / "conexus/secrets/model-providers.json", {
                "version": 1, "active_id": profile["id"], "profiles": [profile],
            })
        switch(release)
        write_runner()
        environment = dict(os.environ, PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin", ALPHALAB_RUNTIME_DIR=str(RUNTIME))
        reports = CONFIG / "historical-reports.json"
        if reports.exists() and not (RUNTIME / "conexus/report-migration.json").exists():
            subprocess.run(["/opt/homebrew/opt/node@22/bin/node", str(CONFIG / "import_local_reports.mjs"), str(release), str(RUNTIME), str(reports)], env=environment, check=True)
        write_json(release / ".alphalab-release.json", {"commit_sha": commit, "deployed_at": datetime.now(timezone.utc).isoformat(), "agent_mode": "local"})
        start(PRIVATE)
        start(WEB)
        start(PUBLIC)
        result = health(commit)
        mismatch = deployment_mismatch(release, result)
        if mismatch:
            raise RuntimeError("Running instance does not match the release manifest: " + "; ".join(mismatch))
        agent = api("/api/conexus/status")
        if not agent.get("available") or agent.get("mode") != "local_harness":
            raise RuntimeError("Local Conexus readiness failed: " + json.dumps(agent))
        write_json(STATE / "local-deployment.json", {"commit_sha": commit, "activated_at": datetime.now(timezone.utc).isoformat(), "rollback_snapshot": str(backup), "agent_mode": "local", "tool_origin": "http://127.0.0.1:8301", "agent_origin": "http://127.0.0.1:8387"})
        pending.unlink(missing_ok=True)
        print(json.dumps({"deployed": True, "health": result, "agent": agent}, ensure_ascii=False))
    except BaseException:
        if backup:
            restore(backup)
        else:
            start(PRIVATE)
            start(WEB)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "start", "stop", "restart", "backup", "deploy", "update", "rollback", "logs"])
    parser.add_argument("reference", nargs="?", default="origin/main")
    args = parser.parse_args()
    lock = (STATE / "operations.lock").open("w")
    if args.action not in {"status", "logs"}:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.action == "status":
        result = {"release": str(CURRENT.resolve()), "services": {x: running(x) for x in [WEB, PRIVATE, PUBLIC, OLD_SSH]}}
        try:
            result.update(health=api("/api/health"), agent=api("/api/conexus/status"), model_providers=api("/api/model-providers"))
        except (OSError, ValueError) as exc:
            result["service_error"] = type(exc).__name__
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.action == "logs":
        for file in [STATE / "server.log", RUNTIME / "conexus/host.log"]:
            print(str(file))
            if file.exists():
                print("\n".join(file.read_text(errors="replace").splitlines()[-35:]))
    elif args.action == "backup":
        snapshot()
    elif args.action in {"stop", "restart"}:
        check_jobs()
        stop(WEB)
        stop(PRIVATE)
        if args.action == "restart":
            start(PRIVATE)
            start(WEB)
            print(json.dumps(health()))
    elif args.action == "start":
        start(PRIVATE)
        start(WEB)
        start(PUBLIC)
        print(json.dumps(health()))
    elif args.action == "update":
        source = HOME / "src/quant-framework-dev3"
        subprocess.run(["git", "-c", "http.version=HTTP/1.1", "fetch", "origin"], cwd=source, check=True)
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip():
            raise RuntimeError("Resolve source checkout changes before updating.")
        # Keep the tested workstation-login fix when merging newer upstream code.
        subprocess.run(["git", "switch", DEPLOYMENT_BRANCH], cwd=source, check=True)
        subprocess.run(["git", "merge", "--no-edit", args.reference], cwd=source, check=True)
        candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        subprocess.run([sys.executable, str(CONFIG / "build_local_release.py"), candidate], check=True)
        deploy(candidate)
    elif args.action == "deploy":
        deploy(args.reference)
    elif args.action == "rollback":
        check_jobs()
        record = json.loads((STATE / "local-deployment.json").read_text())
        target = Path(args.reference) if args.reference != "origin/main" else Path(record["rollback_snapshot"])
        stop(WEB)
        stop(PRIVATE)
        snapshot("before-rollback", offline=True)
        restore(target)


if __name__ == "__main__":
    main()
