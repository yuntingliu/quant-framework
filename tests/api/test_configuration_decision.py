"""Disposable initial migrations only; no production authority or providers."""

from __future__ import annotations

import json
import multiprocessing
import os
import pickle
import sqlite3
import subprocess
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from alphalab.dataio.runtime import runtime_execution_lock
from alphalab.utils import configuration_decision as protocol
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_maintenance import execution_access
from alphalab.utils.configuration_store import ConfigurationStore, ConfigurationStoreError
from apps.api.configuration_app import create_configuration_app
from apps.api.services import configuration_decision_service as service
from apps.api.services import configuration_migration_service as migration
from apps.api.services.configuration_profile_service import ConfigurationProfileServices
from tests.api.test_configuration_migration_service import CANARIES, native_documents, write_sources

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def node():
    value = os.environ.get("ALPHALAB_TEST_NODE")
    if not value:
        pytest.skip("Explicit supported Node 22 is required for decision interoperability")
    return value


def closed_json(root):
    owner = ConfigurationBackend.json(root)
    owner.close()
    return owner


@pytest.fixture
def staged(tmp_path, node):
    root = tmp_path / "installation Ω"
    write_sources(root, native_documents())
    report = migration.migrate_legacy_configuration(
        root, node_executable=node, acknowledge_offline=True
    )
    owner = closed_json(root)

    def prepare(**changes):
        options = dict(
            node_executable=node,
            expected_installation_id=report.installation_id,
            expected_revision=report.revision,
            acknowledge_offline=True,
        )
        options.update(changes)
        return service.prepare_sqlite_candidate(owner, **options)

    return root, report, owner, prepare


def value(root, report, *, state="prepared"):
    return {
        "protocol_version": 1,
        "state": state,
        "backend": "sqlite",
        "root_key": protocol.root_identity(root),
        "installation_id": report.installation_id,
        "decision_id": uuid4().hex,
        "revision": report.revision,
        "evidence": {
            "kind": "legacy-exact-v1",
            "verification_id": uuid4().hex,
            "migration_revision": report.revision,
            "store_protocol_version": 1,
            "selection_kinds": dict(zip(("models", "search", "rqdata"), report.selection_kinds)),
        },
    }


def seed(root, record, tmp_path):
    # Only tests can manufacture committed records, only beneath pytest's root.
    assert root.resolve().is_relative_to(tmp_path.resolve())
    directory = root / protocol.NAMESPACE
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o777 if os.name == "nt" else 0o700)
    directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    descriptor = os.open(directory / "decision.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(record))


def redacted(error):
    text = str(error) + repr(error) + str(error.args) + "".join(traceback.format_exception(error))
    text += pickle.dumps(error).decode("utf-8", errors="replace")
    for key in CANARIES:
        assert key not in text
    assert error.__cause__ is None and error.__context__ is None


def fails(action, code=None):
    with pytest.raises(ConfigurationStoreError) as caught:
        action()
    if code:
        assert caught.value.code == code
    redacted(caught.value)


def test_no_record_is_legacy_even_with_a_valid_staged_store(staged):
    root, report, owner, prepare = staged
    before = migration._read_sources(root)
    assert protocol.read_decision_record(root) is None
    assert service.inspect_configuration_decision(root) == {"state": "legacy", "backend": "json"}
    assert not (root / protocol.NAMESPACE).exists()
    assert migration._read_sources(root) == before


def test_prepared_candidate_is_inactive_detached_and_restart_persistent(staged):
    root, report, owner, prepare = staged
    sources = migration._read_sources(root)
    snapshot = ConfigurationStore.open_existing(root).read_snapshot()
    record = prepare()
    assert record["state"] == "prepared" and record["backend"] == "sqlite"
    assert record["revision"] == report.revision
    assert protocol.read_decision_record(root).as_dict() == record
    assert service.inspect_configuration_decision(root) == record
    assert prepare(expected_decision_id=record["decision_id"]) == record
    assert migration._read_sources(root) == sources
    assert ConfigurationStore.open_existing(root).read_snapshot().revision == snapshot.revision
    assert {p.name for p in (root / protocol.NAMESPACE).iterdir()} == {"decision.json"}
    assert (root / protocol.NAMESPACE / "decision.json").stat().st_nlink == 1
    wire = json.dumps(record)
    for secret in CANARIES:
        assert secret not in wire
    assert str(root) not in wire
    record["evidence"]["selection_kinds"]["models"] = "none"
    assert (
        protocol.read_decision_record(root).as_dict()["evidence"]["selection_kinds"]["models"]
        == "profile"
    )


def test_synthetic_committed_record_is_readable_but_cannot_be_published_or_overwritten(
    staged, tmp_path
):
    root, report, owner, prepare = staged
    record = value(root, report, state="committed")
    seed(root, record, tmp_path)
    assert service.inspect_configuration_decision(root) == record
    fails(prepare, "REVISION_CONFLICT")
    # No product path can write an active record, even with a real offline token.
    other = tmp_path / "other"
    other.mkdir()
    from alphalab.utils.configuration_deployment import coordinated_offline

    with coordinated_offline(closed_json(other), stop_services=lambda: None) as offline:
        active = value(other, report, state="committed")
        parsed = protocol.parse_decision_record(
            json.dumps(active), root_key=protocol.root_identity(other)
        )
        fails(lambda: protocol._publish_prepared(offline, parsed), "REVISION_CONFLICT")
    assert not (other / protocol.NAMESPACE).exists()


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("protocol_version",), True),
        (("protocol_version",), "1"),
        (("protocol_version",), 2),
        (("state",), "active"),
        (("state",), []),
        (("backend",), "json"),
        (("root_key",), "0" * 64),
        (("installation_id",), "../private"),
        (("decision_id",), ""),
        (("revision",), None),
        (("api_key",), CANARIES[0]),
        (("evidence", "password"), CANARIES[3]),
        (("evidence", "kind"), "backup"),
        (("evidence", "verification_id"), CANARIES[0]),
        (("evidence", "migration_revision"), "f" * 32),
        (("evidence", "store_protocol_version"), True),
        (("evidence", "selection_kinds", "models"), "unknown"),
    ],
)
def test_record_allowlist_and_exact_types(staged, path, replacement):
    root, report, owner, prepare = staged
    record = value(root, report)
    parent = record
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = replacement
    fails(
        lambda: protocol.parse_decision_record(
            json.dumps(record), root_key=protocol.root_identity(root)
        )
    )
    assert not (root / protocol.NAMESPACE).exists()


_SELECTION_VALUES = {"models": "profile", "rqdata": "environment", "search": "none"}
_SELECTION_KEY_CASES = [
    *[
        pytest.param({key: _SELECTION_VALUES[key] for key in keys}, True, id="/".join(keys))
        for keys in permutations(_SELECTION_VALUES)
    ],
    pytest.param({"models,rqdata,search": "none"}, False, id="single-combined-key"),
    pytest.param({"models,rqdata": "environment", "search": "none"}, False, id="combined-pair"),
    pytest.param(
        {"models,rqdata,search": "none", "models": "profile", "search": "none"},
        False,
        id="combined-plus-individuals",
    ),
    pytest.param(
        {**_SELECTION_VALUES, "models,rqdata,search": "none"},
        False,
        id="combined-plus-all-required",
    ),
    *[
        pytest.param(
            {key: kind for key, kind in _SELECTION_VALUES.items() if key != missing},
            False,
            id=f"missing-{missing}",
        )
        for missing in _SELECTION_VALUES
    ],
    pytest.param({**_SELECTION_VALUES, "extra": "none"}, False, id="unexpected-field"),
]


@pytest.mark.parametrize("state", ["prepared", "committed"])
@pytest.mark.parametrize("selections,valid", _SELECTION_KEY_CASES)
def test_selection_keys_agree_between_python_and_node(tmp_path, node, state, selections, valid):
    root = tmp_path / "installation \u03a9"
    root.mkdir()
    report = SimpleNamespace(
        installation_id="b" * 32,
        revision="d" * 32,
        selection_kinds=("profile", "none", "environment"),
    )
    baseline = value(root, report, state=state)
    root_key = protocol.root_identity(root)
    assert (
        protocol.parse_decision_record(json.dumps(baseline), root_key=root_key).as_dict()
        == baseline
    )
    candidate = json.loads(json.dumps(baseline))
    candidate["evidence"]["selection_kinds"] = selections
    wire = json.dumps(candidate)
    if valid:
        assert protocol.parse_decision_record(wire, root_key=root_key).as_dict() == candidate
    else:
        fails(
            lambda: protocol.parse_decision_record(wire, root_key=root_key), "INVALID_CONFIGURATION"
        )
    module = (REPO / "integrations/conexus/configuration-decision.mjs").as_uri()
    program = """
import assert from 'node:assert/strict';
import { inspect } from 'node:util';
import { validateDecisionForRoot, ConfigurationDecisionError } from MODULE;
let text = '';
for await (const part of process.stdin) text += part;
const input = JSON.parse(text);
// A passing control rules out unrelated root or record validation failures.
const baseline = await validateDecisionForRoot(JSON.stringify(input.baseline), input.root);
assert.equal(baseline.state, input.baseline.state);
if (input.valid) {
    const result = await validateDecisionForRoot(input.wire, input.root);
    assert.deepEqual(JSON.parse(JSON.stringify(result)), JSON.parse(input.wire));
} else {
    await assert.rejects(validateDecisionForRoot(input.wire, input.root), error => {
        assert.ok(error instanceof ConfigurationDecisionError);
        assert.equal(error.code, 'DECISION_UNAVAILABLE');
        assert.equal(error.cause, undefined);
        const diagnostics = inspect(error, { depth: Infinity }) + JSON.stringify(error) + error.stack;
        for (const canary of input.canaries) assert.ok(!diagnostics.includes(canary));
        return true;
    });
}
""".replace("MODULE", json.dumps(module))
    result = subprocess.run(
        [node, "--input-type=module", "--eval", program],
        input=json.dumps(
            {
                "root": str(root),
                "baseline": baseline,
                "wire": wire,
                "valid": valid,
                "canaries": CANARIES,
            }
        ).encode("utf-8"),
        capture_output=True,
        timeout=15,
        **migration._subprocess_options(),
    )
    assert result.returncode == 0, "Python/Node selection-key validation disagreed"
    for canary in CANARIES:
        assert canary.encode() not in result.stdout + result.stderr
    assert not (root / protocol.NAMESPACE).exists()
    assert not (root / migration._DESTINATION).exists()


@pytest.mark.parametrize(
    "edit",
    [
        lambda wire: '{"protocol_version":0,' + wire[1:],
        lambda wire: '{"\\u0070rotocol_version":0,' + wire[1:],
        lambda wire: wire.replace(
            '"verification_id":', '"verification_id":"bad","verification_id":'
        ),
        lambda wire: wire.replace('"protocol_version": 1', '"protocol_version": 1.0'),
        lambda wire: wire.replace('"protocol_version": 1', '"protocol_version": NaN'),
        lambda wire: wire + " true",
        lambda wire: b"\xef\xbb\xbf" + wire.encode("utf-8"),
        lambda wire: wire[:-1],
        lambda wire: " " * 16385,
    ],
)
def test_ambiguous_malformed_and_oversized_json(staged, edit):
    root, report, owner, prepare = staged
    fails(
        lambda: protocol.parse_decision_record(
            edit(json.dumps(value(root, report))), root_key=protocol.root_identity(root)
        )
    )


@pytest.mark.parametrize("kind", ["empty", "pending", "corrupt", "permission", "enumeration"])
def test_existing_invalid_authority_never_falls_back(staged, monkeypatch, kind):
    root, report, owner, prepare = staged
    directory = root / protocol.NAMESPACE
    directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    if kind == "pending":
        (directory / "decision.pending").write_text("incomplete")
    if kind == "corrupt":
        (directory / "decision.json").write_text(CANARIES[0])
    if kind == "permission":
        original = Path.lstat

        def denied(path, *args, **kwargs):
            if path == directory:
                raise PermissionError(CANARIES[1])
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", denied)
    if kind == "enumeration":
        original = Path.iterdir

        def denied(path):
            if path == directory:
                raise OSError(CANARIES[2])
            return original(path)

        monkeypatch.setattr(Path, "iterdir", denied)
    fails(lambda: service.inspect_configuration_decision(root))
    fails(prepare)


@pytest.mark.parametrize("field", ["installation_id", "revision"])
def test_wrong_store_identity_or_generation_fails_closed(staged, tmp_path, field):
    root, report, owner, prepare = staged
    record = value(root, report)
    record[field] = "f" * 32
    if field == "revision":
        record["evidence"]["migration_revision"] = record[field]
    seed(root, record, tmp_path)
    fails(lambda: service.inspect_configuration_decision(root))


def test_foreign_root_noncanonical_paths_and_linked_records_are_rejected(staged, tmp_path):
    root, report, owner, prepare = staged
    fails(lambda: protocol.root_identity(root / ".." / root.name), "UNSAFE_STORAGE")
    other = tmp_path / "foreign"
    other.mkdir()
    seed(other, value(root, report), tmp_path)
    fails(lambda: protocol.read_decision_record(other), "INSTALLATION_MISMATCH")
    prepare()
    os.link(root / protocol.NAMESPACE / "decision.json", tmp_path / "linked-metadata.json")
    fails(lambda: protocol.read_decision_record(root), "UNSAFE_STORAGE")


@pytest.mark.parametrize("field", ["revision", "evidence", "root_key"])
def test_missing_required_fields_are_rejected(staged, field):
    root, report, owner, prepare = staged
    record = value(root, report)
    del record[field]
    fails(
        lambda: protocol.parse_decision_record(
            json.dumps(record), root_key=protocol.root_identity(root)
        )
    )


@pytest.mark.parametrize("platform,mode", [("nt", 0o777), ("posix", 0o700)])
def test_namespace_creation_preserves_platform_permission_policy(
    staged, monkeypatch, platform, mode
):
    from types import SimpleNamespace

    from alphalab.utils.configuration_deployment import coordinated_offline

    root, report, owner, prepare = staged
    record = protocol.parse_decision_record(
        json.dumps(value(root, report)), root_key=protocol.root_identity(root)
    )
    calls = []
    original = Path.mkdir
    with coordinated_offline(owner, stop_services=lambda: None) as offline:

        def intercept(path, *args, **kwargs):
            if path == root / protocol.NAMESPACE:
                calls.append(kwargs["mode"])
                raise OSError("Intercept before directory creation")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(protocol, "os", SimpleNamespace(name=platform, path=os.path))
        monkeypatch.setattr(Path, "mkdir", intercept)
        fails(lambda: protocol._publish_prepared(offline, record))
    assert calls == [mode]
    assert not (root / protocol.NAMESPACE).exists()


@pytest.mark.parametrize("kind", ["missing", "corrupt", "journal"])
def test_missing_corrupt_or_recovery_required_store_is_never_repaired(staged, kind):
    root, report, owner, prepare = staged
    path = root / migration._DESTINATION
    if kind == "missing":
        path.rename(path.with_name("test-held.sqlite3"))
    elif kind == "corrupt":
        path.write_bytes(b"not SQLite")
    else:
        path.with_name(path.name + "-journal").write_bytes(b"invalid journal")
    fails(prepare)
    assert not (root / protocol.NAMESPACE).exists()
    if kind == "missing":
        assert not path.exists()
    if kind == "corrupt":
        assert path.read_bytes() == b"not SQLite"


@pytest.mark.parametrize(
    "service_name,secret_name",
    [("models", "api_key"), ("search", "brave_api_key"), ("rqdata", "password")],
)
@pytest.mark.parametrize("secret", ["", "synthetic-rotated-credential"])
def test_legacy_clears_and_rotations_reject_stale_staged_credentials(
    staged, service_name, secret_name, secret
):
    root, report, owner, prepare = staged
    before = ConfigurationStore.open_existing(root).read_snapshot()
    path = root / migration._SOURCES[service_name]
    document = json.loads(path.read_text())
    document["profiles"][0][secret_name] = secret
    path.write_text(json.dumps(document))
    fails(prepare)
    assert ConfigurationStore.open_existing(root).read_snapshot().revision == before.revision
    assert not (root / protocol.NAMESPACE).exists(), "Stale migrated credentials were admitted"


def test_saved_sqlite_rotation_even_if_matching_legacy_cannot_select_history(staged):
    root, report, owner, prepare = staged
    store = ConfigurationStore.open_existing(root)
    api = ConfigurationProfileServices(store)
    api.models.save_profile(
        {
            "id": "model",
            "name": "Model",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "provider/model",
            "api_key": "synthetic-rotated",
        },
        activate=True,
    )
    path = root / migration._SOURCES["models"]
    document = json.loads(path.read_text())
    document["profiles"][0]["api_key"] = "synthetic-rotated"
    path.write_text(json.dumps(document))
    latest = store.read_snapshot()
    fails(lambda: prepare(expected_revision=latest.revision))
    assert store.read_snapshot().revision == latest.revision
    assert not (root / protocol.NAMESPACE).exists()


@pytest.mark.parametrize("service_name", ["models", "search", "rqdata", "referenced-model"])
def test_incomplete_selected_profiles_never_borrow_environment(
    tmp_path, node, monkeypatch, service_name
):
    root = tmp_path / "incomplete"
    docs = native_documents()
    if service_name in {"models", "referenced-model"}:
        docs["models"]["profiles"][0 if service_name == "models" else 1]["api_key"] = ""
    elif service_name == "search":
        docs["search"]["profiles"][0].update(provider="brave", brave_api_key="")
    else:
        docs["rqdata"]["profiles"][0]["password"] = ""
    write_sources(root, docs)
    report = migration.migrate_legacy_configuration(
        root, node_executable=node, acknowledge_offline=True
    )
    for key in ("CONEXUS_MODEL_API_KEY", "BRAVE_SEARCH_API_KEY", "RQ_PASSWORD"):
        monkeypatch.setenv(key, CANARIES[4])
    fails(
        lambda: service.prepare_sqlite_candidate(
            closed_json(root),
            node_executable=node,
            expected_installation_id=report.installation_id,
            expected_revision=report.revision,
            acknowledge_offline=True,
        )
    )
    assert not (root / protocol.NAMESPACE).exists()


def test_missing_sources_require_explicit_none_not_unverified_environment(tmp_path, node):
    root = tmp_path / "missing-sources"
    root.mkdir()
    choices = {key: "none" for key in ("models", "search", "rqdata")}
    report = migration.migrate_legacy_configuration(
        root, node_executable=node, acknowledge_offline=True, unselected_intents=choices
    )
    record = service.prepare_sqlite_candidate(
        closed_json(root),
        node_executable=node,
        expected_installation_id=report.installation_id,
        expected_revision=report.revision,
        acknowledge_offline=True,
        unselected_intents=choices,
    )
    assert set(record["evidence"]["selection_kinds"].values()) == {"none"}
    assert all(not (root / path).exists() for path in migration._SOURCES.values())
    other = tmp_path / "environment"
    other.mkdir()
    report = migration.migrate_legacy_configuration(
        other, node_executable=node, acknowledge_offline=True
    )
    fails(
        lambda: service.prepare_sqlite_candidate(
            closed_json(other),
            node_executable=node,
            expected_installation_id=report.installation_id,
            expected_revision=report.revision,
            acknowledge_offline=True,
        )
    )
    assert not (other / protocol.NAMESPACE).exists()


@pytest.mark.parametrize("kind", ["json", "sqlite", "worker", "legacy-worker", "agent"])
def test_live_owners_workers_and_unknown_agent_markers_block_preparation(staged, kind):
    root, report, owner, prepare = staged
    if kind == "agent":
        path = root / "conexus/project/host.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("999999")
        fails(prepare)
        assert path.read_text() == "999999"
    else:
        lease = (
            ConfigurationBackend.json(root)
            if kind == "json"
            else ConfigurationBackend.sqlite(root, installation_id=report.installation_id)
            if kind == "sqlite"
            else execution_access(root)
            if kind == "worker"
            else runtime_execution_lock(root, "captured-rq-execution")
        )
        with lease:
            fails(prepare)
    assert not (root / protocol.NAMESPACE).exists()


@pytest.mark.parametrize("status", ["queued", "running", "interrupted", "unknown"])
@pytest.mark.parametrize("kind", ["agent", "rqdata"])
def test_unfinished_or_ambiguous_jobs_block(staged, status, kind):
    root, report, owner, prepare = staged
    if kind == "agent":
        path = root / "conexus/project/state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"runs": [{"status": status}]}))
    else:
        with closing(sqlite3.connect(root / "app/dataio.db")) as db:
            db.execute("CREATE TABLE sync_jobs (status TEXT)")
            db.execute("INSERT INTO sync_jobs VALUES (?)", (status,))
            db.commit()
    fails(prepare, "STORE_BUSY")
    assert not (root / protocol.NAMESPACE).exists()


def test_operator_acknowledgement_and_expected_identity_are_required(staged):
    root, report, owner, prepare = staged
    fails(lambda: prepare(acknowledge_offline=False), "INVALID_ARGUMENT")
    fails(lambda: prepare(expected_installation_id="f" * 32), "INSTALLATION_MISMATCH")
    fails(lambda: prepare(expected_revision="f" * 32), "REVISION_CONFLICT")
    fails(lambda: prepare(expected_decision_id="f" * 32), "REVISION_CONFLICT")
    assert not (root / protocol.NAMESPACE).exists()


def test_competing_threads_cannot_both_publish(staged):
    root, report, owner, prepare = staged
    barrier = threading.Barrier(2)

    def compete(_):
        barrier.wait(timeout=5)
        try:
            return prepare()["decision_id"]
        except ConfigurationStoreError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert len([item for item in results if item]) == 1
    assert service.inspect_configuration_decision(root)["decision_id"] in results


def _process_prepare(root, node, installation, revision, ready, go, results):
    owner = closed_json(root)
    ready.put(True)
    if not go.wait(10):
        return
    try:
        record = service.prepare_sqlite_candidate(
            owner,
            node_executable=node,
            expected_installation_id=installation,
            expected_revision=revision,
            acknowledge_offline=True,
        )
        results.put(record["decision_id"])
    except ConfigurationStoreError:
        results.put(None)


def test_competing_spawned_processes_have_one_publication(staged, node):
    root, report, owner, prepare = staged
    context = multiprocessing.get_context("spawn")
    ready, results, go = context.Queue(), context.Queue(), context.Event()
    children = [
        context.Process(
            target=_process_prepare,
            args=(root, node, report.installation_id, report.revision, ready, go, results),
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            child.start()
        assert ready.get(timeout=20) and ready.get(timeout=20)
        go.set()
        values = [results.get(timeout=25), results.get(timeout=25)]
        assert len([item for item in values if item]) == 1
        for child in children:
            child.join(timeout=5)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        ready.close()
        results.close()


@pytest.mark.parametrize("point", ["before-node", "before-link", "after-link"])
def test_interrupted_preparation_is_inactive_and_never_auto_repaired(staged, monkeypatch, point):
    root, report, owner, prepare = staged
    if point == "before-node":
        monkeypatch.setattr(
            service,
            "_verify_record_node",
            lambda *_: (_ for _ in ()).throw(RuntimeError(CANARIES[0])),
        )
    elif point == "before-link":
        monkeypatch.setattr(
            protocol.os, "link", lambda *_a, **_k: (_ for _ in ()).throw(OSError(CANARIES[1]))
        )
    else:
        original = Path.unlink

        def stop(path, *args, **kwargs):
            if path.name == "decision.pending":
                raise OSError(CANARIES[2])
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", stop)
    fails(prepare)
    if point == "before-node":
        assert not (root / protocol.NAMESPACE).exists()
    else:
        assert (root / protocol.NAMESPACE / "decision.pending").exists()
        fails(lambda: service.inspect_configuration_decision(root))
        fails(prepare)
    assert ConfigurationStore.open_existing(root).read_snapshot().revision == report.revision


def test_source_mutation_during_publication_invalidates_prepared_record(staged, monkeypatch):
    root, report, owner, prepare = staged
    original = protocol._publish_prepared

    def change(*args, **kwargs):
        path = root / migration._SOURCES["rqdata"]
        doc = json.loads(path.read_text())
        doc["profiles"][0]["password"] = ""
        path.write_text(json.dumps(doc))
        return original(*args, **kwargs)

    monkeypatch.setattr(protocol, "_publish_prepared", change)
    fails(prepare, "UPDATE_FAILED")
    fails(lambda: service.inspect_configuration_decision(root), "UPDATE_FAILED")


def test_json_production_compatibility_and_environment_cannot_activate(staged, monkeypatch):
    root, report, owner, prepare = staged
    prepare()
    monkeypatch.setenv("ALPHALAB_CONFIGURATION_BACKEND", "sqlite")
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    with (
        ConfigurationBackend.json(root) as backend,
        TestClient(create_configuration_app(backend)) as client,
    ):
        assert backend.mode == "json"
        assert client.get("/api/model-providers").status_code == 200
        assert (
            client.post("/api/search-providers/test", json={"provider": "off"}).json()["ok"] is True
        )


def test_negative_control_skipping_fresh_source_equivalence_admits_stale_candidate(
    staged, monkeypatch
):
    root, report, owner, prepare = staged
    path = root / migration._SOURCES["rqdata"]
    docs = json.loads(path.read_text())
    docs["profiles"][0]["password"] = ""
    path.write_text(json.dumps(docs))
    with monkeypatch.context() as patch:
        patch.setattr(migration, "verify_offline_migration", lambda *_a, **_k: report)
        result = prepare()
        with pytest.raises(AssertionError, match="Stale credentials were admitted"):
            assert result["state"] != "prepared", "Stale credentials were admitted"
    fails(lambda: service.inspect_configuration_decision(root), "UPDATE_FAILED")


def test_negative_control_corruption_fallback_is_detected(staged, monkeypatch):
    root, report, owner, prepare = staged
    directory = root / protocol.NAMESPACE
    directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    (directory / "decision.pending").write_text("partial")
    with monkeypatch.context() as patch:
        patch.setattr(protocol, "read_decision_record", lambda _: None)
        result = service.inspect_configuration_decision(root)
        with pytest.raises(AssertionError, match="Corrupt authority selected JSON"):
            assert result["backend"] != "json", "Corrupt authority selected JSON"
    fails(lambda: service.inspect_configuration_decision(root))


def test_negative_control_conflicting_publication_is_detected(staged, monkeypatch):
    root, report, owner, prepare = staged
    first = prepare()

    # Simulate the original unsafe administrative pattern only in test memory:
    # overwriting without expected-state comparison, instead of write-once.
    barrier = threading.Barrier(2)
    publication_lock = threading.Lock()

    def unsafe(offline, record, **_kwargs):
        # Deliberately ignores ownership, CAS and no-replace publication, like
        # an unmanaged administrative writer. Both candidates are valid records.
        directory = root / protocol.NAMESPACE
        barrier.wait(timeout=5)
        # Serializing only the write is insufficient: both competing callers
        # can still claim success without checking the expected prior decision.
        with publication_lock:
            (directory / "decision.json").write_text(record._wire)
        return record

    from alphalab.utils.configuration_deployment import coordinated_offline

    choices = [
        protocol.parse_decision_record(
            json.dumps(value(root, report)), root_key=protocol.root_identity(root)
        )
        for _ in range(2)
    ]
    other = choices[0]
    with monkeypatch.context() as patch:
        patch.setattr(protocol, "_publish_prepared", unsafe)
        with coordinated_offline(owner, stop_services=lambda: None) as offline:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(lambda item: protocol._publish_prepared(offline, item), choices)
                )
        with pytest.raises(AssertionError, match="Concurrent conflicting publications succeeded"):
            assert len(results) <= 1, "Concurrent conflicting publications succeeded"
        assert protocol.read_decision_record(root).as_dict()["decision_id"] != first["decision_id"]
    # Corrected primitive rejects the already published conflicting state.
    with coordinated_offline(owner, stop_services=lambda: None) as offline:
        fails(lambda: protocol._publish_prepared(offline, other), "REVISION_CONFLICT")
