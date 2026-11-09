"""Offline migration over real private temporary stores and synthetic secrets."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import pickle
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import gettempdir
from types import SimpleNamespace

import pytest

from alphalab.dataio import rq_profiles
from alphalab.utils import configuration_maintenance as maintenance
from alphalab.utils import configuration_store as storage
from apps.api.services import configuration_migration_service as migration
from apps.api.services import model_provider_service as models
from apps.api.services import search_provider_service as search
from apps.api.services.configuration_profile_service import ConfigurationProfileServices

REPO = Path(__file__).resolve().parents[2]
CANARIES = (
    "migration-model-key-6101",
    "migration-inactive-key-6102",
    "migration-brave-key-6103",
    "migration-rq-password-6104",
    "migration-environment-key-6105",
)


def native_documents():
    return {
        "models": {
            "version": 1,
            "active_id": "model",
            "profiles": [
                {
                    "id": "model",
                    "name": "Model",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "provider/model",
                    "api_key": CANARIES[0],
                },
                {
                    "id": "inactive",
                    "name": "Inactive",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "provider/alternate",
                    "api_key": CANARIES[1],
                },
            ],
        },
        "search": {
            "version": 2,
            "active_id": "search",
            "profiles": [
                {
                    "id": "search",
                    "name": "Search",
                    "provider": "openrouter",
                    "model_profile_id": "inactive",
                    "brave_api_key": CANARIES[2],
                },
                {
                    "id": "disabled",
                    "name": "Disabled",
                    "provider": "off",
                    "model_profile_id": None,
                    "brave_api_key": "",
                },
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
                    "password": CANARIES[3],
                },
            ],
        },
    }


@pytest.fixture(scope="session")
def node():
    candidate = os.environ.get("ALPHALAB_TEST_NODE") or shutil.which("node")
    if not candidate:
        pytest.skip("An explicit supported Node 22 runtime is required for migration verification.")
    version = subprocess.run(
        [candidate, "--version"], capture_output=True, timeout=10, check=True
    ).stdout
    parts = version.decode().strip().lstrip("v").split(".")
    if int(parts[0]) != 22 or int(parts[1]) < 18:
        pytest.skip("Migration verification requires Node 22.18 or later within major 22.")
    return candidate


def write_sources(root, documents):
    for key, value in documents.items():
        path = root / migration._SOURCES[key]
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o777 if os.name == "nt" else 0o700)
        path.write_text(json.dumps(value), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)


@pytest.mark.parametrize("platform_name,expected_mode", [("nt", 0o777), ("posix", 0o700)])
@pytest.mark.parametrize("entrypoint", ["api", "migration"])
def test_directory_mode_policy_during_api_startup_and_migration(
    monkeypatch, platform_name, expected_mode, entrypoint
):
    from apps.api import main

    root = Path(gettempdir()) / f"alphalab-mode-probe-{time.time_ns()}"
    calls = []
    real_platform = os.name
    # Replace only the maintenance module's os reference, never os.name itself:
    # pathlib must continue to use the host platform for its concrete Path type.
    monkeypatch.setattr(
        maintenance, "os", SimpleNamespace(name=platform_name, path=os.path, getpid=os.getpid)
    )
    monkeypatch.setattr(main, "runtime_root", lambda: root)

    def intercept(path, *, mode=0o777, parents=False, exist_ok=False):
        calls.append((path, mode, parents, exist_ok))
        raise OSError("Stop before directory creation or lock acquisition.")

    monkeypatch.setattr(Path, "mkdir", intercept)
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        if entrypoint == "api":

            async def startup():
                async with main.configuration_lifespan(main.app):
                    pytest.fail("The intercepted startup must not proceed.")

            asyncio.run(startup())
        else:
            migration.migrate_legacy_configuration(
                root, node_executable="unused-node", acknowledge_offline=True
            )
    assert caught.value.code == "STORE_UNAVAILABLE"
    assert calls == [(root / "app/secrets", expected_mode, True, True)]
    assert os.name == real_platform
    assert not root.exists()


@pytest.mark.parametrize("platform_name,expected_mode", [("nt", 0o777), ("posix", 0o700)])
def test_legacy_fixture_directory_mode_policy(monkeypatch, platform_name, expected_mode):
    root = Path(gettempdir()) / f"alphalab-fixture-mode-probe-{time.time_ns()}"
    calls = []
    real_platform = os.name

    def intercept(path, *, mode=0o777, parents=False, exist_ok=False):
        calls.append((path, mode, parents, exist_ok))
        raise OSError("Stop before fixture directory creation.")

    monkeypatch.setattr(Path, "mkdir", intercept)
    with monkeypatch.context() as fixture_patch:
        fixture_patch.setattr(sys.modules[__name__], "os", SimpleNamespace(name=platform_name))
        with pytest.raises(OSError, match="Stop before fixture"):
            write_sources(root, {"models": {"version": 1}})
    assert calls == [(root / "conexus/secrets", expected_mode, True, True)]
    assert os.name == real_platform
    assert not root.exists()


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path / "installation Ω"
    documents = native_documents()
    write_sources(root, documents)
    return root, documents


def saved_bytes(root):
    return {
        key: (root / path).read_bytes() if (root / path).exists() else None
        for key, path in migration._SOURCES.items()
    }


def run_migration(root, node, **options):
    return migration.migrate_legacy_configuration(
        root, node_executable=node, acknowledge_offline=True, **options
    )


def assert_private(value):
    text = repr(value) + str(value)
    if isinstance(value, BaseException):
        text += "".join(traceback.format_exception(value)) + repr(value.args)
        assert value.__cause__ is None
        assert value.__context__ is None
        text += repr(pickle.loads(pickle.dumps(value)))
    for secret in CANARIES:
        assert secret not in text


def generation_count(root):
    with closing(
        sqlite3.connect((root / migration._DESTINATION).as_uri() + "?mode=ro", uri=True)
    ) as connection:
        return connection.execute("SELECT count(*) FROM generations").fetchone()[0]


def test_missing_files_preserve_environment_intent_without_resolving_values(
    tmp_path, node, monkeypatch
):
    root = tmp_path / "fresh"
    for key in ("CONEXUS_MODEL_API_KEY", "BRAVE_SEARCH_API_KEY", "RQ_PASSWORD"):
        monkeypatch.setenv(key, CANARIES[4])
    report = run_migration(root, node)
    snapshot = storage.ConfigurationStore.open_existing(root).read_snapshot()
    assert snapshot.revision == report.revision
    assert snapshot.installation_id == report.installation_id
    assert snapshot.selections == {key: {"kind": "environment"} for key in migration._SOURCES}
    assert all(not document["profiles"] for document in snapshot.documents.values())
    assert all(value is None for value in saved_bytes(root).values())
    assert generation_count(root) == 1
    assert_private(report)
    assert_private(json.dumps(report.as_dict()))


def test_native_profiles_credentials_and_inactive_references_survive_restart(legacy, node):
    root, expected = legacy
    before = saved_bytes(root)
    report = run_migration(root, node)
    snapshot = storage.ConfigurationStore.open_existing(root).read_snapshot()
    assert snapshot.documents == expected
    assert snapshot.selections == {
        key: {"kind": "profile", "id": value["active_id"]} for key, value in expected.items()
    }
    assert saved_bytes(root) == before
    assert generation_count(root) == 1
    assert report.as_dict()["profile_counts"] == {"models": 2, "search": 2, "rqdata": 1}
    assert report.as_dict()["backend_activated"] is False
    assert_private(json.dumps(report.as_dict()))
    # Explicit acknowledgement verification only: no new generation or JSON writes.
    assert (
        migration.verify_legacy_migration(root, node_executable=node, acknowledge_offline=True)
        == report
    )
    assert generation_count(root) == 1
    assert saved_bytes(root) == before


@pytest.mark.parametrize("provider", ["brave", "off", "openrouter"])
def test_search_v1_migrates_only_in_memory_and_retains_original_key(legacy, node, provider):
    root, expected = legacy
    original = {
        "version": 1,
        "provider": provider,
        "model_profile_id": "inactive",
        "brave_api_key": " " + CANARIES[2] + " ",
    }
    expected["search"] = original
    write_sources(root, expected)
    before = saved_bytes(root)
    run_migration(root, node)
    snapshot = storage.ConfigurationStore.open_existing(root).read_snapshot()
    migrated = snapshot.documents["search"]
    assert migrated["version"] == 2 and migrated["active_id"] == "default"
    assert snapshot.selections["search"] == {"kind": "profile", "id": "default"}
    assert migrated["profiles"][0] == {
        "id": "default",
        "name": "Default",
        **{key: value for key, value in original.items() if key != "version"},
    }
    assert saved_bytes(root) == before


@pytest.mark.parametrize("missing", [False, True])
def test_cleared_and_incomplete_saved_profiles_never_borrow_environment_credentials(
    legacy, node, missing
):
    root, expected = legacy
    expected["models"]["profiles"][0]["api_key"] = ""
    expected["search"]["profiles"][0].update(provider="brave", brave_api_key="")
    expected["rqdata"]["profiles"][0]["password"] = ""
    source = copy.deepcopy(expected)
    if missing:
        for service, field in (
            ("models", "api_key"),
            ("search", "brave_api_key"),
            ("rqdata", "password"),
        ):
            del source[service]["profiles"][0][field]
    write_sources(root, source)
    before = saved_bytes(root)
    run_migration(root, node)
    services = ConfigurationProfileServices(
        storage.ConfigurationStore.open_existing(root),
        environment={
            "CONEXUS_MODEL_BASE_URL": "https://openrouter.ai/api/v1",
            "CONEXUS_MODEL_ID": "provider/environment",
            "CONEXUS_MODEL_API_KEY": CANARIES[4],
            "BRAVE_SEARCH_API_KEY": CANARIES[4],
            "RQ_USER": "environment-user",
            "RQ_HOST": "environment.example:16011",
            "RQ_PASSWORD": CANARIES[4],
        },
    )
    snapshot = services.read_snapshot()
    assert snapshot.documents.extract_private() == expected
    assert saved_bytes(root) == before
    for key in migration._SOURCES:
        assert snapshot.selections[key]["kind"] == "profile"
        assert snapshot.active_profile(key) is None


def test_none_is_an_explicit_override_only_for_null_legacy_selection(legacy, node):
    root, documents = legacy
    for document in documents.values():
        document["active_id"] = None
    write_sources(root, documents)
    report = run_migration(root, node, unselected_intents={key: "none" for key in documents})
    assert set(report.selection_kinds) == {"none"}
    snapshot = storage.ConfigurationStore.open_existing(root).read_snapshot()
    assert snapshot.documents == documents
    assert snapshot.selections == {key: {"kind": "none"} for key in documents}


@pytest.mark.parametrize(
    "options", [{"models": "none"}, {"unknown": "none"}, {"models": {"kind": "none"}}]
)
def test_ambiguous_selection_overrides_fail_before_initialization(legacy, node, options):
    root, _ = legacy
    with pytest.raises(storage.ConfigurationStoreError):
        run_migration(root, node, unselected_intents=options)
    assert not (root / migration._DESTINATION).exists()


@pytest.mark.parametrize(
    "malformed",
    [
        b'{"version":1,"version":1}',
        b'{"version":1,"vers\\u0069on":1}',
        b"{",
        b'{"version":NaN}',
        b'{"version":Infinity}',
        b"\xff",
        b"[]",
        b"[" * 1100 + b"]" * 1100,
        b" " * (migration._MAX_SOURCE_BYTES + 1),
    ],
)
def test_invalid_json_is_sanitized_and_cannot_create_a_store(legacy, node, malformed):
    root, _ = legacy
    (root / migration._SOURCES["models"]).write_bytes(malformed)
    before = saved_bytes(root)
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert_private(caught.value)
    assert not (root / migration._DESTINATION).exists()
    assert saved_bytes(root) == before


@pytest.mark.parametrize(
    "case",
    [
        "version",
        "boolean-version",
        "duplicate-id",
        "active-id",
        "unknown-field",
        "secret-type",
        "too-many",
        "missing-reference",
        "wrong-endpoint",
        "unsafe-url",
        "public-secret",
        "encoded-secret",
    ],
)
def test_invalid_native_documents_fail_preflight_without_mutation(legacy, node, case):
    root, documents = legacy
    model = documents["models"]["profiles"][0]
    if case == "version":
        documents["models"]["version"] = 3
    if case == "boolean-version":
        documents["models"]["version"] = True
    if case == "duplicate-id":
        documents["models"]["profiles"].append(copy.deepcopy(model))
    if case == "active-id":
        documents["models"]["active_id"] = "missing"
    if case == "unknown-field":
        model[CANARIES[0]] = "private"
    if case == "secret-type":
        model["api_key"] = {"secret": CANARIES[0]}
    if case == "too-many":
        documents["models"]["profiles"] = [{**model, "id": f"profile{i}"} for i in range(21)]
        documents["models"]["active_id"] = "profile0"
    if case == "missing-reference":
        documents["search"]["profiles"][0]["model_profile_id"] = "missing"
    if case == "wrong-endpoint":
        documents["models"]["profiles"][1]["base_url"] = "https://other.example/v1"
    if case == "unsafe-url":
        model["base_url"] = "https://user:password@gateway.example/v1"
    if case == "public-secret":
        documents["rqdata"]["profiles"][0]["name"] = CANARIES[0]
    if case == "encoded-secret":
        model["name"] = "".join(f"%{ord(char):02x}" for char in CANARIES[3])
    write_sources(root, documents)
    before = saved_bytes(root)
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert caught.value.code == "INVALID_CONFIGURATION"
    assert_private(caught.value)
    assert not (root / migration._DESTINATION).exists()
    assert saved_bytes(root) == before


def test_explicit_offline_acknowledgement_is_required_without_side_effects(tmp_path, node):
    root = tmp_path / "unacknowledged"
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        migration.migrate_legacy_configuration(root, node_executable=node)
    assert caught.value.code == "INVALID_ARGUMENT"
    assert not root.exists()


@pytest.mark.parametrize("existing", [b"", b"corrupt private database", b"SQLite format 3\0"])
def test_existing_destination_is_never_opened_replaced_or_repaired(legacy, node, existing):
    root, _ = legacy
    destination = root / migration._DESTINATION
    destination.write_bytes(existing)
    before = saved_bytes(root)
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert caught.value.code == "STORE_EXISTS"
    assert destination.read_bytes() == existing and saved_bytes(root) == before


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_orphan_sidecars_are_not_adopted(legacy, node, suffix):
    root, _ = legacy
    path = Path(str(root / migration._DESTINATION) + suffix)
    path.write_bytes(b"synthetic orphan")
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert caught.value.code == "STORE_CORRUPT"
    assert path.read_bytes() == b"synthetic orphan"
    assert not (root / migration._DESTINATION).exists()


def test_interrupted_initialization_preserves_partial_store_without_retry_or_recovery(
    legacy, node, monkeypatch
):
    root, _ = legacy
    before, execute = saved_bytes(root), storage._Session.execute

    def fail(session, sql, *args):
        if sql.startswith("INSERT INTO store_metadata"):
            raise RuntimeError(CANARIES[0])
        return execute(session, sql, *args)

    with monkeypatch.context() as patch:
        patch.setattr(storage._Session, "execute", fail)
        with pytest.raises(storage.ConfigurationStoreError) as caught:
            run_migration(root, node)
    assert_private(caught.value)
    destination = root / migration._DESTINATION
    partial = destination.read_bytes()
    with pytest.raises(storage.ConfigurationStoreError) as again:
        run_migration(root, node)
    assert again.value.code == "STORE_EXISTS"
    with pytest.raises(storage.ConfigurationStoreError):
        migration.verify_legacy_migration(root, node_executable=node, acknowledge_offline=True)
    assert destination.read_bytes() == partial
    assert saved_bytes(root) == before


@pytest.mark.parametrize("field", ["revision", "installation_id", "protocol_version"])
def test_node_revision_mismatch_fails_without_claiming_success_and_can_be_verified_later(
    legacy, node, monkeypatch, field
):
    root, _ = legacy
    before, verify = saved_bytes(root), migration._verify_node

    def mismatch(expected, executable):
        return verify(
            {**expected, field: 2 if field == "protocol_version" else "0" * 32}, executable
        )

    with monkeypatch.context() as patch:
        patch.setattr(migration, "_verify_node", mismatch)
        with pytest.raises(storage.ConfigurationStoreError) as caught:
            run_migration(root, node)
    assert caught.value.code == "STORE_UNAVAILABLE"
    assert_private(caught.value)
    assert generation_count(root) == 1 and saved_bytes(root) == before
    report = migration.verify_legacy_migration(root, node_executable=node, acknowledge_offline=True)
    assert (
        report.revision == storage.ConfigurationStore.open_existing(root).read_snapshot().revision
    )


def test_verification_never_rolls_back_a_later_explicit_credential_clear(legacy, node):
    root, documents = legacy
    first = run_migration(root, node)
    store = storage.ConfigurationStore.open_existing(root)

    def clear(snapshot):
        value = snapshot.documents
        value["models"]["profiles"][0]["api_key"] = ""
        return value, snapshot.selections

    current = store.update(clear, expected_revision=first.revision)
    with pytest.raises(storage.ConfigurationStoreError):
        migration.verify_legacy_migration(root, node_executable=node, acknowledge_offline=True)
    assert store.read_snapshot().revision == current.revision
    assert store.read_snapshot().documents["models"]["profiles"][0]["api_key"] == ""
    assert saved_bytes(root)["models"] == json.dumps(documents["models"]).encode()


@pytest.mark.parametrize("phase", ["before", "after"])
def test_external_source_change_is_detected_without_overwriting_the_edit(
    legacy, node, monkeypatch, phase
):
    root, documents = legacy
    documents["models"]["profiles"][0]["name"] = "External edit"
    name = "_require_node" if phase == "before" else "_verify_node"
    original = getattr(migration, name)

    def interfere(*args):
        result = original(*args)
        write_sources(root, documents)
        return result

    monkeypatch.setattr(migration, name, interfere)
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert caught.value.code == "UPDATE_FAILED"
    assert saved_bytes(root)["models"] == json.dumps(documents["models"]).encode()
    assert (root / migration._DESTINATION).exists() == (phase == "after")


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
def test_supported_writer_cannot_save_during_migration_fence(legacy, monkeypatch, service):
    root, documents = legacy
    module = {"models": models, "search": search, "rqdata": rq_profiles}[service]
    monkeypatch.setattr(module, "settings_path", lambda: root / migration._SOURCES[service])
    before = saved_bytes(root)
    with maintenance.configuration_access(root, exclusive=True):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                module.save_profile, documents[service]["profiles"][0], activate=True
            )
            with pytest.raises(storage.ConfigurationStoreError) as caught:
                pending.result(timeout=5)
    assert caught.value.code == "STORE_BUSY"
    assert saved_bytes(root) == before


def test_active_writer_blocks_migration_before_sqlite_creation(legacy, node, monkeypatch):
    root, documents = legacy
    monkeypatch.setattr(models, "settings_path", lambda: root / migration._SOURCES["models"])
    started, release, original = threading.Event(), threading.Event(), models.prepare_profile

    def pause(*args):
        started.set()
        assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(models, "prepare_profile", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            models.save_profile, documents["models"]["profiles"][0], activate=True
        )
        try:
            assert started.wait(5)
            with pytest.raises(storage.ConfigurationStoreError) as caught:
                run_migration(root, node)
            assert caught.value.code == "STORE_BUSY"
            assert not (root / migration._DESTINATION).exists()
        finally:
            release.set()
        pending.result(timeout=5)


def test_live_api_lifespan_excludes_migration(legacy, node, monkeypatch):
    from fastapi.testclient import TestClient

    from apps.api.main import app

    root, _ = legacy
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))
    with TestClient(app):
        with pytest.raises(storage.ConfigurationStoreError) as caught:
            run_migration(root, node)
    assert caught.value.code == "STORE_BUSY"
    assert not (root / migration._DESTINATION).exists()


def test_current_agent_launcher_holds_offline_exclusion(legacy, node, monkeypatch):
    import alphalab.local_agent as launcher

    root, _ = legacy

    @contextmanager
    def fake_host(*args, **kwargs):
        yield object()

    monkeypatch.setattr(launcher, "RUNTIME_DIR", root)
    monkeypatch.setattr(launcher, "_local_agent", fake_host)
    with launcher.local_agent(REPO, api_port=8000):
        with pytest.raises(storage.ConfigurationStoreError) as caught:
            run_migration(root, node)
    assert caught.value.code == "STORE_BUSY"


def test_agent_host_marker_fails_closed_even_if_stale(legacy, node):
    root, _ = legacy
    marker = root / "conexus/project/host.lock"
    marker.parent.mkdir(parents=True)
    marker.write_text("synthetic-stale-owner")
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert caught.value.code == "STORE_BUSY"
    assert marker.read_text() == "synthetic-stale-owner"


def test_independent_installations_keep_credentials_and_identity_separate(legacy, tmp_path, node):
    root, documents = legacy
    second = tmp_path / "other"
    documents["models"]["profiles"][0]["api_key"] = CANARIES[1]
    write_sources(second, documents)
    a, b = run_migration(root, node), run_migration(second, node)
    assert a.installation_id != b.installation_id and a.revision != b.revision
    first = storage.ConfigurationStore.open_existing(root).read_snapshot()
    other = storage.ConfigurationStore.open_existing(second).read_snapshot()
    assert first.documents["models"]["profiles"][0]["api_key"] == CANARIES[0]
    assert other.documents["models"]["profiles"][0]["api_key"] == CANARIES[1]


def test_public_cli_report_contains_no_canaries_or_private_paths(legacy, node):
    root, _ = legacy
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(REPO / "scripts/migrate_configuration.py"),
            "--root",
            str(root),
            "--node",
            node,
            "--acknowledge-offline",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["backend_activated"] is False
    assert str(root) not in result.stdout + result.stderr
    assert_private(result.stdout + result.stderr)
    again = subprocess.run(
        [
            sys.executable,
            "-B",
            str(REPO / "scripts/migrate_configuration.py"),
            "--root",
            str(root),
            "--node",
            node,
            "--acknowledge-offline",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert again.returncode == 1 and json.loads(again.stdout)["error"] == "STORE_EXISTS"
    assert_private(again.stdout + again.stderr)


def test_lock_and_sources_reject_links_without_following_them(legacy, node):
    root, _ = legacy
    source = root / migration._SOURCES["models"]
    alias = source.with_name("hardlink.json")
    os.link(source, alias)
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert caught.value.code == "UNSAFE_STORAGE"
    assert not (root / migration._DESTINATION).exists()


def test_windows_path_and_fd_ctime_disagreement_is_not_a_false_source_change(legacy, monkeypatch):
    root, _ = legacy
    path = root / migration._SOURCES["models"]
    before, original = path.read_bytes(), os.fstat

    def changed_ctime(fd):
        value = original(fd)
        fields = {
            key: getattr(value, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        }
        fields["st_ctime_ns"] += 1
        return SimpleNamespace(**fields)

    monkeypatch.setattr(migration.os, "fstat", changed_ctime)
    if os.name == "nt":
        assert migration._read_source(path)[1] == before
    else:
        with pytest.raises(storage.ConfigurationStoreError):
            migration._read_source(path)


@pytest.mark.parametrize(
    "service,field,secret",
    [
        ("models", "api_key", CANARIES[0]),
        ("search", "brave_api_key", CANARIES[2]),
        ("rqdata", "password", CANARIES[3]),
    ],
)
@pytest.mark.parametrize("encoded", [False, True])
def test_original_and_normalized_secrets_cannot_enter_cross_service_metadata(
    legacy, node, service, field, secret, encoded
):
    root, documents = legacy
    documents[service]["profiles"][0][field] = " " + secret + " "
    public_value = "".join(f"%{ord(char):02x}" for char in secret) if encoded else secret
    target = "search" if service == "models" else "models"
    documents[target]["profiles"][0]["name"] = public_value
    write_sources(root, documents)
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert caught.value.code == "INVALID_CONFIGURATION"
    assert_private(caught.value)
    assert not (root / migration._DESTINATION).exists()


def test_node_verifies_private_model_search_documents_without_receiving_rqdata(
    legacy, node, monkeypatch
):
    root, _ = legacy
    original, seen = migration._verify_node, []

    def inspect_private_pipe(expected, executable):
        private = json.dumps(expected)
        assert CANARIES[0] in private and CANARIES[1] in private and CANARIES[2] in private
        assert CANARIES[3] not in private
        assert set(expected["documents"]) == {"models", "search"}
        assert set(expected["selections"]) == {"models", "search"}
        seen.append(True)
        return original(expected, executable)

    monkeypatch.setattr(migration, "_verify_node", inspect_private_pipe)
    report = run_migration(root, node)
    assert seen == [True]
    assert_private(json.dumps(report.as_dict()))


@pytest.mark.parametrize("behavior", ["unsupported", "timeout", "diagnostics"])
def test_node_failures_never_expose_private_child_output_or_exception_payloads(
    legacy, node, monkeypatch, behavior
):
    root, _ = legacy
    before, original = saved_bytes(root), subprocess.run

    def fail_private(arguments, **options):
        if "--version" in arguments:
            if behavior == "unsupported":
                return subprocess.CompletedProcess(arguments, 0, stdout=b"v24.0.0\n")
            return original(arguments, **options)
        if behavior == "timeout":
            raise subprocess.TimeoutExpired(CANARIES[0], 1, output=CANARIES[2], stderr=CANARIES[3])
        return subprocess.CompletedProcess(
            arguments, 1, stdout=CANARIES[0].encode(), stderr=CANARIES[3].encode()
        )

    monkeypatch.setattr(migration.subprocess, "run", fail_private)
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert_private(caught.value)
    assert saved_bytes(root) == before
    assert (root / migration._DESTINATION).exists() == (behavior != "unsupported")


def _spawn_owned(script, *arguments):
    return subprocess.Popen(
        [sys.executable, "-B", "-c", script, *map(str, arguments)],
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        **migration._subprocess_options(),
    )


def _stop_owned(child):
    if child.poll() is None:
        child.kill()  # Only the Popen child created by this test.
    child.wait(timeout=10)
    for stream in (child.stdin, child.stdout, child.stderr):
        stream.close()


def _wait_marker(marker, children, seconds=20):
    deadline = time.monotonic() + seconds
    while not marker.exists():
        assert all(child.poll() is None for child in children), "Test-owned fixture exited early."
        assert time.monotonic() < deadline, "Test-owned fixture did not reach its barrier."
        time.sleep(0.02)


def test_simultaneous_migration_processes_publish_exactly_one_generation(legacy, node, tmp_path):
    root, _ = legacy
    script = """
import json,sys,time
from pathlib import Path
from apps.api.services.configuration_migration_service import migrate_legacy_configuration
from alphalab.utils.configuration_store import ConfigurationStoreError
root,node,ready,go=map(Path,sys.argv[1:])
ready.touch()
while not go.exists(): time.sleep(0.01)
try:
 report=migrate_legacy_configuration(root,node_executable=node,acknowledge_offline=True)
 print(json.dumps({'success':report.as_dict()}),flush=True)
except ConfigurationStoreError as e:
 print(json.dumps({'error':e.code}),flush=True)
"""
    go = tmp_path / "go"
    children = [
        _spawn_owned(script, root, node, tmp_path / f"ready-{index}", go) for index in range(2)
    ]
    try:
        for index in range(2):
            _wait_marker(tmp_path / f"ready-{index}", children)
        before = saved_bytes(root)
        go.touch()
        reports = []
        for child in children:
            stdout, stderr = child.communicate(timeout=40)
            assert child.returncode == 0
            assert_private(stdout + stderr)
            reports.append(json.loads(stdout))
        assert sum("success" in report for report in reports) == 1
        assert [report["error"] for report in reports if "error" in report][0] in {
            "STORE_BUSY",
            "STORE_EXISTS",
        }
        assert generation_count(root) == 1
        assert saved_bytes(root) == before
    finally:
        for child in children:
            _stop_owned(child)


def test_real_process_termination_during_initialization_never_activates_partial_state(
    legacy, node, tmp_path
):
    root, _ = legacy
    marker = tmp_path / "transaction-open"
    script = """
import sys,time
from pathlib import Path
from alphalab.utils import configuration_store as storage
from apps.api.services.configuration_migration_service import migrate_legacy_configuration
original=storage._Session.execute
def pause(session,sql,*args):
 result=original(session,sql,*args)
 if sql.startswith('INSERT INTO store_metadata'):
  Path(sys.argv[3]).touch()
  time.sleep(60)
 return result
storage._Session.execute=pause
migrate_legacy_configuration(Path(sys.argv[1]),node_executable=sys.argv[2],acknowledge_offline=True)
"""
    before = saved_bytes(root)
    child = _spawn_owned(script, root, node, marker)
    try:
        _wait_marker(marker, [child])
        child.kill()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode != 0
        assert_private(stdout + stderr)
    finally:
        _stop_owned(child)
    destination = root / migration._DESTINATION
    after_crash = destination.read_bytes()
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        run_migration(root, node)
    assert caught.value.code == "STORE_EXISTS"
    with pytest.raises(storage.ConfigurationStoreError):
        migration.verify_legacy_migration(root, node_executable=node, acknowledge_offline=True)
    assert destination.read_bytes() == after_crash
    assert saved_bytes(root) == before


def test_process_exit_releases_shared_lease_without_removing_lock_file(legacy, node, tmp_path):
    root, _ = legacy
    marker = tmp_path / "lease-held"
    script = """
import sys,time
from pathlib import Path
from alphalab.utils.configuration_maintenance import configuration_access
with configuration_access(Path(sys.argv[1])):
 Path(sys.argv[2]).touch()
 time.sleep(60)
"""
    child = _spawn_owned(script, root, marker)
    try:
        _wait_marker(marker, [child])
        with pytest.raises(storage.ConfigurationStoreError) as caught:
            run_migration(root, node)
        assert caught.value.code == "STORE_BUSY"
    finally:
        _stop_owned(child)
    assert (root / maintenance._RELATIVE_LOCK).is_file()
    run_migration(root, node)
    assert generation_count(root) == 1


def test_api_startup_cannot_enter_an_exclusive_maintenance_window(legacy, monkeypatch):
    from fastapi.testclient import TestClient

    from apps.api.main import app

    root, _ = legacy
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(root))
    with maintenance.configuration_access(root, exclusive=True):
        with pytest.raises(storage.ConfigurationStoreError) as caught:
            with TestClient(app):
                pytest.fail("Startup must not enter an offline window.")
    assert caught.value.code == "STORE_BUSY"


def test_shared_use_is_concurrent_and_nested_errors_do_not_leak_or_unlock_early(legacy):
    root, _ = legacy
    with maintenance.configuration_access(root):
        with pytest.raises(RuntimeError):
            with maintenance.configuration_access(root):
                raise RuntimeError("Synthetic body failure")
        with ThreadPoolExecutor(max_workers=1) as pool:

            def shared_use():
                with maintenance.configuration_access(root):
                    return True

            assert pool.submit(shared_use).result(timeout=5)
        with pytest.raises(storage.ConfigurationStoreError):
            with maintenance.configuration_access(root, exclusive=True):
                pytest.fail("A shared lease cannot be upgraded.")
    with maintenance.configuration_access(root, exclusive=True):
        pass


def test_overlapping_same_thread_shared_lifetimes_keep_the_fence_until_the_last_exit(legacy):
    root, _ = legacy
    first, second = maintenance.configuration_access(root), maintenance.configuration_access(root)
    first.__enter__()
    second.__enter__()
    first.__exit__(None, None, None)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:

            def exclusive_use():
                with maintenance.configuration_access(root, exclusive=True):
                    return True

            with pytest.raises(storage.ConfigurationStoreError) as caught:
                pool.submit(exclusive_use).result(timeout=5)
        assert caught.value.code == "STORE_BUSY"
    finally:
        second.__exit__(None, None, None)
    with maintenance.configuration_access(root, exclusive=True):
        pass


def test_exclusive_lock_timeout_is_bounded_without_modifying_sources(legacy):
    root, _ = legacy
    before = saved_bytes(root)

    def timed_attempt():
        started = time.monotonic()
        with pytest.raises(storage.ConfigurationStoreError) as caught:
            with maintenance.configuration_access(root, exclusive=True, timeout=0.05):
                pytest.fail("Another owner holds the lease.")
        return time.monotonic() - started, caught.value.code

    with maintenance.configuration_access(root):
        with ThreadPoolExecutor(max_workers=1) as pool:
            elapsed, code = pool.submit(timed_attempt).result(timeout=5)
    assert 0.04 <= elapsed < 2
    assert code == "STORE_BUSY"
    assert saved_bytes(root) == before


@pytest.mark.parametrize("during_unlock", [False, True])
def test_lock_failures_are_redacted_and_handles_are_released(legacy, monkeypatch, during_unlock):
    root, _ = legacy
    original = maintenance._lock

    def private_failure(descriptor, exclusive, *, unlock=False):
        if unlock == during_unlock:
            raise RuntimeError(CANARIES[3])
        return original(descriptor, exclusive, unlock=unlock)

    with monkeypatch.context() as patch:
        patch.setattr(maintenance, "_lock", private_failure)
        with pytest.raises(storage.ConfigurationStoreError) as caught:
            with maintenance.configuration_access(root, exclusive=True):
                pass
    assert_private(caught.value)
    with maintenance.configuration_access(root, exclusive=True):
        pass


def test_verify_only_does_not_create_a_missing_installation(tmp_path, node):
    root = tmp_path / "missing-verification"
    with pytest.raises(storage.ConfigurationStoreError) as caught:
        migration.verify_legacy_migration(root, node_executable=node, acknowledge_offline=True)
    assert caught.value.code == "STORE_MISSING"
    assert not root.exists()


@pytest.mark.parametrize("bad_arguments", [["--none", CANARIES[3]], ["--unknown", CANARIES[0]]])
def test_cli_argument_errors_do_not_echo_rejected_credential_values(tmp_path, node, bad_arguments):
    root = tmp_path / "must-not-create"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(REPO / "scripts/migrate_configuration.py"),
            "--root",
            str(root),
            "--node",
            node,
            *bad_arguments,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "Check the explicit migration arguments."
    assert_private(result.stderr)
    assert not root.exists()
