import base64

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dashboard.backend.auth import HttpBasicAuthMiddleware


def test_hosted_http_and_editor_websocket_require_credentials(monkeypatch):
    monkeypatch.setenv("ALPHALAB_WEB_AUTH_ENABLED", "true")
    monkeypatch.setenv("ALPHALAB_WEB_USERNAME", "dev")
    monkeypatch.setenv("ALPHALAB_WEB_PASSWORD", "test-secret")
    app = FastAPI()
    app.add_middleware(HttpBasicAuthMiddleware)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.websocket("/api/python-editor/lsp")
    async def websocket(socket: WebSocket):
        await socket.accept()
        await socket.send_text("ready")
        await socket.close()

    client = TestClient(app)
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"Authorization": "Bearer unrelated"}).status_code == 401
    assert client.get("/api/health", auth=("dev", "wrong")).status_code == 401
    assert client.get("/api/health", auth=("dev", "test-secret")).status_code == 200
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/python-editor/lsp"):
            pass
    assert error.value.code == 1008
    header = "Basic " + base64.b64encode(b"dev:test-secret").decode()
    with client.websocket_connect("/api/python-editor/lsp", headers={"Authorization": header}) as socket:
        assert socket.receive_text() == "ready"
    monkeypatch.delenv("ALPHALAB_WEB_PASSWORD")
    assert client.get("/api/health").status_code == 503


def test_run_bearer_is_forwarded_only_to_run_endpoints(monkeypatch):
    monkeypatch.setenv("ALPHALAB_WEB_AUTH_ENABLED", "true")
    monkeypatch.setenv("ALPHALAB_WEB_USERNAME", "dev")
    monkeypatch.setenv("ALPHALAB_WEB_PASSWORD", "test-secret")
    app = FastAPI()
    app.add_middleware(HttpBasicAuthMiddleware)

    @app.get("/api/conexus/runs/example")
    def run():
        # A stand-in for the upstream run-token verifier; it rejects this token.
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "invalid run token"}, status_code=403)

    client = TestClient(app)
    assert client.get("/api/conexus/runs/example", headers={"Authorization": "Bearer invalid"}).status_code == 403
    assert client.get("/api/conexus/runs/example").status_code == 401


def test_deployment_health_does_not_scan_market_datasets(monkeypatch, tmp_path):
    from alphalab import ResultStore
    from alphalab.dataio.catalog import DataCatalog
    from dashboard.backend import main

    def unexpected_scan(_catalog):
        raise AssertionError("health probes must not scan historical datasets")

    monkeypatch.setenv("ALPHALAB_WEB_AUTH_ENABLED", "false")
    monkeypatch.setattr(DataCatalog, "summary", unexpected_scan)
    monkeypatch.setattr(main, "ResultStore", lambda: ResultStore(tmp_path / "health.db"))
    response = TestClient(main.app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["runtime_profile"] == "not_checked"
    assert response.json()["data_status_url"] == "/api/data/providers"
