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


def test_service_authorization_never_forwards_the_browser_token(monkeypatch):
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "server-only-token")

    headers = conexus._upstream_headers(_request(), service_authorization=True)

    assert headers["authorization"] == "Bearer server-only-token"


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
