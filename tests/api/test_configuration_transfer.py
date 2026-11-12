"""Disposable real stores and production routes; synthetic secrets, no probes."""

from __future__ import annotations

import copy
import json
import os
import pickle
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from alphalab.utils import configuration_store as storage
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_maintenance import configuration_snapshot_access
from alphalab.utils.configuration_transfer_plans import transfer_plans
from apps.api.services import configuration_schema as schema
from apps.api.services import configuration_transfer_service as transfer
from apps.api.services.configuration_profile_service import ConfigurationProfileServices
from tests.api import test_configuration_activation as activation
from tests.api.test_configuration_migration_service import CANARIES, native_documents, write_sources

candidate = activation.candidate
node = activation.node
activated_agent = activation.activated_agent
production_client = activation.production_client
FRESH = "transfer-fresh-private-value-9071"


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in {"localhost", "127.0.0.1", "::1"}:
            raise AssertionError("No external calls in configuration transfer tests")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)


@pytest.fixture
def owned(candidate, node):
    activation.activate(candidate, node)
    with ConfigurationBackend.production(candidate[0]) as owner:
        yield owner, transfer.ConfigurationTransferService(owner)


@pytest.fixture
def inactive_owned(tmp_path):
    documents = native_documents()
    selections = {
        key: {"kind": "profile", "id": document["active_id"]} for key, document in documents.items()
    }
    root = tmp_path / "inactive-installation"
    store = storage.ConfigurationStore.initialize(root, documents=documents, selections=selections)
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        yield owner, transfer.ConfigurationTransferService(owner)


@pytest.fixture
def anchored_inactive_owned(inactive_owned, monkeypatch):
    """Exercise the real activation-bound store, without claiming durable ownership."""
    owner, service = inactive_owned
    snapshot = owner.store.read_snapshot()
    bound = owner.store.bind_activation(
        snapshot.revision, {key: choice["kind"] for key, choice in snapshot.selections.items()}
    )
    monkeypatch.setattr(owner, "_store", bound)
    yield owner, service


def public(value):
    text = repr(value) + str(value)
    if isinstance(value, BaseException):
        assert value.__cause__ is None and value.__context__ is None
        text += "".join(traceback.format_exception(value)) + repr(pickle.dumps(value))
    for secret in (*CANARIES, FRESH):
        assert secret not in text


def exported(service):
    return json.loads(service.export())


def options(document, *, services=None, mode="replace", action="clear"):
    services = list(transfer.SERVICES) if services is None else services
    return dict(
        configuration=document,
        services=services,
        mode=mode,
        apply_selections=True,
        credential_policy=[
            {"service": key, "id": row["id"], "action": action}
            for key in services
            for row in document[key]["profiles"]
        ],
    )


def apply(service, preview, *, credentials=None, kind="import"):
    return service.apply(
        preview["receipt"],
        expected_revision=preview["revision"],
        confirm=True,
        consents=preview["required_consents"],
        credentials=credentials or [],
        kind=kind,
    )


def unchanged(before, after):
    assert (after.revision, after.documents, after.selections) == (
        before.revision,
        before.documents,
        before.selections,
    )


def test_sqlite_export_canonical_preview_and_atomic_apply(owned):
    owner, service = owned
    document = exported(service)
    public(service.export())
    assert schema.serialize_transfer_document(document) == service.export()
    before = owner.store.read_snapshot()
    for key in transfer.SERVICES:
        document[key]["profiles"][0]["name"] = "Transferred " + key
        document[key]["selection"] = {"kind": "none"}
    preview = service.preview(**options(document))
    assert preview["can_apply"] and preview["receipt"]
    public(preview)
    unchanged(before, owner.store.read_snapshot())
    result = apply(service, preview)
    after = owner.store.read_snapshot()
    assert after.revision == result["revision"] != before.revision
    for key in transfer.SERVICES:
        assert all(row[transfer.SECRET[key]] == "" for row in after.documents[key]["profiles"])
        assert after.selections[key] == {"kind": "none"}
    assert len(after._history) == len(before._history) + 1
    public(result)
    with pytest.raises(transfer.TransferError, match="already attempted"):
        apply(service, preview)


def test_explicit_retention_and_replacement_are_private(owned):
    owner, service = owned
    document = exported(service)
    document["models"]["profiles"][0]["name"] = "Retained metadata edit"
    preview = service.preview(**options(document, services=["models"], action="retain"))
    result = apply(service, preview)
    public(result)
    rows = {row["id"]: row for row in owner.store.read_snapshot().documents["models"]["profiles"]}
    assert rows["model"]["api_key"] == CANARIES[0]
    assert rows["inactive"]["api_key"] == CANARIES[1]
    document = exported(service)
    preview = service.preview(**options(document, services=["rqdata"], action="replace"))
    assert {"service": "rqdata", "id": "rq", "code": "PRIVATE_INPUT_REQUIRED"} in preview[
        "missing_credentials"
    ]
    with pytest.raises(transfer.TransferError):
        apply(service, preview)
    assert service.operation(preview["receipt"])["state"] == "pending"
    result = apply(
        service, preview, credentials=[{"service": "rqdata", "id": "rq", "value": FRESH}]
    )
    assert owner.store.read_snapshot().documents["rqdata"]["profiles"][0]["password"] == FRESH
    public(result)
    plans = owner.root / "app/secrets/configuration-transfers"
    for path in plans.iterdir():
        public(path.read_text())


@pytest.mark.parametrize("service_name", transfer.SERVICES)
def test_retention_requires_strict_same_profile_destination(owned, service_name):
    owner, service = owned
    document = exported(service)
    field = {"models": "model", "search": "provider", "rqdata": "user"}[service_name]
    document[service_name]["profiles"][0][field] = {
        "models": "provider/different-model",
        "search": "brave",
        "rqdata": "different-user",
    }[service_name]
    before = owner.store.read_snapshot()
    with pytest.raises(transfer.TransferError) as caught:
        service.preview(**options(document, services=[service_name], action="retain"))
    assert caught.value.code == "RETENTION_NOT_ELIGIBLE"
    unchanged(before, owner.store.read_snapshot())
    public(caught.value)


def test_stale_expired_and_replayed_receipts(owned, monkeypatch):
    owner, service = owned
    document = exported(service)
    preview = service.preview(**options(document))
    with monkeypatch.context() as patch:
        patch.setattr(transfer.time, "time", lambda: preview["expires"])
        with pytest.raises(transfer.TransferError) as caught:
            apply(service, preview)
        assert caught.value.code == "RECEIPT_EXPIRED"
    owner.store.update(lambda previous: (previous.documents, previous.selections))
    with pytest.raises(transfer.TransferError) as caught:
        apply(service, preview)
    assert caught.value.code == "STALE_PREVIEW"


def test_logical_rollback_is_new_generation_without_old_credentials(owned):
    owner, service = owned
    old = exported(service)
    document = copy.deepcopy(old)
    document["rqdata"]["profiles"] = [
        {**old["rqdata"]["profiles"][0], "id": "new-rq", "name": "New account metadata"}
    ]
    document["rqdata"]["selection"] = {"kind": "profile", "id": "new-rq"}
    preview = service.preview(**options(document, services=["rqdata"]))
    result = apply(service, preview)
    assert owner.store.read_snapshot().documents["rqdata"]["profiles"][0]["password"] == ""
    with pytest.raises(transfer.TransferError):
        service.rollback_preview(
            result["operation"],
            credential_policy=[{"service": "rqdata", "id": "rq", "action": "retain"}],
        )
    rollback = service.rollback_preview(
        result["operation"],
        credential_policy=[{"service": "rqdata", "id": "rq", "action": "clear"}],
    )
    undone = apply(service, rollback, kind="rollback")
    assert undone["revision"] not in {result["revision"], preview["revision"]}
    assert exported(service) == old
    row = owner.store.read_snapshot().documents["rqdata"]["profiles"][0]
    assert row["id"] == "rq" and row["password"] == ""
    public(rollback)
    public(undone)


def test_json_export_is_coordinated_and_json_apply_remains_disabled(tmp_path):
    root = tmp_path / "json-installation"
    write_sources(root, native_documents())
    with ConfigurationBackend.production(root) as owner:
        service = transfer.ConfigurationTransferService(owner)
        document = exported(service)
        public(document)
        assert owner.mode == "json"
        before = {p: p.read_bytes() for p in root.rglob("*.json")}
        preview = service.preview(**options(document))
        assert not preview["can_apply"] and preview["receipt"] is None
        assert "UNSUPPORTED_JSON_APPLY" in {row["code"] for row in preview["diagnostics"]}
        assert not (root / "app/secrets/configuration-store.sqlite3").exists()
        assert {p: p.read_bytes() for p in before} == before
        with configuration_snapshot_access(root):
            with pytest.raises(transfer.TransferError) as caught:
                service.export()
            assert caught.value.code == "BUSY"


@pytest.mark.parametrize(
    "fault", ["unknown", "credential", "duplicate", "version", "oversize", "reference", "url"]
)
def test_malformed_imports_are_rejected_without_generations(owned, fault):
    owner, service = owned
    document = exported(service)
    value = document
    if fault == "unknown":
        document["private"] = CANARIES[3]
    elif fault == "credential":
        document["models"]["profiles"][0]["api_key"] = CANARIES[0]
    elif fault == "duplicate":
        value = json.dumps(document).replace('"version": 1', '"version": 1, "vers\\u0069on": 1', 1)
    elif fault == "version":
        document["version"] = 0
    elif fault == "oversize":
        value = " " * (schema.MAX_DOCUMENT_BYTES + 1)
    elif fault == "reference":
        document["search"]["profiles"][1]["model_profile_id"] = "absent"
    else:
        document["models"]["profiles"][0]["base_url"] = "https://user:private@gateway.example"
    before = owner.store.read_snapshot()
    request = options(document)
    request["configuration"] = value
    with pytest.raises(transfer.TransferError) as caught:
        service.preview(**request)
    unchanged(before, owner.store.read_snapshot())
    public(caught.value)


@pytest.mark.parametrize("legacy", ["models", "search-v1", "search-v2", "rqdata"])
def test_supported_migrations_are_in_memory_and_explained(owned, legacy):
    owner, service = owned
    document = exported(service)
    if legacy == "search-v1":
        document["search"] = {"version": 1, "provider": "brave"}
        key = "search"
    else:
        key = "search" if legacy == "search-v2" else legacy
        document[key]["active_id"] = document[key].pop("selection").get("id")
    original = copy.deepcopy(document)
    migrated = schema.validate_transfer_document(document)
    before = owner.store.read_snapshot()
    request = options(migrated, services=[key])
    request["configuration"] = json.dumps(document)
    preview = service.preview(**request)
    assert preview["migrations"]
    assert document == original
    unchanged(before, owner.store.read_snapshot())


def test_missing_policy_none_and_environment_never_inherit(owned, monkeypatch):
    owner, service = owned
    monkeypatch.setenv("CONEXUS_MODEL_API_KEY", CANARIES[4])
    document = exported(service)
    request = options(document, services=["models"])
    request["credential_policy"] = []
    preview = service.preview(**request)
    assert not preview["can_apply"] and preview["receipt"] is None
    assert preview["diagnostics"][0]["code"] == "CREDENTIAL_DECISION_REQUIRED"
    document["models"]["selection"] = {"kind": "environment"}
    preview = service.preview(**options(document, services=["models"]))
    assert not preview["can_apply"]
    assert "ENVIRONMENT_SELECTION_UNSUPPORTED" in {d["code"] for d in preview["diagnostics"]}
    document["models"]["selection"] = {"kind": "none"}
    result = apply(service, service.preview(**options(document, services=["models"])))
    assert ConfigurationProfileServices(owner.store).models.active_profile() is None
    public(result)


def test_new_profile_and_reappearing_id_never_borrow_old_keys(owned):
    owner, service = owned
    document = exported(service)
    document["rqdata"]["profiles"] = []
    document["rqdata"]["selection"] = {"kind": "none"}
    apply(service, service.preview(**options(document, services=["rqdata"])))
    document["rqdata"] = {
        "version": 1,
        "selection": {"kind": "profile", "id": "rq"},
        "profiles": [
            {
                "id": "rq",
                "name": "Restored metadata",
                "provider": "rqdata",
                "user": "fixture-user",
                "host": "rq.example:16011",
            }
        ],
    }
    with pytest.raises(transfer.TransferError) as caught:
        service.preview(**options(document, services=["rqdata"], action="retain"))
    assert caught.value.code == "RETENTION_NOT_ELIGIBLE"
    preview = service.preview(**options(document, services=["rqdata"], action="replace"))
    with pytest.raises(transfer.TransferError) as caught:
        apply(
            service, preview, credentials=[{"service": "rqdata", "id": "rq", "value": CANARIES[3]}]
        )
    assert caught.value.code == "REVOKED_CREDENTIAL"
    apply(service, preview, credentials=[{"service": "rqdata", "id": "rq", "value": FRESH}])
    assert owner.store.read_snapshot().documents["rqdata"]["profiles"][0]["password"] == FRESH


def test_untouched_extensions_and_search_v1_are_preserved(owned):
    owner, service = owned

    def legacy(old):
        docs, choices = old.documents, old.selections
        docs["search"] = {"version": 1, "provider": "brave", "brave_api_key": CANARIES[2]}
        choices["search"] = {"kind": "profile", "id": "default"}
        docs["rqdata"]["private_unrelated"] = {"keep": [1, 2, 3]}
        return docs, choices

    owner.store.update(legacy)
    before = owner.store.read_snapshot()
    # Strict export refuses private extension fields; internal comparison views
    # must not drop those fields from an unrelated service during import.
    with pytest.raises(transfer.TransferError):
        service.export()
    document, _ = transfer._view(before.documents, before.selections)
    document["models"]["profiles"][0]["name"] = "Portable model metadata"
    preview = service.preview(**options(document, services=["models"], action="retain"))
    apply(service, preview)
    after = owner.store.read_snapshot()
    assert after.documents["search"] == before.documents["search"]
    assert after.documents["rqdata"] == before.documents["rqdata"]
    assert after._texts[1:] == before._texts[1:]


def test_competing_imports_capacity_and_rollback_conflicts(owned, monkeypatch):
    owner, service = owned
    document = exported(service)
    first = service.preview(**options(document))
    second = service.preview(**options(document))
    before = owner.store.read_snapshot()
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_MAX_GENERATIONS", len(before._history))
        with pytest.raises(transfer.TransferError) as caught:
            apply(service, first)
        assert caught.value.code == "HISTORY_CAPACITY"
        unchanged(before, owner.store.read_snapshot())
    assert service.operation(first["receipt"])["state"] == "rejected"
    result = apply(service, second)
    with pytest.raises(transfer.TransferError):
        apply(service, first)
    owner.store.update(lambda old: (old.documents, old.selections))
    with pytest.raises(transfer.TransferError) as caught:
        service.rollback_preview(
            result["operation"], credential_policy=options(document)["credential_policy"]
        )
    assert caught.value.code == "ROLLBACK_CONFLICT"


def test_uncertain_acknowledgement_is_inspected_not_replayed(owned, monkeypatch):
    owner, service = owned
    preview = service.preview(**options(exported(service)))
    original = transfer.ConfigurationTransferService._publish

    def uncertain(self, *args):
        original(self, *args)
        raise OSError(CANARIES[3])

    before = owner.store.read_snapshot()
    with monkeypatch.context() as patch:
        patch.setattr(transfer.ConfigurationTransferService, "_publish", uncertain)
        with pytest.raises(transfer.TransferError) as caught:
            apply(service, preview)
    assert caught.value.code == "APPLICATION_UNCERTAIN"
    public(caught.value)
    assert len(owner.store.read_snapshot()._history) == len(before._history) + 1
    assert service.operation(preview["receipt"])["state"] == "uncertain"
    with pytest.raises(transfer.TransferError) as caught:
        apply(service, preview)
    assert caught.value.code == "RECEIPT_USED"
    assert len(owner.store.read_snapshot()._history) == len(before._history) + 1


@pytest.fixture
def fake_sdk_for_app(activated_agent):
    owner = activated_agent[0]
    with owner.scope():
        from apps.api import main
    main.app.state.configuration_data_options = {"sdk_factory": activation.ActivationSDK}
    try:
        yield
    finally:
        del main.app.state.configuration_data_options


@pytest.fixture
def api_client(fake_sdk_for_app, production_client):
    import httpx

    client, runtime, _ = production_client
    client.base_url = httpx.URL("http://127.0.0.1")
    client._transport.client = ("127.0.0.1", 50111)
    client.headers["X-AlphaLab-Configuration-Consent"] = "local-configuration-transfer"
    return client, runtime


def apply_body(plan):
    return dict(
        receipt=plan["receipt"],
        expected_revision=plan["revision"],
        confirm=True,
        consents=plan["required_consents"],
        credentials=[],
    )


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
def test_actual_production_routes_export_apply_rollback_and_readiness(api_client):
    client, runtime = api_client
    prefix = "/api/configuration-transfer"
    response = client.get(prefix + "/export")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    original = response.json()
    document = copy.deepcopy(original)
    document["models"]["selection"] = {"kind": "none"}
    preview = client.post(prefix + "/preview", json=options(document, services=["models"]))
    assert preview.status_code == 200, preview.text
    public(preview.text)
    result = client.post(prefix + "/apply", json=apply_body(preview.json()))
    assert result.status_code == 200, result.text
    status = client.get("/api/conexus/status")
    assert status.status_code == 200, status.text
    assert status.json()["model_configured"] is False
    assert client.post(prefix + "/apply", json=apply_body(preview.json())).status_code == 409
    rollback = client.post(
        prefix + "/rollback/preview",
        json={
            "operation": result.json()["operation"],
            "credential_policy": options(original, services=["models"])["credential_policy"],
        },
    )
    assert rollback.status_code == 200, rollback.text
    undo = client.post(prefix + "/rollback/apply", json=apply_body(rollback.json()))
    assert undo.status_code == 200, undo.text
    assert client.get(prefix + "/export").json() == original
    assert client.get("/api/model-providers").json()["configured"] is False
    assert undo.json()["revision"] != result.json()["revision"]
    assert all(
        row["api_key"] == ""
        for row in runtime.backend.store.read_snapshot().documents["models"]["profiles"]
    )
    public(undo.text)


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
def test_actual_api_local_authorization_and_private_validation(api_client, monkeypatch):
    client, runtime = api_client
    prefix = "/api/configuration-transfer"
    for origin in ("https://evil.example", "null", "http://user:secret@localhost"):
        assert client.get(prefix + "/export", headers={"Origin": origin}).status_code == 403
    for header in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP"):
        assert client.get(prefix + "/export", headers={header: "127.0.0.1"}).status_code == 403
    client._transport.client = ("198.51.100.7", 1234)
    assert client.get(prefix + "/export").status_code == 403
    client._transport.client = ("127.0.0.1", 1234)
    assert (
        client.get(prefix + "/export", headers={"X-AlphaLab-Configuration-Consent": ""}).status_code
        == 403
    )
    before = runtime.backend.store.read_snapshot()
    for payload in (
        '{"configuration":{"password":"' + CANARIES[3] + '"}}',
        '{"credentials":"' + CANARIES[0] + '","credentials":null}',
        '{"configuration":NaN}',
        '"' + FRESH + '"',
        '{"root":"' + CANARIES[1] + '","backend":"sqlite"}',
    ):
        response = client.post(
            prefix + "/preview", content=payload, headers={"Content-Type": "application/json"}
        )
        assert response.status_code in {422, 503}, response.text
        public(response.text)
    unchanged(before, runtime.backend.store.read_snapshot())
    monkeypatch.setenv("ALPHALAB_WEB_AUTH_ENABLED", "true")
    monkeypatch.setenv("ALPHALAB_WEB_USERNAME", "synthetic-user")
    monkeypatch.setenv("ALPHALAB_WEB_PASSWORD", FRESH)
    assert client.get(prefix + "/export").status_code == 401
    assert client.get(prefix + "/export", auth=("synthetic-user", FRESH)).status_code == 200


def test_receipts_and_operations_survive_owner_restart(candidate, node):
    activation.activate(candidate, node)
    root = candidate[0]
    with ConfigurationBackend.production(root) as owner:
        service = transfer.ConfigurationTransferService(owner)
        preview = service.preview(**options(exported(service), services=["rqdata"]))
    with ConfigurationBackend.production(root) as restarted:
        result = apply(transfer.ConfigurationTransferService(restarted), preview)
    with ConfigurationBackend.production(root) as restarted:
        service = transfer.ConfigurationTransferService(restarted)
        assert service.operation(result["operation"])["revision"] == result["revision"]
        with pytest.raises(transfer.TransferError) as caught:
            apply(service, preview)
        assert caught.value.code == "RECEIPT_USED"


def test_concurrent_receipt_attempts_publish_at_most_once(owned):
    owner, service = owned
    preview = service.preview(**options(exported(service)))
    before = owner.store.read_snapshot()

    def attempt(_):
        try:
            return apply(service, preview)["state"]
        except transfer.TransferError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count("committed") == 1
    assert all(value in {"committed", "RECEIPT_USED", "BUSY"} for value in results)
    assert len(owner.store.read_snapshot()._history) == len(before._history) + 1


def test_process_safe_plan_claim_has_one_winner(owned):
    owner, service = owned
    preview = service.preview(**options(exported(service)))
    program = """
import sys,time
from alphalab.utils.configuration_transfer_plans import transfer_plans
try:
    with transfer_plans(sys.argv[1]) as plans:
        time.sleep(.15)
        plans.claim(sys.argv[2])
except Exception:
    print('rejected')
else:
    print('claimed')
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-B", "-c", program, str(owner.root), preview["receipt"]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        for _ in range(2)
    ]
    try:
        outputs = [child.communicate(timeout=15) for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert sorted(out.strip() for out, _ in outputs) == [b"claimed", b"rejected"]
    for out, err in outputs:
        public(out.decode() + err.decode())


@pytest.mark.parametrize("phase", ["claimed", "committed"])
def test_process_interruption_never_replays_a_claimed_plan(candidate, node, phase):
    activation.activate(candidate, node)
    root = candidate[0]
    with ConfigurationBackend.production(root) as owner:
        service = transfer.ConfigurationTransferService(owner)
        preview = service.preview(**options(exported(service), services=["rqdata"]))
        prior = owner.store.read_snapshot().revision
    program = """
import json,sys
from alphalab.utils.configuration_backend import ConfigurationBackend
from apps.api.services.configuration_transfer_service import ConfigurationTransferService
data=json.loads(sys.stdin.readline())
original=ConfigurationTransferService._publish
def interrupted(self,*args):
    if sys.argv[2]=='committed': original(self,*args)
    print('paused',flush=True)
    sys.stdin.readline()
ConfigurationTransferService._publish=interrupted
with ConfigurationBackend.production(sys.argv[1]) as owner:
    ConfigurationTransferService(owner).apply(data['receipt'],expected_revision=data['revision'],confirm=True,consents=data['required_consents'],credentials=[])
"""
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", program, str(root), phase],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        child.stdin.write(json.dumps(preview) + "\n")
        child.stdin.flush()
        assert pool.submit(child.stdout.readline).result(timeout=15) == "paused\n"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        pool.shutdown(wait=True)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()
    with ConfigurationBackend.production(root) as owner:
        service = transfer.ConfigurationTransferService(owner)
        assert service.operation(preview["receipt"])["state"] == "claimed"
        assert (owner.store.read_snapshot().revision != prior) == (phase == "committed")
        with pytest.raises(transfer.TransferError) as caught:
            apply(service, preview)
        assert caught.value.code == "RECEIPT_USED"


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
def test_negative_control_raw_snapshot_export_leaks_through_real_api(api_client, monkeypatch):
    client, runtime = api_client
    path = "/api/configuration-transfer/export"
    public(client.get(path).text)

    def unsafe_export(self):
        return json.dumps(self.backend.store.read_snapshot().documents)

    with monkeypatch.context() as patch:
        patch.setattr(transfer.ConfigurationTransferService, "export", unsafe_export)
        leaked = client.get(path)
        assert leaked.status_code == 200
        assert all(secret in leaked.text for secret in CANARIES[:4])
        with pytest.raises(AssertionError):
            public(leaked.text)
    public(client.get(path).text)


def test_negative_control_destination_identity_bypass_retains_old_key(owned, monkeypatch):
    owner, service = owned
    document = exported(service)
    row = next(row for row in document["models"]["profiles"] if row["id"] == "model")
    row["model"] = "provider/different-model"
    request = options(document, services=["models"], action="retain")
    with pytest.raises(transfer.TransferError) as caught:
        service.preview(**request)
    assert caught.value.code == "RETENTION_NOT_ELIGIBLE"
    with monkeypatch.context() as patch:
        patch.setattr(transfer, "_identity", lambda *args: ())
        apply(service, service.preview(**request))
        row = next(
            row
            for row in owner.store.read_snapshot().documents["models"]["profiles"]
            if row["id"] == "model"
        )
        with pytest.raises(AssertionError, match="Destination changed"):
            assert row["api_key"] != CANARIES[0], "Destination changed but old key was retained"


def test_negative_control_stale_binding_overwrites_a_winning_edit(owned, monkeypatch):
    owner, service = owned
    document = exported(service)
    preview = service.preview(**options(document, services=["rqdata"]))
    profile = owner.store.read_snapshot().documents["rqdata"]["profiles"][0].copy()
    ConfigurationProfileServices(owner.store).rqdata.save_profile(
        {**profile, "name": "Winning new name"}, activate=True
    )
    with pytest.raises(transfer.TransferError) as caught:
        apply(service, preview)
    assert caught.value.code == "STALE_PREVIEW"
    with monkeypatch.context() as patch:
        patch.setattr(transfer.ConfigurationTransferService, "_check_binding", lambda *args: None)
        apply(service, preview)
        with pytest.raises(AssertionError, match="Stale preview overwrote"):
            assert (
                owner.store.read_snapshot().documents["rqdata"]["profiles"][0]["name"]
                == "Winning new name"
            ), "Stale preview overwrote the winner"


def test_negative_control_replay_guards_prevent_two_concurrent_publications(owned, monkeypatch):
    from alphalab.utils.configuration_transfer_plans import TransferPlans

    owner, service = owned
    preview = service.preview(**options(exported(service), services=["rqdata"]))
    before = owner.store.read_snapshot()

    def attempt(_):
        # Bounded caller contention: reattempt BUSY only, never an uncertain or
        # already consumed outcome. This is a disposable negative-control caller.
        for _ in range(100):
            try:
                return apply(service, preview)["state"]
            except transfer.TransferError as error:
                if error.code != "BUSY":
                    return error.code
                time.sleep(0.01)
        return "BUSY"

    with monkeypatch.context() as patch:
        patch.setattr(transfer.ConfigurationTransferService, "_check_binding", lambda *args: None)
        patch.setattr(TransferPlans, "claim", TransferPlans.read)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        assert results == ["committed", "committed"]
        with pytest.raises(AssertionError, match="Receipt replay published twice"):
            assert len(owner.store.read_snapshot()._history) == len(before._history) + 1, (
                "Receipt replay published twice"
            )


def test_negative_control_split_publication_detects_mixed_services(owned, monkeypatch):
    owner, service = owned
    document = exported(service)
    identifiers = {key: document[key]["profiles"][0]["id"] for key in transfer.SERVICES}
    before = owner.store.read_snapshot()
    for key in transfer.SERVICES:
        document[key]["profiles"][0]["name"] = "Imported " + key
    preview = service.preview(**options(document))

    def split(self, docs, selections, revision):
        partial = self.backend.store.read_snapshot().documents
        partial["models"] = docs["models"]
        self.backend.store.update(lambda old: (partial, old.selections), expected_revision=revision)
        raise OSError("Synthetic interruption between independent service writes")

    def names(documents):
        return tuple(
            next(row["name"] for row in documents[key]["profiles"] if row["id"] == identifiers[key])
            for key in transfer.SERVICES
        )

    with monkeypatch.context() as patch:
        patch.setattr(transfer.ConfigurationTransferService, "_publish", split)
        with pytest.raises(transfer.TransferError):
            apply(service, preview)
        with pytest.raises(AssertionError, match="Mixed service publication"):
            assert names(owner.store.read_snapshot().documents) in {
                names(before.documents),
                names(document),
            }, "Mixed service publication"


def test_negative_control_rollback_history_lookup_resurrects_cleared_key(owned, monkeypatch):
    owner, service = owned
    document = exported(service)
    result = apply(service, service.preview(**options(document, services=["rqdata"])))
    rollback = service.rollback_preview(
        result["operation"],
        credential_policy=options(document, services=["rqdata"], action="retain")[
            "credential_policy"
        ],
    )
    assert {"service": "rqdata", "id": "rq", "code": "CREDENTIAL_REQUIRED"} in rollback[
        "missing_credentials"
    ]
    original = transfer._native

    def historical_fallback(*args, **kwargs):
        documents, selections, undecided = original(*args, **kwargs)
        history = owner.store.read_snapshot().extract_history_private()
        documents["rqdata"]["profiles"][0]["password"] = history[0]["rqdata"]["profiles"][0][
            "password"
        ]
        return documents, selections, undecided

    with monkeypatch.context() as patch:
        patch.setattr(transfer, "_native", historical_fallback)
        apply(service, rollback, kind="rollback")
        resolved = (
            ConfigurationProfileServices(owner.store).rqdata.active_config().extract_private()
        )
        with pytest.raises(AssertionError, match="Historical credential reactivated"):
            assert resolved["RQ_PASSWORD"] != CANARIES[3], "Historical credential reactivated"


def test_negative_control_cross_installation_receipt_binding(owned, tmp_path, monkeypatch):
    owner, service = owned
    preview = service.preview(**options(exported(service), services=["rqdata"]))
    with transfer_plans(owner.root) as plans:
        stolen = plans.read(preview["receipt"])
    root = tmp_path / "independent-installation"
    docs = native_documents()
    choices = {key: {"kind": "profile", "id": value["active_id"]} for key, value in docs.items()}
    store = storage.ConfigurationStore.initialize(root, documents=docs, selections=choices)
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as other:
        other_service = transfer.ConfigurationTransferService(other)
        with transfer_plans(root, create=True) as plans:
            plans.write(stolen, new=True)
        before = store.read_snapshot()
        with pytest.raises(transfer.TransferError) as caught:
            apply(other_service, preview)
        assert caught.value.code == "WRONG_INSTALLATION"
        with monkeypatch.context() as patch:
            patch.setattr(
                transfer.ConfigurationTransferService, "_check_binding", lambda *args: None
            )
            apply(other_service, preview)
            with pytest.raises(AssertionError, match="Foreign receipt modified"):
                assert store.read_snapshot().revision == before.revision, (
                    "Foreign receipt modified another installation"
                )


def test_negative_control_none_must_not_resolve_environment_credentials(owned, monkeypatch):
    owner, service = owned
    document = exported(service)
    document["models"]["selection"] = {"kind": "none"}
    apply(service, service.preview(**options(document, services=["models"])))
    environment = {
        "CONEXUS_MODEL_BASE_URL": "https://openrouter.ai/api/v1",
        "CONEXUS_MODEL_ID": "provider/model",
        "CONEXUS_MODEL_API_KEY": FRESH,
    }
    profiles = ConfigurationProfileServices(owner.store, environment=environment)
    assert profiles.models.active_profile() is None
    from apps.api.services.configuration_profile_service import ProfileSnapshot

    original = ProfileSnapshot._active

    def fallback(self, key):
        if key == "models" and self.selections[key]["kind"] == "none":
            return {
                "id": "environment",
                "name": "Environment",
                "base_url": self._environment["CONEXUS_MODEL_BASE_URL"],
                "model": self._environment["CONEXUS_MODEL_ID"],
                "api_key": self._environment["CONEXUS_MODEL_API_KEY"],
            }
        return original(self, key)

    with monkeypatch.context() as patch:
        patch.setattr(ProfileSnapshot, "_active", fallback)
        with pytest.raises(AssertionError, match="Explicit none borrowed"):
            assert profiles.models.active_profile() is None, (
                "Explicit none borrowed environment credentials"
            )
    assert profiles.models.active_profile() is None


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
def test_negative_control_default_framework_validation_echoes_private_input(api_client):
    from fastapi import Body

    from apps.api import main

    client, _ = api_client
    protected = client.post(
        "/api/configuration-transfer/preview", json={"configuration": {"password": CANARIES[3]}}
    )
    public(protected.text)

    async def unprotected(value: int = Body()):
        return {"value": value}

    previous = list(main.app.router.routes)
    try:
        main.app.add_api_route(
            "/api/configuration-transfer/test-only-unprotected", unprotected, methods=["POST"]
        )
        response = client.post(
            "/api/configuration-transfer/test-only-unprotected", json=CANARIES[3]
        )
        assert response.status_code == 422
        assert CANARIES[3] in response.text
        with pytest.raises(AssertionError):
            public(response.text)
    finally:
        main.app.router.routes[:] = previous


def test_actual_json_production_export_and_unsupported_apply(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from alphalab import ResultStore

    root = tmp_path / "json-api"
    write_sources(root, native_documents())
    with ConfigurationBackend.production(root) as owner, owner.scope():
        from apps.api import main

        monkeypatch.setattr(main, "ResultStore", lambda: ResultStore(root / "app/alphalab.db"))
        main.app.state.configuration_backend = owner
        try:
            with TestClient(
                main.app,
                base_url="http://127.0.0.1",
                client=("127.0.0.1", 50001),
                headers={"X-AlphaLab-Configuration-Consent": "local-configuration-transfer"},
            ) as client:
                response = client.get("/api/configuration-transfer/export")
                assert response.status_code == 200, response.text
                public(response.text)
                preview = client.post(
                    "/api/configuration-transfer/preview", json=options(response.json())
                )
                assert preview.status_code == 200, preview.text
                assert not preview.json()["can_apply"] and preview.json()["receipt"] is None
                result = client.post(
                    "/api/configuration-transfer/apply",
                    json=dict(
                        receipt="a" * 32,
                        expected_revision="b" * 32,
                        confirm=True,
                        consents=[],
                        credentials=[],
                    ),
                )
                assert result.status_code == 422
                assert result.json()["detail"]["code"] == "UNSUPPORTED_JSON_APPLY"
                assert (
                    owner.mode == "json"
                    and not (root / "app/secrets/configuration-store.sqlite3").exists()
                )
        finally:
            del main.app.state.configuration_backend


@pytest.mark.parametrize("fault", ["pending", "invalid-result", "duplicate", "limit"])
def test_private_plan_damage_is_not_repaired_or_publicly_exposed(owned, monkeypatch, fault):
    from alphalab.utils import configuration_transfer_plans as controls

    owner, service = owned
    preview = service.preview(**options(exported(service)))
    directory = owner.root / "app/secrets/configuration-transfers"
    path = directory / (preview["receipt"] + ".json")
    if fault == "pending":
        (directory / ".interrupted.pending").write_text("synthetic incomplete plan")
    elif fault == "invalid-result":
        value = json.loads(path.read_text())
        value["result_revision"] = CANARIES[3]
        path.write_text(json.dumps(value))
    elif fault == "duplicate":
        raw = path.read_text()
        path.write_text(raw.replace('"version": 1', '"version": 1,"version":1'))
    else:
        monkeypatch.setattr(controls, "MAX_PLANS", 1)
    before = owner.store.read_snapshot()
    files = {p: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(transfer.TransferError) as caught:
        if fault == "limit":
            service.preview(**options(exported(service)))
        else:
            apply(service, preview)
    public(caught.value)
    unchanged(before, owner.store.read_snapshot())
    assert {p: p.read_bytes() for p in directory.iterdir()} == files


def test_failure_before_commit_rolls_back_all_services_and_consumes_receipt(owned, monkeypatch):
    owner, service = owned
    document = exported(service)
    for key in transfer.SERVICES:
        document[key]["profiles"][0]["name"] = "Attempted metadata " + key
    preview = service.preview(**options(document))
    before = owner.store.read_snapshot()
    original = storage._Session.execute
    reached = []

    def fail(session, sql, parameters=()):
        if sql.startswith("UPDATE store_metadata SET current_revision"):
            reached.append(True)
            raise OSError(CANARIES[0])
        return original(session, sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(storage._Session, "execute", fail)
        with pytest.raises(transfer.TransferError) as caught:
            apply(service, preview)
    assert reached == [True]
    assert caught.value.code == "APPLICATION_REJECTED"
    public(caught.value)
    unchanged(before, owner.store.read_snapshot())
    assert len(owner.store.read_snapshot()._history) == len(before._history)
    assert service.operation(preview["receipt"])["state"] == "rejected"


def test_failed_operation_acknowledgement_leaves_confirmable_current_state(owned, monkeypatch):
    from alphalab.utils.configuration_transfer_plans import TransferPlans

    owner, service = owned
    preview = service.preview(**options(exported(service), services=["rqdata"]))
    before = owner.store.read_snapshot()
    original = TransferPlans.write

    def lost_ack(self, value, **kwargs):
        original(self, value, **kwargs)
        if value["state"] == "committed":
            raise OSError(CANARIES[3])

    with monkeypatch.context() as patch:
        patch.setattr(TransferPlans, "write", lost_ack)
        with pytest.raises(transfer.TransferError) as caught:
            apply(service, preview)
    assert caught.value.code == "APPLICATION_UNCERTAIN"
    public(caught.value)
    status = service.operation(preview["receipt"])
    assert status["state"] == "committed"
    assert status["revision"] == status["current_revision"] != before.revision
    with pytest.raises(transfer.TransferError) as caught:
        apply(service, preview)
    assert caught.value.code == "RECEIPT_USED"


def test_json_writer_in_another_process_respects_export_snapshot_lease(tmp_path):
    root = tmp_path / "json-writer"
    write_sources(root, native_documents())
    program = """
import sys
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.services.model_provider_service import read_settings,save_profile
try:
    with ConfigurationBackend.production(sys.argv[1]) as owner, owner.json_scope():
        profile=read_settings()['profiles'][0]
        save_profile({**profile,'name':'Concurrent JSON write'},activate=False)
except ConfigurationStoreError:
    print('blocked')
else:
    print('written')
"""
    with ConfigurationBackend.production(root):
        before = {p: p.read_bytes() for p in root.rglob("*.json")}
        with configuration_snapshot_access(root, exclusive=True):
            result = subprocess.run(
                [sys.executable, "-B", "-c", program, str(root)], capture_output=True, timeout=15
            )
        assert result.returncode == 0 and result.stdout.strip() == b"blocked"
        assert {p: p.read_bytes() for p in before} == before
        result = subprocess.run(
            [sys.executable, "-B", "-c", program, str(root)], capture_output=True, timeout=15
        )
        assert result.returncode == 0 and result.stdout.strip() == b"written"
        public(result.stdout.decode() + result.stderr.decode())


@pytest.mark.parametrize(
    "fault,code",
    [
        ("confirmation", "CONSENT_REQUIRED"),
        ("consents", "CONSENT_REQUIRED"),
        ("revision", "CONSENT_REQUIRED"),
        ("missing", "PRIVATE_INPUT_REQUIRED"),
        ("duplicate", "PRIVATE_INPUT_REQUIRED"),
        ("unexpected", "PRIVATE_INPUT_REQUIRED"),
        ("type", "PRIVATE_INPUT_REQUIRED"),
        ("control", "PRIVATE_INPUT_REQUIRED"),
    ],
)
def test_private_apply_input_rejection_keeps_receipt_and_generation(owned, fault, code):
    owner, service = owned
    preview = service.preview(**options(exported(service), services=["rqdata"], action="replace"))
    before = owner.store.read_snapshot()
    body = apply_body(preview)
    body["credentials"] = [{"service": "rqdata", "id": "rq", "value": FRESH}]
    if fault == "confirmation":
        body["confirm"] = False
    elif fault == "consents":
        body["consents"] = []
    elif fault == "revision":
        body["expected_revision"] = "0" * 32
    elif fault == "missing":
        body["credentials"] = []
    elif fault == "duplicate":
        body["credentials"] *= 2
    elif fault == "unexpected":
        body["credentials"][0]["id"] = "other-profile"
    elif fault == "type":
        body["credentials"][0]["value"] = {"private": CANARIES[3]}
    else:
        body["credentials"][0]["value"] = FRESH + "\n"
    with pytest.raises(transfer.TransferError) as caught:
        service.apply(**body)
    assert caught.value.code == code
    public(caught.value)
    unchanged(before, owner.store.read_snapshot())
    assert service.operation(preview["receipt"])["state"] == "pending"


@pytest.mark.parametrize(
    "service_name,identifier,field,value",
    [
        ("models", "model", "base_url", "https://gateway.example/v1"),
        ("search", "search", "model_profile_id", "model"),
        ("rqdata", "rq", "host", "other-rq.example:16011"),
    ],
)
def test_retention_endpoint_reference_and_host_changes_are_not_metadata_renames(
    owned, service_name, identifier, field, value
):
    owner, service = owned
    document = exported(service)
    next(row for row in document[service_name]["profiles"] if row["id"] == identifier)[field] = (
        value
    )
    schema.validate_transfer_document(document)  # Do not pass for unrelated reference failures.
    before = owner.store.read_snapshot()
    with pytest.raises(transfer.TransferError) as caught:
        service.preview(**options(document, services=[service_name], action="retain"))
    assert caught.value.code == "RETENTION_NOT_ELIGIBLE"
    unchanged(before, owner.store.read_snapshot())
    public(caught.value)


def test_live_sqlite_export_never_mixes_concurrent_generations(owned):
    owner, service = owned

    def stamp(index):
        def update(previous):
            documents = previous.documents
            for key in transfer.SERVICES:
                for row in documents[key]["profiles"]:
                    row["name"] = "Capture " + str(index)
            return documents, previous.selections

        owner.store.update(update)

    stamp(0)
    barrier = Barrier(2)

    def publish():
        barrier.wait(timeout=10)
        for index in range(1, 7):
            stamp(index)

    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(publish)
        barrier.wait(timeout=10)
        captured = []
        for _ in range(12):
            document = exported(service)
            names = {row["name"] for key in transfer.SERVICES for row in document[key]["profiles"]}
            assert len(names) == 1
            captured.extend(names)
            public(document)
        writer.result(timeout=20)
    assert captured
    assert exported(service)["models"]["profiles"][0]["name"] == "Capture 6"


def _all_public_settings(owner):
    snapshot = ConfigurationProfileServices(owner.store).read_snapshot()
    result = {}
    for key in transfer.SERVICES:
        try:
            result[key] = snapshot.public_settings(key)
        except storage.ConfigurationStoreError as error:
            assert error.code == "INVALID_CONFIGURATION"
            raise AssertionError("Committed public settings are unreadable") from None
        public(result[key])
    return result


def _history_size(owner):
    return len(owner.store.with_history_checks().read_snapshot()._history)


def _partial_models(service):
    document = exported(service)
    document["models"]["profiles"] = [
        row for row in document["models"]["profiles"] if row["id"] == "inactive"
    ]
    document["models"]["selection"] = {"kind": "profile", "id": "inactive"}
    schema.validate_transfer_document(document)
    return document


def _assert_omitted_model_ready(preview):
    assert {"service": "models", "id": "model", "code": "CREDENTIAL_REQUIRED"} not in preview[
        "missing_credentials"
    ], "Merge falsely reports a retained configured model as missing"
    assert "ALLOW_UNCONFIGURED" not in preview["required_consents"]


@pytest.mark.parametrize("credential", ["saved", "remote", "workstation", "Model"])
def test_correction_publication_validates_generated_and_cross_service_values(
    inactive_owned, credential
):
    owner, service = inactive_owned
    plan = service.preview(**options(exported(service), services=["rqdata"], action="replace"))
    before = owner.store.read_snapshot()
    files = {
        p: p.read_bytes() for p in (owner.root / "app/secrets/configuration-transfers").iterdir()
    }
    with pytest.raises(transfer.TransferError) as caught:
        apply(service, plan, credentials=[{"service": "rqdata", "id": "rq", "value": credential}])
    assert caught.value.code == (
        "CREDENTIAL_IN_METADATA" if credential == "Model" else "UNSAFE_METADATA"
    )
    public(caught.value)
    assert credential not in str(caught.value) + repr(caught.value.args) + repr(
        pickle.dumps(caught.value)
    )
    unchanged(before, owner.store.read_snapshot())
    assert {p: p.read_bytes() for p in files} == files
    assert service.operation(plan["receipt"])["state"] == "pending"
    assert all(row["configured"] for row in _all_public_settings(owner).values())


@pytest.mark.parametrize("owner_fixture", ["inactive_owned", "anchored_inactive_owned", "owned"])
def test_correction_valid_replacement_reuses_publication_contract_and_receipt(
    request, owner_fixture, monkeypatch
):
    owner, service = request.getfixturevalue(owner_fixture)
    plan = service.preview(**options(exported(service), services=["rqdata"], action="replace"))
    before = owner.store.read_snapshot()
    count = _history_size(owner)
    credential = "saved"
    with pytest.raises(transfer.TransferError):
        apply(service, plan, credentials=[{"service": "rqdata", "id": "rq", "value": credential}])
    unchanged(before, owner.store.read_snapshot())
    assert service.operation(plan["receipt"])["state"] == "pending"
    original = ConfigurationProfileServices._check_publication
    checked = []

    # Inspect the plan directly: operation() would reenter the exclusive plan lease.
    def checked_without_reentry(profiles, current, proposed):
        with (
            owner.root / "app/secrets/configuration-transfers" / (plan["receipt"] + ".json")
        ).open() as stream:
            assert json.load(stream)["state"] == "pending"
        assert current.revision == before.revision
        assert CANARIES[3] in transfer._snapshot_credential_values(current._snapshot)
        assert FRESH in transfer._snapshot_credential_values(proposed._snapshot)
        checked.append(True)
        return original(profiles, current, proposed)

    monkeypatch.setattr(ConfigurationProfileServices, "_check_publication", checked_without_reentry)
    result = apply(service, plan, credentials=[{"service": "rqdata", "id": "rq", "value": FRESH}])
    assert checked == [True]
    assert result["revision"] == owner.store.read_snapshot().revision != before.revision
    assert _history_size(owner) == count + 1
    assert all(row["configured"] for row in _all_public_settings(owner).values())
    assert owner.store.read_snapshot().documents["rqdata"]["profiles"][0]["password"] == FRESH
    for path in (owner.root / "app/secrets/configuration-transfers").iterdir():
        public(path.read_text())
    with pytest.raises(transfer.TransferError) as caught:
        apply(service, plan, credentials=[{"service": "rqdata", "id": "rq", "value": FRESH}])
    assert caught.value.code == "RECEIPT_USED"
    assert _history_size(owner) == count + 1


@pytest.mark.parametrize("service_name", transfer.SERVICES)
def test_correction_historical_credentials_rejected_in_new_public_metadata(
    inactive_owned, service_name
):
    owner, service = inactive_owned
    snapshot = owner.store.read_snapshot()
    row = next(
        row
        for row in snapshot.documents[service_name]["profiles"]
        if row["id"] == snapshot.documents[service_name]["active_id"]
    )
    previous_secret = row[transfer.SECRET[service_name]]
    getattr(ConfigurationProfileServices(owner.store), service_name).save_profile(
        {**row, transfer.SECRET[service_name]: FRESH + "-rotated"}, activate=True
    )
    document = exported(service)
    document["models"]["profiles"][0]["name"] = "".join(
        "%" + format(ord(character), "02x") for character in previous_secret
    )
    before = owner.store.read_snapshot()
    with pytest.raises(transfer.TransferError) as caught:
        service.preview(**options(document, services=["models"], action="retain"))
    assert caught.value.code == "CREDENTIAL_IN_METADATA"
    public(caught.value)
    unchanged(before, owner.store.read_snapshot())
    _all_public_settings(owner)


@pytest.mark.parametrize("original_secret", ["saved", " saved "])
@pytest.mark.parametrize("rotate_first", [False, True])
def test_correction_publication_scans_cleared_current_and_historical_values(
    tmp_path, original_secret, rotate_first
):
    documents = native_documents()
    for document in documents.values():
        document["active_id"] = None
    documents["rqdata"]["profiles"][0]["password"] = original_secret
    choices = {key: {"kind": "none"} for key in transfer.SERVICES}
    root = tmp_path / "unselected-installation"
    store = storage.ConfigurationStore.initialize(root, documents=documents, selections=choices)
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        service = transfer.ConfigurationTransferService(owner)
        if rotate_first:
            row = owner.store.read_snapshot().documents["rqdata"]["profiles"][0]
            ConfigurationProfileServices(owner.store).rqdata.save_profile(
                {**row, "password": FRESH}, activate=False
            )
        _all_public_settings(owner)
        document = exported(service)
        document["rqdata"]["selection"] = {"kind": "profile", "id": "rq"}
        plan = service.preview(**options(document, services=["rqdata"]))
        before = owner.store.read_snapshot()
        with pytest.raises(transfer.TransferError) as caught:
            apply(service, plan)
        assert caught.value.code == "UNSAFE_METADATA"
        public(caught.value)
        assert original_secret.strip() not in str(caught.value)
        unchanged(before, owner.store.read_snapshot())
        assert service.operation(plan["receipt"])["state"] == "pending"
        _all_public_settings(owner)


@pytest.mark.parametrize("owner_fixture", ["inactive_owned", "anchored_inactive_owned", "owned"])
def test_correction_capacity_rejects_inactive_and_durable_publication(
    request, owner_fixture, monkeypatch
):
    owner, service = request.getfixturevalue(owner_fixture)
    before = owner.store.read_snapshot()
    assert _history_size(owner) == 1
    monkeypatch.setattr(storage, "_MAX_GENERATIONS", 1)
    plan = service.preview(**options(exported(service)))
    with pytest.raises(transfer.TransferError) as caught:
        apply(service, plan)
    assert caught.value.code == "HISTORY_CAPACITY"
    public(caught.value)
    unchanged(before, owner.store.read_snapshot())
    assert _history_size(owner) == 1
    assert service.operation(plan["receipt"])["state"] == "rejected"
    exported(service)
    _all_public_settings(owner)


@pytest.mark.parametrize("owner_fixture", ["inactive_owned", "anchored_inactive_owned", "owned"])
def test_correction_below_capacity_has_one_child_generation(request, owner_fixture, monkeypatch):
    owner, service = request.getfixturevalue(owner_fixture)
    before = owner.store.read_snapshot()
    anchor = owner.store._anchor
    monkeypatch.setattr(storage, "_MAX_GENERATIONS", 2)
    plan = service.preview(**options(exported(service), services=["rqdata"], action="retain"))
    result = apply(service, plan)
    assert _history_size(owner) == 2
    with closing(
        sqlite3.connect(owner.root / "app/secrets/configuration-store.sqlite3")
    ) as database:
        parent = database.execute(
            "SELECT parent_revision FROM generations WHERE revision=?", (result["revision"],)
        ).fetchone()
    assert parent == (before.revision,)
    assert owner.store._anchor == anchor
    exported(service)
    _all_public_settings(owner)


@pytest.mark.parametrize("owner_fixture", ["inactive_owned", "anchored_inactive_owned", "owned"])
def test_correction_rollback_uses_same_capacity_guard(request, owner_fixture, monkeypatch):
    owner, service = request.getfixturevalue(owner_fixture)
    document = exported(service)
    document["rqdata"]["profiles"][0]["name"] = "Transfer to undo"
    monkeypatch.setattr(storage, "_MAX_GENERATIONS", 2)
    result = apply(
        service, service.preview(**options(document, services=["rqdata"], action="retain"))
    )
    rollback = service.rollback_preview(
        result["operation"],
        credential_policy=options(document, services=["rqdata"], action="retain")[
            "credential_policy"
        ],
    )
    before = owner.store.read_snapshot()
    with pytest.raises(transfer.TransferError) as caught:
        apply(service, rollback, kind="rollback")
    assert caught.value.code == "HISTORY_CAPACITY"
    unchanged(before, owner.store.read_snapshot())
    assert _history_size(owner) == 2
    assert service.operation(result["operation"])["state"] == "committed"
    assert service.operation(rollback["receipt"])["state"] == "rejected"
    exported(service)


def test_correction_concurrent_save_after_preflight_is_not_overwritten(inactive_owned, monkeypatch):
    owner, service = inactive_owned
    document = exported(service)
    document["models"]["profiles"][0]["name"] = "Losing import"
    plan = service.preview(**options(document, services=["models"], action="retain"))
    count = _history_size(owner)
    original = transfer.ConfigurationTransferService._publish
    attempts = []
    winner = []

    def race(instance, documents, selections, revision):
        attempts.append(True)
        row = instance.backend.store.read_snapshot().documents["rqdata"]["profiles"][0]
        ConfigurationProfileServices(instance.backend.store).rqdata.save_profile(
            {**row, "name": "Winning unrelated save"}, activate=True
        )
        winner.append(instance.backend.store.read_snapshot())
        return original(instance, documents, selections, revision)

    monkeypatch.setattr(transfer.ConfigurationTransferService, "_publish", race)
    with pytest.raises(transfer.TransferError) as caught:
        apply(service, plan)
    # A different observed revision remains conservative/uncertain, never retried.
    assert caught.value.code == "APPLICATION_UNCERTAIN"
    assert attempts == [True]
    unchanged(winner[0], owner.store.read_snapshot())
    assert _history_size(owner) == count + 1
    assert service.operation(plan["receipt"])["state"] == "uncertain"
    _all_public_settings(owner)


@pytest.mark.parametrize("action", ["retain", "replace"])
def test_correction_merge_omitted_model_remains_configured(inactive_owned, action):
    owner, service = inactive_owned
    document = _partial_models(service)
    request = options(document, services=["models"], mode="merge", action=action)
    request["apply_selections"] = False
    before = owner.store.read_snapshot()
    preview = service.preview(**request)
    _assert_omitted_model_ready(preview)
    unchanged(before, owner.store.read_snapshot())
    assert preview["changes"]["models"]["removed"] == []
    credentials = (
        [{"service": "models", "id": "inactive", "value": FRESH}] if action == "replace" else []
    )
    apply(service, preview, credentials=credentials)
    after = owner.store.read_snapshot()
    old = {row["id"]: row for row in before.documents["models"]["profiles"]}
    new = {row["id"]: row for row in after.documents["models"]["profiles"]}
    assert new["model"] == old["model"]
    assert new["inactive"]["api_key"] == (
        FRESH if action == "replace" else old["inactive"]["api_key"]
    )
    assert after.selections == before.selections
    assert all(row["configured"] for row in _all_public_settings(owner).values())
    public(preview)


@pytest.mark.parametrize("decision", [None, "clear"])
def test_correction_merge_does_not_infer_policy_for_imported_profiles(inactive_owned, decision):
    owner, service = inactive_owned
    document = _partial_models(service)
    document["models"]["profiles"][0]["name"] = "Imported inactive edit"
    request = options(document, services=["models"], mode="merge")
    if decision is None:
        request["credential_policy"] = []
    before = owner.store.read_snapshot()
    preview = service.preview(**request)
    assert {"service": "models", "id": "model", "code": "CREDENTIAL_REQUIRED"} not in preview[
        "missing_credentials"
    ]
    assert {"service": "models", "id": "inactive", "code": "CREDENTIAL_REQUIRED"} in preview[
        "missing_credentials"
    ]
    if decision is None:
        assert not preview["can_apply"] and preview["receipt"] is None
        assert "CREDENTIAL_DECISION_REQUIRED" in {row["code"] for row in preview["diagnostics"]}
        unchanged(before, owner.store.read_snapshot())
    else:
        apply(service, preview)
        profiles = _all_public_settings(owner)
        assert profiles["models"]["configured"] is False
        assert profiles["search"]["configured"] is False
        assert profiles["models"]["selection"] == {"kind": "profile", "id": "inactive"}
        rows = {
            row["id"]: row for row in owner.store.read_snapshot().documents["models"]["profiles"]
        }
        assert rows["inactive"]["api_key"] == "" and rows["model"]["api_key"] == CANARIES[0]


@pytest.mark.parametrize("recreated", [False, True])
def test_correction_merge_new_or_recreated_id_never_borrows_credentials(inactive_owned, recreated):
    owner, service = inactive_owned
    original = exported(service)
    if recreated:
        removal = _partial_models(service)
        apply(service, service.preview(**options(removal, services=["models"], action="retain")))
    document = _partial_models(service)
    new_profile = next(row.copy() for row in original["models"]["profiles"] if row["id"] == "model")
    identifier = "model" if recreated else "new-model"
    new_profile.update(id=identifier, name="Imported profile without credential")
    document["models"]["profiles"].append(new_profile)
    document["models"]["selection"] = {"kind": "profile", "id": identifier}
    request = options(document, services=["models"], mode="merge", action="retain")
    request["credential_policy"][-1]["action"] = "clear"
    preview = service.preview(**request)
    assert {"service": "models", "id": identifier, "code": "CREDENTIAL_REQUIRED"} in preview[
        "missing_credentials"
    ]
    apply(service, preview)
    rows = {row["id"]: row for row in owner.store.read_snapshot().documents["models"]["profiles"]}
    assert rows[identifier]["api_key"] == "" and rows["inactive"]["api_key"] == CANARIES[1]
    assert _all_public_settings(owner)["models"]["configured"] is False


@pytest.mark.parametrize("mode", ["merge", "replace"])
def test_correction_replace_removes_omitted_profiles_while_merge_keeps_them(inactive_owned, mode):
    owner, service = inactive_owned
    request = options(_partial_models(service), services=["models"], mode=mode, action="retain")
    preview = service.preview(**request)
    assert preview["changes"]["models"]["removed"] == ([] if mode == "merge" else ["model"])
    apply(service, preview)
    identifiers = {row["id"] for row in owner.store.read_snapshot().documents["models"]["profiles"]}
    assert ("model" in identifiers) == (mode == "merge")
    assert all(row["configured"] for row in _all_public_settings(owner).values())


def test_correction_same_id_across_services_has_independent_credential_decisions(inactive_owned):
    owner, service = inactive_owned

    def shared_ids(previous):
        documents, selections = previous.documents, previous.selections
        for key in transfer.SERVICES:
            documents[key]["profiles"] = documents[key]["profiles"][:1]
            documents[key]["profiles"][0]["id"] = "shared"
            documents[key]["active_id"] = "shared"
            selections[key] = {"kind": "profile", "id": "shared"}
        documents["search"]["profiles"][0]["model_profile_id"] = "shared"
        return documents, selections

    owner.store.with_history_checks().update(shared_ids)
    count = _history_size(owner)
    request = options(exported(service), mode="merge", action="retain")
    for row in request["credential_policy"]:
        row["action"] = {"models": "retain", "search": "clear", "rqdata": "replace"}[row["service"]]
    preview = service.preview(**request)
    assert not any(row["code"] == "CREDENTIAL_REQUIRED" for row in preview["missing_credentials"])
    apply(service, preview, credentials=[{"service": "rqdata", "id": "shared", "value": FRESH}])
    assert _history_size(owner) == count + 1
    documents = owner.store.read_snapshot().documents
    assert documents["models"]["profiles"][0]["api_key"] == CANARIES[0]
    assert documents["search"]["profiles"][0]["brave_api_key"] == ""
    assert documents["rqdata"]["profiles"][0]["password"] == FRESH
    assert all(row["configured"] for row in _all_public_settings(owner).values())
    public(preview)


def test_correction_negative_control_publication_preflight_detects_unreadable_commit(
    inactive_owned, monkeypatch
):
    owner, service = inactive_owned
    plan = service.preview(**options(exported(service), services=["rqdata"], action="replace"))
    before = owner.store.read_snapshot()
    with monkeypatch.context() as patch:
        patch.setattr(
            transfer.ConfigurationTransferService, "_preflight_publication", lambda *args: None
        )
        result = apply(
            service, plan, credentials=[{"service": "rqdata", "id": "rq", "value": "saved"}]
        )
        assert result["state"] == "committed" and result["revision"] != before.revision
        with pytest.raises(AssertionError, match="Committed public settings are unreadable"):
            _all_public_settings(owner)


def test_correction_negative_control_unchecked_publication_detects_capacity_bypass(
    inactive_owned, monkeypatch
):
    owner, service = inactive_owned
    before = owner.store.read_snapshot()
    monkeypatch.setattr(storage, "_MAX_GENERATIONS", 1)
    plan = service.preview(**options(exported(service)))

    def unchecked(instance, documents, selections, revision):
        return instance.backend.store.update(
            lambda previous: (documents, selections), expected_revision=revision
        )

    with monkeypatch.context() as patch:
        patch.setattr(transfer.ConfigurationTransferService, "_publish", unchecked)
        result = apply(service, plan)
        with closing(
            sqlite3.connect(owner.root / "app/secrets/configuration-store.sqlite3")
        ) as database:
            assert database.execute("SELECT count(*) FROM generations").fetchone() == (2,)
        with pytest.raises(AssertionError, match="Unchecked publication exceeded capacity"):
            assert result["revision"] == before.revision, "Unchecked publication exceeded capacity"
        with pytest.raises(transfer.TransferError) as caught:
            exported(service)
        assert caught.value.code == "UNAVAILABLE"


def test_correction_negative_control_original_merge_classification_is_detected(
    inactive_owned, monkeypatch
):
    owner, service = inactive_owned
    request = options(_partial_models(service), services=["models"], mode="merge", action="retain")
    request["apply_selections"] = False
    original = transfer._missing
    before = owner.store.read_snapshot()

    def vulnerable(document, documents, decisions):
        distorted = copy.deepcopy(documents)
        for key in transfer.SERVICES:
            for row in transfer._rows(distorted, key).values():
                action = decisions.get((key, row["id"])) if key == "models" else "retain"
                available = action == "replace" or (
                    action == "retain" and bool(row.get(transfer.SECRET[key], "").strip())
                )
                if not available:
                    row[transfer.SECRET[key]] = ""
        return original(document, distorted, decisions)

    with monkeypatch.context() as patch:
        patch.setattr(transfer, "_missing", vulnerable)
        preview = service.preview(**request)
        with pytest.raises(AssertionError, match="Merge falsely reports"):
            _assert_omitted_model_ready(preview)
    _assert_omitted_model_ready(service.preview(**request))
    unchanged(before, owner.store.read_snapshot())


@pytest.mark.parametrize("activated_agent", ["managed"], indirect=True)
def test_correction_full_api_rejects_public_collision_then_accepts_corrected_input(api_client):
    client, runtime = api_client
    prefix = "/api/configuration-transfer"
    document = client.get(prefix + "/export").json()
    response = client.post(
        prefix + "/preview", json=options(document, services=["rqdata"], action="replace")
    )
    assert response.status_code == 200
    plan = response.json()
    body = apply_body(plan)
    body["credentials"] = [{"service": "rqdata", "id": "rq", "value": "saved"}]
    before = runtime.backend.store.read_snapshot()
    rejected = client.post(prefix + "/apply", json=body)
    assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "UNSAFE_METADATA"
    assert "saved" not in rejected.text
    unchanged(before, runtime.backend.store.read_snapshot())
    assert (
        client.post(prefix + "/operation", json={"operation": plan["receipt"]}).json()["state"]
        == "pending"
    )
    body["credentials"][0]["value"] = FRESH
    accepted = client.post(prefix + "/apply", json=body)
    assert accepted.status_code == 200
    for path in ("/api/model-providers", "/api/search-providers", "/api/data-source-profiles"):
        response = client.get(path)
        assert response.status_code == 200
        public(response.text)
    assert _history_size(runtime.backend) == len(before._history) + 1
    assert client.post(prefix + "/apply", json=body).status_code == 409
