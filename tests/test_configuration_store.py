"""Private-store contracts: isolated files, test-owned workers, no providers."""

from __future__ import annotations

import asyncio
import builtins
import copy
import json
import multiprocessing
import os
import pickle
import re
import shutil
import socket
import sqlite3
import threading
import time
import traceback
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from alphalab.utils import configuration_store as implementation
from alphalab.utils.configuration_store import (
    PROTOCOL_VERSION,
    ConfigurationSnapshot,
    ConfigurationStore,
    ConfigurationStoreError,
)

SERVICES = ("models", "search", "rqdata")
CANARIES = (
    "fixture-storage-model-secret-1937",
    "fixture-storage-brave-secret-2846",
    "fixture-storage-rq-secret-3759",
)
RELATIVE_PATH = Path("app/secrets/configuration-store.sqlite3")


class PrivateInterruption(BaseException):
    pass


class PrivateKeyboardInterrupt(KeyboardInterrupt):
    pass


class PrivateCancellation(asyncio.CancelledError):
    pass


def hostile_exception(kind, payload, failure):
    """Construct errors whose Python descriptors must never be invoked."""
    accesses = []

    def forbidden(instance):
        accesses.append(kind)
        raise failure(payload)

    bases = {
        "store_code": ConfigurationStoreError,
        "exit_code": SystemExit,
        "cancel_class": asyncio.CancelledError,
        "keyboard_class": KeyboardInterrupt,
        "sqlite_fields": sqlite3.OperationalError,
    }
    attributes = {
        "__class__": property(forbidden),
        "__notes__": property(forbidden),
        "__str__": forbidden,
        "__repr__": forbidden,
    }
    if kind == "store_code":
        attributes["code"] = property(forbidden)
    elif kind == "exit_code":
        attributes["code"] = property(forbidden)
    elif kind == "sqlite_fields":
        attributes.update(
            sqlite_errorcode=property(forbidden),
            args=property(forbidden),
            __dict__=property(forbidden),
        )
    error_type = type("HostileFixtureError", (bases[kind],), attributes)
    error = error_type.__new__(error_type)
    if kind == "exit_code":
        SystemExit.__init__(error, payload)
    else:
        BaseException.__init__(error, payload)
    values = BaseException.__dict__["__dict__"].__get__(error)
    values["code"] = "UPDATE_FAILED"
    values["sqlite_errorcode"] = 5
    values["__notes__"] = [payload]
    return error, accesses


def native(counter=0):
    documents = {
        "models": {
            "version": 1,
            "active_id": "model",
            "profiles": [
                {
                    "id": "model",
                    "name": "Model",
                    "base_url": "http://localhost:9999/v1",
                    "model": "provider/model-name",
                    "api_key": CANARIES[0],
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
                    "model_profile_id": None,
                    "brave_api_key": CANARIES[1],
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
                    "user": "saved-user",
                    "host": "rq.example:16011",
                    "password": CANARIES[2],
                }
            ],
        },
    }
    selections = {}
    for service, document in documents.items():
        document["counter"] = counter
        selections[service] = {"kind": "profile", "id": document["active_id"]}
    return documents, selections


def initialize(root, *, timeout=5.0):
    documents, selections = native()
    return ConfigurationStore.initialize(
        root, documents=documents, selections=selections, timeout=timeout
    )


def advance(snapshot):
    documents = snapshot.documents
    counter = documents["models"]["counter"] + 1
    for document in documents.values():
        document["counter"] = counter
    return documents, snapshot.selections


def counters(snapshot):
    return tuple(snapshot.documents[service]["counter"] for service in SERVICES)


def private_rows(root, sql, parameters=()):
    with closing(
        sqlite3.connect((root / RELATIVE_PATH).as_uri() + "?mode=ro", uri=True)
    ) as connection:
        return connection.execute(sql, parameters).fetchall()


def tamper(root, sql, parameters=()):
    with closing(sqlite3.connect(root / RELATIVE_PATH)) as connection:
        connection.execute(sql, parameters)
        connection.commit()


def tree_bytes(root):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def safe_failure(action, code, caplog=None):
    with pytest.raises(ConfigurationStoreError) as caught:
        action()
    error = caught.value
    assert error.code == code
    visible = (
        str(error)
        + repr(error)
        + repr(error.args)
        + json.dumps(vars(error))
        + "".join(traceback.format_exception(type(error), error, error.__traceback__))
    )
    for secret in CANARIES:
        assert secret not in visible
        assert secret.encode() not in pickle.dumps(error)
        if caplog is not None:
            assert secret not in caplog.text
    assert error.__cause__ is None and error.__context__ is None
    assert pickle.loads(pickle.dumps(error)).code == code
    return error


def _child_updates(root, start, ready, worker, count):
    store = ConfigurationStore.open_existing(root, timeout=15)
    ready.send("ready")
    ready.close()
    if not start.wait(20):
        raise RuntimeError("Test worker did not receive its start signal")
    for iteration in range(count):

        def change(snapshot):
            documents, selections = advance(snapshot)
            documents["models"]["profiles"].append(
                {
                    "id": f"worker-{worker}-{iteration}",
                    "name": "Worker model",
                    "base_url": "http://localhost:9999/v1",
                    "model": "provider/model-name",
                    "api_key": "",
                }
            )
            return documents, selections

        store.update(change)


def _child_read(root, ready):
    snapshot = ConfigurationStore.open_existing(root).read_snapshot()
    documents, selections = native()
    ready.send(
        (
            snapshot.installation_id,
            snapshot.revision,
            snapshot.documents == documents and snapshot.selections == selections,
        )
    )
    ready.close()


def _child_compare_swap(root, revision, start, ready):
    store = ConfigurationStore.open_existing(root, timeout=15)
    ready.send("ready")
    if not start.wait(20):
        raise RuntimeError("Test writer did not receive its start signal")
    try:
        snapshot = store.update(advance, expected_revision=revision)
    except ConfigurationStoreError as error:
        ready.send(error.code)
    else:
        ready.send(snapshot.revision)
    finally:
        ready.close()


def _child_interrupted(root, ready, phase):
    store = ConfigurationStore.open_existing(root, timeout=15)
    execute = implementation._Session.execute

    def stop_at_boundary(session, sql, parameters=()):
        if sql == "BEGIN IMMEDIATE" and phase != "cold":
            execute(session, "PRAGMA cache_size=1")
            execute(session, "PRAGMA cache_spill=ON")
        result = execute(session, sql, parameters)
        reached = (
            (phase == "insert" and sql.startswith("INSERT INTO generations"))
            or (phase == "publish" and sql.startswith("UPDATE store_metadata"))
            or (phase == "cold" and sql.startswith("UPDATE store_metadata"))
            or (phase == "committed" and sql == "COMMIT")
        )
        if reached:
            ready.send("ready")
            ready.close()
            while True:
                time.sleep(0.05)
        return result

    def change(snapshot):
        documents, selections = advance(snapshot)
        if phase != "cold":
            for document in documents.values():
                document["padding"] = "x" * 128000  # Force an actual journal/cache spill.
        return documents, selections

    with patch.object(implementation._Session, "execute", stop_at_boundary):
        store.update(change)


def stop_owned_process(process):
    # Every caller passes only a Process object created by this test suite.
    if process.is_alive():
        process.terminate()
    process.join(timeout=10)
    if process.is_alive():
        process.kill()
        process.join(timeout=10)
    assert not process.is_alive()


def test_explicit_initialization_publishes_complete_private_generation(tmp_path):
    root = tmp_path / "installation"
    documents, selections = native()
    before = copy.deepcopy((documents, selections))
    store = ConfigurationStore.initialize(root, documents=documents, selections=selections)
    snapshot = store.read_snapshot()
    assert isinstance(snapshot, ConfigurationSnapshot)
    assert snapshot.documents == documents and snapshot.selections == selections
    assert snapshot.protocol_version == PROTOCOL_VERSION == 1
    assert re.fullmatch(r"[a-f0-9]{32}", snapshot.revision)
    assert re.fullmatch(r"[a-f0-9]{32}", snapshot.installation_id)
    assert snapshot.revision != snapshot.installation_id
    assert (documents, selections) == before
    assert set(tree_bytes(root)) == {str(RELATIVE_PATH)}
    assert private_rows(root, "SELECT parent_revision FROM generations") == [(None,)]
    assert private_rows(root, "PRAGMA journal_mode") == [("delete",)]


def test_missing_store_open_and_recovery_never_create_files(tmp_path):
    root = tmp_path / "absent"
    safe_failure(lambda: ConfigurationStore.open_existing(root), "STORE_MISSING")
    safe_failure(lambda: ConfigurationStore.recover_existing(root), "STORE_MISSING")
    assert not root.exists()


def test_existing_native_files_are_neither_adopted_read_nor_modified(tmp_path):
    legacy = [
        Path("conexus/secrets/model-providers.json"),
        Path("conexus/secrets/search-providers.json"),
        Path("app/secrets/rqdata-profiles.json"),
    ]
    for path, canary in zip(legacy, CANARIES):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        (tmp_path / path).write_text(canary, encoding="utf-8")  # Deliberately not valid JSON.
    before = tree_bytes(tmp_path)
    safe_failure(lambda: ConfigurationStore.open_existing(tmp_path), "STORE_MISSING")
    documents, selections = native(2)
    store = ConfigurationStore.initialize(tmp_path, documents=documents, selections=selections)
    assert counters(store.read_snapshot()) == (2, 2, 2)
    for path in legacy:
        assert (tmp_path / path).read_bytes() == before[str(path)]


def test_storage_never_changes_existing_permissions_or_calls_chmod(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Storage must not change existing permissions or Windows ACLs")

    with monkeypatch.context() as guard:
        guard.setattr(os, "chmod", forbidden)
        guard.setattr(Path, "chmod", forbidden)
        if hasattr(os, "fchmod"):
            guard.setattr(os, "fchmod", forbidden)
        store = initialize(tmp_path)
        store.update(advance)
        ConfigurationStore.open_existing(tmp_path).read_snapshot()
        ConfigurationStore.recover_existing(tmp_path)


def test_creation_modes_use_posix_privacy_or_ordinary_windows_inheritance(tmp_path, monkeypatch):
    modes = {}
    original_open, original_mkdir = os.open, Path.mkdir

    def create_file(path, flags, mode=0o777, **kwargs):
        if Path(path) == tmp_path / RELATIVE_PATH:
            modes["file"] = mode
        return original_open(path, flags, mode, **kwargs)

    def create_directory(path, mode=0o777, parents=False, exist_ok=False):
        if path == (tmp_path / RELATIVE_PATH).parent:
            modes["directory"] = mode
        return original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    with monkeypatch.context() as guard:
        guard.setattr(os, "open", create_file)
        guard.setattr(Path, "mkdir", create_directory)
        initialize(tmp_path)
    assert modes == (
        {"file": 0o666, "directory": 0o777}
        if os.name == "nt"
        else {"file": 0o600, "directory": 0o700}
    )


@pytest.mark.parametrize("existing", [b"", b"unrelated-private-file", b"SQLite format 3\0"])
def test_initialization_never_overwrites_existing_files(tmp_path, existing):
    path = tmp_path / RELATIVE_PATH
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_bytes(existing)
    safe_failure(lambda: initialize(tmp_path), "STORE_EXISTS")
    assert path.read_bytes() == existing


def test_second_initialization_preserves_existing_store(tmp_path):
    initialize(tmp_path)
    before = tree_bytes(tmp_path)
    safe_failure(lambda: initialize(tmp_path), "STORE_EXISTS")
    assert tree_bytes(tmp_path) == before


def test_reads_are_detached_immutable_and_do_not_mutate_files(tmp_path):
    store = initialize(tmp_path)
    snapshot = store.read_snapshot()
    before = tree_bytes(tmp_path)
    original_documents, original_selections = native()
    view = snapshot.documents
    view["models"]["profiles"][0]["api_key"] = "changed by caller"
    snapshot.selections["models"]["id"] = "changed"
    assert snapshot.documents == original_documents
    assert snapshot.selections == original_selections
    for _ in range(3):
        assert (
            ConfigurationStore.open_existing(tmp_path).read_snapshot().documents
            == original_documents
        )
    assert tree_bytes(tmp_path) == before
    with pytest.raises(AttributeError, match="immutable"):
        snapshot.revision = "changed"
    for secret in CANARIES:
        assert secret not in str(snapshot) + repr(snapshot) + repr([snapshot]) + repr(store)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(snapshot)
    with pytest.raises(TypeError):
        json.dumps(snapshot)


def test_input_and_callback_aliases_cannot_mutate_snapshots_or_database(tmp_path):
    documents, selections = native()
    store = ConfigurationStore.initialize(tmp_path, documents=documents, selections=selections)
    first = store.read_snapshot()
    documents["models"]["profiles"][0]["api_key"] = "caller changed source"
    selections["models"]["id"] = "caller changed source"
    candidate = native(1)
    second = store.update(lambda snapshot: candidate)
    candidate[0]["models"]["counter"] = 999
    candidate[1]["models"]["id"] = "mutated after callback"
    assert counters(first) == (0, 0, 0)
    assert counters(second) == counters(store.read_snapshot()) == (1, 1, 1)


def test_atomic_updates_publish_fresh_opaque_revisions_and_retain_private_history(tmp_path):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    second = store.update(advance, expected_revision=first.revision)
    third = store.update(advance)
    assert counters(second) == (1, 1, 1) and counters(third) == (2, 2, 2)
    assert len({first.revision, second.revision, third.revision}) == 3
    assert first.installation_id == second.installation_id == third.installation_id
    assert private_rows(
        tmp_path, "SELECT revision, parent_revision FROM generations ORDER BY ordinal"
    ) == [
        (first.revision, None),
        (second.revision, first.revision),
        (third.revision, second.revision),
    ]
    assert not hasattr(store, "rollback") and not hasattr(store, "prune_generations")


def test_expected_revision_conflict_rejects_before_running_stale_callback(tmp_path):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    second = store.update(advance)
    called = []
    safe_failure(
        lambda: store.update(
            lambda snapshot: called.append(True), expected_revision=first.revision
        ),
        "REVISION_CONFLICT",
    )
    assert called == []
    assert store.read_snapshot().revision == second.revision
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(2,)]


def test_independent_roots_have_separate_ids_revisions_and_data(tmp_path):
    one, two = initialize(tmp_path / "one"), initialize(tmp_path / "two")
    first, other = one.read_snapshot(), two.read_snapshot()
    assert first.installation_id != other.installation_id
    assert first.revision != other.revision
    assert first.documents == other.documents  # IDs are not derived from these same secrets.
    one.update(advance)
    assert counters(two.read_snapshot()) == (0, 0, 0)
    safe_failure(lambda: two.update(advance, expected_revision=first.revision), "REVISION_CONFLICT")


def test_copied_database_is_not_silently_adopted_at_another_root(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    initialize(one)
    initialize(two)
    shutil.copyfile(one / RELATIVE_PATH, two / RELATIVE_PATH)
    before = tree_bytes(two)
    safe_failure(lambda: ConfigurationStore.open_existing(two), "INSTALLATION_MISMATCH")
    assert tree_bytes(two) == before


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("kind", ["profile", "environment", "none"])
def test_selection_intents_persist_without_environment_resolution(
    tmp_path, monkeypatch, service, kind
):
    documents, selections = native()
    if kind != "profile":
        documents[service]["active_id"] = None
        selections[service] = {"kind": kind}

    def forbidden(*args, **kwargs):
        pytest.fail("Storage must not resolve environments or contact providers")

    with monkeypatch.context() as guard:
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(os, "environ", None)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(socket, "create_connection", forbidden)
        store = ConfigurationStore.initialize(tmp_path, documents=documents, selections=selections)
        snapshot = store.update(lambda current: (current.documents, current.selections))
        assert snapshot.selections == selections
        assert ConfigurationStore.open_existing(tmp_path).read_snapshot().selections == selections


def test_native_credentials_paths_and_incomplete_profiles_are_preserved_without_transfer_validation(
    tmp_path,
):
    documents, selections = native()
    documents["models"]["profiles"][0].update(model="C:/private/model.bin", api_key="")
    documents["rqdata"]["profiles"][0]["password"] = ""
    documents["search"]["profiles"][0]["model_profile_id"] = "currently-unavailable"
    store = ConfigurationStore.initialize(tmp_path, documents=documents, selections=selections)
    assert store.read_snapshot().documents == documents


@pytest.mark.parametrize("provider", ["off", "brave", "openrouter"])
def test_provided_search_v1_is_stored_without_adoption_or_migration(tmp_path, provider):
    documents, selections = native()
    documents["search"] = {"version": 1, "provider": provider, "brave_api_key": CANARIES[1]}
    selections["search"] = {"kind": "profile", "id": "default"}
    store = ConfigurationStore.initialize(tmp_path, documents=documents, selections=selections)
    assert store.read_snapshot().documents == documents
    assert not (tmp_path / "conexus").exists()


@pytest.mark.parametrize("timeout", [-1, 31, float("nan"), float("inf"), True, "1", None])
def test_timeout_arguments_are_bounded_and_sanitized(tmp_path, timeout):
    safe_failure(
        lambda: ConfigurationStore.open_existing(tmp_path, timeout=timeout), "INVALID_ARGUMENT"
    )


@pytest.mark.parametrize("bad", [None, False, [], {}, "invalid"])
def test_invalid_callback_results_abort_transaction(tmp_path, bad):
    store = initialize(tmp_path)
    before = store.read_snapshot()
    safe_failure(lambda: store.update(lambda snapshot: bad), "INVALID_CONFIGURATION")
    assert store.read_snapshot().revision == before.revision
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]


@pytest.mark.parametrize(
    "case",
    [
        "missing_service",
        "extra_service",
        "native_version",
        "duplicate_id",
        "dangling_active",
        "selection",
        "conflicting_selection",
        "cycle",
        "nonfinite",
        "opaque",
        "unicode",
    ],
)
def test_invalid_structural_inputs_roll_back_without_leaking_credentials(tmp_path, case, caplog):
    store = initialize(tmp_path)
    documents, selections = native(1)
    if case == "missing_service":
        documents.pop("rqdata")
    elif case == "extra_service":
        documents[CANARIES[0]] = CANARIES[1]
    elif case == "native_version":
        documents["models"]["version"] = True
    elif case == "duplicate_id":
        documents["models"]["profiles"] *= 2
    elif case == "dangling_active":
        documents["models"]["active_id"] = CANARIES[0]
    elif case == "selection":
        selections["models"] = {"kind": CANARIES[1]}
    elif case == "conflicting_selection":
        selections["models"] = {"kind": "none"}
    elif case == "cycle":
        documents["models"]["cycle"] = documents
    elif case == "nonfinite":
        documents["models"]["value"] = float("nan")
    elif case == "opaque":

        class SecretObject:
            def __repr__(self):
                raise AssertionError("An opaque value must not be coerced")

        documents["models"]["value"] = SecretObject()
    else:
        documents["models"]["value"] = CANARIES[2] + "\ud800"
    previous = store.read_snapshot().revision
    safe_failure(
        lambda: store.update(lambda snapshot: (documents, selections)),
        "INVALID_CONFIGURATION",
        caplog,
    )
    assert store.read_snapshot().revision == previous
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]


@pytest.mark.parametrize("kind", ["large", "deep", "many_values"])
def test_document_resource_limits_apply_before_initialization(tmp_path, kind):
    documents, selections = native()
    if kind == "large":
        documents["models"]["value"] = "x" * (4 * 1024 * 1024 + 1)
    elif kind == "many_values":
        documents["models"]["value"] = [None] * 33000
    else:
        value = None
        for _ in range(40):
            value = [value]
        documents["models"]["value"] = value
    safe_failure(
        lambda: ConfigurationStore.initialize(tmp_path, documents=documents, selections=selections),
        "INVALID_CONFIGURATION",
    )
    assert not (tmp_path / "app").exists()


def test_callback_validation_errors_are_sanitized_and_do_not_publish(tmp_path, caplog):
    store = initialize(tmp_path)
    first = store.read_snapshot()

    def rejected(snapshot):
        raise ValueError(" ".join(CANARIES))

    safe_failure(lambda: store.update(rejected), "UPDATE_FAILED", caplog)
    assert store.read_snapshot().revision == first.revision


@pytest.mark.parametrize(
    "kind,expected",
    [
        (asyncio.CancelledError, asyncio.CancelledError),
        (FutureCancelledError, FutureCancelledError),
        (KeyboardInterrupt, KeyboardInterrupt),
        (SystemExit, SystemExit),
        (GeneratorExit, GeneratorExit),
        (InterruptedError, InterruptedError),
        (BaseException, BaseException),
        (PrivateInterruption, BaseException),
        (PrivateKeyboardInterrupt, KeyboardInterrupt),
        (PrivateCancellation, asyncio.CancelledError),
    ],
)
@pytest.mark.parametrize("point", ["callback", "after_insert", "after_publish"])
def test_callback_interruptions_are_redacted_and_rolled_back(
    tmp_path, monkeypatch, caplog, kind, expected, point
):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    canary = (
        "fixture-interruption-"
        + kind.__module__
        + "-"
        + kind.__name__
        + "-"
        + point
        + "-secret-4681"
    )
    secrets = (*CANARIES, canary)
    execute = implementation._Session.execute

    def interrupt():
        error = kind(" ".join(secrets))
        error.private_payload = " ".join(secrets)
        if hasattr(error, "add_note"):
            error.add_note(" ".join(secrets))
        raise error from ValueError(canary + "-cause")

    def interrupted(snapshot):
        if point == "callback":
            interrupt()
        return advance(snapshot)

    def injected(session, sql, parameters=()):
        result = execute(session, sql, parameters)
        if (point == "after_insert" and sql.startswith("INSERT INTO generations")) or (
            point == "after_publish" and sql.startswith("UPDATE store_metadata")
        ):
            interrupt()
        return result

    caught = clean_type = clean_chain = clean_frames = False
    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", injected)
        try:
            store.update(interrupted)
        except BaseException as error:
            caught = True
            clean_type = type(error) is expected
            clean_chain = error.__cause__ is None and error.__context__ is None
            clean_frames = not {"interrupt", "interrupted", "injected"}.intersection(
                frame.name for frame in traceback.extract_tb(error.__traceback__)
            )
            visible = str(error) + repr(error) + repr(error.args) + repr(vars(error))
            visible += "".join(traceback.format_exception(type(error), error, error.__traceback__))
            visible += repr(pickle.dumps(error))
            leaked = any(secret in visible or secret in caplog.text for secret in secrets)
            safe_exit = expected is not SystemExit or error.code == 1
    # Assert after leaving the handler: a negative control must not print the
    # raw interruption as the AssertionError's context.
    assert caught, "An interruption must propagate, never become a successful update."
    assert not leaked, "The interruption exposed its private fixture value."
    assert clean_type and clean_chain and clean_frames
    assert safe_exit
    assert store.read_snapshot().revision == first.revision
    assert store.read_snapshot().documents == first.documents
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]
    assert counters(store.update(advance)) == (1, 1, 1)  # Both locks were released.


@pytest.mark.parametrize("status", [None, 0, 1, 7, 130])
def test_redacted_system_exit_preserves_plain_exit_status(tmp_path, status):
    store = initialize(tmp_path)

    def interrupted(snapshot):
        raise SystemExit(status)

    with pytest.raises(SystemExit) as caught:
        store.update(interrupted)
    assert caught.value.code == status
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert counters(store.read_snapshot()) == (0, 0, 0)


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("store_code", ConfigurationStoreError),
        ("exit_code", SystemExit),
        ("cancel_class", asyncio.CancelledError),
        ("keyboard_class", KeyboardInterrupt),
        ("sqlite_fields", ConfigurationStoreError),
    ],
)
@pytest.mark.parametrize("point", ["callback", "after_publish"])
@pytest.mark.parametrize(
    "failure", [RuntimeError, KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_hostile_exception_descriptors_never_run_or_escape(
    tmp_path, monkeypatch, caplog, kind, expected, point, failure
):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    canary = f"fixture-hostile-{kind}-{point}-{failure.__module__}-{failure.__name__}-secret-9136"
    secrets = (*CANARIES, canary)
    payload = " ".join(secrets)
    execute = implementation._Session.execute
    accesses = []

    def interrupt():
        error, observed = hostile_exception(kind, payload, failure)
        accesses.append(observed)
        raise error

    def interrupted(snapshot):
        if point == "callback":
            interrupt()
        return advance(snapshot)

    def injected(session, sql, parameters=()):
        result = execute(session, sql, parameters)
        if point == "after_publish" and sql.startswith("UPDATE store_metadata"):
            interrupt()
        return result

    caught = clean_type = clean_chain = False
    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", injected)
        try:
            store.update(interrupted)
        except BaseException as error:
            caught = True
            clean_type = type(error) is expected
            clean_chain = error.__cause__ is None and error.__context__ is None
            # A failing implementation can return a hostile object or chain.
            # Inspect only native args until both public-boundary checks pass.
            visible = repr(BaseException.args.__get__(error))
            if clean_type and clean_chain:
                visible += str(error) + repr(error) + repr(vars(error))
                visible += "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                )
                visible += repr(pickle.dumps(error))
            leaked = any(secret in visible or secret in caplog.text for secret in secrets)
            exit_safe = expected is not SystemExit or (
                clean_type and SystemExit.code.__get__(error) == 1
            )
            expected_code = (
                "UPDATE_FAILED" if kind == "store_code" or point == "callback" else "STORE_BUSY"
            )
            code_safe = expected is not ConfigurationStoreError or (
                clean_type and error.code == expected_code
            )
    assert caught and clean_type and clean_chain
    assert not leaked
    assert accesses and not any(accesses), "Classification invoked a hostile descriptor."
    assert exit_safe and code_safe
    assert store.read_snapshot().revision == first.revision
    assert store.read_snapshot().documents == first.documents
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]
    assert counters(store.update(advance)) == (1, 1, 1)


@pytest.mark.parametrize(
    "kind,expected",
    [
        (asyncio.CancelledError, asyncio.CancelledError),
        (FutureCancelledError, FutureCancelledError),
        (KeyboardInterrupt, KeyboardInterrupt),
        (SystemExit, SystemExit),
        (PrivateInterruption, BaseException),
        (ValueError, ConfigurationStoreError),
    ],
)
@pytest.mark.parametrize(
    "failure", [RuntimeError, KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
@pytest.mark.parametrize("point", ["callback", "after_publish"])
def test_classification_failure_preserves_interruption_and_clears_private_chains(
    tmp_path, monkeypatch, caplog, kind, expected, failure, point
):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    canary = f"fixture-classifier-{kind.__module__}-{kind.__name__}-{failure.__name__}-{point}-secret-0247"
    secrets = (*CANARIES, canary)
    execute = implementation._Session.execute
    classified = False

    def broken_classifier(error, depth=0):
        nonlocal classified
        classified = True
        raise failure(" ".join(secrets))

    def interrupted(snapshot):
        if point == "callback":
            raise kind(" ".join(secrets))
        return advance(snapshot)

    def injected(session, sql, parameters=()):
        result = execute(session, sql, parameters)
        if point == "after_publish" and sql.startswith("UPDATE store_metadata"):
            raise kind(" ".join(secrets))
        return result

    caught = clean_type = clean_chain = False
    with monkeypatch.context() as guard:
        guard.setattr(implementation, "_redacted_interruption", broken_classifier)
        guard.setattr(implementation._Session, "execute", injected)
        try:
            store.update(interrupted)
        except BaseException as error:
            caught = True
            clean_type = type(error) is expected
            clean_chain = error.__cause__ is None and error.__context__ is None
            visible = str(error) + repr(error) + repr(error.args) + repr(vars(error))
            visible += "".join(traceback.format_exception(type(error), error, error.__traceback__))
            visible += repr(pickle.dumps(error))
            leaked = any(secret in visible or secret in caplog.text for secret in secrets)
            exit_safe = expected is not SystemExit or error.code == 1
    assert classified and caught and clean_type and clean_chain
    assert not leaked
    assert exit_safe
    assert store.read_snapshot().revision == first.revision
    assert store.read_snapshot().documents == first.documents
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]


def test_forged_class_does_not_fabricate_cancellation(tmp_path):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    accesses = []

    class ForgedInterruption(BaseException):
        @property
        def __class__(self):
            accesses.append(True)
            return asyncio.CancelledError

    def interrupted(snapshot):
        raise ForgedInterruption(" ".join(CANARIES))

    caught = correct = clean_chain = False
    try:
        store.update(interrupted)
    except BaseException as error:
        caught = True
        correct = type(error) is BaseException
        clean_chain = error.__context__ is None and error.__cause__ is None
        leaked = any(secret in str(error) + repr(error) for secret in CANARIES)
    assert caught and correct and clean_chain
    assert not leaked and not accesses
    assert store.read_snapshot().revision == first.revision


def test_exception_storage_bypasses_hostile_dictionary_hooks_and_keys(tmp_path):
    store = initialize(tmp_path)
    accessed, armed = [], False

    class HostileDictionary(dict):
        def get(self, *args):
            accessed.append("get")
            raise RuntimeError(" ".join(CANARIES))

        def items(self):
            accessed.append("items")
            raise RuntimeError(" ".join(CANARIES))

    class HostileKey:
        def __hash__(self):
            return hash("code")

        def __eq__(self, other):
            if armed:
                accessed.append("key")
                raise RuntimeError(" ".join(CANARIES))
            return False

    error = ConfigurationStoreError("UPDATE_FAILED")
    values = HostileDictionary()
    values[HostileKey()] = " ".join(CANARIES)
    values["code"] = "UPDATE_FAILED"
    BaseException.__dict__["__dict__"].__set__(error, values)
    armed = True

    def interrupted(snapshot):
        raise error

    safe_failure(lambda: store.update(interrupted), "UPDATE_FAILED")
    assert not accessed
    assert counters(store.read_snapshot()) == (0, 0, 0)


@pytest.mark.parametrize("value", [None, {}, 42, "unknown-private-code"])
def test_unrecognized_native_error_code_is_always_sanitized(tmp_path, value):
    store = initialize(tmp_path)

    def interrupted(snapshot):
        error = ConfigurationStoreError("UPDATE_FAILED")
        error.code = value
        raise error

    safe_failure(lambda: store.update(interrupted), "STORE_UNAVAILABLE")
    assert counters(store.read_snapshot()) == (0, 0, 0)


@pytest.mark.parametrize("hostile", [False, True])
def test_async_task_remains_cancelled_with_a_redacted_cancellation(tmp_path, hostile):
    store = initialize(tmp_path)
    canary = "fixture-task-cancellation-secret-6803"

    def interrupted(snapshot):
        if hostile:
            error, _ = hostile_exception("cancel_class", canary, RuntimeError)
            raise error
        raise asyncio.CancelledError(canary)

    async def run():
        async def operation():
            return store.update(interrupted)

        task = asyncio.create_task(operation())
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert task.cancelled()
        assert canary not in str(caught.value) + repr(caught.value.args)
        assert caught.value.__cause__ is None and caught.value.__context__ is None

    asyncio.run(run())
    assert counters(store.read_snapshot()) == (0, 0, 0)


@pytest.mark.parametrize("status", [None, 0, 7])
def test_exit_subclass_uses_native_status_without_calling_code_property(tmp_path, status):
    store = initialize(tmp_path)
    error, accesses = hostile_exception("exit_code", " ".join(CANARIES), RuntimeError)
    SystemExit.__init__(error, status)

    def interrupted(snapshot):
        raise error

    with pytest.raises(SystemExit) as caught:
        store.update(interrupted)
    assert type(caught.value) is SystemExit and caught.value.code == status
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert not accesses
    assert all(secret not in repr(pickle.dumps(caught.value)) for secret in CANARIES)
    assert counters(store.read_snapshot()) == (0, 0, 0)


@pytest.mark.parametrize(
    "kind", [asyncio.CancelledError, KeyboardInterrupt, SystemExit, ValueError]
)
def test_sanitized_exceptions_do_not_inherit_private_ambient_context(tmp_path, kind):
    store = initialize(tmp_path)
    canary = "fixture-ambient-" + kind.__name__ + "-secret-7914"

    def interrupted(snapshot):
        raise kind(canary + "-callback")

    caught = leaked = False
    try:
        raise ValueError(canary + "-outer-handler")
    except ValueError:
        try:
            store.update(interrupted)
        except BaseException as error:
            caught = True
            clean_chain = error.__context__ is None and error.__cause__ is None
            visible = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            leaked = canary in visible
    assert caught and clean_chain
    assert not leaked
    assert counters(store.read_snapshot()) == (0, 0, 0)


@pytest.mark.skipif(not hasattr(builtins, "BaseExceptionGroup"), reason="Python 3.11+ groups")
@pytest.mark.parametrize("cancellation", [asyncio.CancelledError, FutureCancelledError])
def test_grouped_cancellation_preserves_structure_without_private_leaves(
    tmp_path, caplog, cancellation
):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    canary = "fixture-grouped-" + cancellation.__module__ + "-secret-5792"
    group_type = builtins.BaseExceptionGroup

    def interrupted(snapshot):
        raise group_type(
            canary,
            [cancellation(canary), group_type(canary, [ValueError(canary), SystemExit(canary)])],
        ) from ValueError(canary)

    caught = clean_structure = clean_chain = leaked = False
    try:
        store.update(interrupted)
    except BaseException as error:
        caught = True
        clean_structure = (
            isinstance(error, group_type)
            and type(error.exceptions[0]) is cancellation
            and type(error.exceptions[1].exceptions[0]) is Exception
            and type(error.exceptions[1].exceptions[1]) is SystemExit
        )
        pending = [error]
        clean_chain = True
        while pending:
            item = pending.pop()
            clean_chain &= item.__cause__ is None and item.__context__ is None
            if isinstance(item, group_type):
                pending.extend(item.exceptions)
        visible = str(error) + repr(error) + repr(pickle.dumps(error))
        visible += "".join(traceback.format_exception(type(error), error, error.__traceback__))
        leaked = canary in visible or canary in caplog.text
    assert caught and clean_structure and clean_chain
    assert not leaked
    assert store.read_snapshot().revision == first.revision
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]


def test_invalid_metadata_singleton_rejects_update_without_false_success(tmp_path, caplog):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    with closing(sqlite3.connect(tmp_path / RELATIVE_PATH)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE store_metadata SET singleton=2")
        connection.commit()
    before = tree_bytes(tmp_path)
    safe_failure(store.read_snapshot, "STORE_CORRUPT", caplog)
    safe_failure(lambda: ConfigurationStore.open_existing(tmp_path), "STORE_CORRUPT", caplog)
    safe_failure(lambda: store.update(advance), "STORE_CORRUPT", caplog)
    assert tree_bytes(tmp_path) == before
    assert private_rows(tmp_path, "SELECT current_revision FROM store_metadata") == [
        (first.revision,)
    ]
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]


@pytest.mark.parametrize("case", ["missing", "multiple", "null_singleton", "text_singleton"])
def test_malformed_metadata_fails_before_callback_and_is_not_repaired(tmp_path, case):
    store = initialize(tmp_path)
    row = private_rows(tmp_path, "SELECT * FROM store_metadata")[0]
    with closing(sqlite3.connect(tmp_path / RELATIVE_PATH)) as connection:
        connection.execute("DROP TABLE store_metadata")
        connection.execute(
            "CREATE TABLE store_metadata "
            "(singleton, protocol_version, installation_id, root_key, current_revision)"
        )
        if case != "missing":
            singleton = {"null_singleton": None, "text_singleton": "1"}.get(case, 1)
            connection.execute(
                "INSERT INTO store_metadata VALUES (?, ?, ?, ?, ?)", (singleton, *row[1:])
            )
            if case == "multiple":
                connection.execute("INSERT INTO store_metadata VALUES (?, ?, ?, ?, ?)", row)
        connection.commit()
    before, called = tree_bytes(tmp_path), False

    def unexpected(snapshot):
        nonlocal called
        called = True
        return advance(snapshot)

    safe_failure(lambda: store.update(unexpected), "STORE_CORRUPT")
    safe_failure(store.read_snapshot, "STORE_CORRUPT")
    safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), "STORE_CORRUPT")
    assert not called and tree_bytes(tmp_path) == before
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]


def test_zero_row_publication_cannot_return_an_unpublished_revision(tmp_path):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    tamper(
        tmp_path,
        "CREATE TRIGGER ignore_publication BEFORE UPDATE ON store_metadata "
        "BEGIN SELECT RAISE(IGNORE); END",
    )
    safe_failure(lambda: store.update(advance), "STORE_CORRUPT")
    assert store.read_snapshot().revision == first.revision
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]


@pytest.mark.parametrize("rowcount", [0, 2])
def test_publication_requires_exactly_one_reported_row(tmp_path, monkeypatch, rowcount):
    store = initialize(tmp_path)
    first, execute = store.read_snapshot(), implementation._Session.execute
    reached = False

    class WrongCount:
        pass

    def injected(session, sql, parameters=()):
        nonlocal reached
        result = execute(session, sql, parameters)
        if sql.startswith("UPDATE store_metadata"):
            reached = True
            result = WrongCount()
            result.rowcount = rowcount
        return result

    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", injected)
        safe_failure(lambda: store.update(advance), "STORE_CORRUPT")
    assert reached
    assert store.read_snapshot().revision == first.revision
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]


@pytest.mark.parametrize("operation", ["read", "update", "other_root"])
def test_callbacks_cannot_reenter_storage_or_introduce_lock_order_cycles(tmp_path, operation):
    store, other = initialize(tmp_path / "one"), initialize(tmp_path / "two")
    calls = {
        "read": store.read_snapshot,
        "update": lambda: store.update(advance),
        "other_root": other.read_snapshot,
    }
    safe_failure(lambda: store.update(lambda snapshot: calls[operation]()), "REENTRANT_ACCESS")
    assert counters(store.read_snapshot()) == (0, 0, 0)


@pytest.mark.parametrize(
    "phase", ["before_insert", "after_insert", "before_publish", "after_publish", "before_commit"]
)
def test_injected_precommit_failures_preserve_previous_generation(
    tmp_path, monkeypatch, phase, caplog
):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    execute = implementation._Session.execute

    def injected(session, sql, parameters=()):
        insert, publish = (
            sql.startswith("INSERT INTO generations"),
            sql.startswith("UPDATE store_metadata"),
        )
        if (
            (phase == "before_insert" and insert)
            or (phase == "before_publish" and publish)
            or (phase == "before_commit" and sql == "COMMIT")
        ):
            raise OSError(" ".join(CANARIES))
        result = execute(session, sql, parameters)
        if (phase == "after_insert" and insert) or (phase == "after_publish" and publish):
            raise OSError(" ".join(CANARIES))
        return result

    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", injected)
        safe_failure(lambda: store.update(advance), "STORE_UNAVAILABLE", caplog)
    assert store.read_snapshot().revision == first.revision
    assert counters(store.read_snapshot()) == (0, 0, 0)
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]
    assert not (tmp_path / (str(RELATIVE_PATH) + "-journal")).exists()


def test_uncertain_postcommit_acknowledgement_leaves_one_complete_new_generation(
    tmp_path, monkeypatch
):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    execute = implementation._Session.execute

    def injected(session, sql, parameters=()):
        result = execute(session, sql, parameters)
        if sql == "COMMIT":
            raise OSError(CANARIES[0])
        return result

    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", injected)
        safe_failure(lambda: store.update(advance), "STORE_UNAVAILABLE")
    snapshot = store.read_snapshot()
    assert snapshot.revision != first.revision and counters(snapshot) == (1, 1, 1)
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(2,)]


def test_failed_initialization_is_never_replaced_or_treated_as_active(tmp_path, monkeypatch):
    execute = implementation._Session.execute

    def injected(session, sql, parameters=()):
        if sql == "COMMIT":
            raise OSError(CANARIES[0])
        return execute(session, sql, parameters)

    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", injected)
        safe_failure(lambda: initialize(tmp_path), "STORE_UNAVAILABLE")
    before = tree_bytes(tmp_path)
    safe_failure(lambda: ConfigurationStore.open_existing(tmp_path), "STORE_CORRUPT")
    safe_failure(lambda: initialize(tmp_path), "STORE_EXISTS")
    assert tree_bytes(tmp_path) == before


def test_concurrent_threads_serialize_whole_read_modify_write(tmp_path):
    initialize(tmp_path)

    def work(index):
        store = ConfigurationStore.open_existing(tmp_path, timeout=10)
        snapshot = store.update(advance)
        assert len(set(counters(snapshot))) == 1
        return snapshot.revision

    with ThreadPoolExecutor(max_workers=8) as executor:
        revisions = list(executor.map(work, range(16)))
    assert len(set(revisions)) == 16
    assert counters(ConfigurationStore.open_existing(tmp_path).read_snapshot()) == (16, 16, 16)


def test_snapshot_stays_consistent_while_another_thread_publishes(tmp_path, monkeypatch):
    store = initialize(tmp_path)
    entered, release, writing = threading.Event(), threading.Event(), threading.Event()
    execute = implementation._Session.execute

    def pause_reader(session, sql, parameters=()):
        # The metadata cursor has already been exhausted. A statement-level
        # read lock alone cannot protect the following generation lookup.
        if sql.startswith("SELECT models, search") and threading.current_thread().name.startswith(
            "snapshot-reader"
        ):
            entered.set()
            assert release.wait(10)
        return execute(session, sql, parameters)

    def changed(snapshot):
        documents, selections = advance(snapshot)
        for service in SERVICES:
            documents[service]["active_id"] = None
            selections[service] = {"kind": "none"}
        writing.set()
        return documents, selections

    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", pause_reader)
        with (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-reader") as readers,
            ThreadPoolExecutor(max_workers=1) as writers,
        ):
            reader = readers.submit(store.read_snapshot)
            try:
                assert entered.wait(10)
                writer = writers.submit(store.update, changed)
                assert writing.wait(10)
                with pytest.raises(FutureTimeoutError):
                    writer.result(timeout=1)
                release.set()
                previous, current = reader.result(timeout=10), writer.result(timeout=10)
            finally:
                release.set()
    assert counters(previous) == (0, 0, 0) and previous.selections == native()[1]
    assert counters(current) == (1, 1, 1)
    assert all(choice == {"kind": "none"} for choice in current.selections.values())


def test_concurrent_python_processes_preserve_every_update(tmp_path):
    store = initialize(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes, pipes = [], []
    try:
        for worker in range(4):
            receive, send = context.Pipe(duplex=False)
            process = context.Process(
                target=_child_updates, args=(str(tmp_path), start, send, worker, 3)
            )
            process.start()
            send.close()
            processes.append(process)
            pipes.append(receive)
        for receive in pipes:
            assert receive.poll(30) and receive.recv() == "ready"
        start.set()
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
    finally:
        for process in processes:
            stop_owned_process(process)
        for receive in pipes:
            receive.close()
    snapshot = store.read_snapshot()
    assert counters(snapshot) == (12, 12, 12)
    assert {row["id"] for row in snapshot.documents["models"]["profiles"]} == {
        "model",
        *(f"worker-{worker}-{iteration}" for worker in range(4) for iteration in range(3)),
    }
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(13,)]


def test_stale_process_writers_cannot_publish_over_the_winner(tmp_path):
    store = initialize(tmp_path)
    revision = store.read_snapshot().revision
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes, pipes = [], []
    try:
        for _ in range(3):
            receive, send = context.Pipe(duplex=True)
            process = context.Process(
                target=_child_compare_swap, args=(str(tmp_path), revision, start, send)
            )
            process.start()
            send.close()
            processes.append(process)
            pipes.append(receive)
        for receive in pipes:
            assert receive.poll(30) and receive.recv() == "ready"
        start.set()
        outcomes = []
        for receive in pipes:
            assert receive.poll(30)
            outcomes.append(receive.recv())
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            stop_owned_process(process)
        for receive in pipes:
            receive.close()
    assert outcomes.count("REVISION_CONFLICT") == 2
    assert outcomes.count(store.read_snapshot().revision) == 1
    assert counters(store.read_snapshot()) == (1, 1, 1)
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(2,)]


def test_concurrent_initialization_has_one_winner_without_overwriting(tmp_path):
    def create(_index):
        try:
            return initialize(tmp_path).read_snapshot().installation_id
        except ConfigurationStoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(create, range(4)))
    current = ConfigurationStore.open_existing(tmp_path).read_snapshot()
    assert outcomes.count("STORE_EXISTS") == 3
    assert outcomes.count(current.installation_id) == 1
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(1,)]


@pytest.mark.parametrize("kind", ["sqlite_writer", "local_writer", "sqlite_reader"])
def test_busy_timeouts_are_bounded_and_do_not_publish(tmp_path, kind):
    store = initialize(tmp_path, timeout=0.15)
    entered, release = threading.Event(), threading.Event()

    def hold():
        if kind == "local_writer":

            def blocked(snapshot):
                entered.set()
                assert release.wait(10)
                return advance(snapshot)

            store.update(blocked)
        else:
            with closing(
                sqlite3.connect(tmp_path / RELATIVE_PATH, isolation_level=None)
            ) as connection:
                connection.execute("BEGIN IMMEDIATE" if kind == "sqlite_writer" else "BEGIN")
                connection.execute("SELECT * FROM store_metadata").fetchall()
                entered.set()
                assert release.wait(10)
                connection.rollback()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(hold)
        try:
            assert entered.wait(10)
            started = time.monotonic()
            safe_failure(lambda: store.update(advance), "STORE_BUSY")
            elapsed = time.monotonic() - started
            assert 0.05 <= elapsed < 2.0
        finally:
            release.set()
            future.result(timeout=10)
    expected = 1 if kind == "local_writer" else 0
    assert counters(store.read_snapshot()) == (expected,) * 3


@pytest.mark.parametrize("phase", ["insert", "publish", "committed"])
def test_killed_test_worker_recovers_only_complete_committed_state(tmp_path, phase):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_child_interrupted, args=(str(tmp_path), send, phase))
    process.start()
    send.close()
    try:
        assert receive.poll(30) and receive.recv() == "ready"
        journal = tmp_path / (str(RELATIVE_PATH) + "-journal")
        if phase != "committed":
            assert journal.is_file() and journal.stat().st_size > 512
        stop_owned_process(process)
        before = tree_bytes(tmp_path)
        if phase != "committed":
            safe_failure(store.read_snapshot, "RECOVERY_REQUIRED")
            assert tree_bytes(tmp_path) == before
        recovered = ConfigurationStore.recover_existing(tmp_path).read_snapshot()
        if phase == "committed":
            assert recovered.revision != first.revision and counters(recovered) == (1, 1, 1)
        else:
            assert recovered.revision == first.revision and recovered.documents == first.documents
        assert private_rows(tmp_path, "PRAGMA quick_check") == [("ok",)]
        assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [
            (2 if phase == "committed" else 1,)
        ]
        assert not journal.exists()
    finally:
        stop_owned_process(process)
        receive.close()


def test_recovery_waits_for_active_process_with_unsynced_journal(tmp_path):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_child_interrupted, args=(str(tmp_path), send, "cold"))
    process.start()
    send.close()
    try:
        assert receive.poll(30) and receive.recv() == "ready"
        journal = (tmp_path / RELATIVE_PATH).with_name(RELATIVE_PATH.name + "-journal")
        assert journal.read_bytes()[:12] == b"\0" * 12
        before = tree_bytes(tmp_path)
        started = time.monotonic()
        safe_failure(
            lambda: ConfigurationStore.recover_existing(tmp_path, timeout=0.15), "STORE_BUSY"
        )
        assert 0.05 <= time.monotonic() - started < 2
        assert tree_bytes(tmp_path) == before
        stop_owned_process(process)
        recovered = ConfigurationStore.recover_existing(tmp_path).read_snapshot()
        assert recovered.revision == first.revision and recovered.documents == first.documents
    finally:
        stop_owned_process(process)
        receive.close()


@pytest.fixture
def interrupted_store(tmp_path):
    store = initialize(tmp_path)
    first = store.update(advance)  # Recovery must not select the older initial generation.
    path = tmp_path / RELATIVE_PATH
    original_header = path.read_bytes()[:100]
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_child_interrupted, args=(str(tmp_path), send, "publish"))
    process.start()
    send.close()
    try:
        assert receive.poll(30) and receive.recv() == "ready"
        stop_owned_process(process)
        journal = path.with_name(path.name + "-journal")
        assert journal.stat().st_size > 512
        assert journal.read_bytes()[:8] == b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"
        yield store, first, path, journal, original_header
    finally:
        stop_owned_process(process)
        receive.close()


@pytest.mark.parametrize("tail", ["short12", "short27", "zero_records", "invalidated"])
def test_native_recovery_accepts_legitimate_trailing_segments(tmp_path, interrupted_store, tail):
    store, first, path, journal, _ = interrupted_store
    data = journal.read_bytes()
    offset, previous = 0, None
    while offset + 28 <= len(data):
        header = data[offset : offset + 28]
        if header[:8] != b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7":
            break
        count = int.from_bytes(header[8:12], "big")
        sector = int.from_bytes(header[20:24], "big")
        page = int.from_bytes(header[24:28], "big")
        assert 0 < count < 0xFFFFFFFF
        end = offset + sector + count * (page + 8)
        assert end <= len(data)
        previous = header
        offset = (end + sector - 1) // sector * sector
    assert previous is not None
    prefix = data[:offset].ljust(offset, b"\0")
    if tail.startswith("short"):
        suffix = b"\0" * int(tail[5:])
    else:
        suffix = bytearray(previous)
        if tail == "zero_records":
            suffix[8:12] = b"\0" * 4
        else:
            suffix[0] = 0
        suffix = bytes(suffix).ljust(sector, b"\0")
    journal.write_bytes(prefix + suffix)
    database_bytes, journal_bytes = path.read_bytes(), journal.read_bytes()

    # Positive control: native SQLite must recover this fixture exactly before
    # the store's acceptance is tested. Restore the same files in the same root.
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as connection:
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    reference = ConfigurationStore.open_existing(tmp_path).read_snapshot()
    assert reference.revision == first.revision and reference.documents == first.documents
    path.write_bytes(database_bytes)
    descriptor = os.open(
        journal, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o666 if os.name == "nt" else 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(journal_bytes)
    before = tree_bytes(tmp_path)
    safe_failure(store.read_snapshot, "RECOVERY_REQUIRED")
    assert tree_bytes(tmp_path) == before
    recovered = ConfigurationStore.recover_existing(tmp_path).read_snapshot()
    assert recovered.revision == first.revision
    assert recovered.installation_id == first.installation_id
    assert recovered.documents == first.documents
    assert recovered.selections == first.selections
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(2,)]
    assert not journal.exists()


@pytest.mark.parametrize("damage", ["magic", "application_id", "protocol"])
def test_hot_journal_restores_damaged_header_and_exact_latest_commit(
    tmp_path, interrupted_store, damage
):
    store, first, path, journal, original_header = interrupted_store
    content = bytearray(path.read_bytes())
    offset, replacement = {
        "magic": (0, b"damaged header!!?"),
        "application_id": (68, b"\0" * 4),
        "protocol": (60, (999).to_bytes(4, "big")),
    }[damage]
    content[offset : offset + len(replacement)] = replacement
    path.write_bytes(content)
    before = tree_bytes(tmp_path)
    expected = "UNSUPPORTED_PROTOCOL" if damage == "protocol" else "STORE_CORRUPT"
    safe_failure(store.read_snapshot, expected)
    safe_failure(lambda: ConfigurationStore.open_existing(tmp_path), expected)
    assert tree_bytes(tmp_path) == before
    recovered = ConfigurationStore.recover_existing(tmp_path).read_snapshot()
    assert recovered.revision == first.revision
    assert recovered.installation_id == first.installation_id
    assert recovered.documents == first.documents
    assert recovered.selections == first.selections
    assert path.read_bytes()[:100] == original_header
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(2,)]
    assert private_rows(tmp_path, "PRAGMA quick_check") == [("ok",)]
    assert not journal.exists()


@pytest.mark.parametrize("journal_case", ["missing", "bad_magic", "truncated", "bad_checksum"])
def test_damaged_header_with_unusable_journal_fails_closed(
    tmp_path, interrupted_store, journal_case, caplog
):
    store, _, path, journal, _ = interrupted_store
    content = bytearray(path.read_bytes())
    content[:16] = b"damaged header!!?"
    path.write_bytes(content)
    journal_bytes = bytearray(journal.read_bytes())
    if journal_case == "missing":
        journal.unlink()  # This fixture owns the file; never touch a live store.
    elif journal_case == "bad_magic":
        journal_bytes[:8] = b"\0" * 8
        journal.write_bytes(journal_bytes)
    elif journal_case == "truncated":
        journal.write_bytes(journal_bytes[:64])
    else:
        sector = int.from_bytes(journal_bytes[20:24], "big")
        page = int.from_bytes(journal_bytes[24:28], "big")
        journal_bytes[sector + 4 + page] ^= 1
        journal.write_bytes(journal_bytes)
    before = tree_bytes(tmp_path)
    safe_failure(store.read_snapshot, "STORE_CORRUPT", caplog)
    safe_failure(lambda: ConfigurationStore.open_existing(tmp_path), "STORE_CORRUPT", caplog)
    assert tree_bytes(tmp_path) == before
    safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), "STORE_CORRUPT", caplog)
    assert path.exists()
    safe_failure(lambda: initialize(tmp_path), "STORE_EXISTS", caplog)


@pytest.mark.parametrize("statement", ["PRAGMA journal_mode", "PRAGMA quick_check"])
def test_sqlite_reported_recovery_errors_are_sanitized_and_fail_closed(
    tmp_path, interrupted_store, monkeypatch, caplog, statement
):
    _, first, _, _, _ = interrupted_store
    execute, reached = implementation._Session.execute, False

    def injected(session, sql, parameters=()):
        nonlocal reached
        if sql == statement:
            reached = True
            error = sqlite3.DatabaseError(" ".join(CANARIES))
            error.sqlite_errorcode = 11
            raise error
        return execute(session, sql, parameters)

    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", injected)
        safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), "STORE_CORRUPT", caplog)
    assert reached
    restored = ConfigurationStore.recover_existing(tmp_path).read_snapshot()
    assert restored.revision == first.revision and restored.documents == first.documents
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(2,)]


@pytest.mark.parametrize("kind", ["directory", "hardlink", "symlink"])
def test_untrusted_journal_paths_are_rejected_without_recovery(tmp_path, kind):
    store = initialize(tmp_path)
    path = tmp_path / RELATIVE_PATH
    journal = path.with_name(path.name + "-journal")
    target = tmp_path / "test-owned-journal-target"
    target.write_bytes(b"untrusted fixture " + CANARIES[0].encode())
    if kind == "directory":
        journal.mkdir(mode=0o700)
    elif kind == "hardlink":
        try:
            os.link(target, journal)
        except OSError:
            pytest.skip("Filesystem does not support test-owned hard links")
    else:
        try:
            journal.symlink_to(target)
        except OSError:
            pytest.skip("Creating symlinks is unavailable without changing OS privileges")
    before = tree_bytes(tmp_path)
    safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), "UNSAFE_STORAGE")
    assert tree_bytes(tmp_path) == before
    assert repr(store) == "ConfigurationStore(<private>)"


@pytest.mark.parametrize("field,value", [(8, 0), (8, 0xFFFFFFFE), (20, 513), (24, 513)])
def test_unusable_journal_cannot_recreate_an_unrecoverable_database(
    tmp_path, interrupted_store, field, value
):
    _, _, path, journal, _ = interrupted_store
    data = bytearray(journal.read_bytes())
    assert data[:8] == b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"
    data[field : field + 4] = value.to_bytes(4, "big")
    journal.write_bytes(data)
    content = bytearray(path.read_bytes())
    content[:16] = b"damaged header!!?"
    path.write_bytes(content)
    safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), "STORE_CORRUPT")
    assert path.exists()
    safe_failure(lambda: initialize(tmp_path), "STORE_EXISTS")


@pytest.mark.parametrize("field,expected", [(60, "UNSUPPORTED_PROTOCOL"), (68, "STORE_CORRUPT")])
def test_restored_header_must_match_store_protocol_and_application(
    tmp_path, interrupted_store, field, expected
):
    _, _, path, journal, _ = interrupted_store
    data = bytearray(journal.read_bytes())
    header_index = data.index(b"SQLite format 3\0")
    assert int.from_bytes(data[header_index - 4 : header_index], "big") == 1
    replacement = (999 if field == 60 else 0).to_bytes(4, "big")
    data[header_index + field : header_index + field + 4] = replacement
    # These header fields are outside SQLite's sparse checksum sample. Keep
    # the actual journal framing, isolating POST-recovery header validation.
    journal.write_bytes(data)
    # Keep the database's current header valid: only journal repair introduces
    # the unsupported header, so a failure cannot come from preflight checks.
    assert path.read_bytes()[:16] == b"SQLite format 3\0"
    safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), expected)
    assert path.read_bytes()[:16] == b"SQLite format 3\0"  # Repair actually happened.
    assert path.read_bytes()[field : field + 4] == replacement
    assert private_rows(tmp_path, "SELECT count(*) FROM generations") == [(2,)]
    safe_failure(lambda: initialize(tmp_path), "STORE_EXISTS")


@pytest.mark.parametrize("case", ["garbage", "empty", "truncated", "payload"])
def test_unrecoverable_storage_is_never_reinitialized(tmp_path, case):
    initialize(tmp_path)
    path = tmp_path / RELATIVE_PATH
    if case == "garbage":
        path.write_text(" ".join(CANARIES), encoding="utf-8")
    elif case == "empty":
        path.write_bytes(b"")
    elif case == "truncated":
        path.write_bytes(path.read_bytes()[:110])
    else:
        tamper(tmp_path, "UPDATE generations SET models=?", (CANARIES[0],))
    before = tree_bytes(tmp_path)
    safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), "STORE_CORRUPT")
    safe_failure(lambda: initialize(tmp_path), "STORE_EXISTS")
    assert tree_bytes(tmp_path) == before


def test_recovery_validates_restored_installation_binding(tmp_path, interrupted_store):
    _, _, path, journal, _ = interrupted_store
    other = tmp_path / "different-installation"
    target = other / RELATIVE_PATH
    target.parent.mkdir(parents=True, mode=0o700)
    shutil.copy2(path, target)
    shutil.copy2(journal, target.with_name(target.name + "-journal"))
    safe_failure(lambda: ConfigurationStore.recover_existing(other), "INSTALLATION_MISMATCH")
    assert private_rows(other, "SELECT count(*) FROM generations") == [(2,)]


def test_restart_persistence_in_a_fresh_python_process(tmp_path):
    first = initialize(tmp_path).read_snapshot()
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_child_read, args=(str(tmp_path), send))
    process.start()
    send.close()
    try:
        assert receive.poll(30)
        installation_id, revision, matches = receive.recv()
        assert matches and installation_id == first.installation_id and revision == first.revision
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        stop_owned_process(process)
        receive.close()


@pytest.mark.parametrize("location", ["header", "metadata"])
def test_unsupported_protocol_does_not_mutate_or_reinitialize(tmp_path, location):
    initialize(tmp_path)
    tamper(
        tmp_path,
        "PRAGMA user_version=999"
        if location == "header"
        else "UPDATE store_metadata SET protocol_version=999",
    )
    before = tree_bytes(tmp_path)
    safe_failure(lambda: ConfigurationStore.open_existing(tmp_path), "UNSUPPORTED_PROTOCOL")
    safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), "UNSUPPORTED_PROTOCOL")
    safe_failure(lambda: initialize(tmp_path), "STORE_EXISTS")
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "case",
    [
        "garbage",
        "empty",
        "truncated",
        "payload",
        "missing_current",
        "missing_table",
        "duplicate_json",
        "nonfinite_json",
    ],
)
def test_corrupt_storage_is_sanitized_without_fallback_or_repair(tmp_path, case, caplog):
    store = initialize(tmp_path)
    path = tmp_path / RELATIVE_PATH
    if case == "garbage":
        path.write_text(" ".join(CANARIES), encoding="utf-8")
    elif case == "empty":
        path.write_bytes(b"")
    elif case == "truncated":
        path.write_bytes(path.read_bytes()[:110])
    elif case == "payload":
        tamper(tmp_path, "UPDATE generations SET models=?", (CANARIES[0],))
    elif case == "missing_current":
        tamper(tmp_path, "UPDATE store_metadata SET current_revision=?", ("a" * 32,))
    elif case == "missing_table":
        tamper(tmp_path, "DROP TABLE generations")
    elif case == "duplicate_json":
        tamper(tmp_path, "UPDATE generations SET models=?", ('{"version":1,"version":1}',))
    else:
        documents, _ = native()
        tamper(
            tmp_path,
            "UPDATE generations SET models=?",
            (json.dumps(documents["models"])[:-1] + ',"extra":NaN}',),
        )
    before = tree_bytes(tmp_path)
    safe_failure(store.read_snapshot, "STORE_CORRUPT", caplog)
    safe_failure(lambda: ConfigurationStore.open_existing(tmp_path), "STORE_CORRUPT", caplog)
    assert tree_bytes(tmp_path) == before


def test_missing_after_open_is_not_recreated_by_read_update_or_recovery(tmp_path):
    store = initialize(tmp_path)
    (tmp_path / RELATIVE_PATH).unlink()
    safe_failure(store.read_snapshot, "STORE_MISSING")
    safe_failure(lambda: store.update(advance), "STORE_MISSING")
    safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), "STORE_MISSING")
    assert not (tmp_path / RELATIVE_PATH).exists()


def test_writer_requires_delete_journaling_and_extra_synchronization(tmp_path, monkeypatch):
    store = initialize(tmp_path)
    seen = []
    execute = implementation._Session.execute

    def observe(session, sql, parameters=()):
        result = execute(session, sql, parameters)
        if sql == "BEGIN IMMEDIATE":
            seen.append(
                (
                    session.connection.execute("PRAGMA journal_mode").fetchone(),
                    session.connection.execute("PRAGMA synchronous").fetchone(),
                )
            )
        return result

    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", observe)
        store.update(advance)
    assert seen == [(("delete",), (3,))]
    tamper(tmp_path, "PRAGMA journal_mode=WAL")
    before = tree_bytes(tmp_path)
    safe_failure(store.read_snapshot, "UNSUPPORTED_STORAGE")
    assert tree_bytes(tmp_path) == before


def test_zero_timeout_succeeds_without_contention(tmp_path):
    store = initialize(tmp_path, timeout=0)
    assert counters(store.update(advance)) == (1, 1, 1)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission checks; Windows ACLs are unchanged")
def test_posix_files_are_private_and_unsafe_existing_permissions_are_not_changed(tmp_path):
    store = initialize(tmp_path)
    path = tmp_path / RELATIVE_PATH
    assert path.stat().st_mode & 0o077 == 0
    assert path.parent.stat().st_mode & 0o077 == 0
    path.chmod(0o644)
    safe_failure(store.read_snapshot, "UNSAFE_STORAGE")
    assert path.stat().st_mode & 0o777 == 0o644


def test_existing_database_hard_links_are_not_followed(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    initialize(one)
    target = two / RELATIVE_PATH
    target.parent.mkdir(parents=True, mode=0o700)
    try:
        os.link(one / RELATIVE_PATH, target)
    except OSError:
        pytest.skip("Filesystem does not support test-owned hard links")
    safe_failure(lambda: ConfigurationStore.open_existing(two), "UNSAFE_STORAGE")


def test_symlinked_database_is_not_followed_or_replaced(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    initialize(one)
    target = two / RELATIVE_PATH
    target.parent.mkdir(parents=True, mode=0o700)
    try:
        target.symlink_to(one / RELATIVE_PATH)
    except OSError:
        pytest.skip("Creating symlinks is unavailable without changing OS privileges")
    safe_failure(lambda: ConfigurationStore.open_existing(two), "UNSAFE_STORAGE")
    safe_failure(lambda: initialize(two), "STORE_EXISTS")


def test_opaque_handles_and_errors_do_not_expose_secret_named_paths(tmp_path, caplog):
    root = tmp_path / CANARIES[0]
    store = initialize(root)
    assert CANARIES[0] not in repr(store)
    (root / RELATIVE_PATH).unlink()
    safe_failure(store.read_snapshot, "STORE_MISSING", caplog)


def test_pointing_at_history_is_corruption_not_automatic_credential_restoration(tmp_path):
    store = initialize(tmp_path)
    old = store.read_snapshot()

    def clear(snapshot):
        documents = snapshot.documents
        documents["search"]["profiles"][0]["brave_api_key"] = ""
        return documents, snapshot.selections

    current = store.update(clear)
    assert current.documents["search"]["profiles"][0]["brave_api_key"] == ""
    tamper(tmp_path, "UPDATE store_metadata SET current_revision=?", (old.revision,))
    before = tree_bytes(tmp_path)
    safe_failure(store.read_snapshot, "STORE_CORRUPT")
    safe_failure(lambda: ConfigurationStore.recover_existing(tmp_path), "STORE_CORRUPT")
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "backend_code,expected",
    [
        (5, "STORE_BUSY"),
        (6, "STORE_BUSY"),
        (776, "RECOVERY_REQUIRED"),
        (11, "STORE_CORRUPT"),
        (26, "STORE_CORRUPT"),
        (14, "STORE_UNAVAILABLE"),
        (13, "STORE_UNAVAILABLE"),
        (None, "STORE_UNAVAILABLE"),
    ],
)
def test_sqlite_errors_do_not_retain_backend_messages_or_context(
    tmp_path, monkeypatch, caplog, backend_code, expected
):
    store = initialize(tmp_path)
    first = store.read_snapshot()
    execute = implementation._Session.execute

    def injected(session, sql, parameters=()):
        if sql == "BEGIN IMMEDIATE":
            error = sqlite3.OperationalError(" ".join(CANARIES))
            if backend_code is not None:
                error.sqlite_errorcode = backend_code
            raise error
        return execute(session, sql, parameters)

    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", injected)
        safe_failure(lambda: store.update(advance), expected, caplog)
    assert store.read_snapshot().revision == first.revision


def test_static_error_fallback_never_echoes_arbitrary_constructor_input():
    error = ConfigurationStoreError(CANARIES[0])
    assert error.code == "STORE_UNAVAILABLE"
    assert CANARIES[0] not in str(error) + repr(error) + json.dumps(vars(error))


@pytest.mark.parametrize(
    "message,read_only,expected",
    [
        ("attempt to write a readonly database", True, "RECOVERY_REQUIRED"),
        ("attempt to write a readonly database", False, "STORE_UNAVAILABLE"),
        ("database disk image is malformed", True, "STORE_CORRUPT"),
        ("file is not a database", True, "STORE_CORRUPT"),
        ("no such table: " + CANARIES[0], True, "STORE_CORRUPT"),
        ("no such column: " + CANARIES[1], True, "STORE_CORRUPT"),
        ("database is locked", True, "STORE_BUSY"),
        ("database table is locked", False, "STORE_BUSY"),
    ],
)
def test_python_310_sqlite_error_fallback_is_static_and_operation_aware(
    tmp_path, monkeypatch, message, read_only, expected
):
    store = initialize(tmp_path)
    execute = implementation._Session.execute

    def injected(session, sql, parameters=()):
        if sql in {"BEGIN", "BEGIN IMMEDIATE"}:
            error = sqlite3.OperationalError(message)
            assert not hasattr(error, "sqlite_errorcode")
            raise error
        return execute(session, sql, parameters)

    with monkeypatch.context() as guard:
        guard.setattr(implementation._Session, "execute", injected)
        operation = store.read_snapshot if read_only else lambda: store.update(advance)
        safe_failure(operation, expected)
    assert counters(store.read_snapshot()) == (0, 0, 0)
