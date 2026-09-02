from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from dashboard.backend.routers import conexus


def _request(authorization: str = "Bearer browser-token") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", authorization.encode("ascii"))],
        }
    )


def _conversation(*messages: dict, updated_at: str = "2026-09-02T08:00:00Z") -> dict:
    return {
        "id": "conversation-1",
        "title": "Shared research",
        "createdAt": "2026-09-02T08:00:00Z",
        "updatedAt": updated_at,
        "messages": list(messages),
    }


def _message(message_id: str, content: str, created_at: str, **extra) -> dict:
    return {
        "id": message_id,
        "role": "user" if message_id == "message-1" else "assistant",
        "content": content,
        "createdAt": created_at,
        "runId": "run-1",
        **extra,
    }


def test_service_authorization_never_forwards_the_browser_token(monkeypatch):
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "server-only-token")

    headers = conexus._upstream_headers(_request(), authorization="workspace")

    assert headers["authorization"] == "Bearer server-only-token"


def test_run_access_request_keeps_the_scoped_run_token():
    headers = conexus._upstream_headers(_request(), authorization="request")

    assert headers["authorization"] == "Bearer browser-token"


def test_manifest_uses_the_server_workspace_credential(monkeypatch):
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "server-only-token")
    observed = {}

    async def forward(request, method, path, *, authorization="request"):
        observed.update(method=method, path=path, authorization=authorization)
        from fastapi.responses import JSONResponse

        return JSONResponse({"ok": True})

    monkeypatch.setattr(conexus, "_forward", forward)
    app = FastAPI()
    app.include_router(conexus.router)

    response = TestClient(app).get(
        "/api/conexus/manifest",
        headers={"Authorization": "Basic workstation-credential"},
    )

    assert response.status_code == 200
    assert observed == {
        "method": "GET",
        "path": "/api/public/harnesses/alphalab-research-agent",
        "authorization": "workspace",
    }


def test_durable_workspace_routes_require_server_configuration(monkeypatch):
    monkeypatch.delenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(conexus.router)
    client = TestClient(app)

    assert client.get("/api/conexus/workspace").status_code == 503
    assert client.post("/api/conexus/runs", json={}).status_code == 503


def test_status_is_unavailable_without_durable_workspace_configuration(monkeypatch):
    monkeypatch.delenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(conexus.router)
    client = TestClient(app)

    response = client.get("/api/conexus/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "mode": "not_configured",
        "publication": "alphalab-research-agent",
        "error": "workspace_not_configured",
    }


def test_agent_conversations_are_persisted_on_the_shared_server(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(conexus.router)
    client = TestClient(app)
    conversation = _conversation(
        _message("message-1", "Compare the strategies", "2026-09-02T08:00:00Z")
    )

    saved = client.put("/api/conexus/conversations/conversation-1", json=conversation)
    listed = client.get("/api/conexus/conversations")

    assert saved.status_code == 200
    assert listed.status_code == 200
    assert listed.json()["conversations"] == [saved.json()["conversation"]]


def test_stale_browser_snapshots_merge_without_losing_shared_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(conexus.router)
    client = TestClient(app)
    first = _message(
        "message-1",
        "Compare the strategies",
        "2026-09-02T08:00:00Z",
        artifacts=[
            {
                "id": "artifact-1",
                "runId": "run-1",
                "title": "Comparison",
                "createdAt": "2026-09-02T08:00:00Z",
                "kind": "document",
                "content": {"markdown": "# Result"},
            }
        ],
    )
    second = _message("message-2", "Completed", "2026-09-02T08:01:00Z")
    assert client.put(
        "/api/conexus/conversations/conversation-1",
        json=_conversation(first),
    ).status_code == 200

    response = client.put(
        "/api/conexus/conversations/conversation-1",
        json=_conversation(second, updated_at="2026-09-02T08:01:00Z"),
    )

    assert response.status_code == 200
    messages = response.json()["conversation"]["messages"]
    assert [message["id"] for message in messages] == ["message-1", "message-2"]
    assert messages[0]["artifacts"][0]["id"] == "artifact-1"


def test_agent_conversation_path_must_match_payload_id(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(conexus.router)

    response = TestClient(app).put(
        "/api/conexus/conversations/another-conversation",
        json=_conversation(),
    )

    assert response.status_code == 409
