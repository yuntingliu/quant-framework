from __future__ import annotations

import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import model_providers
from apps.api.services import model_provider_service as service


def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    monkeypatch.delenv("CONEXUS_MODEL_BASE_URL", raising=False)
    app = FastAPI()
    app.include_router(model_providers.router)
    return TestClient(app)


def profile(**changes):
    return {"id": "gateway", "name": "Gateway", "base_url": "https://provider.example/v1",
            "model": "tool-model", "api_key": "private-test-key", **changes}


def test_profiles_preserve_key_without_returning_it_and_do_not_reuse_it_at_a_new_origin(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    response = api.put("/api/model-providers", json=profile())
    assert response.status_code == 200
    assert "private-test-key" not in response.text
    assert response.json()["profiles"][0]["has_api_key"] is True
    assert api.get("/api/model-providers").json()["active_id"] == "gateway"
    assert api.put("/api/model-providers", json=profile(api_key="", model="another-model")).status_code == 200
    assert service.active_profile()["api_key"] == "private-test-key"
    response = api.put("/api/model-providers", json=profile(api_key="", base_url="https://other.example/v1"))
    assert response.status_code == 422
    assert service.active_profile()["base_url"] == "https://provider.example/v1"
    assert json.loads(service.settings_path().read_text())["version"] == 1


def test_model_selection_provenance_distinguishes_saved_environment_id(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    response = api.put("/api/model-providers", json=profile(id="environment"))
    assert response.status_code == 200 and response.json()["source"] == "saved"
    document = json.loads(service.settings_path().read_text())
    document["active_id"] = None
    service.settings_path().write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("CONEXUS_MODEL_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("CONEXUS_MODEL_ID", "environment-model")
    monkeypatch.setenv("CONEXUS_MODEL_API_KEY", "ui-environment-private-canary-3491")
    response = api.get("/api/model-providers")
    assert response.status_code == 200
    assert response.json()["source"] == "environment"
    assert response.json()["active_id"] == "environment"
    assert response.json()["profiles"][0]["id"] == "environment"
    for private in ("private-test-key", "ui-environment-private-canary-3491", "environment.example"):
        assert private not in response.text
    assert json.loads(service.settings_path().read_text()) == document


def test_provider_settings_are_local_only_and_reject_unsafe_urls(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    response = api.put("/api/model-providers", json=profile(api_key="private-test-key" * 1000))
    assert response.status_code == 422
    assert "private-test-key" not in response.text
    for url in ["http://remote.example/v1", "https://user:secret@example.com", "https://example.com/v1?key=secret"]:
        assert api.put("/api/model-providers", json=profile(base_url=url)).status_code == 422
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "remote")
    assert api.put("/api/model-providers", json=profile()).status_code == 409


def test_connection_test_uses_saved_key_and_sanitizes_provider_errors(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    api.put("/api/model-providers", json=profile())
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer private-test-key"
        return httpx.Response(401, json={"error": "private-test-key and provider-private-information"})
    original = httpx.AsyncClient
    monkeypatch.setattr(model_providers.httpx, "AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    response = api.post("/api/model-providers/test", json=profile(api_key=""))
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "HTTP 401" in response.json()["message"]
    assert "private-test-key" not in response.text
    assert len(seen) == 1


def test_loopback_model_test_accepts_no_key_and_does_not_persist_unsaved_settings(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    original = httpx.AsyncClient
    def handler(request):
        assert "authorization" not in request.headers
        assert str(request.url) == "http://127.0.0.1:12345/v1/chat/completions"
        assert json.loads(request.content)["messages"] == [{"role": "user", "content": "Reply with OK."}]
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    monkeypatch.setattr(model_providers.httpx, "AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    response = api.post("/api/model-providers/test", json=profile(api_key="", base_url="http://127.0.0.1:12345/v1"))
    assert response.json()["ok"] is True
    assert not service.settings_path().exists()


def test_connection_test_respects_explicit_proxy_but_keeps_loopback_direct(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    monkeypatch.setenv("ALPHALAB_MODEL_PROXY", "http://127.0.0.1:12346")
    observed = []
    original = httpx.AsyncClient

    def configured_client(**kwargs):
        observed.append(kwargs.pop("proxy"))
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )), **kwargs)

    monkeypatch.setattr(model_providers.httpx, "AsyncClient", configured_client)
    assert api.post("/api/model-providers/test", json=profile()).json()["ok"] is True
    assert api.post("/api/model-providers/test", json=profile(
        base_url="http://127.0.0.1:12345/v1", api_key="",
    )).json()["ok"] is True
    assert observed == ["http://127.0.0.1:12346", None]
