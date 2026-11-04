"""Exercise real Conexus execution with a deterministic local model provider."""

from __future__ import annotations

import hashlib
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


def wait_for(client, path, done=lambda response: response.status_code == 200, *, timeout=35):
    deadline = time.monotonic() + timeout
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
    monkeypatch.setenv("CONEXUS_MODEL_ID", "")
    monkeypatch.setenv("CONEXUS_MODEL_BASE_URL", "")
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path / "runtime"))
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
                            wait_for(client, "/api/health")
                            assert client.get("/api/conexus/status").json()["mode"] == "local_harness"
                            if attempt == 0:
                                assert client.get("/api/conexus/status").json()["available"] is False
                                configured = client.put("/api/model-providers", json={
                                    "id": "integration", "name": "Integration model", "model": "test/local",
                                    "base_url": f"http://127.0.0.1:{model.server_port}/v1", "api_key": "",
                                })
                                assert configured.status_code == 200, configured.text
                                assert client.get("/api/conexus/status").json()["available"] is True
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
                                                        lambda r: r.json().get("run", {}).get("status") in {"completed", "failed", "blocked"},
                                                        # Include slow Windows CI shell startup in the run budget.
                                                        timeout=90 if sys.platform == "win32" else 35)
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


def test_real_host_honours_idempotency_correlates_jobs_and_survives_restart(tmp_path, monkeypatch):
    """The Host side of the lifecycle contract that the fault-replay suite simulates."""
    requests = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            step = len(requests)
            name, args = ("use", {
                "node_id": "alphalab-project-run", "capability": "tool.invoke",
                "input": {"command": "backtest.run", "confirm_python_execution": True, "args": {
                    "project_id": "sdk-v1-default", "start_date": "2025-01-02", "end_date": "2025-03-31",
                }},
            }) if step == 1 else ("complete", {"status": "done", "summary": "Lifecycle integration completed."})
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"step-{step}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)},
            }]}, "finish_reason": "tool_calls"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    for name in ("CONEXUS_MODEL_API_KEY", "CONEXUS_MODEL_ID", "CONEXUS_MODEL_BASE_URL"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    monkeypatch.setattr("alphalab.local_agent.RUNTIME_DIR", tmp_path / "runtime")
    api_port = free_port()
    submission = {
        "exposureId": "alphalab-research-agent", "input": {"request": "Run one backtest."},
        "conversation": {"id": "lifecycle", "title": "Lifecycle", "createdAt": "2026-09-10T00:00:00Z",
                         "messageId": "lifecycle-user", "message": "Run one backtest."},
    }
    key = hashlib.sha256(b"lifecycle:lifecycle-user").hexdigest()

    def host_lookup():
        return httpx.get(
            f"{os.environ['CONEXUS_WEB_ORIGIN']}/api/alphalab/submissions/{key}", trust_env=False, timeout=5,
            headers={"Authorization": f"Bearer {os.environ['CONEXUS_PUBLICATION_WORKSPACE_TOKEN']}"},
        )

    try:
        for attempt in range(2):
            with local_agent(ROOT, api_port=api_port):
                with (tmp_path / "backend.log").open("a", encoding="utf-8") as log:
                    backend = subprocess.Popen(
                        [sys.executable, "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
                        cwd=ROOT, stdout=log, stderr=log,
                    )
                    try:
                        with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=10) as client:
                            wait_for(client, "/api/health")
                            if attempt == 0:
                                assert client.put("/api/model-providers", json={
                                    "id": "integration", "name": "Integration model", "model": "test/local",
                                    "base_url": f"http://127.0.0.1:{model.server_port}/v1", "api_key": "",
                                }).status_code == 200
                                created = client.post("/api/conexus/runs", json=submission)
                                assert created.status_code < 300, created.text
                                run_id = created.json()["run"]["id"]
                                # Directly at the Host: a repeated key gets the original Run and token back.
                                direct = {"Authorization": f"Bearer {os.environ['CONEXUS_PUBLICATION_WORKSPACE_TOKEN']}", "Idempotency-Key": key}
                                url = f"{os.environ['CONEXUS_WEB_ORIGIN']}/api/public/harnesses/alphalab-research-agent/runs"
                                again = httpx.post(url, headers=direct, json={"exposureId": submission["exposureId"], "input": submission["input"]}, trust_env=False, timeout=10)
                                assert again.status_code == 202 and again.json()["run"]["id"] == run_id
                                refused = httpx.post(url, headers={**direct, "Authorization": "Bearer wrong"}, json={}, trust_env=False, timeout=10)
                                assert refused.status_code == 401 and "accessToken" not in refused.text
                                # A backtest waits for the user: the lifecycle says so, and the answer goes through the API.
                                waiting = wait_for(client, f"/api/conexus/runs/{run_id}",
                                                   lambda r: r.json()["run"].get("pendingInteraction") is not None,
                                                   timeout=90 if sys.platform == "win32" else 35).json()
                                assert waiting["run"]["lifecycle"]["phase"] == "awaiting_input"
                                assert waiting["run"]["lifecycle"]["jobs"] == [], "nothing runs before approval"
                                interaction = waiting["run"]["pendingInteraction"]["id"]
                                approved = client.post(f"/api/conexus/runs/{run_id}/interactions/{interaction}/answer", json={"answer": "Approve"})
                                assert approved.status_code < 300, approved.text
                            # Through the API, after completion and again after a restart of both processes.
                            duplicate = client.post("/api/conexus/runs", json=submission)
                            assert duplicate.status_code == 200, duplicate.text
                            assert duplicate.json()["run"]["id"] == run_id and duplicate.json()["replayed"] is True
                            known = host_lookup().json()
                            assert known["known"] is True and known["run"]["id"] == run_id
                            wait_for(client, f"/api/conexus/runs/{run_id}", lambda r: r.json()["run"]["status"] == "completed",
                                     timeout=90 if sys.platform == "win32" else 35)
                            trace = wait_for(client, f"/api/conexus/runs/{run_id}/trace", lambda r: r.json()["complete"] is True).json()
                            assert trace["messageId"] == "lifecycle-user" and trace["phase"] == "completed"
                            assert trace["tools"][0]["command"] == "backtest.run"
                            assert [job["kind"] for job in trace["jobs"]] == ["backtest"], "the real tool must carry its Run id"
                            assert {item["kind"] for item in trace["entries"]} >= {"submission.accepted", "run.started", "model.turn", "tool.result", "job.linked", "run.completed"}
                            assert "run-secret" not in json.dumps(trace) and "accessToken" not in json.dumps(trace)
                            assert "start_date" not in json.dumps(trace), "an approval's arguments stay out of the trace"
                            session = client.get("/api/conexus/conversations").json()
                            turns = next(item for item in session["conversations"] if item["id"] == "lifecycle")["messages"]
                            assert len([item for item in turns if item["role"] == "user"]) == 1
                            assert len([item for item in turns if item["role"] == "assistant"]) == 1
                    finally:
                        backend.terminate()
                        backend.wait(timeout=15)
        assert len(requests) == 2, "duplicates across a restart must not bill another model call"
        # This file holds live Run credentials.
        records = next(tmp_path.rglob("run-submissions.json"))
        if sys.platform != "win32":
            assert records.stat().st_mode & 0o077 == 0
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
