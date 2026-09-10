"""Exercise real Conexus execution with a deterministic local model provider."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from alphalab.local_agent import local_agent

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    not (ROOT / "build/conexus/node_modules/.package-lock.json").is_file(),
    reason="Build the pinned Conexus runtime to run the local Agent integration test.",
)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(client, path, done=lambda response: response.status_code == 200):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        try:
            response = client.get(path)
            if done(response):
                return response
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"Timed out waiting for {path}")


def test_local_agent_calls_python_tools_persists_report_and_recovers(tmp_path, monkeypatch):
    requests = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            step = len(requests)
            if step == 1:
                name, args = "use", {
                    "node_id": "alphalab-project-run", "capability": "tool.invoke", "input": {"command": "workspace.context"},
                }
            elif step == 2:
                name, args = "create", {"nodes": [{
                    "type": "note", "label": "Local integration report",
                    "description": "AlphaLab quantitative report", "content": "# Local integration report\n\nPersistent evidence.",
                }]}
            elif step == 3:
                name, args = "edit", {"operations": [{
                    "kind": "patch", "node_id": "alphalab-workspace-commands-v1",
                    "set": {"data": {"version": 1, "requestId": "integration", "commands": []}},
                }]}
            else:
                name, args = "complete", {"status": "done", "summary": "Local Agent integration completed."}
            body = json.dumps({"choices": [{"message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": f"step-{step}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }],
            }, "finish_reason": "tool_calls"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("CONEXUS_MODEL_API_KEY", "")
    monkeypatch.setenv("CONEXUS_MODEL_ID", "test/local")
    monkeypatch.setenv("CONEXUS_MODEL_BASE_URL", f"http://127.0.0.1:{model.server_port}/v1")
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    monkeypatch.setattr("alphalab.local_agent.RUNTIME_DIR", tmp_path / "runtime")
    api_port = free_port()
    try:
        for attempt in range(2):
            with local_agent(ROOT, api_port=api_port):
                with (tmp_path / "backend.log").open("a", encoding="utf-8") as log:
                    backend = subprocess.Popen(
                        [sys.executable, "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
                        cwd=ROOT, stdout=log, stderr=log,
                    )
                    try:
                        with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=2) as client:
                            wait_for(client, "/")
                            assert client.get("/api/conexus/status").json()["mode"] == "local_harness"
                            if attempt == 0:
                                created = client.post("/api/conexus/runs", json={
                                    "exposureId": "alphalab-research-agent", "input": {"request": "Read local context and save an integration report."},
                                    "conversation": {
                                        "id": "local-integration", "title": "Local integration",
                                        "createdAt": "2026-09-10T00:00:00Z", "messageId": "local-integration-user",
                                        "message": "Read local context and save an integration report.",
                                    },
                                })
                                assert created.status_code < 300, created.text
                                result = created.json()
                                run_id = result["run"]["id"]
                                assert "accessToken" not in result
                                with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=5) as authorized:
                                    finished = wait_for(authorized, f"/api/conexus/runs/{run_id}",
                                                        lambda r: r.json().get("run", {}).get("status") in {"completed", "failed", "blocked"})
                                    assert finished.json()["run"]["status"] == "completed", finished.text
                                    assert finished.json()["run"]["nodeChanges"]["created"], requests[-1]["messages"]
                                    events = authorized.get(f"/api/conexus/runs/{run_id}/events")
                                    assert events.status_code == 200
                                    assert "Local Agent integration completed." in events.text
                            # Recovery survives both Python restart and the local Host's new port.
                            assert client.get(f"/api/conexus/runs/{run_id}").json()["run"]["status"] == "completed"
                            session = client.get("/api/conexus/conversations").json()
                            conversation = next(item for item in session["conversations"] if item["id"] == "local-integration")
                            replies = [item for item in conversation["messages"] if item["role"] == "assistant"]
                            assert len(replies) == 1
                            assert replies[0]["content"] == "Local Agent integration completed."
                            assert any(item["kind"] == "document" for item in replies[0]["artifacts"])
                            workspace = client.get("/api/conexus/workspace").json()["workspace"]
                            reports = [node for node in workspace["nodes"] if node.get("description") == "AlphaLab quantitative report"]
                            assert len(reports) == 1, workspace
                            assert "Persistent evidence." in reports[0]["values"]["content"]
                    finally:
                        backend.terminate()
                        backend.wait(timeout=15)
        # Tool code ran in Conexus's real Node process and reached the Python API.
        results = [message["content"] for message in requests[-1]["messages"] if message["role"] == "tool"]
        assert any("sdk-v1-default" in result for result in results), results
        assert not any('"success":false' in result for result in results), results
        prompt = "\n".join(message["content"] for message in requests[0]["messages"] if message["role"] == "system")
        assert "{{ALPHALAB_SDK_SKILL}}" not in prompt
        assert "{{ALPHALAB_WORKSPACE_COMMAND_SCHEMA}}" not in prompt
        assert "factor.research" in prompt
        assert "backtest.validation" in prompt
    finally:
        model.shutdown()
        model.server_close()
        thread.join(timeout=3)


def test_duplicate_launcher_preserves_existing_host_connection(tmp_path, monkeypatch):
    monkeypatch.setattr("alphalab.local_agent.RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setenv("CONEXUS_MODEL_ID", "")
    monkeypatch.setenv("CONEXUS_MODEL_BASE_URL", "")
    connection = tmp_path / "runtime/conexus/secrets/alphalab-connection.json"
    lock = tmp_path / "runtime/conexus/project/host.lock"
    with local_agent(ROOT, api_port=free_port()) as child:
        original = connection.read_bytes()
        origin = json.loads(original)["origin"]
        with pytest.raises(RuntimeError, match=f"already locked by Host PID {child.pid}"):
            with local_agent(ROOT, api_port=free_port()):
                pytest.fail("Two Hosts must not own the same data directory.")
        assert child.poll() is None
        assert connection.read_bytes() == original
        assert int(lock.read_text()) == child.pid
        with httpx.Client(trust_env=False, timeout=2) as client:
            assert client.get(f"{origin}/health").json()["ok"] is True
    assert child.returncode == 0
    assert not lock.exists()


def test_abrupt_launcher_exit_releases_host_and_allows_restart(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr("alphalab.local_agent.RUNTIME_DIR", runtime)
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("CONEXUS_MODEL_ID", "")
    monkeypatch.setenv("CONEXUS_MODEL_BASE_URL", "")
    connection = runtime / "conexus/secrets/alphalab-connection.json"
    lock = runtime / "conexus/project/host.lock"
    state = runtime / "conexus/project/state.json"
    # os._exit skips every Python finally block, as a forced launcher exit does.
    # Trigger it in the actual interpreter, including Windows venv subprocesses.
    script = """
import os, sys
from pathlib import Path
from alphalab.local_agent import local_agent
with local_agent(Path.cwd(), api_port=int(sys.argv[1])):
    sys.stdin.buffer.read(1)
    os._exit(23)
"""
    with (tmp_path / "launcher.log").open("w", encoding="utf-8") as log:
        launcher = subprocess.Popen(
            [sys.executable, "-u", "-c", script, str(free_port())],
            cwd=ROOT, env=os.environ.copy(), stdin=subprocess.PIPE, stdout=log, stderr=log,
        )
        try:
            deadline = time.monotonic() + 30
            while not connection.exists() and time.monotonic() < deadline:
                assert launcher.poll() is None, (tmp_path / "launcher.log").read_text()
                time.sleep(0.1)
            assert connection.exists(), (tmp_path / "launcher.log").read_text()
            connected = json.loads(connection.read_text())
            with httpx.Client(trust_env=False, timeout=2) as client:
                assert client.get(f"{connected['origin']}/health").json()["ok"] is True
            persisted = json.loads(state.read_text(encoding="utf-8"))
            launcher.stdin.write(b"x")
            launcher.stdin.flush()
            assert launcher.wait(timeout=10) == 23
            deadline = time.monotonic() + 15
            while lock.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not lock.exists(), "The orphan Host did not release its lock."
            assert json.loads(state.read_text(encoding="utf-8")) == persisted
            with socket.socket() as probe:
                probe.settimeout(0.5)
                host_port = int(connected["origin"].rsplit(":", 1)[1])
                assert probe.connect_ex(("127.0.0.1", host_port)) != 0
            with local_agent(ROOT, api_port=free_port()) as restarted:
                assert json.loads(connection.read_text())["pid"] == restarted.pid
                assert json.loads(state.read_text(encoding="utf-8"))["nodes"] == persisted["nodes"]
            assert restarted.returncode == 0
            assert not lock.exists()
        finally:
            launcher.stdin.close()
            if launcher.poll() is None:
                launcher.wait(timeout=35)
