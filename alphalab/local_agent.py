"""Lifecycle of the bundled Conexus host; execution stays in Conexus."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from alphalab.utils.env import load_env_files
from alphalab.utils.paths import RUNTIME_DIR


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def local_agent(root: Path, *, api_port: int, agent_port: int = 0) -> Iterator[subprocess.Popen]:
    """Start one local host and clean up only the process owned by this launcher."""

    import httpx

    load_env_files()
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("The local Agent requires Node.js 22.18+ or 24 and npm.")
    version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    major, minor, *_ = (int(part) for part in version.split("."))
    if (major, minor) < (22, 18):
        raise RuntimeError("The local Agent requires Node.js 22.18+ or 24.")
    runtime = root / "runtime/conexus"
    if not (runtime / "UPSTREAM.json").is_file():
        raise RuntimeError(
            "Bundled Conexus runtime is missing. Use the full deployment package, or run "
            "python scripts/build_conexus_runtime.py. "
            "Use --agent off for the manual workbench."
        )
    if not (runtime / "node_modules/.package-lock.json").is_file():
        print("Installing local Agent dependencies for this platform...", flush=True)
        result = subprocess.run([npm, "ci", "--omit=dev", "--no-fund"], cwd=runtime)
        if result.returncode:
            raise RuntimeError("Local Agent dependency installation failed; retry npm ci in runtime/conexus.")
    port = agent_port or _free_port()
    if not 1 <= port <= 65535 or port == api_port:
        raise RuntimeError("Agent port must be valid and different from the AlphaLab port.")
    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Local Agent port {port} is already in use.") from exc
    state = RUNTIME_DIR.resolve() / "conexus"
    secret_root = state / "secrets"
    secret_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    admin_path = secret_root / "admin-token"
    try:
        with admin_path.open("x", encoding="utf-8") as output:
            output.write(secrets.token_urlsafe(32))
        admin_path.chmod(0o600)
    except FileExistsError:
        pass
    connection = secret_root / "alphalab-connection.json"
    connection.unlink(missing_ok=True)
    env = {
        **os.environ,
        "CONEXUS_LOCAL_ROOT": str(state / "project"),
        "CONEXUS_LOCAL_TOKEN": admin_path.read_text(encoding="utf-8").strip(),
        "CONEXUS_LOCAL_PORT": str(port),
        "ALPHALAB_AGENT_CONNECTION_FILE": str(connection),
        "CONEXUS_PUBLICATION_SLUG": "alphalab-research-agent",
        "ALPHALAB_API_ORIGIN": f"http://127.0.0.1:{api_port}",
        "ALPHALAB_AGENT_MODE": "local",
    }
    log_path = state / "host.log"
    # Windows helpers are hidden; stdout/stderr stay in the local diagnostic log.
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log_path.open("a", encoding="utf-8") as log:
        child = subprocess.Popen(
            [node, str(root / "integrations/conexus/local-host.mjs")],
            cwd=root, env=env, stdout=log, stderr=log, creationflags=creationflags,
        )
        previous = {}
        try:
            deadline = time.monotonic() + 45
            with httpx.Client(trust_env=False, timeout=1) as client:
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise RuntimeError(f"Conexus startup failed. Inspect {log_path}")
                    try:
                        connected = json.loads(connection.read_text(encoding="utf-8"))
                        response = client.get(f"http://127.0.0.1:{port}/api/public/harnesses/alphalab-research-agent/descriptor")
                        if response.status_code == 200:
                            break
                    except (OSError, ValueError, httpx.HTTPError):
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError(f"Conexus startup timed out. Inspect {log_path}")
            for name, value in {
                "CONEXUS_WEB_ORIGIN": connected["origin"],
                "CONEXUS_PUBLICATION_SLUG": connected["slug"],
                "CONEXUS_PUBLICATION_WORKSPACE_TOKEN": connected["token"],
            }.items():
                previous[name] = os.environ.get(name)
                os.environ[name] = value
            print("Local Conexus Agent ready.", flush=True)
            if not env.get("CONEXUS_MODEL_ID", "").strip() or not env.get("CONEXUS_MODEL_BASE_URL", "").strip():
                print("Configure CONEXUS_MODEL_BASE_URL and CONEXUS_MODEL_ID in .env to use the Agent.", flush=True)
            yield child
        finally:
            if child.poll() is None:
                try:
                    with httpx.Client(trust_env=False, timeout=2) as client:
                        client.post(f"http://127.0.0.1:{port}/api/local/shutdown",
                                    headers={"Authorization": f"Bearer {env['CONEXUS_LOCAL_TOKEN']}"})
                except httpx.HTTPError:
                    pass
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait(timeout=5)
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def serve_with_local_agent(root: Path, *, host: str, port: int, agent_port: int = 0) -> None:
    import uvicorn

    with local_agent(root, api_port=port, agent_port=agent_port) as child:
        server = uvicorn.Server(uvicorn.Config("dashboard.backend.main:app", host=host, port=port))
        stopped = threading.Event()

        def monitor() -> None:
            while not stopped.wait(0.5):
                if child.poll() is not None:
                    server.should_exit = True
                    return

        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            server.run()
            if child.poll() is not None:
                raise RuntimeError("The local Conexus Agent stopped unexpectedly; inspect data/runtime/conexus/host.log.")
        finally:
            stopped.set()
            watcher.join(timeout=2)
