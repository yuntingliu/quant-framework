"""Measure the existing single-Agent production integration without paid models.

Each scenario starts the canonical local Host and FastAPI in a fresh private
runtime. No Agent loop, financial engine or production policy is replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCENARIOS = ("success", "missing_config", "invalid_model", "invalid_endpoint", "text_only")
BASE_COMMIT = "d12bbb3ee763b046b3333c9134f4bf6e508412d8"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL)


def source_identity():
    # A separately exported handover has no .git directory. Never accidentally
    # attribute it to an enclosing checkout; verify its explicit source manifest.
    manifest_path = ROOT.parent / "source-manifest.json"
    if ROOT.name == "source" and manifest_path.is_file():
        from scripts.package_collaboration_handover import verify

        manifest = verify(ROOT.parent)
        return {"base_commit": manifest["base_commit"], "working_commit": manifest["base_commit"],
                "core_revision": manifest["core_revision"], "dirty": True,
                "diff_sha256": manifest["tracked_diff_sha256"],
                "dependency_hashes": {**manifest["source_files"],
                                      "../source-manifest.json": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}}
    paths = ["pyproject.toml", "apps/desktop/package-lock.json", "integrations/conexus/runtime.lock.json"]
    # A dirty checkout's new source files must be identifiable too, not only its
    # tracked diff. Raw user data and ignored runtime artifacts are never read.
    paths += git("ls-files", "--others", "--exclude-standard", "-z").decode().strip("\0").split("\0")
    hashes = {}
    for name in filter(None, paths):
        path = ROOT / name
        if path.is_file():
            hashes[name.replace("\\", "/")] = hashlib.sha256(path.read_bytes()).hexdigest()
    lock = json.loads((ROOT / "integrations/conexus/runtime.lock.json").read_text())
    return {
        "base_commit": BASE_COMMIT,
        "working_commit": git("rev-parse", "HEAD").decode().strip(),
        "core_revision": lock["revision"],
        "dirty": bool(git("status", "--porcelain")),
        "diff_sha256": hashlib.sha256(git("diff", "--binary", "HEAD")).hexdigest(),
        "dependency_hashes": hashes,
    }


def wait_for(client, path, predicate, timeout):
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = client.get(path)
            if predicate(response):
                return response
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {path}")


def run_worker(scenario: str, output: Path, instrument_usage: bool = False, ui_smoke: bool = False) -> bool:
    import httpx

    from alphalab.collaboration import validate_run_record
    from alphalab.local_agent import local_agent

    task_path = ROOT / "tests/fixtures/collaboration/baseline-task.json"
    task = json.loads(task_path.read_text())
    identity = source_identity()
    model_id = "scripted/missing" if scenario == "invalid_model" else "scripted/local"
    attempts = []
    usage_rows = []
    observed_context = False

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            nonlocal observed_context
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            step = len(attempts) + 1
            observed_context |= any(
                message.get("role") == "tool" and "sdk-v1-default" in str(message.get("content"))
                and '"success":false' not in str(message.get("content"))
                for message in payload.get("messages", [])
            )
            status = 200
            name = None
            args = {}
            if self.path != "/v1/chat/completions" or payload.get("model") != "scripted/local":
                status = 404
            elif step > 4 or (scenario == "text_only" and step > 2):
                status = 503
            elif scenario != "text_only":
                if step == 1:
                    name, args = "use", {
                        "node_id": "alphalab-project-run", "capability": "tool.invoke",
                        "input": {"command": "workspace.context"},
                    }
                elif step == 2:
                    name, args = "create", {"nodes": [{
                        "type": "note", "label": task["report_title"],
                        "description": "AlphaLab quantitative report", "content": task["report_content"],
                    }]}
                elif step == 3:
                    name, args = "edit", {"operations": [{
                        "kind": "patch", "node_id": "alphalab-workspace-commands-v1",
                        "set": {"data": {"version": 1, "requestId": "baseline", "commands": []}},
                    }]}
                else:
                    name, args = "complete", {"status": "done", "summary": task["summary"]}
            attempts.append({
                "id": f"call-{step}", "role": "researcher", "round": 0,
                "status": "succeeded" if status == 200 else "failed", "http_status": status,
                "tool_names": [name] if name else [],
            })
            if status != 200:
                body = {"error": {"message": "FAKE_PROVIDER_SECRET_MUST_NOT_LEAK"}}
            else:
                message = {"role": "assistant", "content": "Text alone is not an accepted completion."}
                if name:
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": f"step-{step}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }]}
                body = {"choices": [{"message": message, "finish_reason": "tool_calls" if name else "stop"}]}
            if instrument_usage:
                # Endpoint-observed baseline accounting. This leaves the normal
                # single-Agent Host unchanged; shared-budget enforcement is
                # verified separately through the opt-in collaboration Host.
                scripted_usage = {"prompt_tokens": 10 * step, "completion_tokens": 5,
                                  "total_tokens": 10 * step + 5}
                body["usage"] = scripted_usage
                usage_rows.append({"call_id": f"call-{step}", "role": "researcher", "http_status": status,
                                   "recorded_at": utc_now(), "usage": scripted_usage})
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    started_at = utc_now()
    clock = time.monotonic()
    startup = 0.0
    elapsed_run = None
    run_id = None
    outcome = "failed"
    reason = "BASELINE_STARTUP_FAILED"
    passed = False
    backend = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    api_port = free_port()
    try:
        with local_agent(ROOT, api_port=api_port):
            with (output / "backend.log").open("w", encoding="utf-8") as log:
                backend = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
                    cwd=ROOT, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                try:
                    with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=5) as client:
                        wait_for(client, "/api/health", lambda r: r.status_code == 200, 45)
                        startup = time.monotonic() - clock
                        if scenario != "missing_config":
                            configured = client.put("/api/model-providers", json={
                                "id": "baseline", "name": "Scripted baseline", "model": model_id,
                                "base_url": f"http://127.0.0.1:{server.server_port}" + ("/wrong/v1" if scenario == "invalid_endpoint" else "/v1"),
                            })
                            configured.raise_for_status()
                        run_clock = time.monotonic()
                        if ui_smoke:
                            ready = {"url": f"http://127.0.0.1:{api_port}/app/", "request": task["request"],
                                     "report_title": task["report_title"], "summary": task["summary"],
                                     "stop_file": "ui-stop.json", "external_model_calls": 0}
                            (output / "ui-ready.json").write_text(json.dumps(ready, indent=2), encoding="utf-8")
                            print(json.dumps(ready), flush=True)
                            observed = wait_for(client, "/api/conexus/conversations", lambda r: r.status_code == 200 and bool(r.json().get("runs")), 600)
                            created = httpx.Response(200, json={"run": observed.json()["runs"][0]["run"]})
                        else:
                            created = client.post("/api/conexus/runs", json={
                                "exposureId": "alphalab-research-agent", "input": {"request": task["request"]},
                                "conversation": {
                                    "id": f"baseline-{output.name}", "title": "Scripted baseline",
                                    "createdAt": started_at, "messageId": f"user-{output.name}",
                                    "message": task["request"],
                                },
                            })
                        if scenario == "missing_config":
                            passed = created.status_code == 503 and "configure a model" in created.text.lower() and not attempts
                            outcome, reason = "invalid_configuration", "MODEL_NOT_CONFIGURED"
                        else:
                            if not ui_smoke:
                                created.raise_for_status()
                            run_id = created.json()["run"]["id"]
                            try:
                                finished = wait_for(client, f"/api/conexus/runs/{run_id}", lambda r: (
                                    r.status_code == 200 and r.json()["run"]["status"] in {"completed", "failed", "blocked", "cancelled"}
                                ), 120)
                            except TimeoutError:
                                client.post(f"/api/conexus/runs/{run_id}/cancel")
                                outcome, reason = "timeout", "BASELINE_DEADLINE"
                            else:
                                run = finished.json()["run"]
                                outcome = run["status"]
                                reason = None if outcome == "completed" else f"SCRIPTED_{scenario.upper()}"
                                if scenario == "success":
                                    workspace = client.get("/api/conexus/workspace").json()["workspace"]
                                    reports = [node for node in workspace["nodes"] if node.get("label") == task["report_title"]]
                                    passed = outcome == "completed" and observed_context and len(reports) == 1 and (
                                        reports[0]["values"]["content"] == task["report_content"]
                                    ) and len(attempts) == 4
                                else:
                                    passed = outcome in {"failed", "blocked"} and "FAKE_PROVIDER_SECRET_MUST_NOT_LEAK" not in finished.text
                                    if scenario != "text_only":
                                        passed &= len(attempts) == 1 and attempts[0]["http_status"] == 404
                                    else:
                                        passed &= 1 <= len(attempts) <= 3 and not any(row["tool_names"] for row in attempts)
                        elapsed_run = time.monotonic() - run_clock
                        if ui_smoke:
                            (output / "ui-terminal.json").write_text(json.dumps({"run_id": run_id, "outcome": outcome, "assertions_passed": passed}), encoding="utf-8")
                            deadline = time.monotonic() + 600
                            while not (output / "ui-stop.json").exists() and time.monotonic() < deadline:
                                time.sleep(0.25)
                finally:
                    backend.terminate()
                    backend.wait(timeout=15)
    except Exception as error:
        # Keep machine paths and provider messages out of the shareable record.
        reason = f"BASELINE_ERROR_{type(error).__name__}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    usage = {"status": "unavailable", "input_tokens": None, "output_tokens": None,
             "reason": "The baseline adapter does not expose provider usage; scripted text is not billed usage."}
    if instrument_usage:
        usage = {"status": "estimated", "input_tokens": sum(row["usage"]["prompt_tokens"] for row in usage_rows),
                 "output_tokens": sum(row["usage"]["completion_tokens"] for row in usage_rows),
                 "reason": "Synthetic token units captured at the loopback endpoint; not model-token measurements or billed usage."}
        passed &= len(usage_rows) == len(attempts)
        (output / "usage-ledger.json").write_text(json.dumps({
            "instrumentation": "endpoint_observation_only", "root_run_id": run_id,
            "task_id": task["fixture_id"], "rows": usage_rows, "totals": usage,
            "total_tokens": usage["input_tokens"] + usage["output_tokens"],
            "reconciled": len(usage_rows) == len(attempts), "host_budget_enforcement": False,
        }, indent=2) + "\n", encoding="utf-8")
    cost = {"status": "scripted_no_external_provider", "amount": "0", "currency": None,
            "pricing_snapshot": None, "reason": "Only a loopback scripted endpoint was used; local compute cost is not measured."}
    totals = {"model_calls": len(attempts), "tool_calls": sum(len(row["tool_names"]) for row in attempts),
              "retries": 0, "searches": 0, "usage": usage, "cost": cost}
    record = {
        "schema_version": 1, "record_id": output.name, "task_id": task["fixture_id"],
        "fixture": {"id": task["fixture_id"], "version": task["version"],
                    "sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(), "manifest_sha256": None},
        "paired_case_id": task["fixture_id"], "arm": "single", "mode": "scripted", "repetition": 1, "seed": 0,
        "source": identity,
        "environment": {"python": platform.python_version(), "node": subprocess.check_output(["node", "--version"], text=True).strip(), "platform": sys.platform},
        "configuration": {"model": model_id, "provider": "loopback-scripted-chat-completions",
                          "prompt_version": "existing-research-canvas", "schema_version": "measurement-v1",
                          "policy_version": None, "evidence_manifest": None,
                          "usage_note": "Counts cover the endpoint-observed calls of this single-role baseline, including rejected requests. No child Agent, automatic transport retry or search is used."},
        "budget": {"wall_seconds": 120, "model_calls": 4, "tokens": None, "cost": None,
                   "currency": None, "enforcement": "Harness deadline/cancellation and scripted endpoint turn limit only; the opt-in collaboration Host supplies separate shared budgets."},
        "root_run_id": run_id, "child_run_ids": [], "started_at": started_at, "finished_at": utc_now(),
        "timings": {"startup_seconds": startup, "request_to_terminal_seconds": elapsed_run,
                    "total_seconds": time.monotonic() - clock, "queue_seconds": None, "start_kind": "cold"},
        "roles": [{"role": "researcher", **totals}], "totals": totals, "attempts": attempts,
        "human_interventions": [], "outcome": {"status": outcome, "reason": reason, "qualified": None, "quality_scores": None},
        "artifacts": ["backend.log", "runtime/conexus/host.log", *(["usage-ledger.json"] if instrument_usage else [])],
    }
    validate_run_record(record)
    (output / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    (output / "verification.json").write_text(json.dumps({
        "scenario": scenario, "assertions_passed": passed, "observed_context_tool_result": observed_context,
        "financial_quality_scored": False,
        "usage_instrumented": instrument_usage,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"{scenario}: {'PASS' if passed else 'FAIL'}; outcome={outcome}; model_calls={len(attempts)}")
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="success")
    parser.add_argument("--output", type=Path, default=ROOT / "build/member1/baseline")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--instrument-usage", action="store_true", help="Record synthetic endpoint usage and reconcile the unchanged fixed baseline.")
    parser.add_argument("--ui-smoke", action="store_true", help="Wait for a browser-submitted baseline run; keep the isolated UI open until ui-stop.json or ten minutes.")
    args = parser.parse_args()
    if args.ui_smoke and args.scenario != "success":
        parser.error("--ui-smoke supports only --scenario success.")
    output = args.output.resolve()
    # Baseline logs include private Host files; keep them inside ignored build/.
    if not output.is_relative_to((ROOT / "build").resolve()):
        parser.error("--output must be inside this checkout's ignored build directory.")
    output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        if args.scenario == "all":
            parser.error("A worker accepts exactly one scenario.")
        return 0 if run_worker(args.scenario, output, args.instrument_usage, args.ui_smoke) else 1
    statuses = []
    for scenario in SCENARIOS if args.scenario == "all" else (args.scenario,):
        target = output / f"{scenario}-{uuid.uuid4().hex[:12]}"
        target.mkdir()
        env = {**os.environ, "ALPHALAB_RUNTIME_DIR": str(target / "runtime"),
               "ALPHALAB_APP_DATA_DIR": str(target / "app"), "ALPHALAB_CACHE_DIR": str(target / "cache"),
               "ALPHALAB_AGENT_MODE": "local"}
        # Explicit empty values defeat .env fallbacks and user-saved settings;
        # provider profiles are created only in this fresh runtime.
        for key in ("CONEXUS_MODEL_BASE_URL", "CONEXUS_MODEL_ID", "CONEXUS_MODEL_API_KEY",
                    "CONEXUS_WEB_ORIGIN", "CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "RQ_USER", "RQ_PASSWORD",
                    "ALPHALAB_WEB_USERNAME", "ALPHALAB_WEB_PASSWORD"):
            env[key] = ""
        env["ALPHALAB_WEB_AUTH_ENABLED"] = "0"
        env["ALPHALAB_WEB_SEARCH_PROVIDER"] = "off"
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--scenario", scenario, "--output", str(target)]
        if args.instrument_usage:
            command.append("--instrument-usage")
        if args.ui_smoke:
            command.append("--ui-smoke")
        try:
            completed = subprocess.run(command, cwd=ROOT, env=env, timeout=1320 if args.ui_smoke else 240, check=False)
            statuses.append({"scenario": scenario, "exit_code": completed.returncode, "record": str((target / "record.json").relative_to(output))})
        except subprocess.TimeoutExpired:
            statuses.append({"scenario": scenario, "exit_code": 1, "record": None, "reason": "WORKER_TIMEOUT"})
    (output / "summary.json").write_text(json.dumps(statuses, indent=2) + "\n", encoding="utf-8")
    return int(any(row["exit_code"] for row in statuses))


if __name__ == "__main__":
    raise SystemExit(main())
