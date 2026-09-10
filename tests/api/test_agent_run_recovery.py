from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import conexus
from apps.api.services import agent_run_service


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    monkeypatch.setenv("CONEXUS_WEB_ORIGIN", "http://127.0.0.1:18787")
    monkeypatch.setenv("CONEXUS_PUBLICATION_SLUG", "alphalab-research-agent")
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "workspace-secret")
    state = {
        "run": {"id": "run-1", "status": "running", "createdAt": "2026-09-10T00:00:00Z", "slug": "alphalab-research-agent", "version": 1},
        "requests": [], "workspace_status": 200, "nodes": [],
    }

    def upstream(request):
        state["requests"].append(request)
        path = request.url.path
        if path.endswith("/runs"):
            assert request.headers["authorization"] == "Bearer workspace-secret"
            assert "conversation" not in json.loads(request.content)
            return httpx.Response(201, json={"run": deepcopy(state["run"]), "accessToken": "run-secret"})
        if path.endswith("/workspace"):
            return httpx.Response(state["workspace_status"], json={"workspace": {"nodes": state["nodes"]}})
        assert request.headers["authorization"] == "Bearer run-secret"
        if path.endswith("/cancel"):
            state["run"].update(status="cancelled", completedAt="2026-09-10T00:01:00Z")
        if path.endswith("/answer"):
            assert json.loads(request.content) == {"answer": "Continue"}
            state["run"].pop("pendingInteraction", None)
        if path.endswith("/events"):
            return httpx.Response(200, stream=httpx.ByteStream(b'data: {"type":"run.started"}\n\n'), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"run": deepcopy(state["run"])})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_class(
        **kwargs, transport=httpx.MockTransport(upstream),
    ))
    app = FastAPI(lifespan=agent_run_service.run_recovery_lifespan)
    app.include_router(conexus.router)
    return app, agent_run_service.conversation_store(), state


def submit(client):
    response = client.post("/api/conexus/runs", json={
        "exposureId": "alphalab-research-agent", "input": {"request": "Research this"},
        "conversation": {"id": "chat-1", "title": "Research", "createdAt": "2026-09-10T00:00:00Z", "messageId": "user-1", "message": "Research this"},
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_background_delivery_persists_reply_without_browser_requests(service):
    app, store, state = service
    with TestClient(app) as client:
        created = submit(client)
        assert "accessToken" not in created
        assert len(store.list_conversations()[0]["messages"]) == 1
        state["run"].update(status="completed", summary="Saved without a browser", completedAt="2026-09-10T00:01:00Z")
        # No HTTP polling or event stream: only the backend lifespan can deliver this reply.
        deadline = time.monotonic() + 6
        while len(store.list_conversations()[0]["messages"]) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        messages = store.list_conversations()[0]["messages"]
        assert {item["content"] for item in messages} == {"Research this", "Saved without a browser"}
        assert not store.pending_runs(agent_run_service.run_scope())
        session = client.get("/api/conexus/conversations").json()
        assert session["runs"][0]["run"]["status"] == "completed"
        assert "run-secret" not in json.dumps(session)
        assert "workspace-secret" not in json.dumps(session)


def test_restart_recovers_question_and_controls_without_browser_token(service, monkeypatch):
    app, store, state = service
    state["run"]["pendingInteraction"] = {"id": "question-1", "question": "Continue?", "kind": "question", "ownerNodeId": "agent"}
    submit(TestClient(app))
    # New app instance and local Host port; no in-memory client token survives.
    monkeypatch.setenv("CONEXUS_WEB_ORIGIN", "http://127.0.0.1:18788")
    restarted = FastAPI()
    restarted.include_router(conexus.router)
    client = TestClient(restarted)
    session = client.get("/api/conexus/conversations").json()
    assert session["runs"][0]["conversationId"] == "chat-1"
    recovered = client.get("/api/conexus/runs/run-1").json()["run"]
    assert recovered["pendingInteraction"]["id"] == "question-1"
    assert client.get("/api/conexus/runs/run-1/events").status_code == 200
    answered = client.post("/api/conexus/runs/run-1/interactions/question-1/answer", json={"answer": "Continue"})
    assert answered.status_code == 200
    assert "pendingInteraction" not in answered.json()["run"]
    cancelled = client.post("/api/conexus/runs/run-1/cancel")
    assert cancelled.json()["run"]["status"] == "cancelled"
    replies = [item for item in store.list_conversations()[0]["messages"] if item["role"] == "assistant"]
    assert len(replies) == 1 and replies[0]["error"] is True


def test_terminal_delivery_is_atomic_idempotent_and_survives_stale_tabs(service):
    app, store, state = service
    initial = submit(TestClient(app))["conversation"]
    stale_run = deepcopy(state["run"])
    terminal = {**stale_run, "status": "completed", "summary": "One reply", "completedAt": "2026-09-10T00:01:00Z"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: store.record_run(agent_run_service.run_scope(), terminal, delivered=True), range(8)))
    store.record_run(agent_run_service.run_scope(), stale_run)
    store.upsert(initial)
    messages = store.list_conversations()[0]["messages"]
    assert len([item for item in messages if item["role"] == "assistant"]) == 1
    assert store.conversation_runs(agent_run_service.run_scope())[0]["run"]["status"] == "completed"
    assert not store.pending_runs(agent_run_service.run_scope())


def test_workspace_outage_does_not_delay_reply_and_artifacts_are_retried(service):
    app, store, state = service
    submit(TestClient(app))
    state["run"].update(status="completed", summary="Report ready", nodeChanges={"created": ["report-1", "report-2"], "updated": []})
    state["workspace_status"] = 503
    asyncio.run(agent_run_service.reconcile_runs())
    def replies():
        return [item for item in store.list_conversations()[0]["messages"] if item["role"] == "assistant"]
    assert replies()[0]["content"] == "Report ready"
    assert store.pending_runs(agent_run_service.run_scope())
    state["workspace_status"] = 200
    state["nodes"] = [{
        "id": "report-1", "type": "note", "label": "Report", "description": "AlphaLab quantitative report",
        "updatedByRunId": "run-1", "values": {"content": "# Persisted report"},
    }, {
        "id": "report-2", "type": "note", "label": "Overwritten report", "description": "AlphaLab quantitative report",
        "createdByRunId": "run-1", "updatedByRunId": "later-run", "values": {"content": "Other run"},
    }]
    asyncio.run(agent_run_service.reconcile_runs())
    assert len(replies()) == 1
    assert [item["producerNodeId"] for item in replies()[0]["artifacts"]] == ["report-1"]
    assert not store.pending_runs(agent_run_service.run_scope())


def test_remote_configuration_change_cannot_reuse_other_host_credentials(service, monkeypatch):
    app, store, _ = service
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "remote")
    submit(TestClient(app))
    monkeypatch.setenv("CONEXUS_WEB_ORIGIN", "https://different-host.invalid")
    assert agent_run_service.run_headers("run-1") == {}
    assert store.conversation_runs(agent_run_service.run_scope()) == []


def test_invalid_conversation_is_rejected_before_starting_a_run(service):
    app, _, state = service
    response = TestClient(app).post("/api/conexus/runs", json={"conversation": {"id": "chat-1"}})
    assert response.status_code == 422
    assert not state["requests"]
