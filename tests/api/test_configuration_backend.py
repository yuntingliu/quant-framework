"""Explicit choices over real temporary stores; providers are test doubles only."""

from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from alphalab.dataio import rq_profiles
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_maintenance import configuration_access
from alphalab.utils.configuration_store import ConfigurationStore, ConfigurationStoreError
from apps.api.configuration_app import create_configuration_app
from apps.api.services import model_provider_service as models
from apps.api.services import search_provider_service as search
from apps.api.services.configuration_binding_service import ModelSearchBindings

KEYS = ("synthetic-backend-model-8101", "synthetic-backend-brave-8102", "synthetic-backend-rq-8103")
REPO = Path(__file__).resolve().parents[2]


def initialize(root):
    documents = {
        "models": {
            "version": 1,
            "active_id": "model",
            "profiles": [
                {
                    "id": "model",
                    "name": "Model",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "provider/model",
                    "api_key": KEYS[0],
                }
            ],
        },
        "search": {
            "version": 2,
            "active_id": "search",
            "profiles": [
                {
                    "id": "search",
                    "name": "Search",
                    "provider": "brave",
                    "model_profile_id": "model",
                    "brave_api_key": KEYS[1],
                }
            ],
        },
        "rqdata": {
            "version": 1,
            "active_id": "rq",
            "profiles": [
                {
                    "id": "rq",
                    "name": "RQ",
                    "provider": "rqdata",
                    "user": "fixture-user",
                    "host": "rq.example:16011",
                    "password": KEYS[2],
                }
            ],
        },
    }
    return ConfigurationStore.initialize(
        root,
        documents=documents,
        selections={
            key: {"kind": "profile", "id": doc["active_id"]} for key, doc in documents.items()
        },
    )


def no_secrets(value):
    for key in KEYS:
        assert key not in str(value)


def expect_error(action, code):
    with pytest.raises(ConfigurationStoreError) as caught:
        action()
    assert caught.value.code == code
    no_secrets(repr(caught.value))
    assert caught.value.__cause__ is None


@pytest.fixture
def configured(tmp_path, monkeypatch):
    root = tmp_path / "installation Ω"
    store = initialize(root)
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))
    return root, store


def test_default_json_ignores_sqlite_and_backend_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALAB_CONFIGURATION_BACKEND", "sqlite")
    monkeypatch.setattr(
        ConfigurationStore, "open_existing", lambda *_: pytest.fail("SQLite read in JSON mode")
    )
    with ConfigurationBackend.json(tmp_path) as backend:
        api = create_configuration_app(backend)
        with TestClient(api) as client:
            assert client.get("/api/model-providers").json()["profiles"] == []
            assert client.get("/api/search-providers").status_code == 200
        assert backend.mode == "json"
    assert not (tmp_path / "app/secrets/configuration-store.sqlite3").exists()


def test_explicit_json_roots_are_isolated_without_environment_mutation(tmp_path):
    before = dict(os.environ)
    with (
        ConfigurationBackend.json(tmp_path / "one") as one,
        ConfigurationBackend.json(tmp_path / "two") as two,
    ):
        first, second = ModelSearchBindings(one), ModelSearchBindings(two)
        first.models.save_profile(
            {
                "id": "a",
                "name": "A",
                "base_url": "http://127.0.0.1:8080/v1",
                "model": "local/model",
                "api_key": "",
            },
            activate=True,
        )
        assert second.models.public_settings()["profiles"] == []
        assert first.models.public_settings()["active_id"] == "a"
    assert dict(os.environ) == before


@pytest.mark.parametrize("case", ["missing", "corrupt", "identity"])
def test_explicit_sqlite_fails_closed_before_api_construction(tmp_path, case):
    root = tmp_path / case
    identity = "a" * 32
    if case != "missing":
        store = initialize(root)
        if case == "corrupt":
            (root / "app/secrets/configuration-store.sqlite3").write_bytes(b"not sqlite")
        else:
            assert store.read_snapshot().installation_id != identity
    with pytest.raises(ConfigurationStoreError) as caught:
        ConfigurationBackend.sqlite(root, installation_id=identity)
    no_secrets(caught.value)
    with configuration_access(root, exclusive=True):
        pass  # Constructor failure released its maintenance lease.


def test_single_owner_fences_all_legacy_reads_writes_and_maintenance(configured):
    root, store = configured
    before = store.read_snapshot()
    with ConfigurationBackend.sqlite(root, installation_id=before.installation_id) as backend:
        binding = ModelSearchBindings(backend)
        for action in (
            lambda: ConfigurationBackend.json(root),
            lambda: ConfigurationBackend.sqlite(root, installation_id=before.installation_id),
            models.read_settings,
            search.read_settings,
            rq_profiles.read_settings,
            lambda: models.save_profile({}, activate=True),
            lambda: search.save_profile({}, activate=True),
        ):
            expect_error(action, "STORE_BUSY")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(lambda: ConfigurationBackend.json(root)) for _ in range(2)]
            for future in futures:
                expect_error(future.result, "STORE_BUSY")
        with backend.borrow():
            expect_error(backend.close, "STORE_BUSY")
        assert binding.models.public_settings()["revision"] == before.revision
    assert store.read_snapshot().revision == before.revision
    assert not (root / "conexus/secrets/model-providers.json").exists()


def test_cross_process_startup_is_excluded_and_release_is_observable(configured):
    root, store = configured
    script = """
import sys
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStoreError
try:
    with ConfigurationBackend.json(sys.argv[1]): pass
except ConfigurationStoreError as error:
    print(error.code)
else: print('OPEN')
"""

    def probe():
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(root)],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        return result.stdout.strip()

    with ConfigurationBackend.sqlite(root, installation_id=store.read_snapshot().installation_id):
        assert probe() == "STORE_BUSY"
    assert probe() == "OPEN"


def test_sqlite_api_edits_cas_clear_and_no_json_access(configured, monkeypatch):
    root, store = configured
    for legacy in (models, search):
        for name in ("read_settings", "public_settings", "save_profile"):
            monkeypatch.setattr(
                legacy, name, lambda *a, **kw: pytest.fail("Legacy backend accessed")
            )
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as backend:
        with TestClient(create_configuration_app(backend)) as client:
            model = client.get("/api/model-providers").json()
            response = client.put(
                "/api/model-providers",
                json={
                    "id": "model",
                    "name": "Renamed",
                    "model": "provider/model",
                    "base_url": "https://openrouter.ai/api/v1",
                    "expected_revision": model["revision"],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["revision"] != model["revision"]
            no_secrets(response.text)
            current = store.read_snapshot()
            stale = client.put(
                "/api/search-providers",
                json={"id": "search", "provider": "off", "expected_revision": model["revision"]},
            )
            assert stale.status_code == 409, stale.text
            assert store.read_snapshot().revision == current.revision
            assert client.get("/api/data-source-profiles").status_code == 404
            assert (
                client.put(
                    "/api/search-providers",
                    json={"id": "search", "provider": "brave", "clear_brave_api_key": True},
                ).status_code
                == 200
            )
            assert store.read_snapshot().documents["search"]["profiles"][0]["brave_api_key"] == ""
            assert client.get("/api/search-providers").json()["configured"] is False
    assert not (root / "conexus/secrets/model-providers.json").exists()
    assert not (root / "conexus/secrets/search-providers.json").exists()


@pytest.mark.parametrize("service", ["models", "search"])
@pytest.mark.parametrize("kind", ["none", "environment", "profile"])
def test_api_selection_intents_persist_and_never_inherit_when_none(configured, service, kind):
    root, store = configured
    environment = {
        "CONEXUS_MODEL_BASE_URL": "https://openrouter.ai/api/v1",
        "CONEXUS_MODEL_ID": "env/model",
        "CONEXUS_MODEL_API_KEY": "synthetic-environment-key",
        "BRAVE_SEARCH_API_KEY": "synthetic-environment-brave",
    }
    choice = {"kind": kind}
    if kind == "profile":
        choice["id"] = "model" if service == "models" else "search"
    path = "/api/model-providers" if service == "models" else "/api/search-providers"
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as backend:
        with TestClient(create_configuration_app(backend, environment=environment)) as client:
            response = client.put(path + "/selection", json=choice)
            assert response.status_code == 200, response.text
            assert response.json()["selection"] == choice
            assert response.json()["configured"] is (kind != "none")
            no_secrets(response.text)
    reopened = ConfigurationStore.open_existing(root).read_snapshot()
    assert reopened.selections[service] == choice


def test_invalid_reference_and_metadata_leave_complete_snapshot_unchanged(configured):
    root, store = configured
    before = store.read_snapshot()
    with ConfigurationBackend.sqlite(root, installation_id=before.installation_id) as backend:
        with TestClient(create_configuration_app(backend)) as client:
            for body in (
                {"id": "search", "provider": "openrouter", "model_profile_id": "absent"},
                {"id": "search", "provider": "brave", "name": KEYS[0]},
            ):
                response = client.put("/api/search-providers", json=body)
                assert response.status_code == 422, response.text
                no_secrets(response.text)
                after = store.read_snapshot()
                assert (after.revision, after.documents, after.selections) == (
                    before.revision,
                    before.documents,
                    before.selections,
                )


def test_search_probe_requires_same_owned_agent_and_never_falls_back(configured, monkeypatch):
    root, store = configured
    monkeypatch.setenv("CONEXUS_WEB_ORIGIN", "http://127.0.0.1:9123")
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "synthetic-transport-token")
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as backend:
        with TestClient(create_configuration_app(backend)) as client:
            response = client.post(
                "/api/search-providers/test", json={"id": "search", "provider": "brave"}
            )
            assert response.status_code == 503
        sent = []

        async def fake_post(self, url, **kwargs):
            sent.append("unexpected private request")
            return httpx.Response(
                200,
                json={
                    "result": {
                        "success": True,
                        "results": [
                            {"title": "Synthetic source", "url": "https://source.example/article"}
                        ],
                    }
                },
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        before = store.read_snapshot().revision
        with TestClient(
            create_configuration_app(backend, agent_identity=backend.public_identity())
        ) as client:
            response = client.post(
                "/api/search-providers/test", json={"id": "search", "provider": "brave"}
            )
            assert response.status_code == 503
            no_secrets(response.text)
        assert sent == []
        assert store.read_snapshot().revision == before
        expect_error(
            lambda: create_configuration_app(backend, agent_identity={}), "INSTALLATION_MISMATCH"
        )


@pytest.fixture(scope="module")
def backend_node():
    candidate = os.environ.get("ALPHALAB_TEST_NODE") or shutil.which("node")
    if not candidate:
        pytest.skip("Node 22.18+ is needed for the genuine SQLite composition regression.")
    version = subprocess.check_output([candidate, "--version"], text=True, timeout=10).strip()
    major, minor, *_ = version.lstrip("v").split(".")
    if int(major) != 22 or int(minor) < 18:
        pytest.skip("The SQLite reader requires Node 22.18+ within major 22.")
    return candidate


@contextmanager
def genuine_node_configuration(owner, node):
    """Keep a genuine test-owned Node composition alive until the API has stopped.

    This private pipe proves the composition's identity, not its association with
    an HTTP endpoint. No authenticated SQLite Host contract currently exists.
    """
    module = (REPO / "integrations/conexus/configuration-backend.mjs").as_uri()
    script = """
import {createInterface} from 'node:readline';
const lines=createInterface({input:process.stdin}), input=lines[Symbol.asyncIterator]();
const contract=JSON.parse((await input.next()).value);
const {createCoordinatedAgentConfiguration}=await import(MODULE);
const runtime=await createCoordinatedAgentConfiguration(contract,
  ()=>async()=>({content:'Synthetic completion'}),
  {env:{},fetchImpl:async()=>{throw new Error('Provider access forbidden in fixture')}});
try {
  console.log(JSON.stringify(runtime.identity));
  await input.next();
} finally {
  await runtime.close(); lines.close(); process.stdin.destroy();
}
""".replace("MODULE", json.dumps(module))
    with owner.borrow():
        child = subprocess.Popen(
            [node, "--input-type=module", "-e", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        inbox = queue.Queue()
        reader = threading.Thread(target=lambda: inbox.put(child.stdout.readline()), daemon=True)
        try:
            reader.start()
            child.stdin.write(json.dumps(owner.node_contract()) + "\n")
            child.stdin.flush()
            identity = json.loads(inbox.get(timeout=15))
            owner.verify_agent_identity(identity)
            assert child.poll() is None
            yield identity, child
        finally:
            if child.poll() is None:
                try:
                    child.stdin.write("{}\n")
                    child.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
            child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()  # Only the process created by this fixture.
                child.wait(timeout=5)
            reader.join(timeout=2)
            assert not reader.is_alive()
            no_secrets(child.stderr.read())
            child.stdout.close()
            child.stderr.close()
            assert child.returncode == 0


@pytest.mark.parametrize("drift", ["unchanged", "origin", "token", "both", "concurrent"])
def test_sqlite_cross_agent_disclosure_fails_closed_with_genuine_node_identity(
    configured, backend_node, monkeypatch, caplog, capsys, drift
):
    root, store = configured
    origin_a, origin_b = "http://127.0.0.1:19401", "http://127.0.0.1:19402"
    token_a, token_b = "synthetic-agent-a-token", "synthetic-agent-b-token"
    monkeypatch.setenv("CONEXUS_WEB_ORIGIN", origin_a)
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", token_a)
    original = httpx.AsyncClient
    sent = []
    lock = threading.Lock()

    def separate_mock_agents(request):
        # Capture booleans only, so a deliberately failing negative control
        # cannot print credential-bearing request objects or bodies.
        body = json.loads(request.content)
        with lock:
            sent.append(
                {
                    "port": urlsplit(str(request.url)).port,
                    "has_a_key": body.get("brave_api_key") == KEYS[1],
                }
            )
        return httpx.Response(
            200,
            json={
                "result": {
                    "success": True,
                    "results": [
                        {"title": "Synthetic source", "url": "https://source.example/article"}
                    ],
                }
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(separate_mock_agents), **kw),
    )
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        with genuine_node_configuration(owner, backend_node) as (identity, child):
            app = create_configuration_app(owner, agent_identity=identity)
            before = store.read_snapshot()
            if drift in {"origin", "both", "concurrent"}:
                monkeypatch.setenv("CONEXUS_WEB_ORIGIN", origin_b)
            if drift in {"token", "both", "concurrent"}:
                monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", token_b)
            with TestClient(app) as client:

                def request(index):
                    if drift == "concurrent":
                        os.environ["CONEXUS_WEB_ORIGIN"] = origin_a if index % 2 else origin_b
                        os.environ["CONEXUS_PUBLICATION_WORKSPACE_TOKEN"] = (
                            token_a if index % 2 else token_b
                        )
                    response = client.post(
                        "/api/search-providers/test", json={"id": "search", "provider": "brave"}
                    )
                    no_secrets(response.text)
                    return response.status_code

                if drift == "concurrent":
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        statuses = list(pool.map(request, range(8)))
                else:
                    statuses = [request(0)]
                assert statuses == [503] * len(statuses), (
                    f"Unverified destination was accepted; dispatches={sent}"
                )
                assert sent == [], "A private request reached an unverified Agent"
                assert child.poll() is None
            after = store.read_snapshot()
            assert (after.revision, after.documents, after.selections) == (
                before.revision,
                before.documents,
                before.selections,
            )
    captured = capsys.readouterr()
    no_secrets(caplog.text + captured.out + captured.err)
    for token in (token_a, token_b):
        assert token not in caplog.text + captured.out + captured.err


def test_sqlite_search_gate_precedes_secret_resolution_and_ambient_lookup(configured, monkeypatch):
    from apps.api.services import agent_run_service

    root, store = configured
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        app = create_configuration_app(owner, agent_identity=owner.public_identity())
        calls = []

        def private_resolution(*args, **kwargs):
            calls.append("private resolution")
            raise AssertionError("Private credentials must not be extracted")

        def ambient_origin():
            calls.append("ambient connection")
            raise AssertionError("Ambient connection must not be inspected")

        monkeypatch.setattr(
            type(app.state.configuration_bindings.search),
            "resolve_test_profile",
            private_resolution,
        )
        monkeypatch.setattr(agent_run_service, "web_origin", ambient_origin)
        with TestClient(app) as client:
            response = client.post(
                "/api/search-providers/test", json={"id": "search", "provider": "brave"}
            )
            assert response.status_code == 503
            no_secrets(response.text)
        assert calls == []


@pytest.mark.parametrize("case", ["foreign", "wrong-backend", "raw-connection", "wrong-decision"])
def test_unverified_or_mismatched_connections_cannot_be_admitted(configured, case):
    root, store = configured
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        value = owner.public_identity()
        if case == "foreign":
            value["installation_id"] = "b" * 32
        elif case == "wrong-backend":
            value["backend"] = "json"
        elif case == "wrong-decision":
            value["decision_id"] = "c" * 32
        else:
            value.update(origin="http://127.0.0.1:19402", token=KEYS[1], pid=12345, slug="fixture")
        expect_error(
            lambda: create_configuration_app(owner, agent_identity=value), "INSTALLATION_MISMATCH"
        )


def test_owner_closed_before_search_request_cannot_release_credentials(configured, monkeypatch):
    root, store = configured
    owner = ConfigurationBackend.sqlite(root, installation_id=store.read_snapshot().installation_id)
    app = create_configuration_app(owner, agent_identity=owner.public_identity())
    owner.close()
    monkeypatch.setattr(
        httpx.AsyncClient, "post", lambda *a, **kw: pytest.fail("Closed owner sent a request")
    )
    # Do not start a new ASGI lifetime for a closed owner; also verify requests
    # cannot bypass the operation-level lifetime check when lifespan is omitted.
    client = TestClient(app)
    try:
        response = client.post(
            "/api/search-providers/test", json={"id": "search", "provider": "brave"}
        )
        assert response.status_code == 503
        no_secrets(response.text)
    finally:
        client.close()


def test_retired_node_identity_never_authorizes_a_replacement_endpoint(
    configured, backend_node, monkeypatch
):
    root, store = configured
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        with genuine_node_configuration(owner, backend_node) as (identity, child):
            app = create_configuration_app(owner, agent_identity=identity)
        assert child.poll() == 0
        monkeypatch.setenv("CONEXUS_WEB_ORIGIN", "http://127.0.0.1:19402")
        monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "synthetic-replacement-token")
        sent = []

        async def post(*args, **kwargs):
            sent.append("unexpected private dispatch")
            return httpx.Response(200, json={})

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        with TestClient(app) as client:
            response = client.post(
                "/api/search-providers/test", json={"id": "search", "provider": "brave"}
            )
            assert response.status_code == 503
        assert sent == []


def test_sqlite_off_test_is_local_and_preserves_all_settings(configured, monkeypatch):
    root, store = configured
    before = store.read_snapshot()
    monkeypatch.setattr(
        httpx.AsyncClient, "post", lambda *a, **kw: pytest.fail("Off sent a request")
    )
    with ConfigurationBackend.sqlite(root, installation_id=before.installation_id) as owner:
        with TestClient(create_configuration_app(owner)) as client:
            response = client.post(
                "/api/search-providers/test", json={"id": "search", "provider": "off"}
            )
            assert response.status_code == 200 and response.json()["ok"] is True
            no_secrets(response.text)
    after = store.read_snapshot()
    assert (after.revision, after.documents, after.selections) == (
        before.revision,
        before.documents,
        before.selections,
    )


def test_production_api_reuses_launcher_json_choice_and_refuses_sqlite(configured, monkeypatch):
    from apps.api import main

    root, store = configured
    monkeypatch.setattr(main, "runtime_root", lambda: root)

    async def start(owner):
        main.app.state.configuration_backend = owner
        try:
            async with main.configuration_lifespan(main.app):
                assert main.app.state.configuration_bindings.backend is owner
        finally:
            del main.app.state.configuration_backend

    with ConfigurationBackend.json(root) as owner:
        asyncio.run(start(owner))
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        with pytest.raises(RuntimeError, match="coordinated JSON"):
            asyncio.run(start(owner))


def test_model_probe_uses_sqlite_secret_without_public_or_persistent_leakage(
    configured, monkeypatch
):
    root, store = configured
    original = httpx.AsyncClient
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer " + KEYS[0]
        return httpx.Response(401, json={"error": KEYS[0]})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    before = store.read_snapshot()
    with ConfigurationBackend.sqlite(root, installation_id=before.installation_id) as owner:
        with TestClient(create_configuration_app(owner)) as client:
            response = client.post(
                "/api/model-providers/test",
                json={
                    "id": "model",
                    "name": "Model",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "provider/model",
                    "api_key": "",
                },
            )
            assert response.status_code == 200
            assert response.json()["ok"] is False
            assert "HTTP 401" in response.json()["message"]
            no_secrets(response.text)
    assert len(seen) == 1
    assert store.read_snapshot().revision == before.revision


def test_two_simultaneous_writers_cannot_overwrite_each_other(configured):
    root, store = configured
    before = store.read_snapshot()
    with ConfigurationBackend.sqlite(root, installation_id=before.installation_id) as owner:
        binding = ModelSearchBindings(owner)

        def save(name):
            try:
                binding.models.save_profile(
                    {
                        "id": "model",
                        "name": name,
                        "base_url": "https://openrouter.ai/api/v1",
                        "model": "provider/model",
                    },
                    activate=True,
                    expected_revision=before.revision,
                )
                return name
            except ConfigurationStoreError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ["First", "Second"]))
        assert results.count("REVISION_CONFLICT") == 1
        after = store.read_snapshot()
        assert after.revision != before.revision
        assert after.documents["models"]["profiles"][0]["name"] in results
        assert after.documents["search"] == before.documents["search"]
        assert after.documents["rqdata"] == before.documents["rqdata"]


def test_broken_store_after_startup_cannot_fall_back_to_json(configured):
    root, store = configured
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        with TestClient(create_configuration_app(owner)) as client:
            database = root / "app/secrets/configuration-store.sqlite3"
            database.write_bytes(b"broken")
            for path in ("/api/model-providers", "/api/search-providers"):
                response = client.get(path)
                assert response.status_code == 503
                no_secrets(response.text)
            assert database.read_bytes() == b"broken"
    assert not (root / "conexus/secrets/model-providers.json").exists()


def test_owner_cleanup_and_identity_validation(configured):
    root, store = configured
    owner = ConfigurationBackend.sqlite(root, installation_id=store.read_snapshot().installation_id)
    with ThreadPoolExecutor(max_workers=1) as pool:
        expect_error(lambda: pool.submit(owner.close).result(), "INVALID_ARGUMENT")
    owner.verify_agent_identity(owner.public_identity())
    expect_error(lambda: owner.verify_agent_identity({"backend": "json"}), "INSTALLATION_MISMATCH")
    owner.close()
    owner.close()
    expect_error(lambda: owner.node_contract(), "STORE_UNAVAILABLE")
    with ConfigurationBackend.json(root):
        pass


def test_native_incomplete_profiles_and_private_extensions_keep_existing_semantics(configured):
    root, store = configured

    def patch(current):
        documents = current.documents
        documents["models"]["profiles"][0].update(base_url="", model="", api_key="")
        documents["models"]["private_extension"] = {"preserve": True}
        return documents, current.selections

    before = store.update(patch)
    with ConfigurationBackend.sqlite(root, installation_id=before.installation_id) as owner:
        with TestClient(
            create_configuration_app(
                owner,
                environment={
                    "CONEXUS_MODEL_API_KEY": "synthetic-env",
                    "CONEXUS_MODEL_ID": "env/model",
                    "CONEXUS_MODEL_BASE_URL": "https://openrouter.ai/api/v1",
                },
            )
        ) as client:
            response = client.get("/api/model-providers")
            assert response.status_code == 200
            assert response.json()["configured"] is False
            assert response.json()["selection"] == {"kind": "profile", "id": "model"}
            assert "private_extension" not in response.text
    after = store.read_snapshot()
    assert after.revision == before.revision and after.documents == before.documents


def test_model_api_replacement_and_clear_cannot_borrow_environment_credentials(configured):
    root, store = configured
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        with TestClient(
            create_configuration_app(
                owner,
                environment={
                    "CONEXUS_MODEL_API_KEY": "synthetic-environment",
                    "CONEXUS_MODEL_ID": "env/model",
                    "CONEXUS_MODEL_BASE_URL": "https://openrouter.ai/api/v1",
                },
            )
        ) as client:
            body = {
                "id": "model",
                "name": "Model",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "provider/model",
                "api_key": "synthetic-replaced-key",
            }
            response = client.put("/api/model-providers", json=body)
            assert response.status_code == 200
            assert "synthetic-replaced-key" not in response.text
            response = client.put(
                "/api/model-providers", json={**body, "api_key": "", "clear_api_key": True}
            )
            assert response.status_code == 200
            assert response.json()["configured"] is False
            before = store.read_snapshot()
            assert before.documents["models"]["profiles"][0]["api_key"] == ""
            response = client.post("/api/model-providers/test", json={**body, "api_key": ""})
            assert response.status_code == 422
            assert store.read_snapshot().revision == before.revision
            no_secrets(response.text)


def test_json_choice_ignores_an_existing_corrupt_sqlite_store(configured, monkeypatch):
    root, _ = configured
    database = root / "app/secrets/configuration-store.sqlite3"
    database.write_bytes(b"invalid SQLite")
    monkeypatch.setattr(
        ConfigurationStore, "open_existing", lambda *_: pytest.fail("SQLite accessed")
    )
    with ConfigurationBackend.json(root) as owner:
        with TestClient(create_configuration_app(owner)) as client:
            assert client.get("/api/model-providers").json()["profiles"] == []
            assert client.get("/api/search-providers").status_code == 200
    assert database.read_bytes() == b"invalid SQLite"


@pytest.mark.parametrize("mismatch", [False, True])
def test_production_launcher_handoff_is_fresh_json_and_failure_releases_owner(
    tmp_path, monkeypatch, mismatch
):
    import alphalab.local_agent as launcher

    root, source = tmp_path / "runtime", tmp_path / "source"
    package = source / "build/conexus"
    package.mkdir(parents=True)
    (package / "UPSTREAM.json").write_text("{}")
    (package / "node_modules").mkdir()
    (package / "node_modules/.package-lock.json").write_text("{}")
    monkeypatch.setattr(launcher, "RUNTIME_DIR", root)
    monkeypatch.setattr(launcher, "load_env_files", lambda: None)
    monkeypatch.setattr(launcher.shutil, "which", lambda *_: "test-node")
    monkeypatch.setattr(launcher.subprocess, "check_output", lambda *a, **kw: "v22.23.4")
    monkeypatch.setattr(launcher.secrets, "token_urlsafe", lambda *_: "synthetic-launcher-token")
    monkeypatch.setenv("ALPHALAB_AGENT_CONFIGURATION_DECISION", '{"backend":"sqlite"}')
    seen, probes = [], []

    class Child:
        pid = 123456
        stdin = io.BytesIO()
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = 0

        def terminate(self):
            pytest.fail("Owned fake child should exit gracefully")

    def popen(*args, **kwargs):
        contract = json.loads(kwargs["env"]["ALPHALAB_AGENT_CONFIGURATION_DECISION"])
        seen.append(contract)
        identity = {key: contract[key] for key in ("backend", "decision_id", "installation_id")}
        connection = Path(kwargs["env"]["ALPHALAB_AGENT_CONNECTION_FILE"])
        connection.write_text(
            json.dumps(
                {
                    "pid": Child.pid,
                    "origin": "http://127.0.0.1:18555",
                    "token": "synthetic-launcher-token",
                    "slug": "fixture",
                    "configuration": {} if mismatch else identity,
                }
            )
        )
        return Child()

    class Probe:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url):
            probes.append(url)
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    monkeypatch.setattr(httpx, "Client", Probe)
    if mismatch:
        expect_error(lambda: _enter_launcher(launcher, source), "INSTALLATION_MISMATCH")
        assert probes == []
    else:
        _enter_launcher(launcher, source)
        assert len(probes) == 1
    assert seen[0]["backend"] == "json" and seen[0]["installation_id"] is None
    assert seen[0]["root"] == str(root)
    assert Child.stdin.closed
    with configuration_access(root, exclusive=True):
        pass


def _enter_launcher(launcher, source):
    with launcher.local_agent(source, api_port=8000, agent_port=18555):
        with pytest.raises(ConfigurationStoreError) as caught:
            with configuration_access(launcher.RUNTIME_DIR, exclusive=True):
                pass
        assert caught.value.code == "STORE_BUSY"


@pytest.mark.parametrize("phase", ["construct", "run"])
def test_api_server_startup_failure_releases_shared_choice_and_app_state(
    tmp_path, monkeypatch, phase
):
    import uvicorn

    import alphalab.local_agent as launcher
    from apps.api import main

    seen = []

    @contextmanager
    def host(*args, configuration_backend, **kwargs):
        seen.append(configuration_backend)
        yield SimpleNamespace(poll=lambda: None)

    class Server:
        def __init__(self, config):
            assert main.app.state.configuration_backend is seen[0]
            if phase == "construct":
                raise RuntimeError("Synthetic startup failure")

        def run(self):
            raise RuntimeError("Synthetic startup failure")

    monkeypatch.setattr(launcher, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(launcher, "_local_agent", host)
    monkeypatch.setattr(uvicorn, "Server", Server)
    with pytest.raises(RuntimeError, match="Synthetic startup failure"):
        launcher.serve_with_local_agent(REPO, host="127.0.0.1", port=8000)
    assert not hasattr(main.app.state, "configuration_backend")
    assert len(seen) == 1 and seen[0].mode == "json"
    expect_error(seen[0].node_contract, "STORE_UNAVAILABLE")
    with configuration_access(tmp_path, exclusive=True):
        pass
