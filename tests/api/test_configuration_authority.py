"""Read-side enforcement only: synthetic decisions in disposable installations."""

from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import threading
import traceback
from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from alphalab.dataio import rq_profiles
from alphalab.dataio.recipes import execute_data_recipe
from alphalab.dataio.sync import SyncJobManager, SyncRequest
from alphalab.utils import configuration_decision as authority
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_deployment import coordinated_offline
from alphalab.utils.configuration_maintenance import configuration_access
from alphalab.utils.configuration_store import ConfigurationStore, ConfigurationStoreError
from alphalab.utils.private_settings import read_json_settings, write_json_settings
from apps.api import main
from apps.api.configuration_app import create_configuration_app
from apps.api.services import model_provider_service as models
from apps.api.services import search_provider_service as search
from tests.api.test_configuration_decision import seed, value
from tests.api.test_configuration_migration_service import CANARIES, native_documents, write_sources


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "installation \u03a9"
    documents = native_documents()
    write_sources(root, documents)
    choices = {key: {"kind": "profile", "id": doc["active_id"]} for key, doc in documents.items()}
    store = ConfigurationStore.initialize(root, documents=documents, selections=choices)
    snapshot = store.read_snapshot()
    report = SimpleNamespace(
        installation_id=snapshot.installation_id,
        revision=snapshot.revision,
        selection_kinds=tuple(choice["kind"] for choice in choices.values()),
    )
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))

    def record(state="committed", **changes):
        candidate = value(root, report, state=state)
        candidate.update(changes)
        seed(root, candidate, tmp_path)
        return candidate

    return root, store, report, documents, record


def rejects(action, code=None):
    with pytest.raises(ConfigurationStoreError) as caught:
        action()
    error = caught.value
    if code:
        assert error.code == code
    diagnostics = repr(error) + str(error) + repr(error.args)
    diagnostics += "".join(traceback.format_exception(error)) + repr(pickle.dumps(error))
    for secret in CANARIES:
        assert secret not in diagnostics
    assert error.__cause__ is None and error.__context__ is None


@pytest.mark.parametrize("state", ["absent", "prepared"])
def test_json_authority_ignores_staged_store_and_allows_prepared_drift(installation, state):
    root, store, report, docs, record = installation
    if state == "prepared":
        record(state)
    with ConfigurationBackend.json(root) as owner:
        with owner.json_scope():
            assert models.active_profile()["api_key"] == CANARIES[0]
            assert rq_profiles.active_config()["RQ_PASSWORD"] == CANARIES[3]
            assert search.public_settings()["provider"] == docs["search"]["profiles"][0]["provider"]
            updated = {**docs["models"]["profiles"][0], "name": "Legitimate JSON edit"}
            assert models.save_profile(updated, activate=True)["profiles"]
            assert models.read_settings()["profiles"][-1]["name"] == "Legitimate JSON edit"
    assert store.read_snapshot().revision == report.revision
    if state == "prepared":
        assert authority.read_decision_record(root).as_dict()["state"] == "prepared"


@pytest.mark.parametrize("name", ["models", "search", "rqdata"])
@pytest.mark.parametrize(
    "operation", ["read", "public", "resolve", "write", "helper-read", "helper-write"]
)
def test_committed_blocks_all_legacy_credential_paths(installation, name, operation):
    root, store, report, docs, record = installation
    record()
    module = {"models": models, "search": search, "rqdata": rq_profiles}[name]
    path = module.settings_path()
    before = path.read_bytes()
    profile = docs[name]["profiles"][0].copy()
    profile["name"] = "Forbidden JSON write"
    resolve = {
        "models": lambda: models.resolve_test_profile(profile),
        "search": lambda: search.resolve_settings(profile),
        "rqdata": lambda: rq_profiles.resolve_connection_profile(profile),
    }
    action = {
        "read": module.read_settings,
        "public": module.public_settings,
        "resolve": resolve[name],
        "write": lambda: module.save_profile(profile, activate=True),
        "helper-read": lambda: read_json_settings(path, default=dict, validate=lambda doc: doc),
        "helper-write": lambda: write_json_settings(path, docs[name], validate=lambda doc: doc),
    }[operation]
    rejects(action, "UNSUPPORTED_STORAGE")
    assert path.read_bytes() == before
    assert store.read_snapshot().revision == report.revision


@pytest.mark.parametrize("kind", ["empty", "pending", "malformed", "unsupported", "wrong-root"])
def test_invalid_authority_blocks_before_credentials_or_fallback(installation, kind, monkeypatch):
    root, store, report, docs, record = installation
    if kind in {"unsupported", "wrong-root"}:
        record(
            "prepared",
            **({"protocol_version": 2} if kind == "unsupported" else {"root_key": "0" * 64}),
        )
    else:
        directory = root / authority.NAMESPACE
        directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        if kind != "empty":
            (directory / ("decision.pending" if kind == "pending" else "decision.json")).write_text(
                CANARIES[0]
            )

    def forbidden(*args, **kwargs):
        pytest.fail("Legacy credentials or environment were consulted")

    monkeypatch.setattr(models, "read_settings", forbidden)
    rejects(lambda: ConfigurationBackend.json(root))
    rejects(lambda: ConfigurationBackend.sqlite(root, installation_id=report.installation_id))


def test_synthetic_committed_is_validated_but_does_not_enable_any_composition(installation):
    root, store, report, docs, record = installation
    committed = record()
    with configuration_access(root):
        assert (
            authority.validate_committed_authority(
                root, installation_id=report.installation_id, decision_id=committed["decision_id"]
            )
            == committed
        )
    rejects(lambda: ConfigurationBackend.json(root), "UNSUPPORTED_STORAGE")
    rejects(
        lambda: ConfigurationBackend.sqlite(root, installation_id=report.installation_id),
        "UNSUPPORTED_STORAGE",
    )


@pytest.mark.parametrize("field", ["installation_id", "decision_id"])
def test_expected_durable_identities_cannot_be_substituted(installation, field):
    root, store, report, docs, record = installation
    record()
    rejects(lambda: authority.validate_committed_authority(root, **{field: "f" * 32}))


@pytest.mark.parametrize(
    "fault",
    ["missing", "corrupt", "journal", "foreign-installation", "wrong-revision", "wrong-selection"],
)
def test_committed_requires_valid_existing_anchor_and_store(installation, fault):
    root, store, report, docs, record = installation
    if fault == "foreign-installation":
        record(installation_id="f" * 32)
    elif fault == "wrong-revision":
        candidate = value(root, report, state="committed")
        candidate["revision"] = candidate["evidence"]["migration_revision"] = "f" * 32
        seed(root, candidate, root.parent)
    elif fault == "wrong-selection":
        candidate = value(root, report, state="committed")
        candidate["evidence"]["selection_kinds"]["models"] = "none"
        seed(root, candidate, root.parent)
    else:
        record()
        database = root / "app/secrets/configuration-store.sqlite3"
        if fault == "missing":
            database.rename(database.with_suffix(".test-held"))
        elif fault == "corrupt":
            database.write_bytes(b"not SQLite")
        else:
            database.with_name(database.name + "-journal").write_bytes(b"invalid journal")
    rejects(lambda: ConfigurationBackend.json(root))


def test_descendant_and_rollback_never_select_history(installation):
    root, store, report, docs, record = installation
    record()

    def edit(current):
        documents = current.documents
        documents["models"]["profiles"][0]["name"] = "Next generation"
        return documents, current.selections

    latest = store.update(edit, expected_revision=report.revision)
    rejects(lambda: authority.validate_committed_authority(root), "REVISION_CONFLICT")
    assert store.read_snapshot().revision == latest.revision
    # An anchor from an invented later revision cannot authorize the old store.
    rejects(lambda: ConfigurationBackend.json(root), "REVISION_CONFLICT")


def test_api_and_local_launcher_block_before_starting_work(installation, monkeypatch):
    from alphalab.local_agent import _local_agent

    root, store, report, docs, record = installation
    owner = ConfigurationBackend.json(root)
    record()  # Test-owned fault injection while the owner's real lease is held.
    try:

        def forbidden(*args, **kwargs):
            pytest.fail("Runtime discovery, network or token access occurred")

        monkeypatch.setattr("shutil.which", forbidden)
        rejects(lambda: _local_agent(root, api_port=9999, configuration_backend=owner).__enter__())
        rejects(lambda: create_configuration_app(owner))
        with pytest.raises(ConfigurationStoreError):
            with TestClient(main.app):
                pass
    finally:
        owner.close()


def test_json_recipe_and_job_admission_block_without_persisting_work(installation):
    root, store, report, docs, record = installation
    manager = SyncJobManager(root, recover_pending=False)
    record()
    try:
        rejects(lambda: manager.submit(SyncRequest(datasets=["bars"], symbols=["000001.SZ"])))
        rejects(manager.recover_pending)
        rejects(
            lambda: execute_data_recipe(
                "raise AssertionError('must not execute')", mode="run", root=root
            )
        )
        assert manager.operations.list_jobs() == []
    finally:
        manager.shutdown()


def test_captured_child_rejects_committed_authority_before_private_handshake(installation):
    from alphalab.dataio.configuration_execution import execute_captured_rq

    root, store, report, docs, record = installation
    record()
    rejects(
        lambda: execute_captured_rq(
            root,
            installation_id=report.installation_id,
            revision=report.revision,
            configuration=None,
            operation="plan",
            source="raise AssertionError('must not run')",
            timeout_seconds=5,
        ),
        "STORE_UNAVAILABLE",
    )
    assert store.read_snapshot().revision == report.revision


@pytest.mark.parametrize("state", ["absent", "prepared"])
def test_json_recovery_does_not_reinterpret_sqlite_tagged_work(installation, state):
    root, store, report, docs, record = installation
    if state == "prepared":
        record(state)
    manager = SyncJobManager(root, recover_pending=False)
    try:
        job = manager.operations.create_job(
            {
                "configuration_backend": "sqlite",
                "configuration_installation_id": report.installation_id,
                "kind": "python_recipe",
                "recipe_source": "raise AssertionError('must not run')",
            }
        )
        manager.recover_pending()
        saved = manager.operations.get_job(job)
        assert saved["status"] == "failed"
        assert "explicit configuration backend" in saved["error"]
        assert all(secret not in repr(saved) for secret in CANARIES)
    finally:
        manager.shutdown()


def test_deployment_rechecks_closed_owner_before_stopping_services(installation):
    root, store, report, docs, record = installation
    owner = ConfigurationBackend.json(root)
    owner.close()
    record()
    stopped = []
    from alphalab.utils.configuration_deployment import DeploymentPreparationError

    with pytest.raises(DeploymentPreparationError):
        with coordinated_offline(owner, stop_services=lambda: stopped.append(True)):
            pytest.fail("Offline capability was minted")
    assert stopped == []


@pytest.mark.parametrize("operation", ["read", "write"])
def test_json_read_keeps_lease_through_credential_access(installation, monkeypatch, operation):
    root, store, report, docs, record = installation
    entered, release = threading.Event(), threading.Event()
    original = Path.read_text

    def pause(path, *args, **kwargs):
        if path == models.settings_path():
            entered.set()
            assert release.wait(5)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            models.read_settings
            if operation == "read"
            else lambda: models.save_profile(docs["models"]["profiles"][0].copy(), activate=True)
        )
        try:
            assert entered.wait(5)

            def exclusive():
                with configuration_access(root, exclusive=True):
                    pass

            rejects(exclusive, "STORE_BUSY")
        finally:
            release.set()
        assert future.result()["profiles"]


@pytest.mark.parametrize("bypass", ["startup", "write", "corrupt-fallback"])
def test_negative_controls_detect_missing_enforcement(installation, monkeypatch, bypass):
    root, store, report, docs, record = installation
    if bypass == "corrupt-fallback":
        directory = root / authority.NAMESPACE
        directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        (directory / "decision.pending").write_text("partial")
    else:
        record()
    with monkeypatch.context() as patch:
        if bypass == "corrupt-fallback":
            patch.setattr(authority, "read_decision_record", lambda root: None)
        else:
            patch.setattr(authority, "check_backend_authority", lambda *args, **kwargs: None)
        if bypass == "write":
            before = models.settings_path().read_bytes()
            models.save_profile(
                {**docs["models"]["profiles"][0], "name": "Unsafe write"}, activate=True
            )
            with pytest.raises(AssertionError, match="Legacy write escaped"):
                assert models.settings_path().read_bytes() == before, "Legacy write escaped"
        else:
            with ConfigurationBackend.json(root) as owner:
                with pytest.raises(AssertionError, match="JSON startup escaped"):
                    assert owner.mode != "json", "JSON startup escaped"
    rejects(lambda: ConfigurationBackend.json(root))


def test_dictionary_cannot_claim_an_authority_lease(installation):
    root, store, report, docs, record = installation
    record()
    rejects(lambda: create_configuration_app({"backend": "sqlite", "root": root}))
    rejects(lambda: models.active_profile())
    rejects(lambda: rq_profiles.active_config())


def test_implicit_sdk_and_health_environment_loading_is_fenced(installation, monkeypatch):
    from alphalab.dataio.providers import rq
    from apps.api.services import data_sync_service

    root, store, report, docs, record = installation
    record()

    def forbidden(*args, **kwargs):
        pytest.fail("Environment loading preceded authority validation")

    monkeypatch.setattr(rq, "load_env_files", forbidden)
    monkeypatch.setattr(data_sync_service, "load_env_files", forbidden)
    rejects(rq.RQDataConfig.from_env, "UNSUPPORTED_STORAGE")
    rejects(data_sync_service.get_health, "UNSUPPORTED_STORAGE")


def test_api_process_configuration_checks_authority_before_dotenv_import(installation):
    root, store, report, docs, record = installation
    record()
    script = """
import json
from alphalab.utils import env
from alphalab.utils.configuration_store import ConfigurationStoreError
called = []
def load():
    called.append(True)
    return []
env.load_env_files = load
try:
    import apps.api.config
except ConfigurationStoreError as error:
    print(json.dumps({'code': error.code, 'loaded': bool(called)}))
else:
    raise AssertionError('API process configuration bypassed authority')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"code": "UNSUPPORTED_STORAGE", "loaded": False}
    assert all(secret not in result.stdout + result.stderr for secret in CANARIES)


@pytest.mark.parametrize(
    "kind,index", [(Exception, 0), (CancelledError, 1), (KeyboardInterrupt, 2), (SystemExit, 3)]
)
def test_owner_exchange_redaction_preserves_interruptions_while_allowing_authority_reads(
    installation, kind, index
):
    from alphalab.utils.configuration_agent import _owned_call

    root, store, report, docs, record = installation
    expected = ConfigurationStoreError if kind is Exception else kind
    with ConfigurationBackend.json(root) as owner:

        def action():
            with owner.borrow():
                raise kind(CANARIES[index])

        with pytest.raises(expected) as caught:
            _owned_call(action)
    diagnostics = repr(caught.value) + str(caught.value.args)
    diagnostics += "".join(traceback.format_exception(caught.value)) + repr(
        pickle.dumps(caught.value)
    )
    assert all(secret not in diagnostics for secret in CANARIES)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert not getattr(caught.value, "__notes__", None)


def test_authority_reads_inside_store_callbacks_remain_forbidden(installation):
    root, store, report, docs, record = installation
    with ConfigurationBackend.json(root) as owner:

        def mutation(current):
            with owner.borrow():
                return current.documents, current.selections

        rejects(
            lambda: store.update(mutation, expected_revision=report.revision), "REENTRANT_ACCESS"
        )
    assert store.read_snapshot().revision == report.revision


def native_aliases(path):
    aliases = [path, path.parent / ".." / path.parent.name / path.name]
    aliases.append(Path(os.path.relpath(path)))
    if os.name == "nt":
        aliases += [
            path.with_name(path.name.upper()),
            path.parent.parent.with_name(path.parent.parent.name.upper()) / "SeCrEtS" / path.name,
            Path(str(path).upper()),
            Path(str(path).replace("\\", "/")),
            path.with_name(path.name + "."),
            path.with_name(path.name + " "),
        ]
        import ctypes

        short = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), short, len(short)):
            aliases.append(Path(short.value))
    return aliases


@pytest.mark.parametrize("state", ["absent", "prepared", "committed", "corrupt"])
@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
def test_native_aliases_keep_authority_through_direct_read_and_write(installation, state, service):
    root, store, report, docs, record = installation
    if state == "corrupt":
        directory = root / authority.NAMESPACE
        directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        (directory / "decision.pending").write_text("invalid metadata")
    elif state != "absent":
        record(state)
    module = {"models": models, "search": search, "rqdata": rq_profiles}[service]
    path = module.settings_path()
    for alias in native_aliases(path):
        before = path.read_bytes()
        if state in {"committed", "corrupt"}:
            rejects(lambda: read_json_settings(alias, default=dict, validate=lambda doc: doc))
            rejects(lambda: write_json_settings(alias, docs[service], validate=lambda doc: doc))
            assert path.read_bytes() == before
        else:
            assert (
                read_json_settings(alias, default=dict, validate=lambda doc: doc) == docs[service]
            )
            assert (
                write_json_settings(alias, docs[service], validate=lambda doc: doc) == docs[service]
            )
    assert store.read_snapshot().revision == report.revision


@pytest.mark.skipif(os.name != "nt", reason="Real Windows case aliases required")
def test_negative_control_original_filename_predicate_leaks_through_windows_alias(
    installation, monkeypatch
):
    from alphalab.utils import private_settings
    from alphalab.utils.configuration_maintenance import json_configuration_access

    root, store, report, docs, record = installation
    record()
    alias = models.settings_path().with_name("MODEL-PROVIDERS.JSON")

    @contextmanager
    def vulnerable(path):
        lease = (
            json_configuration_access(path.parents[2])
            if path.name
            in {"model-providers.json", "search-providers.json", "rqdata-profiles.json"}
            else nullcontext()
        )
        with lease:
            yield path

    with monkeypatch.context() as control:
        control.setattr(private_settings, "_profile_access", vulnerable)
        with pytest.raises(AssertionError, match="Alias disclosed a saved credential"):
            exposed = read_json_settings(alias, default=dict, validate=lambda doc: doc)
            assert exposed["profiles"][0]["api_key"] != CANARIES[0], (
                "Alias disclosed a saved credential"
            )
    rejects(lambda: read_json_settings(alias, default=dict, validate=lambda doc: doc))


def test_custom_hardlink_cannot_hide_a_native_profile(installation):
    root, store, report, docs, record = installation
    alias = root / "custom-profile.json"
    os.link(models.settings_path(), alias)
    record()
    rejects(
        lambda: read_json_settings(alias, default=dict, validate=lambda doc: doc), "UNSAFE_STORAGE"
    )
    rejects(lambda: write_json_settings(alias, {}, validate=lambda doc: doc), "UNSAFE_STORAGE")


@pytest.mark.skipif(os.name != "nt", reason="Windows alternate data streams")
def test_windows_default_stream_alias_is_not_an_unprotected_custom_file(installation):
    root, store, report, docs, record = installation
    record()
    alias = Path(str(models.settings_path()) + "::$DATA")
    rejects(
        lambda: read_json_settings(alias, default=dict, validate=lambda doc: doc), "UNSAFE_STORAGE"
    )
    rejects(lambda: write_json_settings(alias, {}, validate=lambda doc: doc), "UNSAFE_STORAGE")


@pytest.mark.skipif(os.name == "nt", reason="POSIX filename case distinction")
def test_posix_distinct_case_custom_file_is_not_a_native_alias(tmp_path):
    path = tmp_path / "MODEL-PROVIDERS.JSON"
    write_json_settings(path, {"custom": True}, validate=lambda doc: doc)
    assert read_json_settings(path, default=dict, validate=lambda doc: doc) == {"custom": True}
