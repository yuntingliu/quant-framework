"""F4: Python ControlStore -> signed HTTP -> durable Host receipt -> reconciliation.

The HTTP Host here is a deterministic protocol double. Native Local Host restart
and disk persistence are tested separately in local-host.test.mjs. No model calls.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
import pytest

from alphalab.collaboration import CollaborationError, ControlStore, encode_json, sha256
from apps.api.services.collaboration_publication_service import (
    publish_final_output, reconcile_publication,
)
from scripts.prepare_collaboration_host import prepare

SECRET = "f4-integration-test-secret-do-not-use-in-production"
ORIGIN = "http://127.0.0.1:8787"
SCOPES = ["model.invoke", "evidence.read", "review.submit", "review.delegate"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def approved(tmp_path):
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
                      "role": "reviewer", "task_id": task_id,
                      "node_id": delegation["node_id"],
                      "child_task_id": delegation["child_task_id"],
                      "attempt_id": delegation["attempt_id"],
                  })
    store.request(task_id, "finish_child", child_task_id=delegation["child_task_id"], status="done")
    store.request(task_id, "finish_root", status="done")
    state = store.request(task_id, "view")
    context = store.evidence.candidate_context(task_id, state["candidate"])
    package = context["research_package"]
    publication = context["evidence"][package["publication"]["artifact_id"]]
    outputs = copy.deepcopy(publication["content"]["outputs"])
    destination = {"report_id": outputs["workspaceResult"]["reportId"], "mode": "create",
                   "expected_workspace_revision": 0, "expected_report_revision": None}
    request = {
        "schema_version": 1, "task_id": task_id,
        "request_id": outputs["workspaceResult"]["requestId"],
        "idempotency_key": "f4-delivery", "expected_task_version": state["version"],
        "candidate": state["candidate"], "manifest": state["manifest"],
        "review_submission_id": review["submission_id"],
        "correction_response_id": None, "draft_report": package["draft_report"],
        "payload_sha256": publication["sha256"], "destination": destination,
        "outputs": outputs,
    }
    return store, request, destination


class DurableHostDouble:
    def __init__(self, *, drop_first_response=False):
        self.receipts = {}
        self.workspace_revision = 0
        self.commits = 0
        self.drop_first_response = drop_first_response
        self.posts = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode().split("?", 1)[0]
        timestamp = request.headers["x-alphalab-publication-timestamp"]
        signature = request.headers["x-alphalab-publication-signature"]
        message = f"{request.method}\n{path}\n{timestamp}\n".encode() + request.content
        expected = hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(signature, expected)
        if request.method == "GET":
            key = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"receipt": self.receipts[key]}) if key in self.receipts else httpx.Response(404)
        assert request.method == "POST"
        self.posts += 1
        envelope = json.loads(request.content)
        intent, final = envelope["intent"], envelope["request"]
        key = intent["idempotency_key"]
        if key not in self.receipts:
            if final["destination"]["expected_workspace_revision"] != self.workspace_revision:
                return httpx.Response(409, json={"error": {"code": "VERSION_CONFLICT"}})
            assert hashlib.sha256(envelope["report_markdown"].encode()).hexdigest() == envelope["report_sha256"]
            self.workspace_revision += 1
            self.commits += 1
            self.receipts[key] = {
                "task_id": final["task_id"], "intent_id": intent["intent_id"],
                "request_id": final["request_id"], "idempotency_key": key,
                "request_sha256": intent["request_sha256"],
                "payload_sha256": final["payload_sha256"],
                "candidate": final["candidate"], "manifest": final["manifest"],
                "review_submission_id": final["review_submission_id"],
                "destination": final["destination"],
                "workspace_revision": self.workspace_revision, "report_revision": 1,
                "committed_at": datetime.now(timezone.utc).isoformat(),
            }
        if self.drop_first_response and self.posts == 1:
            raise httpx.ReadError("response lost after durable commit", request=request)
        return httpx.Response(200, json={"receipt": self.receipts[key]})


@pytest.mark.anyio
async def test_successful_publication_and_idempotent_retry(tmp_path):
    store, request, destination = approved(tmp_path)
    host = DurableHostDouble()
    async with httpx.AsyncClient(transport=httpx.MockTransport(host)) as client:
        first = await publish_final_output(request=request, control_store=store,
            evidence_store=store.evidence, destination=destination, host_origin=ORIGIN,
            publication_secret=SECRET, client=client)
        second = await publish_final_output(request=request, control_store=store,
            evidence_store=store.evidence, destination=destination, host_origin=ORIGIN,
            publication_secret=SECRET, client=client)
    assert first == second
    assert host.commits == host.posts == 1
    assert store.get_publication(request["task_id"], idempotency_key=request["idempotency_key"])["status"] == "committed"
    assert store.request(request["task_id"], "view")["status"] == "completed"


@pytest.mark.anyio
async def test_lost_response_recovers_receipt_without_second_commit(tmp_path):
    store, request, destination = approved(tmp_path)
    host = DurableHostDouble(drop_first_response=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(host)) as client:
        receipt = await publish_final_output(request=request, control_store=store,
            evidence_store=store.evidence, destination=destination, host_origin=ORIGIN,
            publication_secret=SECRET, client=client)
    assert receipt == host.receipts[request["idempotency_key"]]
    assert host.commits == host.posts == 1


@pytest.mark.anyio
async def test_reconciliation_after_control_store_restart(tmp_path):
    store, request, destination = approved(tmp_path)
    host = DurableHostDouble()
    intent = store.prepare_publication(request["task_id"], request, destination=destination)
    # Simulate Host commit with no Python receipt persistence (process interruption).
    from apps.api.services.collaboration_publication_service import _report_bytes
    from apps.api.services.workspace_output_service import prepare_workspace_outputs
    report = _report_bytes(store.evidence, request)
    envelope = {"schema_version": 1, "intent": intent, "request": request,
                "report_markdown": report.decode(), "report_sha256": sha256(report),
                "prepared_outputs": prepare_workspace_outputs(request["request_id"], request["outputs"])}
    body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
    from apps.api.services.collaboration_publication_service import _signed_headers
    host(httpx.Request("POST", ORIGIN + "/api/alphalab/publications", content=body,
                       headers=_signed_headers(SECRET, "POST", "/api/alphalab/publications", body)))
    reopened = ControlStore(tmp_path / "control", tmp_path / "evidence")
    assert reopened.get_publication(request["task_id"], idempotency_key=request["idempotency_key"])["status"] == "pending"
    async with httpx.AsyncClient(transport=httpx.MockTransport(host)) as client:
        recovered = await reconcile_publication(control_store=reopened, task_id=request["task_id"],
            idempotency_key=request["idempotency_key"], host_origin=ORIGIN,
            publication_secret=SECRET, client=client)
    assert recovered == host.receipts[request["idempotency_key"]]
    assert host.commits == 1
    assert reopened.get_publication(request["task_id"], idempotency_key=request["idempotency_key"])["status"] == "committed"


@pytest.mark.anyio
async def test_competing_delivery_cannot_replace_pending_intent(tmp_path):
    store, request, destination = approved(tmp_path)
    host = DurableHostDouble()
    async with httpx.AsyncClient(transport=httpx.MockTransport(host)) as client:
        await publish_final_output(request=request, control_store=store,
            evidence_store=store.evidence, destination=destination, host_origin=ORIGIN,
            publication_secret=SECRET, client=client)
        competing = copy.deepcopy(request)
        competing["idempotency_key"] = "competing-delivery"
        with pytest.raises(CollaborationError) as error:
            await publish_final_output(request=competing, control_store=store,
                evidence_store=store.evidence, destination=destination, host_origin=ORIGIN,
                publication_secret=SECRET, client=client)
    assert error.value.code in {"INVALID_STATE", "VERSION_CONFLICT"}
    assert host.commits == 1
