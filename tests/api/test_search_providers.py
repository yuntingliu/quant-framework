from __future__ import annotations

import json
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import search_providers
from apps.api.services import model_provider_service as models
from apps.api.services import search_provider_service as service


def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    monkeypatch.setenv("ALPHALAB_WEB_SEARCH_PROVIDER", "openrouter")
    monkeypatch.setenv("CONEXUS_WEB_ORIGIN", "http://127.0.0.1:8387")
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "fixture-host-token")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("CONEXUS_MODEL_BASE_URL", raising=False)
    models.save_profile(
        {
            "id": "search-account",
            "name": "Search account",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "test-model",
            "api_key": "fixture-model-key",
        },
        activate=True,
    )
    app = FastAPI()
    app.include_router(search_providers.router)
    return TestClient(app)


def stored_profile(profile_id: str = "default") -> dict:
    return next(row for row in service.read_settings()["profiles"] if row["id"] == profile_id)


def test_search_selection_persists_and_overrides_environment_without_returning_keys(
    monkeypatch, tmp_path
):
    api = client(monkeypatch, tmp_path)
    assert api.get("/api/search-providers").json()["provider"] == "openrouter"
    response = api.put(
        "/api/search-providers", json={"provider": "brave", "brave_api_key": "fixture-brave-key"}
    )
    assert response.status_code == 200
    assert "fixture-brave-key" not in response.text
    assert response.json()["active_id"] == "default"
    assert response.json()["has_brave_api_key"] is True
    assert response.json()["profiles"][0]["id"] == "default"
    assert api.get("/api/search-providers").json()["provider"] == "brave"
    assert api.put("/api/search-providers", json={"provider": "off"}).status_code == 200
    assert api.put("/api/search-providers", json={"provider": "brave"}).status_code == 200
    assert stored_profile()["brave_api_key"] == "fixture-brave-key"
    assert api.get("/api/search-providers").json()["scope"] == "workstation"
    if os.name != "nt":
        assert service.settings_path().stat().st_mode & 0o077 == 0


def test_search_can_use_an_inactive_saved_openrouter_account(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    models.save_profile(
        {
            "id": "local-model",
            "name": "Local",
            "base_url": "http://127.0.0.1:12345/v1",
            "model": "local",
            "api_key": "",
        },
        activate=True,
    )
    assert api.put("/api/search-providers", json={"provider": "openrouter"}).status_code == 422
    response = api.put(
        "/api/search-providers",
        json={"provider": "openrouter", "model_profile_id": "search-account"},
    )
    assert response.status_code == 200
    assert models.active_profile()["id"] == "local-model"
    assert response.json()["model_profile_id"] == "search-account"
    assert "fixture-model-key" not in response.text


def test_invalid_search_settings_are_private_and_remote_mode_cannot_save(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    response = api.put(
        "/api/search-providers",
        json={"provider": "brave", "brave_api_key": "sensitive-test-key" * 1000},
    )
    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == "Check the profile name, provider, model profile, and Brave key length."
    )
    assert "sensitive-test-key" not in response.text
    assert api.put("/api/search-providers", json={"provider": "brave"}).status_code == 422
    assert not service.settings_path().exists()
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "remote")
    assert api.put("/api/search-providers", json={"provider": "off"}).status_code == 409
    assert api.post("/api/search-providers/test", json={"provider": "off"}).status_code == 409


def test_disabled_search_test_sends_nothing_and_does_not_save(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    response = api.post("/api/search-providers/test", json={"provider": "off"})
    assert response.json()["ok"] is True
    assert not service.settings_path().exists()


def test_search_test_uses_authenticated_local_host_and_does_not_persist(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    original = httpx.AsyncClient

    def handler(request):
        assert str(request.url) == "http://127.0.0.1:8387/api/alphalab/search/test"
        assert request.headers["Authorization"] == "Bearer fixture-host-token"
        payload = json.loads(request.content)
        assert payload == {"provider": "openrouter", "model_profile_id": "search-account"}
        return httpx.Response(
            200,
            json={
                "result": {
                    "success": True,
                    "results": [
                        {"url": "javascript:bad", "title": "Rejected"},
                        {"url": "https://example.com/source", "title": "Source"},
                    ],
                }
            },
        )

    monkeypatch.setattr(
        search_providers.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    response = api.post(
        "/api/search-providers/test",
        json={"provider": "openrouter", "model_profile_id": "search-account"},
    )
    assert response.json() == {
        "ok": True,
        "message": "Search responded with source links.",
        "results": [{"title": "Source", "url": "https://example.com/source"}],
    }
    assert not service.settings_path().exists()


def test_search_test_does_not_forward_credentials_to_a_remote_host(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    monkeypatch.setenv("CONEXUS_WEB_ORIGIN", "https://remote.example")
    assert (
        api.post(
            "/api/search-providers/test",
            json={"provider": "brave", "brave_api_key": "fixture-brave-key"},
        ).status_code
        == 503
    )


def test_search_test_never_echoes_host_or_provider_error_bodies(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        search_providers.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"result": {"success": False, "error": "fixture-model-key"}}
                ),
            ),
            **kwargs,
        ),
    )
    response = api.post("/api/search-providers/test", json={"provider": "openrouter"})
    assert response.json()["ok"] is False
    assert "fixture-model-key" not in response.text


@pytest.mark.parametrize("identity", [{}, {"id": None, "name": None}])
def test_existing_ui_payloads_and_null_names_keep_active_profile_identity(
    monkeypatch, tmp_path, identity
):
    api = client(monkeypatch, tmp_path)
    payload = {"provider": "brave", "model_profile_id": None, "brave_api_key": "ui-key", **identity}
    created = api.put("/api/search-providers", json=payload)
    assert created.status_code == 200
    assert created.json()["profiles"][0]["name"] == "Default"
    named = {"id": "named", "name": "Named search", "provider": "off"}
    assert api.put("/api/search-providers", json=named).status_code == 200
    updated = api.put(
        "/api/search-providers", json={"provider": "off", "brave_api_key": "", **identity}
    )
    assert updated.status_code == 200
    assert updated.json()["active_id"] == "named"
    assert stored_profile("named")["name"] == "Named search"
    assert {
        "provider",
        "model_profile_id",
        "has_brave_api_key",
        "configured",
    } <= updated.json().keys()


def test_named_updates_preserve_omitted_model_reference_and_explicit_null_follows_active_model(
    monkeypatch, tmp_path
):
    api = client(monkeypatch, tmp_path)
    payload = {
        "id": "named",
        "name": "Named",
        "provider": "openrouter",
        "model_profile_id": "search-account",
    }
    assert api.put("/api/search-providers", json=payload).status_code == 200
    response = api.put(
        "/api/search-providers", json={"id": "named", "name": None, "provider": "openrouter"}
    )
    assert response.status_code == 200
    assert response.json()["model_profile_id"] == "search-account"
    response = api.put(
        "/api/search-providers", json={"provider": "openrouter", "model_profile_id": None}
    )
    assert response.status_code == 200
    assert response.json()["model_profile_id"] is None


@pytest.mark.parametrize("replacement", ["", "replacement-key"])
def test_key_clear_is_respected_by_unsaved_connection_tests(monkeypatch, tmp_path, replacement):
    api = client(monkeypatch, tmp_path)
    assert (
        api.put(
            "/api/search-providers", json={"provider": "brave", "brave_api_key": "stored-key"}
        ).status_code
        == 200
    )
    before = service.settings_path().read_bytes()

    def unexpected_request(**kwargs):
        pytest.fail("A cleared or conflicting key must not reach the local host")

    monkeypatch.setattr(search_providers.httpx, "AsyncClient", unexpected_request)
    response = api.post(
        "/api/search-providers/test",
        json={"provider": "brave", "brave_api_key": replacement, "clear_brave_api_key": True},
    )
    assert response.status_code == 422
    assert "stored-key" not in response.text
    assert service.settings_path().read_bytes() == before
    response = api.put(
        "/api/search-providers", json={"provider": "brave", "clear_brave_api_key": True}
    )
    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_brave_probe_uses_only_the_named_profiles_key_without_saving(monkeypatch, tmp_path, caplog):
    api = client(monkeypatch, tmp_path)
    for identifier in ("a", "b"):
        assert (
            api.put(
                "/api/search-providers",
                json={
                    "id": identifier,
                    "name": identifier,
                    "provider": "brave",
                    "brave_api_key": identifier + "-private-key",
                },
            ).status_code
            == 200
        )
    before = service.settings_path().read_bytes()
    original = httpx.AsyncClient

    def handler(request):
        assert json.loads(request.content) == {
            "provider": "brave",
            "brave_api_key": "a-private-key",
        }
        return httpx.Response(200, json={"result": {"success": False, "error": "a-private-key"}})

    monkeypatch.setattr(
        search_providers.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    response = api.post("/api/search-providers/test", json={"id": "a", "provider": "brave"})
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "a-private-key" not in response.text + caplog.text
    assert "b-private-key" not in response.text + caplog.text
    assert service.settings_path().read_bytes() == before


def test_invalid_settings_file_is_sanitized_for_all_api_operations(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    path = service.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 999, "brave_api_key": "hidden-key"}', encoding="utf-8")
    before = path.read_bytes()
    responses = [
        api.get("/api/search-providers"),
        api.put("/api/search-providers", json={"provider": "off"}),
        api.post("/api/search-providers/test", json={"provider": "off"}),
    ]
    for response, expected_status in zip(responses, [503, 422, 422]):
        assert response.status_code == expected_status
        assert "hidden-key" not in response.text
    assert path.read_bytes() == before


def environment_brave_client(monkeypatch, tmp_path):
    api = client(monkeypatch, tmp_path)
    monkeypatch.setenv("ALPHALAB_WEB_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fixture-environment-brave-key")
    return api


@pytest.mark.parametrize("identity", [{}, {"id": None, "name": None}])
@pytest.mark.parametrize("key_input", [{}, {"brave_api_key": ""}, {"brave_api_key": "  "}])
def test_environment_brave_legacy_save_and_test_preserve_the_effective_key(
    monkeypatch, tmp_path, caplog, identity, key_input
):
    api = environment_brave_client(monkeypatch, tmp_path)
    public = api.get("/api/search-providers")
    assert public.json()["configured"] is True
    assert public.json()["has_brave_api_key"] is True
    assert public.json()["source"] == "environment"
    assert not service.settings_path().exists()
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        assert str(request.url) == "http://127.0.0.1:8387/api/alphalab/search/test"
        payload = json.loads(request.content)
        assert payload == {"provider": "brave", "brave_api_key": "fixture-environment-brave-key"}
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "result": {
                    "success": True,
                    "results": [
                        {"title": "Source", "url": "https://example.com/source"},
                    ],
                }
            },
        )

    monkeypatch.setattr(
        search_providers.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    payload = {"provider": "brave", "model_profile_id": None, **identity, **key_input}
    probe = api.post("/api/search-providers/test", json=payload)
    assert probe.status_code == 200
    assert probe.json()["ok"] is True
    assert not service.settings_path().exists()
    saved = api.put("/api/search-providers", json=payload)
    assert saved.status_code == 200
    assert saved.json()["active_id"] == "default"
    assert saved.json()["configured"] is True
    assert saved.json()["source"] == "saved"
    assert stored_profile()["brave_api_key"] == "fixture-environment-brave-key"

    # Once saved, the profile owns its key; a later environment change cannot replace it.
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fixture-rotated-environment-key")
    before = service.settings_path().read_bytes()
    repeated = api.post("/api/search-providers/test", json=payload)
    assert repeated.status_code == 200
    assert repeated.json()["ok"] is True
    assert service.settings_path().read_bytes() == before
    assert len(calls) == 2
    visible = public.text + probe.text + saved.text + repeated.text + caplog.text
    assert "fixture-environment-brave-key" not in visible
    assert "fixture-rotated-environment-key" not in visible


@pytest.mark.parametrize(
    "identity",
    [
        {"id": "unrelated", "name": "Unrelated"},
        {"id": "default", "name": "Default"},
        {"id": "environment", "name": "Environment"},
        {"name": "Unrelated"},
    ],
)
@pytest.mark.parametrize(
    "method,route", [("PUT", "/api/search-providers"), ("POST", "/api/search-providers/test")]
)
def test_environment_key_is_never_inherited_by_new_named_requests(
    monkeypatch, tmp_path, caplog, identity, method, route
):
    api = environment_brave_client(monkeypatch, tmp_path)

    def unexpected_request(**kwargs):
        pytest.fail("A new named profile cannot use the environment key")

    monkeypatch.setattr(search_providers.httpx, "AsyncClient", unexpected_request)
    response = api.request(
        method, route, json={"provider": "brave", "brave_api_key": "", **identity}
    )
    assert response.status_code == 422
    assert not service.settings_path().exists()
    assert api.get("/api/search-providers").json()["source"] == "environment"
    assert "fixture-environment-brave-key" not in response.text + caplog.text


def test_environment_legacy_clear_overrides_fallback_and_replacement_is_explicit(
    monkeypatch, tmp_path, caplog
):
    api = environment_brave_client(monkeypatch, tmp_path)

    def unexpected_request(**kwargs):
        pytest.fail("A cleared key must never reach the local host")

    monkeypatch.setattr(search_providers.httpx, "AsyncClient", unexpected_request)
    clear = {"provider": "brave", "brave_api_key": "", "clear_brave_api_key": True}
    rejected = api.post("/api/search-providers/test", json=clear)
    assert rejected.status_code == 422
    assert not service.settings_path().exists()
    conflict = api.put(
        "/api/search-providers", json={**clear, "brave_api_key": "fixture-replacement-key"}
    )
    assert conflict.status_code == 422
    assert not service.settings_path().exists()
    cleared = api.put("/api/search-providers", json=clear)
    assert cleared.status_code == 200
    assert cleared.json()["active_id"] == "default"
    assert cleared.json()["configured"] is False
    assert cleared.json()["has_brave_api_key"] is False
    assert stored_profile()["brave_api_key"] == ""
    assert os.environ["BRAVE_SEARCH_API_KEY"] == "fixture-environment-brave-key"
    before = service.settings_path().read_bytes()
    blank = {"provider": "brave", "brave_api_key": ""}
    for method, route in [("PUT", "/api/search-providers"), ("POST", "/api/search-providers/test")]:
        response = api.request(method, route, json=blank)
        assert response.status_code == 422
    assert service.settings_path().read_bytes() == before
    assert api.get("/api/search-providers").json()["source"] == "saved"
    replacement = api.put(
        "/api/search-providers", json={**blank, "brave_api_key": "fixture-replacement-key"}
    )
    assert replacement.status_code == 200
    assert api.put("/api/search-providers", json=blank).status_code == 200
    assert stored_profile()["brave_api_key"] == "fixture-replacement-key"
    visible = rejected.text + conflict.text + cleared.text + replacement.text + caplog.text
    assert "fixture-environment-brave-key" not in visible
    assert "fixture-replacement-key" not in visible


def test_saved_named_profile_keeps_its_secret_even_for_legacy_requests_with_environment_key(
    monkeypatch, tmp_path
):
    api = environment_brave_client(monkeypatch, tmp_path)
    named = {
        "id": "named",
        "name": "Named",
        "provider": "brave",
        "brave_api_key": "fixture-named-key",
    }
    assert api.put("/api/search-providers", json=named).status_code == 200
    blank = {"provider": "brave", "brave_api_key": ""}
    assert api.put("/api/search-providers", json=blank).status_code == 200
    assert stored_profile("named")["brave_api_key"] == "fixture-named-key"
    assert (
        api.put("/api/search-providers", json={**blank, "clear_brave_api_key": True}).status_code
        == 200
    )
    assert api.put("/api/search-providers", json=blank).status_code == 422
    assert api.post("/api/search-providers/test", json=blank).status_code == 422
    assert stored_profile("named")["brave_api_key"] == ""
    assert {row["id"] for row in service.read_settings()["profiles"]} == {"named"}


def test_legacy_environment_off_then_brave_preserves_the_key(monkeypatch, tmp_path):
    api = environment_brave_client(monkeypatch, tmp_path)
    assert (
        api.put("/api/search-providers", json={"provider": "off", "brave_api_key": ""}).status_code
        == 200
    )
    assert stored_profile()["brave_api_key"] == "fixture-environment-brave-key"
    resumed = api.put("/api/search-providers", json={"provider": "brave", "brave_api_key": ""})
    assert resumed.status_code == 200
    assert resumed.json()["configured"] is True


def test_environment_probe_provider_errors_do_not_expose_credentials(monkeypatch, tmp_path, caplog):
    api = environment_brave_client(monkeypatch, tmp_path)
    original = httpx.AsyncClient

    def handler(request):
        assert json.loads(request.content)["brave_api_key"] == "fixture-environment-brave-key"
        raise httpx.ConnectError("fixture-environment-brave-key", request=request)

    monkeypatch.setattr(
        search_providers.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    response = api.post(
        "/api/search-providers/test", json={"provider": "brave", "brave_api_key": ""}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "fixture-environment-brave-key" not in response.text + caplog.text
    assert not service.settings_path().exists()


def test_failed_environment_save_is_private_and_leaves_environment_effective(
    monkeypatch, tmp_path, caplog
):
    api = environment_brave_client(monkeypatch, tmp_path)

    def fail_write(*args, **kwargs):
        raise OSError("fixture-environment-brave-key")

    with monkeypatch.context() as failure:
        failure.setattr(service, "write_json_settings", fail_write)
        response = api.put("/api/search-providers", json={"provider": "brave", "brave_api_key": ""})
    assert response.status_code == 503
    assert "fixture-environment-brave-key" not in response.text + caplog.text
    assert not service.settings_path().exists()
    assert api.get("/api/search-providers").json()["configured"] is True
    assert (
        api.put(
            "/api/search-providers", json={"provider": "brave", "brave_api_key": ""}
        ).status_code
        == 200
    )
