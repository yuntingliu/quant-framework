"""Private Mac mini operations for the authenticated dev3 local workstation."""

from __future__ import annotations

import argparse
import base64
import json
import os
import plistlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_deployment import (
    coordinated_offline,
    create_backup,
    protected,
    reject_restore,
    require_idle,
    write_pending_model,
)

HOME = Path.home()
CONFIG = HOME / ".config/alphalab-dev3"
STATE = HOME / ".local/state/alphalab-dev3"
RUNTIME = HOME / ".local/share/alphalab-dev3/runtime"
CURRENT = HOME / "apps/alphalab-dev3"
RELEASES = HOME / "releases/alphalab-dev3"
DOMAIN = "gui/" + str(getattr(os, "getuid", lambda: 0)())
WEB = "net.acetoken.alphalab-dev3"
PRIVATE = WEB + "-agent-api"
PUBLIC = WEB + "-tunnel"
OLD_SSH = WEB + "-agent-tunnel"
DEPLOYMENT_BRANCH = "deploy/dev3-local-20261019"
PLISTS = HOME / "Library/LaunchAgents"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o777 if os.name == "nt" else 0o700)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    if os.name != "nt":
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
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, check=check, timeout=10
    )


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
    credential = base64.b64encode(
        (conf["ALPHALAB_WEB_USERNAME"] + ":" + conf["ALPHALAB_WEB_PASSWORD"]).encode()
    ).decode()
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
            if result.get("status") == "ok" and (
                expected is None or result.get("commit_sha") == expected
            ):
                return result
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Authenticated service health timed out.")


def check_jobs():
    require_idle(RUNTIME)


def offline_operation():
    # JSON is the current production contract, not a guess based on file presence.
    # A live SQLite owner conflicts before we stop services or touch configuration.
    with ConfigurationBackend.json(RUNTIME) as decision:
        pass
    return coordinated_offline(decision, stop_services=stop_services)


def stop_services():
    stop(WEB)
    stop(PRIVATE)
    stop(OLD_SSH)


@protected
def snapshot(tag="manual", offline=False, session=None):
    if session is None:
        with offline_operation() as owned:
            return snapshot(tag, offline=True, session=owned)
    session._check()
    if session.root != RUNTIME.resolve() or not offline:
        raise ValueError
    if (
        not tag
        or len(tag) > 96
        or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in tag)
    ):
        raise ValueError
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = STATE / "backups" / (stamp + "-" + tag)
    report = create_backup(
        session,
        target,
        config=CONFIG,
        plists=PLISTS.glob("net.acetoken.alphalab-dev3*.plist"),
        metadata={
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tag": tag,
            "offline": offline,
            "release": str(CURRENT.resolve()),
            "old_ssh_registered": running(OLD_SSH),
        },
    )
    print(json.dumps(report), flush=True)
    return target


def write_runner():
    # The native Host owns Agent execution. The existing loopback forwarder
    # authenticates tool requests to the same protected Python backend.
    runner = """import json,os,sys,threading
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
"""
    path = CONFIG / "run_server.py"
    path.write_text(runner)
    if os.name != "nt":
        path.chmod(0o600)
    plist = {
        "Label": WEB,
        "ProgramArguments": [str(CURRENT / ".venv/bin/python"), str(path)],
        "WorkingDirectory": str(CURRENT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ExitTimeOut": 40,
        "StandardOutPath": str(STATE / "server.log"),
        "StandardErrorPath": str(STATE / "server.log"),
        "EnvironmentVariables": {"PYTHONDONTWRITEBYTECODE": "1"},
    }
    path = PLISTS / (WEB + ".plist")
    path.write_bytes(plistlib.dumps(plist))
    if os.name != "nt":
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
    reject_restore(backup)


@protected
def deploy(ref):
    source = HOME / "src/quant-framework-dev3"
    commit = subprocess.check_output(
        ["git", "rev-parse", ref + "^{commit}"], cwd=source, text=True
    ).strip()
    release = RELEASES / commit
    validation = release / ".mac-local-build-validated.json"
    if not validation.is_file() or json.loads(validation.read_text())["commit_sha"] != commit:
        raise RuntimeError("Build and validation must finish before activation.")
    backup = None
    try:
        with offline_operation() as offline:
            backup = snapshot("before-local-" + commit[:8], offline=True, session=offline)
            launch("disable", DOMAIN + "/" + OLD_SSH)
            conf = values()
            for key in [
                "CONEXUS_WEB_ORIGIN",
                "CONEXUS_PUBLICATION_SLUG",
                "CONEXUS_PUBLICATION_WORKSPACE_TOKEN",
            ]:
                conf.pop(key, None)
            conf["ALPHALAB_AGENT_MODE"] = "local"
            conf["ALPHALAB_INSTANCE_ID"] = "dev3"
            conf["ALPHALAB_CACHE_DIR"] = str(RUNTIME / "cache")
            temp = CONFIG / "alphalab.env.tmp"
            temp.write_text(
                "".join(
                    key + "=" + json.dumps(str(value)) + "\n" for key, value in sorted(conf.items())
                )
            )
            if os.name != "nt":
                temp.chmod(0o600)
            os.replace(temp, CONFIG / "alphalab.env")
            pending = CONFIG / "model-profile.pending.json"
            if pending.exists():
                write_pending_model(offline, json.loads(pending.read_text()))
            switch(release)
            write_runner()
            reports = CONFIG / "historical-reports.json"
            if reports.exists() and not (RUNTIME / "conexus/report-migration.json").exists():
                # No offline maintenance fence is bypassed by an uncoordinated
                # report-import subprocess. Prepare it separately before deployment.
                raise RuntimeError("Historical report import requires separate preparation.")
            write_json(
                release / ".alphalab-release.json",
                {
                    "commit_sha": commit,
                    "deployed_at": datetime.now(timezone.utc).isoformat(),
                    "agent_mode": "local",
                },
            )
        # Lifetimes may start only after both offline fences have been released.
        start(PRIVATE)
        start(WEB)
        start(PUBLIC)
        health(commit)
        agent = api("/api/conexus/status")
        if not agent.get("available") or agent.get("mode") != "local_harness":
            raise RuntimeError("Local Conexus readiness failed.")
        write_json(
            STATE / "local-deployment.json",
            {
                "commit_sha": commit,
                "activated_at": datetime.now(timezone.utc).isoformat(),
                "rollback_snapshot": str(backup),
                "agent_mode": "local",
                "tool_origin": "http://127.0.0.1:8301",
                "agent_origin": "http://127.0.0.1:8387",
            },
        )
        pending.unlink(missing_ok=True)
        print(json.dumps({"deployed": True, "commit_sha": commit}))
    except BaseException:
        # No directory restoration, credential rollback, old-release selection,
        # or automatic restart. Keep both current state and the private backup.
        # Stop only these owned listeners if failure followed service startup.
        if backup is not None:
            stop_services()
        raise


def main():
    if sys.platform != "darwin":
        raise RuntimeError("This operations entry point requires macOS.")
    import fcntl

    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "status",
            "start",
            "stop",
            "restart",
            "backup",
            "deploy",
            "update",
            "rollback",
            "logs",
        ],
    )
    parser.add_argument("reference", nargs="?", default="origin/main")
    args = parser.parse_args()
    lock = (STATE / "operations.lock").open("w")
    if args.action not in {"status", "logs"}:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.action == "status":
        result = {
            "release": str(CURRENT.resolve()),
            "services": {x: running(x) for x in [WEB, PRIVATE, PUBLIC, OLD_SSH]},
        }
        try:
            result.update(
                health=api("/api/health"),
                agent=api("/api/conexus/status"),
                model_providers=api("/api/model-providers"),
            )
        except (OSError, ValueError) as exc:
            result["service_error"] = type(exc).__name__
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.action == "logs":
        for file in [STATE / "server.log", RUNTIME / "conexus/host.log"]:
            print(str(file))
            if file.exists():
                print("\n".join(file.read_text(errors="replace").splitlines()[-35:]))
    elif args.action == "backup":
        registered = [label for label in (PRIVATE, WEB) if running(label)]
        snapshot()
        for label in registered:
            start(label)
    elif args.action in {"stop", "restart"}:
        with offline_operation():
            pass
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
        subprocess.run(
            ["git", "-c", "http.version=HTTP/1.1", "fetch", "origin"], cwd=source, check=True
        )
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip():
            raise RuntimeError("Resolve source checkout changes before updating.")
        # Keep the tested workstation-login fix when merging newer upstream code.
        subprocess.run(["git", "switch", DEPLOYMENT_BRANCH], cwd=source, check=True)
        subprocess.run(["git", "merge", "--no-edit", args.reference], cwd=source, check=True)
        candidate = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip()
        subprocess.run(
            [sys.executable, str(CONFIG / "build_local_release.py"), candidate], check=True
        )
        deploy(candidate)
    elif args.action == "deploy":
        deploy(args.reference)
    elif args.action == "rollback":
        reject_restore()


if __name__ == "__main__":
    main()
