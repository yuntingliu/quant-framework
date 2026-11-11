"""Disposable activation installations; no real cutover, provider or SDK calls."""

from __future__ import annotations

import json
import multiprocessing
import os
import secrets
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from alphalab.utils import configuration_decision as authority
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStore, ConfigurationStoreError
from apps.api.services import configuration_activation_service as activation
from apps.api.services import configuration_decision_service as preparation
from apps.api.services import configuration_migration_service as migration
from apps.api.services.configuration_profile_service import ConfigurationProfileServices
from tests.api.test_configuration_decision import value
from tests.api.test_configuration_migration_service import CANARIES, native_documents, write_sources


@pytest.fixture
def node():
    candidate = os.environ.get("ALPHALAB_TEST_NODE")
    if not candidate:
        pytest.skip("Explicit test Node 22 runtime required")
    return candidate


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / "installation"
    write_sources(root, native_documents())
    documents, choices = migration._prepare(migration._read_sources(root), {})
    store = ConfigurationStore.initialize(root, documents=documents, selections=choices)
    snapshot = store.read_snapshot()
    report = SimpleNamespace(
        installation_id=snapshot.installation_id,
        revision=snapshot.revision,
        selection_kinds=tuple(choices[key]["kind"] for key in ("models", "search", "rqdata")),
    )
    record = authority.parse_decision_record(
        json.dumps(value(root, report)), root_key=authority.root_identity(root)
    )
    owner = ConfigurationBackend.json(root)
    owner.close()
    with preparation._offline(owner) as offline:
        authority._publish_prepared(offline, record)
    core = tmp_path / "substituted-core.mjs"
    core.write_text(
        "export function createLocalHost(){}; export function createLocalRuntime(){}; "
        "export function createModelCompletion(){}; export function compileHarnessWorkspace(){}"
    )
    manifest = tmp_path / "UPSTREAM.json"
    manifest.write_bytes(activation._LOCK.read_bytes())
    monkeypatch.setattr(activation, "_CORE", core)
    monkeypatch.setattr(activation, "_MANIFEST", manifest)
    return root, store, owner, record


def activate(candidate, node, **changes):
    root, store, owner, record = candidate
    data = record.as_dict()
    options = dict(
        node_executable=node,
        expected_installation_id=data["installation_id"],
        expected_decision_id=data["decision_id"],
        expected_revision=data["revision"],
        acknowledge_offline=True,
        acknowledge_current_credentials=True,
    )
    options.update(changes)
    return activation.activate_sqlite_candidate(owner, **options)


def test_activation_descendants_restart_and_rotation_use_current_state(candidate, node):
    root, raw, old_owner, prepared = candidate
    committed = activate(candidate, node)
    assert committed == {**prepared.as_dict(), "state": "committed"}
    original = (root / authority.NAMESPACE / "decision.json").read_bytes()
    with ConfigurationBackend.production(root) as owner:
        assert owner.durable and owner.mode == "sqlite"
        assert owner.public_identity()["decision_id"] == committed["decision_id"]
        profiles = ConfigurationProfileServices(owner.store)
        profile = raw.read_snapshot().documents["models"]["profiles"][0].copy()
        profile.update(name="Renamed after activation", api_key="rotated-synthetic-model-key")
        result = profiles.models.save_profile(profile, activate=False)
        assert result["revision"] != committed["revision"]
        assert (root / authority.NAMESPACE / "decision.json").read_bytes() == original
    with ConfigurationBackend.production(root) as restarted:
        assert restarted.store.read_snapshot().revision == result["revision"]
        current = ConfigurationProfileServices(restarted.store).models.public_settings()
        assert "rotated-synthetic-model-key" not in json.dumps(current)
        assert any(row["name"] == "Renamed after activation" for row in current["profiles"])
    assert authority.validate_committed_authority(root) == committed


@pytest.mark.parametrize("changed", ["acknowledge_offline", "acknowledge_current_credentials"])
def test_activation_requires_both_explicit_administrative_acknowledgements(
    candidate, node, changed
):
    with pytest.raises(ConfigurationStoreError):
        activate(candidate, node, **{changed: False})
    assert authority.read_decision_record(candidate[0]).as_dict()["state"] == "prepared"


def test_activation_rejects_json_drift_and_live_owners(candidate, node):
    root, store, old, record = candidate
    with ConfigurationBackend.json(root):
        with pytest.raises(ConfigurationStoreError):
            activate(candidate, node)
    path = root / "conexus/secrets/model-providers.json"
    original = path.read_bytes()
    source = json.loads(original)
    source["profiles"][0]["api_key"] = "changed-synthetic-key"
    path.write_text(json.dumps(source))
    with pytest.raises(ConfigurationStoreError):
        activate(candidate, node)
    assert authority.read_decision_record(root).as_dict()["state"] == "prepared"
    assert store.read_snapshot().revision == record.as_dict()["revision"]


def test_missing_runtime_blocks_publication(candidate, node, monkeypatch):
    monkeypatch.setattr(activation, "_CORE", candidate[0] / "missing.mjs")
    with pytest.raises(ConfigurationStoreError):
        activate(candidate, node)
    assert authority.read_decision_record(candidate[0]).as_dict()["state"] == "prepared"


@pytest.mark.parametrize(
    "fault", ["missing-parent", "cycle", "fork", "gap", "rollback", "history-json"]
)
def test_ancestry_fails_closed_without_history_selection(candidate, node, fault):
    root, raw, _, record = candidate
    activate(candidate, node)
    anchor = record.as_dict()["revision"]
    second = raw.update(lambda old: (old.documents, old.selections))
    third = raw.update(lambda old: (old.documents, old.selections))
    path = root / "app/secrets/configuration-store.sqlite3"
    with sqlite3.connect(path) as db:
        if fault == "missing-parent":
            db.execute(
                "UPDATE generations SET parent_revision=? WHERE revision=?",
                ("f" * 32, third.revision),
            )
        elif fault == "cycle":
            db.execute(
                "UPDATE generations SET parent_revision=? WHERE revision=?",
                (third.revision, anchor),
            )
        elif fault == "fork":
            db.execute(
                "UPDATE generations SET parent_revision=? WHERE revision=?",
                (anchor, third.revision),
            )
        elif fault == "gap":
            db.execute("DELETE FROM generations WHERE revision=?", (second.revision,))
        elif fault == "rollback":
            db.execute("UPDATE store_metadata SET current_revision=?", (anchor,))
        else:
            db.execute("UPDATE generations SET models='{}' WHERE revision=?", (anchor,))
    with pytest.raises(ConfigurationStoreError) as caught:
        ConfigurationBackend.production(root)
    assert all(secret not in repr(caught.value) for secret in CANARIES)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT current_revision FROM store_metadata").fetchone()[0] == (
            anchor if fault == "rollback" else third.revision
        )


def test_interrupted_publication_leaves_fail_closed_evidence(candidate, node, monkeypatch):
    original = authority.os.replace

    def fail(source, target):
        if Path(source).name == "decision.pending":
            raise OSError(CANARIES[0])
        return original(source, target)

    monkeypatch.setattr(authority.os, "replace", fail)
    with pytest.raises(ConfigurationStoreError) as caught:
        activate(candidate, node)
    assert CANARIES[0] not in repr(caught.value)
    assert (candidate[0] / authority.NAMESPACE / "decision.pending").exists()
    with pytest.raises(ConfigurationStoreError):
        ConfigurationBackend.production(candidate[0])


@pytest.fixture
def activated_agent(candidate, node, monkeypatch, tmp_path, request):
    from alphalab.utils import configuration_agent as agent

    activate(candidate, node)
    root = candidate[0]
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))
    monkeypatch.setenv("ALPHALAB_AUTHORITY_PYTHON", str(__import__("sys").executable))
    original = subprocess.Popen
    children = []
    control, trace = tmp_path / "control", tmp_path / "trace"
    fixture = Path(__file__).resolve().parents[1] / "fixtures/configuration_agent.mjs"

    def launch(command, **options):
        if command == [node, str(agent._ENTRY)]:
            mode = getattr(request, "param", "fixture")
            if mode.startswith("managed"):
                loader = (
                    r"""
import { registerHooks } from 'node:module';
import { readFileSync } from 'node:fs';
globalThis.__configurationFixture = CONTROLS;
// The actual managed entry defaults to global fetch. Replace it before startup:
// every provider operation in this test-owned process must remain synthetic.
globalThis.fetch = async url => Response.json(String(url).includes('openrouter.ai')
  ? { choices: [{ message: { annotations: [{ type: 'url_citation', url_citation: { title: 'Synthetic', url: 'https://source.example/article' } }] } }] }
  : { web: { results: [{ title: 'Synthetic', url: 'https://source.example/article' }] } });
registerHooks({ resolve(specifier, context, next) {
  if (specifier.endsWith('/build/conexus/apps/local-host/src/index.mjs')) return { url: CORE, shortCircuit: true };
  return next(specifier, context);
}, load(url, context, next) {
  if (DISABLE && url === ENTRY) {
    const source = readFileSync(new URL(url), 'utf8');
    const replaced = source.replace(/    \/\/ Managed readiness hooks begin\.[\s\S]*?    \/\/ Managed readiness hooks end\.\r?\n/, '');
    if (source === replaced) throw new Error('Negative control did not replace the hooks');
    return { format: 'module', shortCircuit: true, source: replaced };
  }
  return next(url, context);
} });
""".replace("CONTROLS", json.dumps({"control": str(control), "trace": str(trace)}))
                    .replace("CORE", json.dumps(fixture.as_uri()))
                    .replace("ENTRY", json.dumps(agent._ENTRY.as_uri()))
                    .replace("DISABLE", "true" if mode == "managed-no-readiness" else "false")
                )
                args = [
                    node,
                    "--import",
                    "data:text/javascript," + quote(loader),
                    str(agent._ENTRY),
                ]
            else:
                args = [node, str(fixture), str(control), str(trace)]
            child = original(args, **options)
            children.append(child)
            return child
        return original(command, **options)

    monkeypatch.setattr(agent.subprocess, "Popen", launch)
    with ConfigurationBackend.production(root) as owner:
        yield owner, agent, children, control, trace
    assert all(child.poll() is not None for child in children)


@pytest.fixture
def production_client(activated_agent, node, monkeypatch):
    from fastapi.testclient import TestClient

    from alphalab import ResultStore

    owner, agent, _, _, _ = activated_agent
    with owner.scope():
        from apps.api import main
    monkeypatch.setattr(main, "RUNTIME_DIR", owner.root)
    monkeypatch.setattr(main, "ResultStore", lambda: ResultStore(owner.root / "app/alphalab.db"))
    monkeypatch.setattr(
        main,
        "managed_configuration_agent",
        lambda backend, **options: agent.managed_configuration_agent(backend, node=node, **options),
    )
    main.app.state.configuration_backend = owner
    main.app.state.configuration_api_origin = "http://127.0.0.1:9998"
    try:
        with TestClient(main.app) as client:
            yield client, main.app.state.configuration_runtime, activated_agent
    finally:
        del main.app.state.configuration_backend
        del main.app.state.configuration_api_origin


def owned_http(runtime, method, path, payload=None):
    token = runtime.agent.extract_http_private(runtime.backend)[2]
    stream = runtime.agent.http_request(
        runtime.backend,
        method,
        path,
        {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        b"" if payload is None else json.dumps(payload).encode(),
        5,
    )
    try:
        wire = b"".join(iter(stream.read, b""))
        assert all(secret.encode() not in wire for secret in (*CANARIES, token))
        return stream.status, json.loads(wire)
    finally:
        stream.close()


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
def test_owned_health_exact_path_and_method_boundary(production_client):
    client, runtime, _ = production_client
    assert owned_http(runtime, "GET", "/api/public/fixture")[0] == 200
    status, health = owned_http(runtime, "GET", "/health")
    assert status == 200 and health["modelConfigured"] is True
    assert client.get("/api/conexus/status").json()["available"] is True
    for method, path in [
        ("POST", "/health"),
        ("HEAD", "/health"),
        ("PUT", "/health"),
        ("GET", "/health/"),
        ("GET", "/health?x=1"),
        ("GET", "/health#x"),
        ("GET", "/%68ealth"),
        ("GET", "/health%3fx=1"),
        ("GET", "http://127.0.0.1:1/health"),
        ("GET", "//other.example/health"),
        ("GET", "/api/local/shutdown"),
        ("GET", "/api/public/../health"),
        ("GET", "/api/public/%zz"),
        ("GET", "/api/public/fixture#ignored"),
    ]:
        with pytest.raises(ConfigurationStoreError) as caught:
            owned_http(runtime, method, path)
        assert caught.value.code == "INVALID_ARGUMENT", (method, path)


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
@pytest.mark.parametrize(
    "failure", ["wrong-proof", "close-proof", "redirect", "unhealthy", "terminated"]
)
def test_full_api_status_fails_safely_for_unavailable_owned_agent(production_client, failure):
    client, runtime, fixture = production_client
    _, _, children, control, _ = fixture
    assert client.get("/api/conexus/status").json()["available"] is True
    if failure == "terminated":
        children[0].terminate()  # Only this fixture's explicitly owned child.
        children[0].wait(timeout=10)
    else:
        control.write_text(failure)
    result = client.get("/api/conexus/status")
    assert result.status_code == 503 or result.json()["available"] is False
    assert all(secret not in result.text for secret in CANARIES)
    assert "127.0.0.1:1" not in result.text


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
def test_managed_readiness_and_run_admission_follow_current_selection(production_client):
    client, runtime, _ = production_client
    profiles = ConfigurationProfileServices(runtime.backend.store)
    documents = runtime.backend.store.read_snapshot().documents
    first, alternate = documents["models"]["profiles"][:2]
    runs = "/api/public/harnesses/alphalab-research-agent/runs"

    def ready(value):
        status, health = owned_http(runtime, "GET", "/health")
        assert status == 200 and health["modelConfigured"] is value
        result = client.get("/api/conexus/status")
        assert result.status_code == 200 and result.json()["available"] is value, result.text
        assert all(secret not in result.text for secret in CANARIES)

    ready(True)
    assert owned_http(runtime, "POST", runs, {})[0] == 202
    for unavailable in ("none", "cleared", "incomplete"):
        if unavailable == "none":
            profiles.models.select({"kind": "none"})
        elif unavailable == "cleared":
            profiles.models.save_profile(
                {**first, "api_key": ""}, activate=True, clear_api_key=True
            )
        else:

            def incomplete(old):
                docs = old.documents
                next(row for row in docs["models"]["profiles"] if row["id"] == first["id"])[
                    "model"
                ] = ""
                return docs, old.selections

            runtime.backend.store.update(incomplete)
        ready(False)
        for _ in range(2):
            assert owned_http(runtime, "POST", runs, {})[0] == 503
            assert owned_http(runtime, "GET", "/api/public/fixture")[1]["created"] == 1
        profiles.models.save_profile(first.copy(), activate=True)
        ready(True)
    profiles.models.select({"kind": "profile", "id": alternate["id"]})
    ready(True)
    assert owned_http(runtime, "POST", runs, {})[0] == 202
    assert owned_http(runtime, "GET", "/api/public/fixture")[1]["created"] == 2


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
@pytest.mark.parametrize("fault", ["missing-selected", "invalid-configuration"])
def test_managed_hooks_reject_configuration_fault_after_parent_preflight(
    production_client, monkeypatch, fault
):
    _, runtime, _ = production_client
    snapshot = runtime.backend.store.read_snapshot()
    path = runtime.backend.root / "app/secrets/configuration-store.sqlite3"
    original = json.dumps(snapshot.documents["models"])
    broken = json.loads(original)
    if fault == "missing-selected":
        broken["profiles"] = [row for row in broken["profiles"] if row["id"] != broken["active_id"]]
    else:
        broken["version"] = 99
    # Isolate the Node hook boundary, as if storage failed immediately after
    # Python's preflight. Keep real process ownership, HMAC and Node leases.
    with monkeypatch.context() as probe:
        probe.setattr(ConfigurationBackend, "_check_authority", lambda _self: None)
        try:
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE generations SET models=? WHERE revision=?",
                    (json.dumps(broken), snapshot.revision),
                )
                db.commit()
            assert owned_http(runtime, "GET", "/health")[1]["modelConfigured"] is False
            for _ in range(2):
                assert (
                    owned_http(
                        runtime, "POST", "/api/public/harnesses/alphalab-research-agent/runs", {}
                    )[0]
                    == 503
                )
            assert owned_http(runtime, "GET", "/api/public/fixture")[1]["created"] == 0
        finally:
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE generations SET models=? WHERE revision=?",
                    (original, snapshot.revision),
                )
                db.commit()


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
def test_negative_control_original_http_allowlist_breaks_full_api_status(
    production_client, monkeypatch
):
    from alphalab.utils.configuration_agent_http import _HttpRequest

    client, _, _ = production_client
    original = _HttpRequest.__init__

    def previous(self, method, path, *args):
        if path == "/health":
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        original(self, method, path, *args)

    assert client.get("/api/conexus/status").json()["available"] is True
    with monkeypatch.context() as probe:
        probe.setattr(_HttpRequest, "__init__", previous)
        with pytest.raises(AssertionError, match="Authenticated status unavailable"):
            assert client.get("/api/conexus/status").json()["available"] is True, (
                "Authenticated status unavailable"
            )
    assert client.get("/api/conexus/status").json()["available"] is True


@pytest.mark.parametrize("activated_agent", ["managed-no-readiness"], indirect=True)
def test_negative_control_omitting_hooks_exposes_false_readiness_and_run_creation(
    production_client,
):
    _, runtime, _ = production_client
    ConfigurationProfileServices(runtime.backend.store).models.select({"kind": "none"})
    with pytest.raises(AssertionError, match="Model none reported ready"):
        assert owned_http(runtime, "GET", "/health")[1]["modelConfigured"] is False, (
            "Model none reported ready"
        )
    with pytest.raises(AssertionError, match="Unconfigured Run created"):
        assert (
            owned_http(runtime, "POST", "/api/public/harnesses/alphalab-research-agent/runs", {})[0]
            == 503
        ), "Unconfigured Run created"
    assert owned_http(runtime, "GET", "/api/public/fixture")[1]["created"] == 1


def test_committed_python_and_real_node_read_descendants_and_authenticate_proxy(
    activated_agent, node
):
    owner, agent, children, control, trace = activated_agent
    with agent.managed_configuration_agent(
        owner, node=node, api_origin="http://127.0.0.1:9998"
    ) as connection:
        connection.authenticate(owner)
        stream = connection.http_request(
            owner, "GET", "/api/public/fixture", {"Accept": "application/json"}, b"", 5
        )
        try:
            result = json.loads(stream.read())
            assert result == {"ok": True, "method": "GET"}
        finally:
            stream.close()
        profiles = ConfigurationProfileServices(owner.store)
        profile = owner.store.read_snapshot().documents["search"]["profiles"][0].copy()
        profile.update(provider="brave", model_profile_id=None)
        profiles.search.save_profile(profile, activate=True)
        result = connection.test_search(
            owner, profiles.search.resolve_test_profile(profile).extract_private()
        )
        assert result["ok"] is True
        assert all(secret not in json.dumps(result) for secret in CANARIES)
    assert children[0].poll() is not None


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
def test_full_workbench_sqlite_lifespan_routes_and_restart(activated_agent, node, monkeypatch):
    from fastapi.testclient import TestClient

    from alphalab import ResultStore

    owner, agent, children, control, trace = activated_agent
    with owner.scope():
        from apps.api import main
    monkeypatch.setattr(main, "RUNTIME_DIR", owner.root)
    monkeypatch.setattr(main, "ResultStore", lambda: ResultStore(owner.root / "app/alphalab.db"))
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "off")
    monkeypatch.setenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "foreign-ambient-token")
    monkeypatch.setenv("CONEXUS_WEB_ORIGIN", "http://127.0.0.1:1")
    main.app.state.configuration_backend = owner
    main.app.state.configuration_api_origin = "http://127.0.0.1:9998"
    monkeypatch.setattr(
        main,
        "managed_configuration_agent",
        lambda backend, **options: agent.managed_configuration_agent(backend, node=node, **options),
    )
    try:
        for _ in range(2):
            with TestClient(main.app) as client:
                for url in (
                    "/api/model-providers",
                    "/api/search-providers",
                    "/api/data-source-profiles",
                    "/api/data-sync/health",
                    "/api/health",
                    "/api/sdk-docs",
                    "/api/conexus/status",
                ):
                    result = client.get(url)
                    assert result.status_code == 200, (url, result.status_code, result.text)
                    assert all(secret not in result.text for secret in CANARIES)
                    if url == "/api/conexus/status":
                        assert result.json()["available"] is True, result.text
                models = client.get("/api/model-providers").json()
                current = owner.store.read_snapshot().documents["models"]["profiles"][0].copy()
                current.update(name="Updated through full API", api_key="")
                result = client.put(
                    "/api/model-providers",
                    json={**current, "activate": False, "expected_revision": models["revision"]},
                )
                assert result.status_code == 200, result.text
                search = client.get("/api/search-providers").json()
                current_search = (
                    owner.store.read_snapshot().documents["search"]["profiles"][0].copy()
                )
                current_search.update(name="Search saved through full API", brave_api_key="")
                result = client.put(
                    "/api/search-providers",
                    json={**current_search, "expected_revision": search["revision"]},
                )
                assert result.status_code == 200, result.text
                result = client.post("/api/search-providers/test", json=current_search)
                assert result.status_code == 200 and result.json()["ok"] is True
                assert all(secret not in result.text for secret in CANARIES)
                rq = client.get("/api/data-source-profiles").json()
                current_rq = owner.store.read_snapshot().documents["rqdata"]["profiles"][0].copy()
                current_rq.pop("provider", None)
                result = client.put(
                    "/api/data-source-profiles",
                    json={
                        **current_rq,
                        "password": "",
                        "clear_password": True,
                        "expected_revision": rq["revision"],
                    },
                )
                assert result.status_code == 200, result.text
    finally:
        del main.app.state.configuration_backend
        del main.app.state.configuration_api_origin
    assert len(children) == 2 and all(child.poll() is not None for child in children)


class ActivationSDK:
    def init(self, user, password, host, *, lazy):
        assert password in {CANARIES[3], "rotated-activation-password"}
        assert "RQ_PASSWORD" not in os.environ
        self.configured = True

    def get_latest_trading_date(self, *, market):
        import pandas as pd

        assert self.configured
        return pd.Timestamp("2025-01-02")

    def all_instruments(self, *, type, market):
        import pandas as pd

        assert self.configured
        return pd.DataFrame(
            {
                "order_book_id": ["000001.XSHE"],
                "symbol": ["Fixture"],
                "type": ["CS"],
                "listed_date": ["1991-01-01"],
                "de_listed_date": ["0000-00-00"],
            }
        )


def test_durable_rq_jobs_capture_current_and_clearing_never_uses_environment(
    candidate, node, monkeypatch
):
    from alphalab.dataio.sync import SyncRequest
    from apps.api.services.configuration_data_service import ConfigurationDataServices

    root, raw, _, record = candidate
    activate(candidate, node)
    monkeypatch.setenv("RQ_PASSWORD", "must-not-be-borrowed")
    with ConfigurationBackend.production(root) as owner:
        with ConfigurationDataServices(
            owner, sdk_factory=ActivationSDK, timeout_seconds=30
        ) as data:
            assert data.connection_test("rq.a_share_daily")["connected"]
            job = data.manager.submit(SyncRequest(datasets=["instruments"]))
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                result = data.manager.operations.get_job(job["id"])
                if result["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.05)
            assert result["status"] == "succeeded", data.public_job(result)
            assert all(key not in json.dumps(result) for key in CANARIES)
            profile = owner.store.read_snapshot().documents["rqdata"]["profiles"][0].copy()
            data.rq_settings(profile={**profile, "password": ""}, clear_password=True)
            with pytest.raises(ConfigurationStoreError):
                data.connection_test("rq.a_share_daily")


def _held_child(root, pipe, release):
    from alphalab.utils.configuration_maintenance import hold_worker_execution

    hold_worker_execution(root)
    pipe.send("ready")
    release.wait(15)
    pipe.close()


def test_actual_worker_prevents_activation_until_exit(candidate, node):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    release = context.Event()
    worker = context.Process(target=_held_child, args=(str(candidate[0]), child, release))
    worker.start()
    child.close()
    try:
        assert parent.poll(15) and parent.recv() == "ready"
        with pytest.raises(ConfigurationStoreError) as caught:
            activate(candidate, node)
        assert caught.value.code == "STORE_BUSY"
        assert authority.read_decision_record(candidate[0]).as_dict()["state"] == "prepared"
    finally:
        release.set()
        worker.join(5)
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
        assert worker.exitcode == 0
        worker.close()
        parent.close()
    assert activate(candidate, node)["state"] == "committed"


def test_concurrent_activation_has_exactly_one_publisher(candidate, node):
    barrier = threading.Barrier(2)

    def publish():
        barrier.wait(5)
        try:
            return activate(candidate, node)["state"]
        except ConfigurationStoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert results.count("committed") == 1
    assert set(results) <= {"committed", "STORE_BUSY", "UNSUPPORTED_STORAGE", "REVISION_CONFLICT"}


def test_commit_ack_failure_preserves_committed_state_without_retry(candidate, node, monkeypatch):
    original = authority._sync_directory

    def fail_after_replace(path):
        record = path / "decision.json"
        if record.exists() and json.loads(record.read_bytes()).get("state") == "committed":
            raise OSError(CANARIES[3])
        return original(path)

    monkeypatch.setattr(authority, "_sync_directory", fail_after_replace)
    with pytest.raises(ConfigurationStoreError) as caught:
        activate(candidate, node)
    assert CANARIES[3] not in repr(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert authority.read_decision_record(candidate[0]).as_dict()["state"] == "committed"
    with ConfigurationBackend.production(candidate[0]) as owner:
        assert owner.durable


def test_negative_control_disabling_ancestry_accepts_a_fork(candidate, node, monkeypatch):
    root, raw, _, record = candidate
    activate(candidate, node)
    raw.update(lambda old: (old.documents, old.selections))
    third = raw.update(lambda old: (old.documents, old.selections))
    with sqlite3.connect(root / "app/secrets/configuration-store.sqlite3") as db:
        db.execute(
            "UPDATE generations SET parent_revision=? WHERE revision=?",
            (record.as_dict()["revision"], third.revision),
        )
    with pytest.raises(ConfigurationStoreError):
        authority.validate_committed_authority(root)
    with monkeypatch.context() as control:
        control.setattr(ConfigurationStore, "_ancestry", lambda *args: None)
        with pytest.raises(AssertionError, match="Fork was accepted"):
            result = authority.validate_committed_authority(root)
            assert result is None, "Fork was accepted"
    with pytest.raises(ConfigurationStoreError):
        authority.validate_committed_authority(root)


def test_negative_control_skipping_source_freshness_detects_publication(
    candidate, node, monkeypatch
):
    root, _, _, record = candidate
    target = root / "conexus/secrets/model-providers.json"
    changed = json.loads(target.read_bytes())
    changed["profiles"][0]["api_key"] = "freshness-negative-control"
    target.write_text(json.dumps(changed))
    with pytest.raises(ConfigurationStoreError):
        activate(candidate, node)
    with monkeypatch.context() as control:
        control.setattr(migration, "verify_offline_migration", lambda *args, **kw: None)
        control.setattr(preparation, "_legacy_equivalent", lambda *args: None)
        with pytest.raises(AssertionError, match="Stale source was activated"):
            activate(candidate, node)
            assert authority.read_decision_record(root).as_dict()["state"] == "prepared", (
                "Stale source was activated"
            )
    assert authority.read_decision_record(root).as_dict()["state"] == "committed"


@pytest.mark.parametrize(
    "mode", ["valid", "wrong-secret", "wrong-installation", "wrong-decision", "copy"]
)
def test_real_node_child_needs_live_owner_bound_proof(candidate, node, mode):
    activate(candidate, node)
    fixture = Path(__file__).resolve().parents[1] / "fixtures/configuration_activation_probe.mjs"
    with ConfigurationBackend.production(candidate[0]) as owner:
        child = subprocess.Popen(
            [node, str(fixture), mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "ALPHALAB_AUTHORITY_PYTHON": str(__import__("sys").executable)},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        secret = secrets.token_hex(32)
        owner.authorize_agent(child.pid, secret)
        wire = (
            json.dumps(
                {
                    "contract": owner.node_contract(),
                    "secret": secret,
                    "token": secrets.token_hex(32),
                    "launch": secrets.token_hex(32),
                    "api_origin": "http://127.0.0.1:9998",
                }
            ).encode()
            + b"\n"
        )
        try:
            output, errors = child.communicate(wire, timeout=35)
            assert child.returncode == 0
            assert all(value.encode() not in output + errors for value in (*CANARIES, secret))
            result = json.loads(output)
            if mode == "copy":
                assert result == {"copiedRejected": True}
            elif mode == "valid":
                assert result == {"accepted": True, "matched": True, "constructed": True}
            else:
                assert result == {
                    "accepted": False,
                    "code": "BACKEND_UNAVAILABLE",
                    "constructed": False,
                }
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(5)
            owner.revoke_agent(child.pid)


def test_idle_durable_owner_can_be_stopped_under_actual_offline_admission(candidate, node):
    from alphalab.utils.configuration_deployment import coordinated_offline
    from alphalab.utils.configuration_maintenance import execution_access

    activate(candidate, node)
    owner = ConfigurationBackend.production(candidate[0])
    request = ConfigurationBackend.offline_decision(candidate[0])
    stopped = []

    def stop_owned():
        # The offline gate is already held: even an otherwise idle owner's
        # bound writer cannot admit work during shutdown.
        with pytest.raises(ConfigurationStoreError, match="busy"):
            with execution_access(owner.root):
                pytest.fail("Admitted work through the exclusive offline gate")
        stopped.append(True)
        owner.close()

    try:
        with coordinated_offline(request, stop_services=stop_owned) as offline:
            assert offline.backend == "sqlite"
            assert offline.installation_id == candidate[3].as_dict()["installation_id"]
        assert stopped == [True]
    finally:
        owner.close()


def test_bound_update_history_limit_rejects_before_commit(candidate):
    from uuid import uuid4

    root, raw, _, record = candidate
    anchor = record.as_dict()["revision"]
    path = root / "app/secrets/configuration-store.sqlite3"
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT models,search,rqdata,selections FROM generations").fetchone()
        previous = anchor
        for _ in range(4095):
            revision = uuid4().hex
            db.execute(
                "INSERT INTO generations(revision,parent_revision,models,search,rqdata,selections) VALUES(?,?,?,?,?,?)",
                (revision, previous, *row),
            )
            previous = revision
        db.execute("UPDATE store_metadata SET current_revision=?", (previous,))
    bound = raw.bind_activation(anchor, record.as_dict()["evidence"]["selection_kinds"])
    with pytest.raises(ConfigurationStoreError) as caught:
        bound.update(lambda old: (old.documents, old.selections), expected_revision=previous)
    assert caught.value.code == "STORE_CORRUPT"
    assert raw.read_snapshot().revision == previous
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM generations").fetchone() == (4096,)


def test_full_api_http_transport_is_pinned_and_releases_completed_bodies(activated_agent, node):
    import asyncio

    import httpx

    from apps.api.services.configuration_agent_transport import OwnedAgentTransport

    owner, agent, children, _, _ = activated_agent
    with agent.managed_configuration_agent(
        owner, node=node, api_origin="http://127.0.0.1:9998"
    ) as connection:
        origin, _, _ = connection.extract_http_private(owner)

        async def exercise():
            transport = OwnedAgentTransport(owner, connection)
            async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
                for _ in range(3):
                    response = await client.get(origin + "/api/public/fixture")
                    assert response.json() == {"ok": True, "method": "GET"}
                    assert not transport._bodies
                    assert all(secret not in response.text for secret in CANARIES)
                with pytest.raises(httpx.ConnectError) as caught:
                    await client.get("http://127.0.0.1:1/api/public/fixture")
                assert caught.value.__cause__ is None and caught.value.__context__ is None
                assert all(secret not in str(caught.value) for secret in CANARIES)
            assert not transport._bodies

        asyncio.run(exercise())
    assert children[0].poll() is not None


def test_committed_restart_recaptures_current_jobs_without_retagging_json_work(candidate, node):
    from alphalab.dataio.runtime import RuntimeStore
    from alphalab.dataio.sync import SyncRequest
    from apps.api.services.configuration_data_service import ConfigurationDataServices

    activate(candidate, node)
    root = candidate[0]
    with ConfigurationBackend.production(root) as owner:
        with ConfigurationDataServices(owner, sdk_factory=ActivationSDK) as first:
            request = SyncRequest(datasets=["instruments"]).model_dump(mode="json")
            first._validate_submission(request)
        operations = RuntimeStore(root).operations
        prior = owner.store.read_snapshot().revision
        request["configuration_capture"] = {
            "installation_id": owner.public_identity()["installation_id"],
            "revision": prior,
            "attempt_id": "a" * 32,
        }
        valid = operations.create_job(request)
        operations.update_job(valid, status="running")
        legacy = operations.create_job(
            SyncRequest(datasets=["instruments"]).model_dump(mode="json")
        )
        foreign = operations.create_job({**request, "configuration_installation_id": "f" * 32})
        profiles = ConfigurationProfileServices(owner.store)
        rq = owner.store.read_snapshot().documents["rqdata"]["profiles"][0].copy()
        profiles.rqdata.save_profile(
            {**rq, "password": "rotated-activation-password"}, activate=True
        )
        latest = owner.store.read_snapshot().revision
    with ConfigurationBackend.production(root) as restarted:
        with ConfigurationDataServices(restarted, sdk_factory=ActivationSDK) as second:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                rows = [second.manager.operations.get_job(key) for key in (valid, legacy, foreign)]
                if all(row["status"] in {"succeeded", "failed", "cancelled"} for row in rows):
                    break
                time.sleep(0.05)
            assert [row["status"] for row in rows] == ["succeeded", "failed", "failed"]
            assert prior != latest
            assert rows[0]["request"]["configuration_capture"]["revision"] == latest
            assert rows[0]["request"]["configuration_capture"]["attempt_id"] != "a" * 32
            assert second.public_job(rows[1]) is None and second.public_job(rows[2]) is None
            assert all(
                secret not in json.dumps(rows)
                for secret in (*CANARIES, "rotated-activation-password")
            )
