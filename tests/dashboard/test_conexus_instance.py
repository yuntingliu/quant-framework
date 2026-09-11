import base64

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.backend.routers.agent_context import router
from scripts.serve_conexus_private_api import create_proxy


def test_instance_identity_is_explicit_and_contains_no_environment(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setenv("ALPHALAB_INSTANCE_ID", "dev3")
    monkeypatch.setenv("ALPHALAB_WEB_PASSWORD", "private")
    assert TestClient(app).get("/api/agent/identity").json() == {"instance_id": "dev3"}
    monkeypatch.delenv("ALPHALAB_INSTANCE_ID")
    assert TestClient(app).get("/api/agent/identity").json() == {"instance_id": ""}


def test_private_ingress_targets_same_authenticated_backend_and_drops_incoming_credentials():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"instance_id": "dev3"}, headers={"set-cookie": "private=1"})

    client = TestClient(create_proxy("user", "password", 8300, transport=httpx.MockTransport(handle)))
    response = client.get("/api/agent/identity", headers={"Authorization": "Bearer other", "Cookie": "other=1"})
    assert response.json() == {"instance_id": "dev3"}
    assert str(seen[0].url) == "http://127.0.0.1:8300/api/agent/identity"
    assert seen[0].headers["authorization"] == "Basic " + base64.b64encode(b"user:password").decode()
    assert "cookie" not in seen[0].headers
    assert "set-cookie" not in response.headers
    assert client.get("/api/conexus/conversations").status_code == 404
    assert client.get("/app/").status_code == 404
    assert len(seen) == 1
