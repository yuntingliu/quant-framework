"""Lifecycle of the bundled Conexus host; execution stays in Conexus."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.env import load_env_files
from alphalab.utils.paths import RUNTIME_DIR


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _local_agent(
    root: Path, *, api_port: int, agent_port: int = 0, configuration_backend: ConfigurationBackend
) -> Iterator[subprocess.Popen]:
    """Start one local host and clean up only the process owned by this launcher."""

    import httpx

    # Refuse authority conflicts before runtime checks, token reads or npm work.
    with configuration_backend.borrow():
        pass
    load_env_files()
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("The local Agent requires Node.js 22.18+ or 24 and npm.")
    version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    major, minor, *_ = (int(part) for part in version.split("."))
    if (major, minor) < (22, 18):
        raise RuntimeError("The local Agent requires Node.js 22.18+ or 24.")
    runtime = root / "build/conexus"
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
            raise RuntimeError("Local Agent dependency installation failed; retry npm ci in build/conexus.")
    port = agent_port or _free_port()
    if not 1 <= port <= 65535 or port == api_port:
        raise RuntimeError("Agent port must be valid and different from the AlphaLab port.")
    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Local Agent port {port} is already in use.") from exc
    if configuration_backend.mode != "json":
        raise RuntimeError("The production Agent requires the coordinated JSON backend.")
    state = configuration_backend.root / "conexus"
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
    env = {
        **os.environ,
        "CONEXUS_LOCAL_ROOT": str(state / "project"),
        "CONEXUS_LOCAL_TOKEN": admin_path.read_text(encoding="utf-8").strip(),
        "CONEXUS_LOCAL_PORT": str(port),
        "ALPHALAB_AGENT_CONNECTION_FILE": str(connection),
        "ALPHALAB_MODEL_PROVIDERS_FILE": str(secret_root / "model-providers.json"),
        "CONEXUS_PUBLICATION_SLUG": "alphalab-research-agent",
        "ALPHALAB_API_ORIGIN": f"http://127.0.0.1:{api_port}",
        "ALPHALAB_AGENT_MODE": "local",
        "ALPHALAB_AGENT_PARENT_PIPE": "1",
        "ALPHALAB_AGENT_CONFIGURATION_DECISION": json.dumps(configuration_backend.node_contract()),
        "ALPHALAB_AUTHORITY_PYTHON": sys.executable,
    }
    log_path = state / "host.log"
    # Windows helpers are hidden; stdout/stderr stay in the local diagnostic log.
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log_path.open("a", encoding="utf-8") as log:
        child = subprocess.Popen(
            [node, str(root / "integrations/conexus/local-host.mjs")],
            cwd=root, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log,
            creationflags=creationflags,
        )
        previous = {}
        try:
            deadline = time.monotonic() + 45
            with httpx.Client(trust_env=False, timeout=1) as client:
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        lock = state / "project/host.lock"
                        try:
                            owner = int(lock.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            owner = 0
                        if owner > 0 and owner != child.pid:
                            raise RuntimeError(
                                f"Conexus data directory is already locked by Host PID {owner}. "
                                f"Close the existing AlphaLab launcher and retry. Inspect {log_path}"
                            )
                        raise RuntimeError(f"Conexus startup failed. Inspect {log_path}")
                    try:
                        connected = json.loads(connection.read_text(encoding="utf-8"))
                        origin = f"http://127.0.0.1:{port}"
                        if connected.get("pid") == child.pid and connected.get("origin") == origin:
                            configuration_backend.verify_agent_identity(connected.get("configuration"))
                            response = client.get(f"{origin}/api/public/harnesses/alphalab-research-agent/descriptor")
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
                print("Open Model providers in the workstation to configure or test a model.", flush=True)
            yield child
        finally:
            # EOF also reaches Node when this launcher is forcibly terminated.
            # It owns only this child's pipe, never another launcher's Host.
            if child.stdin is not None:
                child.stdin.close()
            if child.poll() is None:
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


@contextmanager
def local_agent(root: Path, *, api_port: int, agent_port: int = 0) -> Iterator[subprocess.Popen]:
    """Hold a shared installation lease throughout the existing Host lifecycle."""
    with ConfigurationBackend.production(RUNTIME_DIR.resolve()) as backend:
        with backend.scope():
            if backend.mode == "sqlite":
                from alphalab.utils.configuration_agent import managed_configuration_agent
                with managed_configuration_agent(
                    backend, api_origin=f"http://127.0.0.1:{api_port}"
                ) as connection:
                    yield connection
                return
            with _local_agent(
                root, api_port=api_port, agent_port=agent_port, configuration_backend=backend
            ) as child:
                yield child


def serve_with_local_agent(root: Path, *, host: str, port: int, agent_port: int = 0) -> None:
    from contextlib import ExitStack

    import uvicorn

    from alphalab.utils.configuration_agent import managed_configuration_agent

    with ExitStack() as lifetime:
        backend = lifetime.enter_context(ConfigurationBackend.production(RUNTIME_DIR.resolve()))
        lifetime.enter_context(backend.scope())
        if backend.mode == "sqlite":
            if host not in {"127.0.0.1", "0.0.0.0"}:
                raise RuntimeError("The owned SQLite Agent requires an IPv4 loopback-accessible API.")
            child = lifetime.enter_context(managed_configuration_agent(
                backend, api_origin=f"http://127.0.0.1:{port}"
            ))
        else:
            child = lifetime.enter_context(_local_agent(
                root, api_port=port, agent_port=agent_port, configuration_backend=backend
            ))
        from apps.api.main import app

        app.state.configuration_backend = backend
        app.state.configuration_api_origin = f"http://127.0.0.1:{port}"
        if backend.mode == "sqlite":
            app.state.configuration_agent = child
        stopped = threading.Event()
        watcher = None

        try:
            server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, timeout_graceful_shutdown=10))

            def monitor() -> None:
                while not stopped.wait(0.5):
                    if child.poll() is not None:
                        server.should_exit = True
                        return

            watcher = threading.Thread(target=monitor, daemon=True)
            watcher.start()
            server.run()
            if child.poll() is not None:
                raise RuntimeError("The local Conexus Agent stopped unexpectedly; inspect data/runtime/conexus/host.log.")
        finally:
            stopped.set()
            if watcher is not None and watcher.ident is not None:
                watcher.join(timeout=2)
            del app.state.configuration_backend
            del app.state.configuration_api_origin
            if hasattr(app.state, "configuration_agent"):
                del app.state.configuration_agent
