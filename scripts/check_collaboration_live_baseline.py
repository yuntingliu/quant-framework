"""Opt-in production Agent check in disposable stores, with a bounded live gateway.

The gateway forwards real provider responses unchanged. It counts requests and
captures usage without retaining prompts, responses, reasoning or credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def worker(output, settings):
    import httpx

    from alphalab.local_agent import local_agent
    from apps.api.services.model_provider_service import validate_profile
    from scripts.collaboration_live_support import write_record
    from scripts.measure_collaboration_baseline import free_port, utc_now, wait_for

    config = json.loads(settings.read_text(encoding="utf-8"))
    profile = validate_profile(next(p for p in config["profiles"] if p["id"] == config["active_id"]))
    if not profile["base_url"].startswith("https://") or not profile.get("api_key"):
        raise ValueError("Live baseline requires an active HTTPS provider and key")
    task = json.loads((ROOT / "tests/fixtures/collaboration/live-baseline-task.json").read_text(encoding="utf-8"))
    rows, lock = [], threading.Lock()
    observed_context = False
    request_deadline = float("inf")

    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            nonlocal observed_context
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or not 0 < size <= 8 * 1024 * 1024:
                self.send_error(400)
                return
            payload = json.loads(self.rfile.read(size))
            with lock:
                if len(rows) >= 12 or time.monotonic() >= request_deadline:
                    self.send_error(429)
                    return
                row = {"call_id": uuid.uuid4().hex, "role": "researcher", "http_status": None,
                       "tool_names": [], "commands": [], "usage": {}, "started_at": utc_now()}
                rows.append(row)
            observed_context |= any(m.get("role") == "tool" and "sdk-v1-default" in str(m.get("content", "")) for m in payload.get("messages", []))
            payload.update(model=profile["model"], max_tokens=4096, stream=False)
            began = time.monotonic()
            status, body = 502, b'{"error":{"message":"Live test transport failed"}}'
            try:
                with httpx.Client(timeout=45, trust_env=False, follow_redirects=False) as provider:
                    with provider.stream("POST", profile["base_url"] + "/chat/completions",
                                         headers={"Authorization": "Bearer " + profile["api_key"]}, json=payload) as response:
                        row["http_status"] = status = response.status_code
                        chunks, length = [], 0
                        for chunk in response.iter_bytes():
                            length += len(chunk)
                            if length > 5 * 1024 * 1024:
                                raise ValueError("Provider response too large")
                            chunks.append(chunk)
                        body = b"".join(chunks)
                value = json.loads(body)
                raw = value.get("usage", {})
                row["usage"] = {k: v for k, v in raw.items() if k in {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"} and type(v) is int and v >= 0}
                for call in value.get("choices", [{}])[0].get("message", {}).get("tool_calls", []):
                    function = call.get("function", {})
                    row["tool_names"].append(function.get("name", "unknown"))
                    try:
                        args = json.loads(function.get("arguments", "{}"))
                        command = args.get("input", {}).get("command")
                        if isinstance(command, str):
                            row["commands"].append(command)
                    except (ValueError, AttributeError):
                        pass
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                status, body = 502, b'{"error":{"message":"Live test transport failed"}}'
            finally:
                row["duration_ms"] = round((time.monotonic() - began) * 1000)
            if profile["api_key"].encode() in body:
                status, body = 502, b'{"error":{"message":"Provider returned unsafe content"}}'
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    gateway = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    gateway.daemon_threads = False  # Drain bounded outstanding receipts before recording totals.
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    port, started, began = free_port(), utc_now(), time.monotonic()
    run, startup, error = {}, 0, None
    try:
        with local_agent(ROOT, api_port=port):
            with (output / "backend.log").open("w", encoding="utf-8") as log:
                backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                           cwd=ROOT, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as client:
                        wait_for(client, "/api/health", lambda r: r.status_code == 200, 45)
                        startup = time.monotonic() - began
                        configured = client.put("/api/model-providers", json={"id": "live-test-gateway", "name": "Bounded live test gateway",
                                                "base_url": f"http://127.0.0.1:{gateway.server_port}/v1", "model": profile["model"]})
                        configured.raise_for_status()
                        conversation_id = "live-baseline-" + uuid.uuid4().hex
                        request_deadline = time.monotonic() + 180
                        created = client.post("/api/conexus/runs", json={"exposureId": "alphalab-research-agent", "input": {"request": task["request"]},
                                              "conversation": {"id": conversation_id, "title": "Live baseline", "createdAt": started,
                                                               "messageId": "user-" + conversation_id, "message": task["request"]}})
                        created.raise_for_status()
                        run = created.json()["run"]
                        try:
                            completed = wait_for(client, f"/api/conexus/runs/{run['id']}", lambda r: r.status_code == 200 and r.json()["run"]["status"] in {"completed", "failed", "blocked", "cancelled"}, 180)
                        except TimeoutError:
                            client.post(f"/api/conexus/runs/{run['id']}/cancel")
                            run["status"] = "timeout"
                            raise
                        run = completed.json()["run"]
                        (output / "public-run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
                        assert run["status"] == "completed", "Live Agent did not complete"
                        workspace_response = client.get("/api/conexus/workspace")
                        workspace_response.raise_for_status()  # Canonical delivery validation is part of this route.
                        workspace = workspace_response.json()["workspace"]
                        reports = [n for n in workspace["nodes"] if n.get("label") == task["report_title"]]
                        assert len(reports) == 1 and task["required_report_text"] in reports[0]["values"]["content"]
                        changed = run.get("nodeChanges", {})
                        expected_outputs = {"alphalab-decision-notebook-v1", "alphalab-workspace-result-v1", "alphalab-workspace-commands-v1"}
                        assert expected_outputs <= set(changed.get("updated", []))
                        assert observed_context and any("workspace.context" in row["commands"] for row in rows)
                        assert any("workspace.outputs.prepare" in row["commands"] for row in rows)
                        history = client.get("/api/conexus/conversations").json()["conversations"]
                        assert any(m["role"] == "assistant" for conv in history if conv["id"] == conversation_id for m in conv["messages"])
                        (output / "report.json").write_text(json.dumps(reports[0], indent=2), encoding="utf-8")
                finally:
                    backend.terminate()
                    backend.wait(timeout=15)
    except Exception as exc:
        error = type(exc).__name__
    finally:
        request_deadline = 0
        gateway.shutdown()
        gateway.server_close()
        thread.join(timeout=3)
    result = {"scenario": "baseline", "status": "passed" if error is None else "failed", "assertions_passed": error is None,
              "task_state": run.get("status", "failed"), "error_code": "LIVE_BASELINE_" + error.upper() if error else None,
              "mode": "live", "model": profile["model"], "root_run_id": run.get("id"), "child_run_ids": [],
              "started_at": started, "finished_at": utc_now(), "seconds": time.monotonic() - began, "startup_seconds": startup,
              "node_version": subprocess.check_output(["node", "--version"], text=True).strip(), "max_model_calls": 12,
              "external_model_calls": len(rows), "observed_context": observed_context, "business_scope": "disposable app/runtime only",
              "final_publication": error is None, "artifacts": str(output)}
    (output / "provider-calls.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (output / "live-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    write_record(output)
    print(json.dumps(result), flush=True)
    return 0 if error is None else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--provider-settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("Explicit --live is required before reading a key or making requests")
    output, settings = args.output.resolve(), args.provider_settings.resolve()
    if not output.is_relative_to((ROOT / "build").resolve()):
        parser.error("Output must stay inside ignored build/")
    output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return worker(output, settings)
    before = hashlib.sha256(settings.read_bytes()).hexdigest()
    env = {**os.environ, "ALPHALAB_RUNTIME_DIR": str(output / "runtime"), "ALPHALAB_APP_DATA_DIR": str(output / "app"),
           "ALPHALAB_CACHE_DIR": str(output / "cache"), "ALPHALAB_AGENT_MODE": "local", "ALPHALAB_WEB_AUTH_ENABLED": "0", "ALPHALAB_WEB_SEARCH_PROVIDER": "off"}
    for key in ("CONEXUS_MODEL_BASE_URL", "CONEXUS_MODEL_ID", "CONEXUS_MODEL_API_KEY", "CONEXUS_WEB_ORIGIN", "CONEXUS_PUBLICATION_WORKSPACE_TOKEN",
                "RQ_USER", "RQ_PASSWORD", "RQ_HOST", "ALPHALAB_WEB_USERNAME", "ALPHALAB_WEB_PASSWORD"):
        env[key] = ""
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--live", "--output", str(output), "--provider-settings", str(settings)],
                            cwd=ROOT, env=env, timeout=250, check=False)
    assert hashlib.sha256(settings.read_bytes()).hexdigest() == before
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
