"""Real Python ControlStore -> patched Node Local Host over loopback HTTP.

No model calls, no external network, no production data. Runs only with the
the patched runtime built with scripts/build_conexus_runtime.py.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from alphalab.collaboration import CollaborationError, ControlStore, encode_json, sha256
from apps.api.services.collaboration_publication_service import (
    destination_from_workspace,
    fetch_local_workspace_snapshot,
    publish_final_output,
    reconcile_publication,
)
from scripts.prepare_collaboration_host import prepare

ROOT = Path(__file__).resolve().parents[2]
HOST_SCRIPT = ROOT / "scripts" / "member2_local_host_acceptance.mjs"
BUILT_RUNTIME = ROOT / "build" / "member-2" / "conexus-runtime"
TOKEN = "member2-local-workspace-token-0123456789"
SECRET = "member2-publication-secret-0123456789abcdef0123456789abcdef"
SCOPES = ["model.invoke", "evidence.read", "review.submit", "review.delegate"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def host_runner(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node.js is required for real Local Host acceptance")
    if not (BUILT_RUNTIME / "node_modules" / "fastify").exists() or not (
        BUILT_RUNTIME / "packages" / "runtime-core" / "dist" / "index.js"
    ).exists():
        pytest.skip("Run scripts/build_conexus_runtime.py before real Local Host acceptance")
    state_root = tmp_path / "host-state"
    processes = []

    def start():
        port = _port()
        env = {**os.environ, "MEMBER2_TEST_WORKSPACE_TOKEN": TOKEN, "MEMBER2_TEST_PUBLICATION_SECRET": SECRET}
        proc = subprocess.Popen(
            [shutil.which("node"), str(HOST_SCRIPT), str(state_root), str(port)],
            cwd=ROOT, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        processes.append(proc)
        origin = f"http://127.0.0.1:{port}"
        until = time.monotonic() + 15
        while time.monotonic() < until:
            if proc.poll() is not None:
                raise AssertionError(f"Local Host exited before ready: {proc.stderr.read()[-3000:]}")
            try:
                response = httpx.get(origin + "/health", timeout=0.3, trust_env=False)
                if response.status_code == 200:
                    return origin, proc
            except httpx.RequestError:
                pass
            time.sleep(0.1)
        proc.terminate()
        raise AssertionError("Local Host did not start within 15 seconds")

    def stop(proc):
        if proc.poll() is None:
            try:
                proc.stdin.write("stop\n")
                proc.stdin.flush()
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait(timeout=5)
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()

    yield start, stop, state_root
    for proc in processes:
        if proc.poll() is None:
            stop(proc)


def _approved(tmp_path):
    fixture = prepare(tmp_path)
    store = ControlStore(tmp_path / "control", tmp_path / "evidence")
    task_id = fixture["task_id"]
    store.register(task_id, fixture["candidate"], {"scopes": SCOPES, "operations": []})
    store.request(task_id, "begin")
    delegation, review = fixture["delegation"], fixture["review"]
    store.request(task_id, "start", delegation=delegation)
    raw = encode_json(review).decode("utf-8")
    store.request(task_id, "submit", child_task_id=delegation["child_task_id"],
                  submission_id=review["submission_id"], payload_json=raw,
                  sha256=sha256(raw.encode("utf-8")), identity={
                      "role": "reviewer", "task_id": task_id, "node_id": delegation["node_id"],
                      "child_task_id": delegation["child_task_id"], "attempt_id": delegation["attempt_id"],
                  })
    store.request(task_id, "finish_child", child_task_id=delegation["child_task_id"], status="done")
    store.request(task_id, "finish_root", status="done")
    state = store.request(task_id, "view")
    context = store.evidence.candidate_context(task_id, state["candidate"])
    package = context["research_package"]
    publication = context["evidence"][package["publication"]["artifact_id"]]
    return store, state, package, publication


def _request(state, package, publication, destination, key="member2-delivery"):
    outputs = copy.deepcopy(publication["content"]["outputs"])
    return {
        "schema_version": 1,
        "task_id": package["task_id"],
        "request_id": outputs["workspaceResult"]["requestId"],
        "idempotency_key": key, "expected_task_version": state["version"],
        "candidate": state["candidate"], "manifest": state["manifest"],
        "review_submission_id": state["children"][-1]["review"]["submission_id"],
        "correction_response_id": None, "draft_report": package["draft_report"],
        "payload_sha256": publication["sha256"], "destination": destination,
        "outputs": outputs,
    }


def _connection(tmp_path, origin):
    path = tmp_path / "connection.json"
    path.write_text(json.dumps({"origin": origin, "slug": "local-agent", "token": TOKEN,
                                "pid": os.getpid()}), encoding="utf-8")
    return path


@pytest.mark.anyio
async def test_real_host_publish_replay_conflict_restart_and_reconciliation(tmp_path, host_runner):
    start, stop, state_root = host_runner
    origin, process = start()
    store, state, package, publication = _approved(tmp_path / "research")
    connection = _connection(tmp_path, origin)
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        snapshot = await fetch_local_workspace_snapshot(connection_file=connection,
            expected_slug="local-agent", client=client)
        destination = destination_from_workspace(snapshot, publication["content"]["outputs"]["workspaceResult"]["reportId"])
        request = _request(state, package, publication, destination)
        receipt = await publish_final_output(request=request, control_store=store,
            evidence_store=store.evidence, destination=destination,
            host_origin=origin, publication_secret=SECRET, client=client)
        assert receipt["workspace_revision"] == snapshot["revision"] + 1
        assert receipt["report_revision"] == 1
        assert store.get_publication(request["task_id"], idempotency_key=request["idempotency_key"])["status"] == "committed"
        assert await publish_final_output(request=request, control_store=store,
            evidence_store=store.evidence, destination=destination,
            host_origin=origin, publication_secret=SECRET, client=client) == receipt
        workspace = await fetch_local_workspace_snapshot(connection_file=connection,
            expected_slug="local-agent", client=client)
        assert workspace["revision"] == receipt["workspace_revision"]
        nodes = {node["id"]: node for node in workspace["nodes"]}
        report_id = destination["report_id"]
        assert nodes[report_id]["values"]["revision"] == 1
        for node_id in ("alphalab-decision-notebook-v1", "alphalab-workspace-result-v1",
                        "alphalab-workspace-commands-v1"):
            assert nodes[node_id]["values"]["data"]
        competing = copy.deepcopy(request)
        competing["idempotency_key"] = "member2-conflicting-delivery"
        with pytest.raises(CollaborationError) as error:
            await publish_final_output(request=competing, control_store=store,
                evidence_store=store.evidence, destination=destination,
                host_origin=origin, publication_secret=SECRET, client=client)
        assert error.value.code in {"INVALID_STATE", "VERSION_CONFLICT"}
    stop(process)
    origin, process = start()
    connection = _connection(tmp_path, origin)
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        snapshot = await fetch_local_workspace_snapshot(connection_file=connection,
            expected_slug="local-agent", client=client)
        assert snapshot["revision"] == receipt["workspace_revision"]
        assert next(node for node in snapshot["nodes"] if node["id"] == report_id)["values"]["revision"] == 1
        reopened = ControlStore(tmp_path / "research" / "control", tmp_path / "research" / "evidence")
        assert await reconcile_publication(control_store=reopened, task_id=request["task_id"],
            idempotency_key=request["idempotency_key"], host_origin=origin,
            publication_secret=SECRET, client=client) == receipt
        from apps.api.services.collaboration_publication_service import _fetch_receipt
        assert await _fetch_receipt(client=client, host_origin=origin, secret=SECRET,
                                    idempotency_key=request["idempotency_key"]) == receipt
    saved = json.loads((state_root / "state.json").read_text(encoding="utf-8"))
    assert len(saved["publicationReceipts"]) == 1
    assert saved["revision"] == receipt["workspace_revision"]
    assert store.request(request["task_id"], "view")["status"] == "completed"


@pytest.mark.anyio
async def test_real_host_receipt_reconciles_after_python_commit_interruption(tmp_path, host_runner):
    """Host commits; Python crashes before recording receipt; restart then reconcile."""
    start, stop, state_root = host_runner
    origin, process = start()
    store, state, package, publication = _approved(tmp_path / "research")
    connection = _connection(tmp_path, origin)
    from apps.api.services.collaboration_publication_service import (
        _report_bytes, _signed_headers, PUBLICATION_PATH,
    )
    from apps.api.services.workspace_output_service import prepare_workspace_outputs
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        snapshot = await fetch_local_workspace_snapshot(connection_file=connection,
            expected_slug="local-agent", client=client)
        destination = destination_from_workspace(snapshot, publication["content"]["outputs"]["workspaceResult"]["reportId"])
        request = _request(state, package, publication, destination, key="member2-interrupted-delivery")
        intent = store.prepare_publication(request["task_id"], request, destination=destination)
        report = _report_bytes(store.evidence, request)
        envelope = {"schema_version": 1, "intent": intent, "request": request,
                    "report_markdown": report.decode("utf-8"), "report_sha256": sha256(report),
                    "prepared_outputs": prepare_workspace_outputs(request["request_id"], request["outputs"])}
        body = json.dumps(envelope, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
        response = await client.post(origin + PUBLICATION_PATH, content=body,
            headers=_signed_headers(SECRET, "POST", PUBLICATION_PATH, body))
        assert response.status_code == 200, response.text
        receipt = response.json()["receipt"]
        assert store.get_publication(request["task_id"], idempotency_key="member2-interrupted-delivery")["status"] == "pending"
    stop(process)
    origin, process = start()
    reopened = ControlStore(tmp_path / "research" / "control", tmp_path / "research" / "evidence")
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        recovered = await reconcile_publication(control_store=reopened,
            task_id=request["task_id"], idempotency_key="member2-interrupted-delivery",
            host_origin=origin, publication_secret=SECRET, client=client)
        assert recovered == receipt
        assert reopened.get_publication(request["task_id"], idempotency_key="member2-interrupted-delivery")["status"] == "committed"
    saved = json.loads((state_root / "state.json").read_text(encoding="utf-8"))
    assert len(saved["publicationReceipts"]) == 1
    assert saved["revision"] == receipt["workspace_revision"]
