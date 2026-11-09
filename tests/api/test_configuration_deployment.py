"""Temporary installations and mocked macOS services; no real deployment/probes."""

from __future__ import annotations

import gc
import importlib.util
import json
import multiprocessing
import os
import sqlite3
import threading
import time
import traceback
from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from pathlib import Path

import pytest
from test_configuration_backend import KEYS, initialize

from alphalab.dataio.runtime import OperationsStore, runtime_execution_lock
from alphalab.dataio.sync import SyncJobManager, SyncRequest
from alphalab.utils import configuration_deployment as deploy
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_maintenance import (
    configuration_access,
    execution_access,
    hold_worker_execution,
)
from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.services.configuration_data_service import ConfigurationDataServices


def decision(root, store=None):
    owner = (
        ConfigurationBackend.json(root)
        if store is None
        else ConfigurationBackend.sqlite(
            root, installation_id=store.read_snapshot().installation_id
        )
    )
    owner.close()
    return owner


def offline(choice, **kwargs):
    return deploy.coordinated_offline(choice, stop_services=lambda: None, **kwargs)


def private(value):
    for key in KEYS:
        assert key not in str(value) + repr(value)


def backup_metadata(tag="manual", **changes):
    return {
        "release": "/previous-release",
        "created_at": "2026-11-09T00:00:00+00:00",
        "tag": tag,
        "old_ssh_registered": False,
        "offline": True,
        **changes,
    }


def assert_unpublished(target):
    assert not (target / "configuration-backup.json").exists(), (
        "An incomplete backup was marked complete"
    )


def assert_redacted(error, *extra):
    rendered = (
        str(error) + repr(error) + str(error.args) + "".join(traceback.format_exception(error))
    )
    private(rendered)
    for secret in extra:
        assert secret not in rendered
    assert error.__cause__ is None and error.__context__ is None


def database(path, sql, values=()):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o777 if os.name == "nt" else 0o700)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(sql, values)
        connection.commit()


def retained_worker(root, ready, release):
    hold_worker_execution(Path(root))
    # The target returns while this synthetic SDK thread remains alive. Its
    # lease must survive target return and release only on actual OS process exit.
    threading.Thread(target=lambda: release.wait(12), daemon=False).start()
    ready.send(os.getpid())
    ready.close()


def orphan_owner(root, ready, release):
    context = multiprocessing.get_context("spawn")
    initialized, sender = context.Pipe(duplex=False)
    child = context.Process(target=retained_worker, args=(root, sender, release))
    child.start()
    sender.close()
    assert initialized.poll(15)
    ready.send(initialized.recv())
    ready.close()
    initialized.close()
    os._exit(29)  # Only this test-created owner; the child exits via its release event.


@pytest.fixture
def worker(tmp_path):
    root = tmp_path / "worker-root"
    choice = decision(root)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    release = context.Event()
    process = context.Process(target=retained_worker, args=(str(root), child, release))
    process.start()
    child.close()
    try:
        assert parent.poll(15)
        assert parent.recv() == process.pid
        yield root, choice, process, release
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        process.close()
        parent.close()


def test_worker_gate_survives_target_return_and_blocks_offline_work(worker):
    _, choice, process, release = worker
    assert process.is_alive()
    started = time.monotonic()
    with pytest.raises(deploy.DeploymentPreparationError) as caught:
        with offline(choice, timeout=0.05):
            pytest.fail("Live child admitted offline")
    assert caught.value.code == "BUSY" and time.monotonic() - started < 1
    release.set()
    process.join(timeout=5)
    assert process.exitcode == 0
    with offline(choice):
        pass


def test_child_gate_survives_abrupt_owner_exit(tmp_path):
    root = tmp_path / "orphan"
    choice = decision(root)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    release = context.Event()
    process = context.Process(target=orphan_owner, args=(str(root), child, release))
    process.start()
    child.close()
    try:
        assert parent.poll(15)
        assert parent.recv() != process.pid
        process.join(timeout=5)
        assert process.exitcode == 29
        with pytest.raises(deploy.DeploymentPreparationError, match="active"):
            with offline(choice, timeout=0.05):
                pytest.fail("Orphan worker was ignored")
    finally:
        release.set()
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        process.close()
        parent.close()
    # Acquiring the exclusive gate proves the orphan's retained descriptor has
    # actually closed; there is no PID-wide kill or automatic lock-file deletion.
    with execution_access(root, exclusive=True, timeout=5):
        pass


def test_negative_control_ignoring_child_gate_admits_live_worker(worker, monkeypatch):
    _, choice, process, _ = worker
    with monkeypatch.context() as control:
        control.setattr(deploy, "execution_access", lambda *a, **kw: nullcontext())
        with offline(choice):
            with pytest.raises(AssertionError, match="Worker must exit before deployment"):
                assert not process.is_alive(), "Worker must exit before deployment"
    with pytest.raises(deploy.DeploymentPreparationError):
        with offline(choice, timeout=0):
            pytest.fail("The real gate was not restored")


@pytest.mark.parametrize("kind", ["maintenance", "sqlite-owner", "legacy-child"])
def test_existing_owners_and_legacy_locks_cannot_be_bypassed(tmp_path, kind):
    root = tmp_path / "root"
    choice = decision(root)
    store = initialize(root) if kind == "sqlite-owner" else None
    lease = (
        ConfigurationBackend.sqlite(root, installation_id=store.read_snapshot().installation_id)
        if store
        else configuration_access(root)
        if kind == "maintenance"
        else runtime_execution_lock(root, "captured-rq-execution")
    )
    with lease:
        with pytest.raises(deploy.DeploymentPreparationError):
            with offline(choice, timeout=0.02):
                pytest.fail("An existing lease was bypassed")
    assert (root / "app/secrets/configuration-maintenance.lock").exists()


def test_gate_blocks_new_job_admission_without_persisting_work(tmp_path):
    root = tmp_path / "root"
    manager = SyncJobManager(root, job_runner=lambda *_: pytest.fail("Job was admitted"))
    try:
        with execution_access(root, exclusive=True):
            for action in (manager.submit, manager.run_now):
                with pytest.raises(ConfigurationStoreError):
                    action(SyncRequest(datasets=["instruments"]))
            with pytest.raises(ConfigurationStoreError):
                manager.submit_recipe("invalid source", project_id="test")
        assert manager.operations.list_jobs() == []
    finally:
        manager.shutdown()


@pytest.mark.parametrize("status", ["queued", "running", "pending", "interrupted", "unknown"])
@pytest.mark.parametrize("kind", ["rq", "agent", "backtest"])
def test_unfinished_jobs_block_before_services_stop(tmp_path, status, kind):
    root = tmp_path / "root"
    choice = decision(root)
    if kind == "rq":
        operations = OperationsStore(root)
        identifier = operations.create_job({"source": "rq"})
        operations.update_job(identifier, status=status)
        gc.collect()
    elif kind == "backtest":
        database(root / "app/alphalab.db", "CREATE TABLE backtest_jobs(status TEXT)")
        database(root / "app/alphalab.db", "INSERT INTO backtest_jobs VALUES (?)", (status,))
    else:
        state = root / "conexus/project/state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"runs": [{"status": status}]}))
    stopped = []
    with pytest.raises(deploy.DeploymentPreparationError) as caught:
        with deploy.coordinated_offline(choice, stop_services=lambda: stopped.append(True)):
            pytest.fail("An unfinished job was ignored")
    assert caught.value.code == "BUSY" and not stopped


def test_bounded_drain_retains_owner_until_pending_work_exits(tmp_path):
    root = tmp_path / "root"
    store = initialize(root)
    release = threading.Event()
    with ConfigurationBackend.sqlite(
        root, installation_id=store.read_snapshot().installation_id
    ) as owner:
        data = ConfigurationDataServices(owner)
        future = data.manager._executor.submit(lambda: release.wait(5))
        with data.manager._lock:
            data.manager._futures["synthetic-pending"] = future
        try:
            started = time.monotonic()
            with pytest.raises(ConfigurationStoreError) as caught:
                data.close(timeout_seconds=0.01)
            assert caught.value.code == "STORE_BUSY" and time.monotonic() - started < 1
            with pytest.raises(ConfigurationStoreError):
                owner.close()
            with pytest.raises(ConfigurationStoreError):
                data.manager.submit(SyncRequest(datasets=["instruments"]))
        finally:
            release.set()
            future.result(timeout=5)
            data.close()
        data.close()


@pytest.mark.parametrize("staged", [False, True])
def test_json_pending_profile_writer_preserves_backend_and_requires_revalidation(tmp_path, staged):
    root = tmp_path / "root"
    store = initialize(root) if staged else None
    revision = store.read_snapshot().revision if store else None
    choice = decision(root)
    with offline(choice) as token:
        result = deploy.write_pending_model(
            token,
            {
                "id": "new",
                "name": "New",
                "model": "provider/model",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": KEYS[0],
                "activate": True,
            },
        )
        private(result)
        assert result["staged_revalidation_required"] is staged
    saved = json.loads((root / "conexus/secrets/model-providers.json").read_text())
    assert saved["active_id"] == "new" and saved["profiles"][0]["api_key"] == KEYS[0]
    if store:
        assert store.read_snapshot().revision == revision


def test_sqlite_decision_rejects_legacy_writer_without_mutation(tmp_path):
    root = tmp_path / "root"
    store = initialize(root)
    original = store.read_snapshot().revision
    with offline(decision(root, store)) as token:
        with pytest.raises(deploy.DeploymentPreparationError) as caught:
            deploy.write_pending_model(token, {"api_key": KEYS[0]})
        assert caught.value.code == "BACKEND_MISMATCH"
    assert not (root / "conexus/secrets/model-providers.json").exists()
    assert store.read_snapshot().revision == original


def test_closed_decision_and_thread_bound_offline_capability_are_required(tmp_path):
    root = tmp_path / "root"
    with ConfigurationBackend.json(root) as live:
        with pytest.raises(deploy.DeploymentPreparationError):
            with offline(live):
                pytest.fail("Live owner was closed implicitly")
    with pytest.raises(deploy.DeploymentPreparationError):
        with offline("json"):
            pytest.fail("A string was treated as backend authority")
    with offline(live) as token:
        for field in ("root", "backend", "installation_id"):
            with pytest.raises(AttributeError):
                setattr(token, field, "cannot-rebind")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(deploy.DeploymentPreparationError):
                pool.submit(token.require_json_writer).result()
    with pytest.raises(deploy.DeploymentPreparationError):
        token.require_json_writer()


def test_failed_stop_releases_fences_and_sanitizes_callback_errors(tmp_path):
    root = tmp_path / "root"

    def fail():
        raise RuntimeError(KEYS[2])

    with pytest.raises(deploy.DeploymentPreparationError) as caught:
        with deploy.coordinated_offline(decision(root), stop_services=fail):
            pytest.fail("Failed stop admitted maintenance")
    private("".join(traceback.format_exception(caught.value)))
    assert caught.value.__context__ is None
    with offline(decision(root)):
        pass


def test_late_queued_job_is_detected_again_after_shutdown(tmp_path):
    root = tmp_path / "root"
    choice = decision(root)

    def late_job():
        OperationsStore(root).create_job({"source": "rq"})

    with pytest.raises(deploy.DeploymentPreparationError) as caught:
        with deploy.coordinated_offline(choice, stop_services=late_job):
            pytest.fail("A late job was ignored")
    assert caught.value.code == "BUSY"


def test_backup_contains_all_three_documents_history_and_bound_identity(tmp_path):
    root = tmp_path / "root"
    store = initialize(root)
    store.update(lambda snap: (snap.documents, snap.selections))
    current = store.read_snapshot()
    target = tmp_path / "backup"
    with offline(decision(root, store)) as token:
        result = deploy.create_backup(token, target)
        assert result["configuration"]["revision"] == current.revision
        assert result["restorable"] is False
        private(result)
        private((target / "configuration-backup.json").read_text())
        with closing(
            sqlite3.connect(target / "runtime/app/secrets/configuration-store.sqlite3")
        ) as connection:
            rows = connection.execute(
                "SELECT models,search,rqdata,selections FROM generations ORDER BY ordinal"
            ).fetchall()
            assert len(rows) == 2
            assert (
                dict(zip(("models", "search", "rqdata"), map(json.loads, rows[-1][:3])))
                == current.documents
            )
            assert json.loads(rows[-1][3]) == current.selections
        assert deploy.verify_backup(token, target) == result
        assert list(target.rglob("*.json")) == [target / "configuration-backup.json"]


def test_nested_wal_application_database_uses_sqlite_backup(tmp_path):
    root = tmp_path / "root"
    choice = decision(root)
    path = root / "nested/state/cache.sqlite3"
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE fixture(value TEXT)")
        live.execute("INSERT INTO fixture VALUES ('committed-in-wal')")
        live.commit()
        assert path.with_name(path.name + "-wal").stat().st_size > 0
        with offline(choice) as token:
            target = tmp_path / "backup"
            deploy.create_backup(token, target)
            saved = target / "runtime/nested/state/cache.sqlite3"
            with closing(sqlite3.connect(saved)) as copy:
                assert copy.execute("SELECT value FROM fixture").fetchone() == ("committed-in-wal",)
                assert copy.execute("PRAGMA journal_mode").fetchone() == ("delete",)
            assert not list(target.rglob("*-wal")) and not list(target.rglob("*-shm"))


@pytest.mark.parametrize("failure", ["corrupt", "missing-manifest", "wrong-root", "extra-file"])
def test_backup_validation_rejects_invalid_or_ambiguous_snapshots(tmp_path, failure):
    root = tmp_path / "root"
    store = initialize(root)
    target = tmp_path / "backup"
    with offline(decision(root, store)) as token:
        deploy.create_backup(token, target)
        if failure == "corrupt":
            (target / "runtime/app/secrets/configuration-store.sqlite3").write_bytes(b"corrupt")
        elif failure == "missing-manifest":
            (target / "configuration-backup.json").rename(target / "incomplete.json")
        elif failure == "extra-file":
            (target / "unexpected").write_text("extra")
        if failure != "wrong-root":
            with pytest.raises(deploy.DeploymentPreparationError):
                deploy.verify_backup(token, target)
    if failure == "wrong-root":
        with offline(decision(tmp_path / "other")) as other:
            with pytest.raises(deploy.DeploymentPreparationError):
                deploy.verify_backup(other, target)


@pytest.mark.parametrize("exception", [RuntimeError, KeyboardInterrupt, SystemExit, CancelledError])
def test_interrupted_backup_has_no_complete_manifest_and_redacts_exceptions(
    tmp_path, monkeypatch, exception
):
    root = tmp_path / "root"
    store = initialize(root)
    target = tmp_path / "incomplete"

    def fail(*args, **kwargs):
        raise exception(KEYS[0])

    monkeypatch.setattr(deploy, "_sqlite_backup", fail)
    with offline(decision(root, store)) as token:
        with pytest.raises(
            deploy.DeploymentPreparationError if exception is RuntimeError else exception
        ) as caught:
            deploy.create_backup(token, target)
        private(caught.value)
        private("".join(traceback.format_exception(caught.value)))
        assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert not (target / "configuration-backup.json").exists()
    assert store.read_snapshot().documents["rqdata"]["profiles"][0]["password"] == KEYS[2]


@pytest.mark.parametrize("kind", ["-journal", "-wal", "-shm", "hot-journal", "corrupt-database"])
def test_interrupted_or_corrupt_storage_is_never_recovered_implicitly(tmp_path, kind):
    root = tmp_path / "root"
    choice = decision(root)
    path = root / "fixture.db"
    if kind in {"hot-journal", "corrupt-database"}:
        database(path, "CREATE TABLE fixture(value TEXT)")
    if kind == "corrupt-database":
        path.write_bytes(b"corrupt")
    else:
        path.with_name(path.name + ("-journal" if kind == "hot-journal" else kind)).write_bytes(
            b"interrupted"
        )
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    with offline(choice) as token:
        with pytest.raises(deploy.DeploymentPreparationError):
            deploy.create_backup(token, tmp_path / "incomplete")
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == before
    assert not (tmp_path / "incomplete/configuration-backup.json").exists()


def test_backup_does_not_adopt_wal_as_a_new_configuration_protocol(tmp_path):
    root = tmp_path / "root"
    store = initialize(root)
    choice = decision(root)
    path = root / "app/secrets/configuration-store.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    original = path.read_bytes()
    with offline(choice) as token:
        with pytest.raises(deploy.DeploymentPreparationError):
            deploy.create_backup(token, tmp_path / "unsupported")
    assert path.read_bytes() == original
    with pytest.raises(ConfigurationStoreError) as caught:
        store.read_snapshot()
    assert caught.value.code == "UNSUPPORTED_STORAGE"


def test_changed_surrounding_runtime_prevents_backup_publication(tmp_path, monkeypatch):
    root = tmp_path / "root"
    choice = decision(root)
    (root / "research.txt").write_text("before")
    original = deploy._copy_file

    def change(source, target):
        original(source, target)
        source.write_text("changed after copying")  # Deliberate non-cooperative test writer.

    monkeypatch.setattr(deploy, "_copy_file", change)
    with offline(choice) as token:
        with pytest.raises(deploy.DeploymentPreparationError) as caught:
            deploy.create_backup(token, tmp_path / "failed")
        assert caught.value.code == "SOURCE_CHANGED"
    assert not (tmp_path / "failed/configuration-backup.json").exists()


def test_negative_control_generation_change_is_detected_beyond_integrity_check(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    store = initialize(root)
    copy = deploy._sqlite_backup
    inventory = deploy._inventory
    baseline = inventory(root)

    def raced(source, target, deadline):
        copy(source, target, deadline)
        store.update(lambda snap: (snap.documents, snap.selections))

    with offline(decision(root, store)) as token:
        with monkeypatch.context() as control:
            control.setattr(deploy, "_sqlite_backup", raced)
            with pytest.raises(deploy.DeploymentPreparationError) as caught:
                deploy.create_backup(token, tmp_path / "detected")
            assert caught.value.code == "SOURCE_CHANGED"
        baseline = inventory(root)
        with monkeypatch.context() as control:
            control.setattr(deploy, "_sqlite_backup", raced)
            control.setattr(deploy, "_inventory", lambda p: baseline if p == root else inventory(p))
            report = deploy.create_backup(token, tmp_path / "negative-control")
            with pytest.raises(AssertionError, match="Current generation was lost"):
                assert report["configuration"]["revision"] == store.read_snapshot().revision, (
                    "Current generation was lost"
                )


def test_restore_guard_prevents_historical_password_resurrection(tmp_path, monkeypatch):
    root = tmp_path / "root"
    store = initialize(root)
    with offline(decision(root, store)) as token:
        target = tmp_path / "backup"
        deploy.create_backup(token, target)

    def clear(snapshot):
        docs = snapshot.documents
        for service, field in (
            ("models", "api_key"),
            ("search", "brave_api_key"),
            ("rqdata", "password"),
        ):
            docs[service]["profiles"][0][field] = ""
        return docs, snapshot.selections

    store.update(clear)
    current = store.read_snapshot()
    path = root / "app/secrets/configuration-store.sqlite3"
    current_bytes = path.read_bytes()
    with pytest.raises(deploy.DeploymentPreparationError) as caught:
        deploy.reject_restore(target)
    assert caught.value.code == "RESTORE_REQUIRES_REVIEW"
    assert path.read_bytes() == current_bytes

    # Negative control: emulate only the old byte-copy restore in this private
    # temporary fixture. Preserve and restore the newer bytes in a finally block.
    def old_restore(*args):
        path.write_bytes((target / "runtime/app/secrets/configuration-store.sqlite3").read_bytes())

    try:
        with monkeypatch.context() as control:
            control.setattr(deploy, "reject_restore", old_restore)
            deploy.reject_restore(target)
            with pytest.raises(AssertionError, match="Revoked credential returned"):
                for service, field in (
                    ("models", "api_key"),
                    ("search", "brave_api_key"),
                    ("rqdata", "password"),
                ):
                    assert not store.read_snapshot().documents[service]["profiles"][0][field], (
                        "Revoked credential returned"
                    )
    finally:
        path.write_bytes(current_bytes)
    with pytest.raises(deploy.DeploymentPreparationError):
        deploy.reject_restore(target)
    assert store.read_snapshot().revision == current.revision


@pytest.fixture
def mac_manager(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "examples/deployment/macos-local/dev3_manager.py"
    spec = importlib.util.spec_from_file_location("test_mac_configuration_manager", path)
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    for name in ("RUNTIME", "CONFIG", "STATE", "PLISTS", "RELEASES"):
        target = tmp_path / name.lower()
        target.mkdir()
        monkeypatch.setattr(manager, name, target)
    monkeypatch.setattr(manager, "CURRENT", tmp_path / "current")
    events = []
    monkeypatch.setattr(manager, "stop", lambda name: events.append(("stop", name)))

    def start(name):
        with ConfigurationBackend.json(manager.RUNTIME):
            pass  # Restart must occur after releasing offline maintenance.
        events.append(("start", name))

    monkeypatch.setattr(manager, "start", start)
    monkeypatch.setattr(manager, "running", lambda name: False)
    return manager, events


def test_json_manager_snapshot_is_offline_and_recursive(mac_manager, capsys):
    manager, events = mac_manager
    initialize(manager.RUNTIME)  # Inactive staged store, not an activation decision.
    backup = manager.snapshot()
    assert (backup / "runtime/app/secrets/configuration-store.sqlite3").exists()
    assert json.loads((backup / "snapshot.json").read_text())["offline"] is True
    with offline(decision(manager.RUNTIME)) as token:
        assert deploy.verify_backup(token, backup)["verified"]
    assert events == [("stop", label) for label in (manager.WEB, manager.PRIVATE, manager.OLD_SSH)]
    private(capsys.readouterr().out)


def test_manager_restore_rejects_before_stopping_or_renaming(mac_manager):
    manager, events = mac_manager
    marker = manager.RUNTIME / "newer-state"
    marker.write_text("keep")
    with pytest.raises(deploy.DeploymentPreparationError):
        manager.restore(manager.STATE / "missing-backup")
    assert marker.read_text() == "keep" and events == []


@pytest.mark.parametrize("fail_health", [False, True])
def test_json_deployment_pending_write_and_failed_health_never_restore(
    mac_manager, monkeypatch, fail_health
):
    manager, events = mac_manager
    commit = "f" * 40
    release = manager.RELEASES / commit
    release.mkdir()
    (release / ".mac-local-build-validated.json").write_text(json.dumps({"commit_sha": commit}))
    monkeypatch.setattr(manager.subprocess, "check_output", lambda *a, **kw: commit)
    monkeypatch.setattr(manager, "launch", lambda *a, **kw: None)
    monkeypatch.setattr(manager, "values", lambda: {"ALPHALAB_AGENT_MODE": "local"})
    monkeypatch.setattr(manager, "write_runner", lambda: None)
    monkeypatch.setattr(manager, "switch", lambda value: events.append(("switch", value)))
    monkeypatch.setattr(manager, "api", lambda *a: {"available": True, "mode": "local_harness"})

    def health(*args):
        if fail_health:
            raise RuntimeError(KEYS[0])

    monkeypatch.setattr(manager, "health", health)
    pending = manager.CONFIG / "model-profile.pending.json"
    pending.write_text(
        json.dumps(
            {
                "id": "model",
                "name": "Model",
                "model": "provider/model",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": KEYS[0],
            }
        )
    )
    if fail_health:
        with pytest.raises(deploy.DeploymentPreparationError) as caught:
            manager.deploy("fixture")
        private(caught.value)
        assert pending.exists()
        assert len([event for event in events if event[0] == "switch"]) == 1
    else:
        manager.deploy("fixture")
        assert not pending.exists()
    saved = json.loads((manager.RUNTIME / "conexus/secrets/model-providers.json").read_text())
    assert saved["profiles"][0]["api_key"] == KEYS[0]
    assert list((manager.STATE / "backups").glob("*/configuration-backup.json"))


@pytest.mark.parametrize("phase", ["initial", "final", "both"])
@pytest.mark.parametrize("location", ["root", "nested"])
def test_inventory_enumeration_errors_fail_closed(tmp_path, monkeypatch, phase, location):
    root = tmp_path / "root"
    store = initialize(root)
    nested = root / "required"
    database(nested / "critical.sqlite3", "CREATE TABLE required_state(value TEXT)")
    original = os.scandir
    passes = 0
    denied = root if location == "root" else nested

    def scandir(path):
        nonlocal passes
        if Path(path) == root:
            passes += 1
        if Path(path) == denied and (phase == "both" or passes == (1 if phase == "initial" else 2)):
            raise PermissionError(KEYS[0])
        return original(path)

    target = tmp_path / "rejected"
    with offline(decision(root, store)) as token:
        with monkeypatch.context() as fault:
            fault.setattr(os, "scandir", scandir)
            with pytest.raises(deploy.DeploymentPreparationError) as caught:
                deploy.create_backup(token, target)
        # SOURCE_CHANGED would indicate a swallowed partial traversal, not propagation.
        assert caught.value.code == "UNAVAILABLE"
        assert_redacted(caught.value)
        assert_unpublished(target)
        with pytest.raises(deploy.DeploymentPreparationError):
            deploy.verify_backup(token, target)
        control = tmp_path / "readable-control"
        report = deploy.create_backup(token, control)
        assert (control / "runtime/required/critical.sqlite3").is_file()
        assert deploy.verify_backup(token, control) == report


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_missing_or_non_directory_required_source_is_rejected(tmp_path, kind):
    root = tmp_path / "root"
    choice = decision(root)
    config = tmp_path / "required-config"
    if kind == "file":
        config.write_text("not a directory")
    target = tmp_path / "not-created"
    with offline(choice) as token:
        with pytest.raises(deploy.DeploymentPreparationError):
            deploy.create_backup(token, target, config=config)
    assert not target.exists()


def test_negative_control_silently_skipped_subtree_is_detected(tmp_path, monkeypatch):
    root = tmp_path / "root"
    store = initialize(root)
    nested = root / "required"
    database(nested / "critical.sqlite3", "CREATE TABLE required_state(value TEXT)")
    original_walk, original_scandir = os.walk, os.scandir

    def ignore_errors(*args, **kwargs):
        kwargs.pop("onerror", None)  # Restore only the original vulnerable walk behavior.
        return original_walk(*args, **kwargs)

    def unreadable(path):
        if Path(path) == nested:
            raise PermissionError("Synthetic enumeration failure")
        return original_scandir(path)

    with offline(decision(root, store)) as token:
        target = tmp_path / "negative-control"
        with monkeypatch.context() as control:
            control.setattr(os, "walk", ignore_errors)
            control.setattr(os, "scandir", unreadable)
            assert deploy.create_backup(token, target)["verified"]
            assert not (target / "runtime/required/critical.sqlite3").exists()
            with pytest.raises(AssertionError, match="incomplete backup was marked complete"):
                assert_unpublished(target)
        rejected = tmp_path / "corrected"
        with monkeypatch.context() as fault:
            fault.setattr(os, "scandir", unreadable)
            with pytest.raises(deploy.DeploymentPreparationError):
                deploy.create_backup(token, rejected)
        assert_unpublished(rejected)


@pytest.mark.parametrize("failure", ["corrupt-copy", "late-error", "cancel"])
def test_candidate_verification_failure_never_publishes_completion(tmp_path, monkeypatch, failure):
    root = tmp_path / "root"
    store = initialize(root)
    original = deploy._verify_candidate
    target = tmp_path / "candidate"

    def verify(token, destination, candidate):
        assert "complete" not in candidate
        assert_unpublished(destination)
        if failure == "corrupt-copy":
            (destination / "runtime/app/secrets/configuration-store.sqlite3").write_bytes(
                b"corrupt"
            )
        result = original(token, destination, candidate)
        if failure == "late-error":
            raise RuntimeError(KEYS[1])
        if failure == "cancel":
            raise CancelledError(KEYS[2])
        return result

    monkeypatch.setattr(deploy, "_verify_candidate", verify)
    with offline(decision(root, store)) as token:
        with pytest.raises(
            CancelledError if failure == "cancel" else deploy.DeploymentPreparationError
        ) as caught:
            deploy.create_backup(token, target)
        assert_redacted(caught.value)
        assert_unpublished(target)
        with pytest.raises(deploy.DeploymentPreparationError):
            deploy.verify_backup(token, target)


def test_interruption_before_atomic_publication_leaves_only_incomplete_artifacts(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    store = initialize(root)
    target = tmp_path / "interrupted-publication"
    attempted = []

    def interrupt(source, destination):
        assert Path(source).is_file() and Path(source).suffix == ".pending"
        assert Path(destination).name == "configuration-backup.json"
        assert_unpublished(target)
        attempted.append(True)
        raise KeyboardInterrupt(KEYS[2])

    monkeypatch.setattr(os, "replace", interrupt)
    with offline(decision(root, store)) as token:
        with pytest.raises(KeyboardInterrupt) as caught:
            deploy.create_backup(token, target)
        assert attempted
        assert_redacted(caught.value)
        assert_unpublished(target)
        with pytest.raises(deploy.DeploymentPreparationError):
            deploy.verify_backup(token, target)


def test_successful_candidate_is_verified_before_atomic_publication(tmp_path, monkeypatch):
    root = tmp_path / "root"
    store = initialize(root)
    target = tmp_path / "published"
    verify, publish = deploy._verify_candidate, deploy._publish_complete_manifest
    events = []

    def checked(token, destination, candidate):
        assert "complete" not in candidate
        assert_unpublished(destination)
        with pytest.raises(deploy.DeploymentPreparationError):
            deploy.verify_backup(token, destination)
        result = verify(token, destination, candidate)
        events.append("verified")
        return result

    def published(destination, candidate):
        assert events == ["verified"]
        publish(destination, candidate)
        events.append("published")

    monkeypatch.setattr(deploy, "_verify_candidate", checked)
    monkeypatch.setattr(deploy, "_publish_complete_manifest", published)
    with offline(decision(root, store)) as token:
        report = deploy.create_backup(token, target)
        assert events == ["verified", "published"]
        assert deploy.verify_backup(token, target) == report
        assert not list(target.glob("*.pending"))


def test_negative_control_early_publication_exposes_failed_backup(tmp_path, monkeypatch):
    root = tmp_path / "root"
    store = initialize(root)
    original = deploy._verify_candidate

    def corrupt(destination):
        (destination / "runtime/app/secrets/configuration-store.sqlite3").write_bytes(b"corrupt")

    def early(token, destination, candidate):
        corrupt(destination)
        deploy._publish_complete_manifest(destination, candidate)  # Original wrong order.
        return deploy.verify_backup(token, destination)

    def corrected(token, destination, candidate):
        corrupt(destination)
        return original(token, destination, candidate)

    with offline(decision(root, store)) as token:
        exposed = tmp_path / "negative-control"
        with monkeypatch.context() as control:
            control.setattr(deploy, "_verify_candidate", early)
            with pytest.raises(deploy.DeploymentPreparationError) as caught:
                deploy.create_backup(token, exposed)
            assert caught.value.code == "INVALID_BACKUP"
            with pytest.raises(AssertionError, match="incomplete backup was marked complete"):
                assert_unpublished(exposed)
        target = tmp_path / "corrected"
        with monkeypatch.context() as fault:
            fault.setattr(deploy, "_verify_candidate", corrected)
            with pytest.raises(deploy.DeploymentPreparationError):
                deploy.create_backup(token, target)
        assert_unpublished(target)


@pytest.mark.parametrize(
    "service,field", [("models", "tag"), ("search", "release"), ("rqdata", "created_at")]
)
@pytest.mark.parametrize("encoding", ["plain", "percent", "nested-percent"])
def test_known_credentials_in_supplied_metadata_leave_no_artifacts(
    tmp_path, capsys, service, field, encoding
):
    root = tmp_path / "root"
    store = initialize(root)
    canary = dict(zip(("models", "search", "rqdata"), KEYS))[service]
    value = canary
    for _ in range({"plain": 0, "percent": 1, "nested-percent": 2}[encoding]):
        value = "".join(f"%{ord(char):02X}" for char in value)
    metadata = backup_metadata(**{field: value})
    target = tmp_path / "not-created"
    with offline(decision(root, store)) as token:
        with pytest.raises(deploy.DeploymentPreparationError) as caught:
            deploy.create_backup(token, target, metadata=metadata)
        assert caught.value.code == "INVALID_METADATA"
        assert_redacted(caught.value, value)
    assert not target.exists()
    output = capsys.readouterr()
    private(output.out + output.err)
    assert value not in output.out + output.err


@pytest.mark.parametrize(
    "source",
    ["inactive", "history", "legacy-model", "legacy-search-v1", "legacy-rqdata", "saved-env"],
)
def test_metadata_checks_all_explicit_saved_credential_sources(tmp_path, source):
    root = tmp_path / "root"
    store = initialize(root)
    canary = "synthetic-deployment-" + source + "-credential"
    config = None
    if source in {"inactive", "history"}:

        def add(snapshot):
            docs = snapshot.documents
            profile = docs["models"]["profiles"][0].copy()
            profile.update(id="inactive", name="Inactive", api_key="  " + canary + "  ")
            docs["models"]["profiles"].append(profile)
            return docs, snapshot.selections

        store.update(add)
        if source == "history":

            def remove(snapshot):
                docs = snapshot.documents
                docs["models"]["profiles"] = docs["models"]["profiles"][:1]
                return docs, snapshot.selections

            store.update(remove)
    elif source == "saved-env":
        config = tmp_path / "config"
        config.mkdir()
        (config / "alphalab.env").write_text("RQ_PASSWORD=" + json.dumps(canary))
    else:
        service = {
            "legacy-model": "models",
            "legacy-search-v1": "search",
            "legacy-rqdata": "rqdata",
        }[source]
        relative, secret = deploy._PROFILE_CREDENTIALS[service]
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        value = (
            {"version": 1, "brave_api_key": canary}
            if service == "search"
            else {"version": 1, "profiles": [{secret: canary}]}
        )
        path.write_text(json.dumps(value))
    with offline(decision(root, store)) as token:
        with pytest.raises(deploy.DeploymentPreparationError) as caught:
            deploy.create_backup(
                token,
                tmp_path / "rejected",
                config=config,
                metadata=backup_metadata(release="/release/" + canary),
            )
        assert caught.value.code == "INVALID_METADATA"
        assert_redacted(caught.value, canary)
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("tag", ["manual", "before-rollback", "before-local-0123abcd"])
def test_supported_tags_keep_public_backup_contract(tmp_path, tag):
    root = tmp_path / "root"
    store = initialize(root)
    metadata = backup_metadata(tag)
    target = tmp_path / "valid"
    with offline(decision(root, store)) as token:
        report = deploy.create_backup(token, target, metadata=metadata)
        assert json.loads((target / "snapshot.json").read_text()) == metadata
        assert set(report) == {"verified", "file_count", "backend", "configuration", "restorable"}
        assert report["verified"] is True and report["restorable"] is False
        assert deploy.verify_backup(token, target) == report
        private((target / "snapshot.json").read_text())


@pytest.mark.parametrize(
    "tag",
    [
        "custom-tag",
        "../escape",
        "before-local-not-a-sha",
        "before-local-012345678",
        "before-local-ABCDEF12",
        "",
    ],
)
def test_arbitrary_tags_are_rejected_before_backup_creation(tmp_path, tag):
    root = tmp_path / "root"
    with offline(decision(root)) as token:
        with pytest.raises(deploy.DeploymentPreparationError) as caught:
            deploy.create_backup(token, tmp_path / "rejected", metadata=backup_metadata(tag))
        assert caught.value.code == "INVALID_METADATA"
    assert not (tmp_path / "rejected").exists()


def test_metadata_is_detached_before_copy_callbacks_can_change_it(tmp_path, monkeypatch):
    root = tmp_path / "root"
    store = initialize(root)
    metadata = backup_metadata()
    original = deploy._sqlite_backup

    def change(source, target, deadline):
        original(source, target, deadline)
        metadata["release"] = KEYS[1]

    monkeypatch.setattr(deploy, "_sqlite_backup", change)
    with offline(decision(root, store)) as token:
        target = tmp_path / "detached"
        report = deploy.create_backup(token, target, metadata=metadata)
        assert metadata["release"] == KEYS[1]
        assert json.loads((target / "snapshot.json").read_text())["release"] == "/previous-release"
        assert deploy.verify_backup(token, target) == report


def test_negative_control_metadata_filter_detects_secret_in_a_valid_tag(tmp_path, monkeypatch):
    root = tmp_path / "root"
    store = initialize(root)
    canary = "before-local-deadbeef"  # A valid tag: tag syntax cannot mask this regression.

    def save(snapshot):
        docs = snapshot.documents
        docs["models"]["profiles"][0]["api_key"] = canary
        return docs, snapshot.selections

    store.update(save)
    original = deploy._reject_metadata_credentials
    with offline(decision(root, store)) as token:
        target = tmp_path / "negative-control"
        with monkeypatch.context() as control:
            control.setattr(deploy, "_reject_metadata_credentials", lambda *_: None)
            assert deploy.create_backup(token, target, metadata=backup_metadata(canary))["verified"]
            with pytest.raises(AssertionError, match="Credential-bearing metadata was persisted"):
                assert canary not in (target / "snapshot.json").read_text(), (
                    "Credential-bearing metadata was persisted"
                )
        assert deploy._reject_metadata_credentials is original
        rejected = tmp_path / "corrected"
        with pytest.raises(deploy.DeploymentPreparationError) as caught:
            deploy.create_backup(token, rejected, metadata=backup_metadata(canary))
        assert caught.value.code == "INVALID_METADATA"
        assert_redacted(caught.value, canary)
        assert not rejected.exists()
