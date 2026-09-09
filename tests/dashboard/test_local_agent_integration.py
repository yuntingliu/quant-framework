"""Exercise real Conexus execution with a deterministic local model provider."""

from __future__ import annotations

import json
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
    not (ROOT / "runtime/conexus/node_modules/.package-lock.json").is_file(),
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
                        [sys.executable, "-m", "uvicorn", "dashboard.backend.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
                        cwd=ROOT, stdout=log, stderr=log,
                    )
                    try:
                        with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=2) as client:
                            wait_for(client, "/")
                            assert client.get("/api/conexus/status").json()["mode"] == "local_harness"
                            if attempt == 0:
                                created = client.post("/api/conexus/runs", json={
                                    "exposureId": "alphalab-research-agent", "input": {"request": "Read local context and save an integration report."},
                                })
                                assert created.status_code < 300, created.text
                                result = created.json()
                                run_id, token = result["run"]["id"], result["accessToken"]
                                with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=2,
                                                  headers={"Authorization": f"Bearer {token}"}) as authorized:
                                    finished = wait_for(authorized, f"/api/conexus/runs/{run_id}",
                                                        lambda r: r.json().get("run", {}).get("status") in {"completed", "failed", "blocked"})
                                    assert finished.json()["run"]["status"] == "completed", finished.text
                                    assert finished.json()["run"]["nodeChanges"]["created"], requests[-1]["messages"]
                                    events = authorized.get(f"/api/conexus/runs/{run_id}/events")
                                    assert events.status_code == 200
                                    assert "Local Agent integration completed." in events.text
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
