"""Disposable recovery installations and canaries; no real settings/providers."""

from __future__ import annotations

import json
import multiprocessing
import os
import pickle
import shutil
import socket
import subprocess
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import pytest

from alphalab.utils import configuration_decision as authority
from alphalab.utils import configuration_deployment as deployment
from alphalab.utils import configuration_store as storage
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_maintenance import configuration_access
from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.services import configuration_recovery_service as recovery
from apps.api.services.configuration_profile_service import ConfigurationProfileServices
from tests.api import test_configuration_activation as activation_tests
from tests.api.test_configuration_migration_service import CANARIES
from tests.test_configuration_store import _child_interrupted, stop_owned_process

activate = activation_tests.activate
candidate = activation_tests.candidate
node = activation_tests.node


@pytest.fixture(autouse=True)
def no_external_python_network(monkeypatch):
    original = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError("External network is forbidden in recovery tests")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)


@pytest.fixture
def committed(candidate, node):
    record = activate(candidate, node)
    return candidate[0], candidate[1], record


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def private(value):
    rendered = str(value) + repr(value)
    if isinstance(value, BaseException):
        rendered += "".join(traceback.format_exception(value)) + repr(pickle.dumps(value))
        assert value.__cause__ is None and value.__context__ is None
    for secret in CANARIES:
        assert secret not in rendered


def recover(root, store, record, node, **changes):
    options = dict(
        installation_id=record["installation_id"],
        decision_id=record["decision_id"],
        current_revision=store.read_snapshot().revision,
        node_executable=node,
        acknowledge_offline=True,
        acknowledge_current_revision=True,
    )
    options.update(changes)
    return recovery.recover_configuration_journal(root, **options)


@pytest.mark.parametrize(
    "state",
    [
        "legacy",
        "prepared",
        "committed",
        "uncertain",
        "empty",
        "activation-pending",
        "prepared-pending",
        "missing-authority",
        "backup",
        "recovery-pending",
    ],
)
def test_recovery_classification_is_nonmutating(candidate, node, tmp_path, state):
    root, store, _, prepared = candidate
    directory = root / authority.NAMESPACE
    if state in {"committed", "uncertain", "missing-authority"}:
        activate(candidate, node)
    if state == "legacy":
        directory.rename(tmp_path / "held-authority")
        store._path.rename(tmp_path / "held-database")
    elif state == "empty":
        (directory / "decision.json").rename(tmp_path / "held-record")
    elif state in {"activation-pending", "prepared-pending"}:
        value = prepared.as_dict()
        if state == "activation-pending":
            value["state"] = "committed"
        pending = directory / "decision.pending"
        pending.write_text(json.dumps(value))
        if os.name != "nt":
            pending.chmod(0o600)
    elif state == "missing-authority":
        with ConfigurationBackend.production(root):
            pass
        directory.rename(tmp_path / "held-authority")
    elif state == "backup":
        (root / authority.BACKUP_FENCE).write_text("deny-only witness")
    elif state == "recovery-pending":
        (root / authority.RECOVERY_NAMESPACE).mkdir(mode=0o777 if os.name == "nt" else 0o700)
    before = tree(root)
    result = recovery.inspect_configuration_recovery(
        root, acknowledgement_uncertain=state == "uncertain"
    )
    expected = {
        "legacy": "legacy_json",
        "prepared": "prepared_candidate",
        "committed": "committed_sqlite",
        "uncertain": "activation_acknowledgement_uncertain",
        "empty": "authority_incomplete",
        "activation-pending": "activation_publication_interrupted",
        "prepared-pending": "prepared_publication_interrupted",
        "missing-authority": "authority_missing",
        "backup": "historical_backup",
        "recovery-pending": "recovery_interrupted",
    }[state]
    assert result["state"] == expected, result
    assert tree(root) == before
    private(result)
    if state in {"missing-authority", "backup", "recovery-pending", "activation-pending", "empty"}:
        with pytest.raises(ConfigurationStoreError):
            ConfigurationBackend.production(root)


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
@pytest.mark.parametrize("destination", ["models", "search", "rqdata"])
@pytest.mark.parametrize("encoded", [False, True])
def test_historical_canaries_cannot_reenter_metadata_after_rotation(
    committed, service, destination, encoded
):
    root, raw, _ = committed
    profiles = ConfigurationProfileServices(raw)
    documents = profiles.read_snapshot().documents.extract_private()
    key = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}[service]
    old = documents[service]["profiles"][0][key]
    changed = documents[service]["profiles"][0].copy()
    changed[key] = "replacement-credential-" + service
    getattr(profiles, service).save_profile(changed, activate=False)
    before = raw.read_snapshot()
    profile = (
        profiles.read_snapshot().documents.extract_private()[destination]["profiles"][0].copy()
    )
    profile["name"] = quote(quote(old, safe=""), safe="") if encoded else old
    # Encode every byte; urllib.quote leaves hyphens/letters unescaped.
    if encoded:
        profile["name"] = "".join("%" + format(ord(c), "02x") for c in old)
    with pytest.raises(ConfigurationStoreError) as caught:
        getattr(profiles, destination).save_profile(profile, activate=False)
    private(caught.value)
    after = raw.read_snapshot()
    assert (after.revision, after.documents, after.selections) == (
        before.revision,
        before.documents,
        before.selections,
    )
    assert old not in json.dumps(getattr(profiles, destination).public_settings())


def test_logical_deletion_and_clearing_retain_private_history_without_fallback(committed):
    root, raw, record = committed
    with ConfigurationBackend.production(root) as owner:
        profiles = ConfigurationProfileServices(owner.store)
        before = profiles.read_snapshot()
        rq_id = before.documents.extract_private()["rqdata"]["active_id"]
        profiles.delete_profile("rqdata", rq_id)
        result = profiles.rqdata.public_settings()
        assert result["selection"] == {"kind": "none"}
        with pytest.raises(ConfigurationStoreError):
            profiles.rqdata.active_config()
        snapshot = owner.store.read_snapshot()
        assert snapshot.selections["rqdata"] == {"kind": "none"}
        assert any(CANARIES[3] in json.dumps(doc) for doc in snapshot.extract_history_private())
        private(snapshot)
        with pytest.raises(TypeError):
            json.dumps(snapshot)
        with pytest.raises(TypeError):
            pickle.dumps(snapshot)
        assert record == authority.validate_committed_authority(root)


@pytest.fixture
def hot_store(committed):
    root, store, record = committed

    # A committed descendant is the expected latest, not the activation anchor.
    def counters(old):
        docs = old.documents
        for document in docs.values():
            document["counter"] = 0
        return docs, old.selections

    latest = store.update(counters)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_child_interrupted, args=(str(root), child, "publish"))
    process.start()
    child.close()
    try:
        assert parent.poll(30) and parent.recv() == "ready"
        stop_owned_process(process)
        yield root, store, record, latest
    finally:
        stop_owned_process(process)
        process.close()
        parent.close()


@pytest.mark.parametrize("header_damage", [False, True])
def test_in_place_journal_recovery_preserves_exact_current_generation(
    hot_store, node, header_damage
):
    root, store, record, latest = hot_store
    path = store._path
    if header_damage:
        with path.open("r+b") as stream:
            stream.write(b"damaged-header!!?")
    before = tree(root)
    assert recovery.inspect_configuration_recovery(root)["state"] == "sqlite_recovery_required"
    assert tree(root) == before
    with pytest.raises(ConfigurationStoreError):
        ConfigurationBackend.production(root)
    result = recovery.recover_configuration_journal(
        root,
        installation_id=record["installation_id"],
        decision_id=record["decision_id"],
        current_revision=latest.revision,
        node_executable=node,
        acknowledge_offline=True,
        acknowledge_current_revision=True,
    )
    assert result["state"] == "journal_recovered"
    restored = store.read_snapshot()
    assert restored.revision == latest.revision != record["revision"]
    assert restored.documents == latest.documents and restored.selections == latest.selections
    assert not (root / authority.RECOVERY_NAMESPACE).exists()
    assert recovery.inspect_configuration_recovery(root)["state"] == "committed_sqlite"
    with pytest.raises(ConfigurationStoreError):
        recover(root, store, record, node)  # No journal: never replay a completed operation.
    private(result)


@pytest.mark.parametrize("failure", ["wrong-revision", "node", "interrupted-copy"])
def test_failed_recovery_keeps_original_bytes_and_a_fail_closed_witness(
    hot_store, node, monkeypatch, failure
):
    root, store, record, latest = hot_store
    before = {
        p: p.read_bytes()
        for p in (store._path, store._path.with_name(store._path.name + "-journal"))
    }
    revision = "f" * 32 if failure == "wrong-revision" else latest.revision
    if failure == "node":
        monkeypatch.setattr(
            recovery, "_verify_node", lambda *args: (_ for _ in ()).throw(OSError(CANARIES[0]))
        )
    if failure == "interrupted-copy":
        monkeypatch.setattr(
            deployment,
            "_copy_file",
            lambda *args: (_ for _ in ()).throw(KeyboardInterrupt(CANARIES[3])),
        )
    with pytest.raises((ConfigurationStoreError, KeyboardInterrupt)) as caught:
        recovery.recover_configuration_journal(
            root,
            installation_id=record["installation_id"],
            decision_id=record["decision_id"],
            current_revision=revision,
            node_executable=node,
            acknowledge_offline=True,
            acknowledge_current_revision=True,
        )
    private(caught.value)
    if failure != "node":
        assert all(p.read_bytes() == wire for p, wire in before.items())
    assert recovery.inspect_configuration_recovery(root)["state"] == "recovery_interrupted"
    with pytest.raises(ConfigurationStoreError):
        ConfigurationBackend.production(root)
    with pytest.raises(ConfigurationStoreError):
        recovery.recover_configuration_journal(
            root,
            installation_id=record["installation_id"],
            decision_id=record["decision_id"],
            current_revision=latest.revision,
            node_executable=node,
            acknowledge_offline=True,
            acknowledge_current_revision=True,
        )


@pytest.mark.parametrize("ack", ["acknowledge_offline", "acknowledge_current_revision"])
def test_recovery_requires_explicit_acknowledgements(hot_store, node, ack):
    root, store, record, latest = hot_store
    before = tree(root)
    options = dict(
        installation_id=record["installation_id"],
        decision_id=record["decision_id"],
        current_revision=latest.revision,
        node_executable=node,
        acknowledge_offline=True,
        acknowledge_current_revision=True,
    )
    options[ack] = False
    with pytest.raises(ConfigurationStoreError):
        recovery.recover_configuration_journal(root, **options)
    assert tree(root) == before


@pytest.mark.parametrize("kind", ["identity", "revision", "namespace", "journal"])
def test_invalid_recovery_evidence_never_repairs_or_recreates(candidate, node, kind):
    root, store, _, record = candidate
    if kind != "namespace":
        activate(candidate, node)
    options = dict(
        installation_id=record.as_dict()["installation_id"],
        decision_id=record.as_dict()["decision_id"],
        current_revision=store.read_snapshot().revision,
        node_executable=node,
        acknowledge_offline=True,
        acknowledge_current_revision=True,
    )
    if kind == "identity":
        options["installation_id"] = "f" * 32
    if kind == "revision":
        options["current_revision"] = "bad"
    before = tree(root)
    with pytest.raises(ConfigurationStoreError) as caught:
        recovery.recover_configuration_journal(root, **options)
    private(caught.value)
    assert tree(root) == before


@pytest.mark.parametrize("limit", ["generations", "bytes"])
def test_capacity_failure_preserves_all_documents_and_authority(committed, monkeypatch, limit):
    root, raw, record = committed
    with ConfigurationBackend.production(root) as owner:
        profiles = ConfigurationProfileServices(owner.store)
        before = owner.store.read_snapshot()
        sizes = sum(len(text.encode()) for row in before._history for text in row)
        if limit == "generations":
            monkeypatch.setattr(storage, "_MAX_GENERATIONS", 2)
        else:
            monkeypatch.setattr(storage, "_MAX_HISTORY_BYTES", sizes * 2)
        # Exactly one same-sized generation fits; the next must reject BEFORE
        # commit, while the last readable head and all other services survive.
        owner.store.update(
            lambda old: (old.documents, old.selections), expected_revision=before.revision
        )
        at_limit = owner.store.read_snapshot()
        profile = at_limit.documents["models"]["profiles"][0].copy()
        with pytest.raises(ConfigurationStoreError) as caught:
            profiles.models.save_profile(
                {**profile, "name": "Rejected for capacity"}, activate=False
            )
        assert caught.value.code == "HISTORY_CAPACITY"
        private(caught.value)
        after = owner.store.read_snapshot()
        assert (after.revision, after.documents, after.selections) == (
            at_limit.revision,
            at_limit.documents,
            at_limit.selections,
        )
        assert authority.validate_committed_authority(root) == record


@pytest.fixture
def saved_backup(committed, tmp_path):
    root, store, record = committed
    (root / "deployment-note.txt").write_text("Synthetic non-secret deployment note")
    target = tmp_path / "private-backup"
    request = ConfigurationBackend.offline_decision(root)
    with deployment.coordinated_offline(request, stop_services=lambda: None) as offline:
        report = deployment.create_backup(offline, target)
    return root, store, record, target, report


def test_backup_identity_integrity_and_freshness_are_distinct(saved_backup):
    root, store, record, target, report = saved_backup
    assert report["verified"] is True and report["restorable"] is False
    assert all(
        report[key] is False
        for key in (
            "freshness_verified",
            "credential_revocation_verified",
            "rollback_provenance_verified",
        )
    )
    assert (target / "runtime" / authority.BACKUP_FENCE).exists()
    with pytest.raises(ConfigurationStoreError):
        ConfigurationBackend.production(target / "runtime")
    profiles = ConfigurationProfileServices(store)
    current = store.read_snapshot().documents["rqdata"]["profiles"][0].copy()
    profiles.rqdata.save_profile({**current, "password": ""}, activate=True, clear_password=True)
    # No backend construction/activation is needed to inspect an old backup.
    checked = deployment.inspect_configuration_backup(
        root, target, installation_id=record["installation_id"]
    )
    assert checked["configuration"]["revision"] != store.read_snapshot().revision
    assert checked["restorable"] is False and checked["freshness_verified"] is False
    before = tree(root)
    with pytest.raises(deployment.DeploymentPreparationError):
        deployment.reject_restore(root, target)
    assert tree(root) == before
    private(json.dumps(checked))


@pytest.mark.parametrize(
    "fault",
    [
        "foreign-root",
        "foreign-id",
        "missing-manifest",
        "pending-manifest",
        "changed-file",
        "absolute-path",
    ],
)
def test_incomplete_conflicting_and_cross_installation_backups_fail_closed(
    saved_backup, tmp_path, fault
):
    root, _, record, target, _ = saved_backup
    identifier = record["installation_id"]
    if fault == "foreign-root":
        root = tmp_path / "different-installation"
        root.mkdir()
    elif fault == "foreign-id":
        identifier = "f" * 32
    elif fault in {"missing-manifest", "pending-manifest"}:
        (target / "configuration-backup.json").rename(target / "incomplete.pending")
    elif fault == "changed-file":
        (target / "runtime/app/secrets/configuration-store.sqlite3").write_bytes(b"incomplete")
    else:
        manifest = json.loads((target / "configuration-backup.json").read_bytes())
        manifest["files"][0]["path"] = str(target / manifest["files"][0]["path"])
        (target / "configuration-backup.json").write_text(json.dumps(manifest))
    before = tree(target)
    with pytest.raises(deployment.DeploymentPreparationError) as caught:
        deployment.inspect_configuration_backup(root, target, installation_id=identifier)
    private(caught.value)
    assert tree(target) == before


def test_backup_paths_cannot_export_retained_secrets(committed, tmp_path):
    root, store, _ = committed
    profiles = ConfigurationProfileServices(store)
    rq = store.read_snapshot().documents["rqdata"]["profiles"][0].copy()
    profiles.rqdata.save_profile({**rq, "password": ""}, activate=True, clear_password=True)
    (root / CANARIES[3]).write_text("synthetic report")
    target = tmp_path / "rejected-backup"
    with deployment.coordinated_offline(
        ConfigurationBackend.offline_decision(root), stop_services=lambda: None
    ) as offline:
        with pytest.raises(deployment.DeploymentPreparationError) as caught:
            deployment.create_backup(offline, target)
    private(caught.value)
    assert not (target / "configuration-backup.json").exists()


def test_backup_detects_mutation_after_initial_digest_verification(saved_backup, monkeypatch):
    root, _, record, target, _ = saved_backup
    manifest = json.loads((target / "configuration-backup.json").read_bytes())
    note = target / "runtime/deployment-note.txt"
    original = deployment._inventory

    def changed(directory):
        if directory == target:
            # Keep every parsed configuration/marker valid. Only the second
            # inventory digest can detect this late arbitrary-file mutation.
            note.write_text("Changed non-secret deployment note")
        return original(directory)

    monkeypatch.setattr(deployment, "_inventory", changed)
    with pytest.raises(deployment.DeploymentPreparationError) as caught:
        deployment.inspect_configuration_backup(
            root, target, installation_id=record["installation_id"]
        )
    assert caught.value.code == "SOURCE_CHANGED"
    assert manifest["complete"] is True  # A completion marker is not freshness proof.


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_orphan_configuration_sidecars_match_node_fail_closed_policy(committed, suffix):
    root, store, _ = committed
    path = store._path.with_name(store._path.name + suffix)
    path.write_bytes(b"")
    before = tree(root)
    with pytest.raises(ConfigurationStoreError) as caught:
        storage.ConfigurationStore.open_existing(root)
    assert caught.value.code == "UNSUPPORTED_STORAGE"
    assert recovery.inspect_configuration_recovery(root)["state"] == "unsupported_state"
    assert tree(root) == before


def test_negative_control_historical_redaction_guard_detects_old_credential(committed, monkeypatch):
    import apps.api.services.configuration_profile_service as policy

    _, raw, _ = committed
    profiles = ConfigurationProfileServices(raw)
    rq = raw.read_snapshot().documents["rqdata"]["profiles"][0].copy()
    profiles.rqdata.save_profile({**rq, "password": "replacement-rq-password"}, activate=True)
    profile = raw.read_snapshot().documents["models"]["profiles"][0].copy()
    profile["name"] = CANARIES[3]
    with pytest.raises(ConfigurationStoreError):
        profiles.models.save_profile(profile, activate=False)
    with monkeypatch.context() as control:
        control.setattr(
            policy,
            "_snapshot_credential_values",
            lambda snap, environment=None: policy._credential_values(snap.documents, environment),
        )
        result = profiles.models.save_profile(profile, activate=False)
        with pytest.raises(AssertionError, match="Historical password exposed"):
            assert CANARIES[3] not in json.dumps(result), "Historical password exposed"
    with pytest.raises(ConfigurationStoreError):
        profiles.models.public_settings()


def test_negative_control_unauthorized_restore_detects_secret_resurrection(
    saved_backup, monkeypatch
):
    root, store, record, target, _ = saved_backup
    profiles = ConfigurationProfileServices(store)
    rq = store.read_snapshot().documents["rqdata"]["profiles"][0].copy()
    profiles.rqdata.save_profile({**rq, "password": ""}, activate=True, clear_password=True)
    with pytest.raises(deployment.DeploymentPreparationError):
        deployment.reject_restore(root, target)

    def unsafe_restore(_root, _backup):
        shutil.copyfile(_backup / "runtime/app/secrets/configuration-store.sqlite3", store._path)
        shutil.copyfile(
            _backup / "runtime" / authority.BACKUP_FENCE, _root / authority.BACKUP_FENCE
        )

    with monkeypatch.context() as control:
        control.setattr(deployment, "reject_restore", unsafe_restore)
        deployment.reject_restore(root, target)
        assert recovery.inspect_configuration_recovery(root)["state"] == "historical_backup"
        with pytest.raises(AssertionError, match="Older credential database replaced"):
            assert store.read_snapshot().revision != record["revision"], (
                "Older credential database replaced"
            )
    with pytest.raises(ConfigurationStoreError):
        ConfigurationBackend.production(root)
    with monkeypatch.context() as control:
        control.setattr(authority, "BACKUP_FENCE", Path("app/secrets/ignored-backup-fence"))
        with ConfigurationBackend.production(root) as unsafe_owner:
            leaked = (
                ConfigurationProfileServices(unsafe_owner.store)
                .rqdata.active_config()
                .extract_private()["RQ_PASSWORD"]
            )
            with pytest.raises(AssertionError, match="Historical credential resurrected"):
                assert leaked != CANARIES[3], "Historical credential resurrected"
    with pytest.raises(ConfigurationStoreError):
        ConfigurationBackend.production(root)


def test_negative_control_pending_activation_never_means_activated(candidate, node, monkeypatch):
    root, _, _, record = candidate
    pending = root / authority.NAMESPACE / "decision.pending"
    value = record.as_dict()
    value["state"] = "committed"
    pending.write_text(json.dumps(value))
    if os.name != "nt":
        pending.chmod(0o600)
    assert (
        recovery.inspect_configuration_recovery(root)["state"]
        == "activation_publication_interrupted"
    )
    with monkeypatch.context() as control:
        original = recovery.inspect_configuration_recovery

        def unsafe_repair(where, **options):
            os.replace(pending, root / authority.NAMESPACE / "decision.json")
            return original(where, **options)

        control.setattr(recovery, "inspect_configuration_recovery", unsafe_repair)
        with pytest.raises(AssertionError, match="Inspection activated a pending record"):
            assert recovery.inspect_configuration_recovery(root)["state"] != "committed_sqlite", (
                "Inspection activated a pending record"
            )
    assert (
        authority.read_decision_record(root).as_dict()["state"] == "committed"
    )  # Only this disposable negative-control root.


def test_negative_control_capacity_guard_detects_unreadable_publication(committed, monkeypatch):
    root, raw, _ = committed
    with ConfigurationBackend.production(root) as owner:
        monkeypatch.setattr(storage, "_MAX_GENERATIONS", 1)
        before = owner.store.read_snapshot()
        with pytest.raises(ConfigurationStoreError):
            owner.store.update(lambda old: (old.documents, old.selections))
        real = storage.ConfigurationStore._ancestry

        def unchecked(store, session, current, **options):
            if not options.get("publishing"):
                return real(store, session, current, **options)

        handle = owner.store
        with monkeypatch.context() as control:
            control.setattr(storage.ConfigurationStore, "_ancestry", unchecked)
            after = handle.update(lambda old: (old.documents, old.selections))
            with pytest.raises(AssertionError, match="Capacity overrun was committed"):
                assert after.revision == before.revision, "Capacity overrun was committed"
        with pytest.raises(ConfigurationStoreError):
            authority.validate_committed_authority(root)


def test_negative_control_cross_installation_pin_is_not_optional(committed, monkeypatch):
    root, _, record = committed
    expected = "f" * 32
    assert expected != record["installation_id"]
    assert (
        recovery.inspect_configuration_recovery(root, expected_installation_id=expected)["state"]
        == "identity_conflict"
    )
    original = recovery._inspect

    def ignore_pin(where, **options):
        options["expected_installation_id"] = None
        return original(where, **options)

    with monkeypatch.context() as control:
        control.setattr(recovery, "_inspect", ignore_pin)
        with pytest.raises(AssertionError, match="Foreign installation accepted"):
            assert (
                recovery.inspect_configuration_recovery(root, expected_installation_id=expected)[
                    "state"
                ]
                == "identity_conflict"
            ), "Foreign installation accepted"


def test_negative_control_child_lifetime_cannot_release_before_actual_exit(committed, monkeypatch):
    from alphalab.utils.configuration_agent import _ChildLease

    root, _, _ = committed
    owner = ConfigurationBackend.production(root)
    # This test owns the waiting child. It models startup before a helper has
    # acquired its independent Agent lease, so the parent's lifetime is vital.
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", "import sys;sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    lifetime = _ChildLease(owner)
    try:
        assert lifetime.release_if_exited(child) is False
        with pytest.raises(ConfigurationStoreError):
            owner.close()

        def too_early(self, _child):
            with self.lock:
                self.owner._users -= 1
                self.held = False
            return True

        with monkeypatch.context() as control:
            control.setattr(_ChildLease, "release_if_exited", too_early)
            lifetime.release_if_exited(child)
            owner.close()
            with configuration_access(root):
                pass
            with pytest.raises(AssertionError, match="Live child lost protection"):
                assert not owner._closed, "Live child lost protection"
    finally:
        child.stdin.close()
        child.wait(timeout=10)
        lifetime.release_if_exited(child)
        owner.close()


def _activation_publication_child(root, node, core, manifest, phase, pipe):
    """Pause a real activation at publication; only the parent owns/kills us."""
    from apps.api.services import configuration_activation_service as activation

    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("No provider calls in publication crash tests")

    socket.socket.connect = forbidden_network
    activation._CORE, activation._MANIFEST = Path(core), Path(manifest)
    real_publish, real_replace = authority._publish_committed, os.replace

    def wait():
        pipe.send(phase)
        pipe.recv()

    def publish(offline, expected):
        if phase == "before":
            wait()
        return real_publish(offline, expected)

    def replace(source, target):
        if Path(source).name == "decision.pending":
            if phase == "during":
                wait()
            result = real_replace(source, target)
            if phase == "after":
                wait()
            return result
        return real_replace(source, target)

    authority._publish_committed, os.replace = publish, replace
    owner = ConfigurationBackend.json(root)
    owner.close()
    activation_tests.activate((Path(root), None, owner, authority.read_decision_record(root)), node)


@pytest.mark.parametrize("phase", ["before", "during", "after"])
def test_real_process_interruption_of_activation_never_promotes_pending_evidence(
    candidate, node, phase
):
    from apps.api.services import configuration_activation_service as activation

    root = candidate[0]
    context = multiprocessing.get_context("spawn")
    receiving, sending = context.Pipe()
    process = context.Process(
        target=_activation_publication_child,
        args=(root, node, activation._CORE, activation._MANIFEST, phase, sending),
    )
    process.start()
    sending.close()
    try:
        assert receiving.poll(25), "Test child did not reach the intended publication boundary"
        assert receiving.recv() == phase
    finally:
        stop_owned_process(process)
        receiving.close()
    before = tree(root)
    result = recovery.inspect_configuration_recovery(
        root, acknowledgement_uncertain=phase == "after"
    )
    assert (
        result["state"]
        == {
            "before": "prepared_candidate",
            "during": "activation_publication_interrupted",
            "after": "activation_acknowledgement_uncertain",
        }[phase]
    ), result
    assert tree(root) == before
    if phase == "during":
        with pytest.raises(ConfigurationStoreError):
            ConfigurationBackend.production(root)
    else:
        with ConfigurationBackend.production(root) as owner:
            assert owner.mode == ("json" if phase == "before" else "sqlite")
    private(result)


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
def test_cleared_historical_credentials_remain_redaction_only_after_restart(committed, service):
    root, raw, _ = committed
    profiles = ConfigurationProfileServices(raw)
    documents = raw.read_snapshot().documents
    row = documents[service]["profiles"][0].copy()
    field = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}[service]
    old = row[field]
    row[field] = ""
    clear = {
        "models": "clear_api_key",
        "search": "clear_brave_api_key",
        "rqdata": "clear_password",
    }[service]
    getattr(profiles, service).save_profile(row, activate=False, **{clear: True})
    with ConfigurationBackend.production(root) as owner:
        services = ConfigurationProfileServices(owner.store)
        before = owner.store.read_snapshot()
        row = next(
            item.copy() for item in before.documents[service]["profiles"] if item["id"] == row["id"]
        )
        assert row[field] == ""
        row["name"] = old
        with pytest.raises(ConfigurationStoreError) as caught:
            getattr(services, service).save_profile(row, activate=False)
        private(caught.value)
        assert owner.store.read_snapshot().revision == before.revision
        assert old not in json.dumps(getattr(services, service).public_settings())


def test_referenced_model_cannot_be_logically_deleted(committed):
    _, raw, _ = committed
    profiles = ConfigurationProfileServices(raw)
    before = raw.read_snapshot()
    model_id = before.documents["models"]["profiles"][0]["id"]
    search = before.documents["search"]["profiles"][0].copy()
    profiles.search.save_profile(
        {**search, "provider": "openrouter", "model_profile_id": model_id}, activate=False
    )
    before = raw.read_snapshot()
    with pytest.raises(ConfigurationStoreError):
        profiles.delete_profile("models", model_id)
    after = raw.read_snapshot()
    assert (after.revision, after.documents, after.selections) == (
        before.revision,
        before.documents,
        before.selections,
    )


def test_independent_latest_revision_evidence_detects_old_consistent_replacement(committed):
    root, raw, record = committed
    old_database = raw._path.read_bytes()
    profiles = ConfigurationProfileServices(raw)
    model = raw.read_snapshot().documents["models"]["profiles"][0].copy()
    profiles.models.save_profile({**model, "name": "New current metadata"}, activate=False)
    current = raw.read_snapshot().revision
    assert current != record["revision"]
    assert (
        recovery.inspect_configuration_recovery(root, expected_current_revision=current)["state"]
        == "committed_sqlite"
    )
    # Test-owned, deliberate whole-database replacement. The old copy is
    # internally valid, so only independent latest-revision evidence detects it.
    raw._path.write_bytes(old_database)
    assert (
        recovery.inspect_configuration_recovery(root, expected_current_revision=current)["state"]
        == "store_revision_conflict"
    )
    assert recovery.inspect_configuration_recovery(root)["state"] == "committed_sqlite"


def test_inspection_interruption_is_redacted_nonmutating_and_not_retried(committed, monkeypatch):
    root, _, _ = committed
    before = tree(root)
    with monkeypatch.context() as control:

        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt(CANARIES[3])

        control.setattr(recovery, "_records", interrupted)
        with pytest.raises(KeyboardInterrupt) as caught:
            recovery.inspect_configuration_recovery(root)
        private(caught.value)
    assert tree(root) == before
    assert recovery.inspect_configuration_recovery(root)["state"] == "committed_sqlite"


def test_cli_errors_do_not_echo_sensitive_arguments(committed):
    root, _, _ = committed
    script = Path(__file__).parents[2] / "scripts/recover_configuration.py"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(script),
            "inspect",
            "--root",
            str(root),
            "--installation-id",
            CANARIES[3],
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"] == "INVALID_ARGUMENT"
    private(result.stdout.decode() + result.stderr.decode())
    result = subprocess.run(
        [sys.executable, "-B", str(script), CANARIES[0], "--root", str(root)],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    private(result.stdout.decode() + result.stderr.decode())


def _hot_recovery_arguments(hot_store, node):
    root, _, record, latest = hot_store
    return dict(
        root=root,
        installation_id=record["installation_id"],
        decision_id=record["decision_id"],
        current_revision=latest.revision,
        node_executable=node,
        acknowledge_offline=True,
        acknowledge_current_revision=True,
    )


def _assert_recovery_fenced(root):
    assert (root / authority.RECOVERY_NAMESPACE).exists(), (
        "Recovery admission reopened before audit"
    )
    assert recovery.inspect_configuration_recovery(root)["state"] == "recovery_interrupted"
    for factory in (ConfigurationBackend.production, ConfigurationBackend.json):
        with pytest.raises(ConfigurationStoreError) as caught:
            factory(root)
        private(caught.value)


def test_final_authority_audit_failure_keeps_recovery_fenced(hot_store, node, monkeypatch):
    root, store, _, latest = hot_store
    original_verify, original_records = recovery._verify_node, recovery._records
    events = []

    def verified(*args):
        original_verify(*args)
        events.append("node-verified")

    def fault_at_final_authority(where):
        if events:
            assert (root / authority.RECOVERY_NAMESPACE).is_dir()
            events.append("final-authority-failed")
            raise OSError(CANARIES[0])
        return original_records(where)

    with monkeypatch.context() as control:
        control.setattr(recovery, "_verify_node", verified)
        control.setattr(recovery, "_records", fault_at_final_authority)
        with pytest.raises(ConfigurationStoreError) as caught:
            recovery.recover_configuration_journal(**_hot_recovery_arguments(hot_store, node))
    assert events == ["node-verified", "final-authority-failed"]
    private(caught.value)
    _assert_recovery_fenced(root)
    snapshot = store.read_snapshot()
    assert (snapshot.revision, snapshot.documents, snapshot.selections) == (
        latest.revision,
        latest.documents,
        latest.selections,
    )


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_final_audit_rejects_new_sidecars_without_unfencing(hot_store, node, monkeypatch, suffix):
    root, store, _, _ = hot_store
    original = recovery._verify_node

    def add_sidecar(*args):
        original(*args)
        store._path.with_name(store._path.name + suffix).write_bytes(b"")

    monkeypatch.setattr(recovery, "_verify_node", add_sidecar)
    with pytest.raises(ConfigurationStoreError) as caught:
        recovery.recover_configuration_journal(**_hot_recovery_arguments(hot_store, node))
    assert caught.value.code == "RECOVERY_REQUIRED"
    _assert_recovery_fenced(root)


def test_successful_recovery_audits_before_final_publication(hot_store, node, monkeypatch):
    root, store, _, latest = hot_store
    original_node = recovery._verify_node
    original_audit, original_finish = recovery._audit_final_authority, recovery._finish_workspace
    original_records = recovery._records
    events = []

    def verified(*args):
        original_node(*args)
        events.append("node")

    def audited(*args):
        assert (root / authority.RECOVERY_NAMESPACE).is_dir()
        original_audit(*args)
        events.append("audit")

    def finish(where):
        assert events == ["node", "audit"]
        original_finish(where)
        events.append("publication")

    def no_postpublication_audit(where):
        assert "publication" not in events, "Fallible authority audit occurred after publication"
        return original_records(where)

    with monkeypatch.context() as control:
        control.setattr(recovery, "_verify_node", verified)
        control.setattr(recovery, "_audit_final_authority", audited)
        control.setattr(recovery, "_finish_workspace", finish)
        control.setattr(recovery, "_records", no_postpublication_audit)
        result = recovery.recover_configuration_journal(**_hot_recovery_arguments(hot_store, node))
    assert events == ["node", "audit", "publication"]
    assert result["state"] == "journal_recovered"
    assert not (root / authority.RECOVERY_NAMESPACE).exists()
    with ConfigurationBackend.production(root) as owner:
        assert owner.mode == "sqlite"
        assert owner.store.read_snapshot().revision == latest.revision
    assert store.read_snapshot().documents == latest.documents
    private(result)


@pytest.mark.parametrize(
    "fault", ["before-rmdir", "after-rmdir", "directory-sync", "lease-release"]
)
def test_uncertain_publication_requires_inspection_and_no_retry(
    hot_store, node, monkeypatch, fault
):
    root, store, _, latest = hot_store
    workspace = root / authority.RECOVERY_NAMESPACE
    original_rmdir, original_sync = type(workspace).rmdir, authority._sync_directory
    original_lease = recovery.configuration_access
    removed = False

    def rmdir(where):
        nonlocal removed
        if where == workspace and fault == "before-rmdir":
            raise OSError(CANARIES[1])
        result = original_rmdir(where)
        if where == workspace:
            removed = True
            if fault == "after-rmdir":
                raise OSError(CANARIES[1])
        return result

    def sync(where):
        if removed and fault == "directory-sync":
            raise OSError(CANARIES[2])
        return original_sync(where)

    @contextmanager
    def lease(*args, **kwargs):
        with original_lease(*args, **kwargs):
            yield
        if fault == "lease-release":
            raise OSError(CANARIES[3])

    with monkeypatch.context() as control:
        control.setattr(type(workspace), "rmdir", rmdir)
        control.setattr(authority, "_sync_directory", sync)
        control.setattr(recovery, "configuration_access", lease)
        with pytest.raises(ConfigurationStoreError) as caught:
            recovery.recover_configuration_journal(**_hot_recovery_arguments(hot_store, node))
    assert caught.value.code == "RECOVERY_PUBLICATION_UNCERTAIN"
    assert "Inspect" in str(caught.value) and "rolled back" not in str(caught.value)
    private(caught.value)
    observed = recovery.inspect_configuration_recovery(
        root, expected_current_revision=latest.revision
    )
    if fault == "before-rmdir":
        assert observed["state"] == "recovery_interrupted"
        _assert_recovery_fenced(root)
    else:
        assert observed["state"] == "committed_sqlite"
        assert not workspace.exists()  # No silent witness recreation.
        with ConfigurationBackend.production(root) as owner:
            assert owner.mode == "sqlite"
            assert owner.store.read_snapshot().revision == latest.revision
    snapshot = store.read_snapshot()
    assert (snapshot.revision, snapshot.documents, snapshot.selections) == (
        latest.revision,
        latest.documents,
        latest.selections,
    )
    before = tree(root)
    with pytest.raises(ConfigurationStoreError):
        recovery.recover_configuration_journal(**_hot_recovery_arguments(hot_store, node))
    assert tree(root) == before


def test_exclusive_leases_block_startup_until_publication_returns(hot_store, node, monkeypatch):
    root, _, _, latest = hot_store
    workspace = root / authority.RECOVERY_NAMESPACE
    original_audit, original_rmdir = recovery._audit_final_authority, type(workspace).rmdir
    events = []

    def attempt_startup(phase):
        program = """
import sys
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStoreError
try:
    owner=ConfigurationBackend.production(sys.argv[1])
except ConfigurationStoreError as error:
    print('blocked:'+error.code)
else:
    owner.close()
    print('admitted')
"""
        child = subprocess.run(
            [sys.executable, "-B", "-c", program, str(root)], capture_output=True, timeout=10
        )
        assert child.returncode == 0
        assert child.stdout.strip() == b"blocked:STORE_BUSY"
        private(child.stdout.decode() + child.stderr.decode())
        events.append(phase)

    def audit(*args):
        original_audit(*args)
        assert workspace.exists()
        attempt_startup("audit")

    def rmdir(where):
        result = original_rmdir(where)
        if where == workspace:
            assert not workspace.exists()
            attempt_startup("published-but-leased")
        return result

    with monkeypatch.context() as control:
        control.setattr(recovery, "_audit_final_authority", audit)
        control.setattr(type(workspace), "rmdir", rmdir)
        result = recovery.recover_configuration_journal(**_hot_recovery_arguments(hot_store, node))
    assert events == ["audit", "published-but-leased"]
    assert result["state"] == "journal_recovered"
    with ConfigurationBackend.production(root) as owner:
        assert owner.store.read_snapshot().revision == latest.revision


def test_negative_control_early_fence_removal_reopens_failed_recovery(hot_store, node, monkeypatch):
    root, _, _, latest = hot_store

    def vulnerable_final_audit(*_args):
        recovery._finish_workspace(root / authority.RECOVERY_NAMESPACE)
        raise OSError(CANARIES[0])

    with monkeypatch.context() as control:
        control.setattr(recovery, "_audit_final_authority", vulnerable_final_audit)
        with pytest.raises(ConfigurationStoreError) as caught:
            recovery.recover_configuration_journal(**_hot_recovery_arguments(hot_store, node))
        with pytest.raises(AssertionError, match="Recovery admission reopened before audit"):
            _assert_recovery_fenced(root)
    private(caught.value)
    with ConfigurationBackend.production(root) as owner:
        assert owner.mode == "sqlite"
        assert owner.store.read_snapshot().revision == latest.revision


def _deletion_native_state(store, service, *, selected, search_v1=False):
    """Valid native extension fields/formatting that unrelated edits must retain."""

    def edit(previous):
        docs, choices = previous.documents, previous.selections
        for key in ("models", "search", "rqdata"):
            docs[key]["private_unrelated"] = {"keep": key, "nested": [1, {"empty": []}]}
        docs["models"]["profiles"][0]["private_extension"] = {"keep": "model"}
        docs["rqdata"]["profiles"][0]["password"] = "  " + CANARIES[3] + "  "
        docs["rqdata"]["profiles"][0]["private_extension"] = {"keep": "rq"}
        if not selected:
            row = docs[service]["profiles"][0].copy()
            row.update(id="delete-me", name="Spare deletion profile")
            secret = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}[service]
            row[secret] = "synthetic-spare-credential-" + service
            docs[service]["profiles"].append(row)
        if search_v1:
            docs["search"] = {
                "version": 1,
                "provider": "brave",
                "brave_api_key": CANARIES[2],
                "private_unrelated": {"keep": "legacy-search", "nested": []},
            }
            choices["search"] = {"kind": "profile", "id": "default"}
        return docs, choices

    return store.update(edit)


def _assert_unrelated_native_unchanged(before, after, service):
    for index, other in enumerate(("models", "search", "rqdata")):
        if other != service:
            assert after.documents[other] == before.documents[other], (
                "Unrelated native document changed"
            )
            assert after._texts[index] == before._texts[index], (
                "Unrelated native JSON was rewritten"
            )
            assert after.selections[other] == before.selections[other]


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
@pytest.mark.parametrize("selected", [False, True])
def test_deletion_preserves_unrelated_native_values_and_restart(committed, service, selected):
    root, store, record = committed
    _deletion_native_state(store, service, selected=selected, search_v1=service == "rqdata")
    with ConfigurationBackend.production(root) as owner:
        services = ConfigurationProfileServices(owner.store)
        before = owner.store.read_snapshot()
        identifier = before.documents[service]["active_id"] if selected else "delete-me"
        deleted = next(
            row for row in before.documents[service]["profiles"] if row["id"] == identifier
        )
        result = services.delete_profile(service, identifier, expected_revision=before.revision)
        after = owner.store.read_snapshot()
        assert result.revision == after.revision != before.revision
        assert not any(row["id"] == identifier for row in after.documents[service]["profiles"])
        _assert_unrelated_native_unchanged(before, after, service)
        if selected:
            assert after.selections[service] == {"kind": "none"}
            assert after.documents[service]["active_id"] is None
            with pytest.raises(ConfigurationStoreError):
                getattr(services, service).active_config()
        else:
            assert after.selections[service] == before.selections[service]
        # Other profiles' credentials remain in their original profile IDs.
        secret = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}[service]
        remaining = {row["id"]: row for row in after.documents[service]["profiles"]}
        for row in before.documents[service]["profiles"]:
            if row["id"] != identifier:
                assert remaining[row["id"]][secret].strip() == row[secret].strip()
        assert any(
            deleted[secret].strip() in json.dumps(row) for row in after.extract_history_private()
        )
        for key in ("models", "search", "rqdata"):
            private(getattr(services, key).public_settings())
    with ConfigurationBackend.production(root) as restarted:
        saved = restarted.store.read_snapshot()
        assert saved.revision == after.revision
        assert saved.documents == after.documents and saved.selections == after.selections
        _assert_unrelated_native_unchanged(before, saved, service)
        assert authority.validate_committed_authority(root) == record


@pytest.mark.parametrize("service", ["models", "search", "rqdata"])
@pytest.mark.parametrize("fault", ["missing", "invalid-id", "stale-revision", "validation"])
def test_invalid_deletion_preserves_all_native_documents(committed, monkeypatch, service, fault):
    root, store, _ = committed
    profiles = ConfigurationProfileServices(store)
    before = store.read_snapshot()
    identifier = before.documents[service]["active_id"]
    expected = before.revision
    if fault == "missing":
        identifier = "absent"
    elif fault == "invalid-id":
        identifier = "../unsafe"
    elif fault == "stale-revision":
        expected = "f" * 32
        assert expected != before.revision
    elif fault == "validation":

        def invalid(*_args):
            raise ValueError(CANARIES[3])

        monkeypatch.setattr(ConfigurationProfileServices, "_check_publication", invalid)
    with pytest.raises(ConfigurationStoreError) as caught:
        profiles.delete_profile(service, identifier, expected_revision=expected)
    private(caught.value)
    if fault == "stale-revision":
        assert caught.value.code == "REVISION_CONFLICT"
    after = store.read_snapshot()
    assert (after.revision, after.documents, after.selections, after._texts) == (
        before.revision,
        before.documents,
        before.selections,
        before._texts,
    )


def test_deleting_legacy_search_normalizes_only_its_own_document(committed):
    _, store, _ = committed
    _deletion_native_state(store, "rqdata", selected=True, search_v1=True)
    profiles = ConfigurationProfileServices(store)
    before = store.read_snapshot()
    profiles.delete_profile("search", "default", expected_revision=before.revision)
    after = store.read_snapshot()
    _assert_unrelated_native_unchanged(before, after, "search")
    assert after.documents["search"] == {"version": 2, "active_id": None, "profiles": []}
    assert after.selections["search"] == {"kind": "none"}
    with pytest.raises(ConfigurationStoreError):
        profiles.search.active_config()


def test_deletion_conflict_preserves_the_winning_generation(committed, monkeypatch):
    _, store, _ = committed
    profiles = ConfigurationProfileServices(store)
    before = store.with_history_checks().read_snapshot()
    original = ConfigurationProfileServices._publish
    winner = []

    def concurrent_publish(self, current, documents, choices):
        def rename(old):
            native = old.documents
            native["models"]["profiles"][0]["name"] = "Concurrent winning edit"
            return native, old.selections

        winner.append(store.update(rename, expected_revision=current.revision))
        return original(self, current, documents, choices)

    with monkeypatch.context() as control:
        control.setattr(ConfigurationProfileServices, "_publish", concurrent_publish)
        with pytest.raises(ConfigurationStoreError) as caught:
            profiles.delete_profile("rqdata", before.documents["rqdata"]["active_id"])
    assert caught.value.code == "REVISION_CONFLICT"
    assert len(winner) == 1
    after = store.with_history_checks().read_snapshot()
    assert (after.revision, after.documents, after.selections) == (
        winner[0].revision,
        winner[0].documents,
        winner[0].selections,
    )
    assert len(after._history) == len(before._history) + 1
    assert after.documents["rqdata"] == before.documents["rqdata"]
    private(caught.value)


def test_negative_control_global_normalization_rewrites_unrelated_native_v1(committed, monkeypatch):
    root, store, _ = committed
    _deletion_native_state(store, "rqdata", selected=True, search_v1=True)
    profiles = ConfigurationProfileServices(store)
    before = store.read_snapshot()
    original = ConfigurationProfileServices._publish

    def vulnerable_publish(self, current, documents, choices):
        from apps.api.services.configuration_profile_service import _documents

        normalized = _documents(current._snapshot)
        normalized["rqdata"] = documents["rqdata"]
        return original(self, current, normalized, choices)

    with monkeypatch.context() as control:
        control.setattr(ConfigurationProfileServices, "_publish", vulnerable_publish)
        profiles.delete_profile("rqdata", before.documents["rqdata"]["active_id"])
        after = store.read_snapshot()
        assert before.documents["search"]["version"] == 1
        assert after.documents["search"]["version"] == 2
        with pytest.raises(AssertionError, match="Unrelated native document changed"):
            _assert_unrelated_native_unchanged(before, after, "rqdata")


@pytest.mark.parametrize("fault", ["uncertain", "interrupt-before", "interrupt-after"])
def test_cli_publication_failures_require_inspection(hot_store, node, monkeypatch, capsys, fault):
    from scripts import recover_configuration as cli

    root, store, record, latest = hot_store
    workspace = root / authority.RECOVERY_NAMESPACE
    original = type(workspace).rmdir

    def rmdir(where):
        if where == workspace and fault == "interrupt-before":
            raise KeyboardInterrupt(CANARIES[0])
        result = original(where)
        if where == workspace:
            if fault == "interrupt-after":
                raise KeyboardInterrupt(CANARIES[1])
            if fault == "uncertain":
                raise OSError(CANARIES[2])
        return result

    arguments = [
        "recover_configuration.py",
        "recover-journal",
        "--root",
        str(root),
        "--installation-id",
        record["installation_id"],
        "--decision-id",
        record["decision_id"],
        "--current-revision",
        latest.revision,
        "--node",
        str(node),
        "--acknowledge-offline",
        "--acknowledge-current-revision",
    ]
    with monkeypatch.context() as control:
        control.setattr(type(workspace), "rmdir", rmdir)
        control.setattr(sys, "argv", arguments)
        exit_code = cli.main()
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert exit_code == (1 if fault == "uncertain" else 130)
    assert value["error"] == (
        "RECOVERY_PUBLICATION_UNCERTAIN" if fault == "uncertain" else "INTERRUPTED"
    )
    assert "Inspect" in value["message"] and "do not retry automatically" in value["message"]
    private(output.out + output.err)
    if fault == "interrupt-before":
        _assert_recovery_fenced(root)
    else:
        assert not workspace.exists()
        assert recovery.inspect_configuration_recovery(root)["state"] == "committed_sqlite"
    assert store.read_snapshot().revision == latest.revision
