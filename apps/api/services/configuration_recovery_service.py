"""Offline inspection and same-installation physical journal recovery only.

No HTTP route, authority repair, historical restore, pruning or rollback.
Filesystem observations do not establish backup provenance or revocation.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import ExitStack

from alphalab.dataio.runtime import runtime_execution_lock
from alphalab.utils import configuration_decision as authority
from alphalab.utils import configuration_deployment as deployment
from alphalab.utils.configuration_maintenance import (
    agent_access,
    checked_root,
    configuration_access,
    execution_access,
)
from alphalab.utils.configuration_store import (
    ConfigurationStore,
    ConfigurationStoreError,
    _safe_call,
)
from apps.api.services import configuration_decision_service as preparation
from apps.api.services import configuration_migration_service as migration
from apps.api.services.configuration_profile_service import ProfileSnapshot, _guard

_MAX_RECOVERY_BYTES = 256 * 1024 * 1024


def _result(state, *, action="manual_investigation", backend=None, record=None):
    value = {
        "state": state,
        "action": action,
        "restorable": False,
        "freshness_verified": False,
        "credential_revocation_verified": False,
        "rollback_provenance_verified": False,
    }
    if backend is not None:
        value["backend"] = backend
    if record is not None:
        value.update({key: record[key] for key in ("installation_id", "decision_id", "revision")})
    return value


def _records(root):
    """Observation of incomplete namespaces, never effective runtime authority."""
    directory = root / authority.NAMESPACE
    if not os.path.lexists(directory):
        return {}, set()
    authority._directory(directory)
    names = {item.name for item in directory.iterdir()}
    if names - {"decision.json", "decision.pending"}:
        raise ConfigurationStoreError("STORE_CORRUPT")
    return {
        name: authority._parse(
            authority._file(directory / name), authority._root_key(root)
        ).as_dict()
        for name in names
    }, names


def _inspect(
    root,
    *,
    acknowledgement_uncertain,
    expected_installation_id,
    expected_decision_id,
    expected_current_revision=None,
):
    if os.path.lexists(root / authority.BACKUP_FENCE):
        return _result("historical_backup", action="restore_prohibited")
    if os.path.lexists(root / authority.RECOVERY_NAMESPACE):
        return _result("recovery_interrupted")
    namespace_exists = os.path.lexists(root / authority.NAMESPACE)
    records, names = _records(root)
    record = records.get("decision.json")
    if not names:
        if namespace_exists:
            return _result("authority_incomplete")
        if (
            os.path.lexists(root / authority.OWNER_WITNESS)
            or expected_installation_id is not None
            or expected_decision_id is not None
        ):
            return _result("authority_missing")
        if os.path.lexists(root / "app/secrets/configuration-store.sqlite3"):
            return _result("unclaimed_store")  # Cannot distinguish staging from lost authority.
        before = migration._read_sources(root)
        migration._prepare(before, {})
        if migration._read_sources(root) != before:
            return _result("inspection_conflict")
        return _result("legacy_json", action="normal_startup", backend="json")
    if record is None:
        pending = records.get("decision.pending")
        if pending is not None:
            return _result(
                "prepared_publication_interrupted"
                if pending["state"] == "prepared"
                else "activation_publication_interrupted"
            )
        return _result("authority_incomplete")
    if (
        expected_installation_id is not None
        and record["installation_id"] != expected_installation_id
    ) or (expected_decision_id is not None and record["decision_id"] != expected_decision_id):
        return _result("identity_conflict")
    if "decision.pending" in names:
        pending = records["decision.pending"]
        if {**record, "state": pending["state"]} != pending:
            return _result("identity_conflict")
        return _result(
            "activation_publication_interrupted"
            if record["state"] == "prepared" and pending["state"] == "committed"
            else "prepared_publication_interrupted"
            if record["state"] == pending["state"] == "prepared"
            else "authority_ambiguous"
        )
    store = ConfigurationStore._handle(root, 0.2)
    if any(
        os.path.lexists(store._path.with_name(store._path.name + suffix))
        for suffix in ("-wal", "-shm")
    ):
        return _result("unsupported_state")
    if store._has_recovery_journal():
        store._check_file(recovering=True)
        return _result(
            "sqlite_recovery_required",
            action="offline_journal_recovery"
            if record["state"] == "committed"
            else "manual_investigation",
            record=record,
        )
    snapshot = store.with_history_checks().read_snapshot()
    ProfileSnapshot(snapshot, {})
    if expected_current_revision is not None and snapshot.revision != expected_current_revision:
        return _result("store_revision_conflict")
    if record["state"] == "prepared":
        try:
            preparation._identity(
                authority.parse_decision_record(
                    json.dumps(record), root_key=authority._root_key(root)
                ),
                snapshot,
            )
            preparation._legacy_equivalent(root, snapshot)
        except ConfigurationStoreError:
            return _result("prepared_stale", backend="json", record=record)
        return _result(
            "prepared_candidate", action="review_explicit_activation", backend="json", record=record
        )
    authority.validate_committed_authority(
        root, installation_id=record["installation_id"], decision_id=record["decision_id"]
    )
    observed, _ = _records(root)
    if observed != records:
        return _result("inspection_conflict")
    if store.read_snapshot().revision != snapshot.revision:
        return _result("inspection_conflict")
    result = _result(
        "activation_acknowledgement_uncertain" if acknowledgement_uncertain else "committed_sqlite",
        action="verify_without_retry" if acknowledgement_uncertain else "normal_startup",
        backend="sqlite",
        record=record,
    )
    result["current_revision"] = snapshot.revision
    result["history_generations"] = len(snapshot._history)
    result["history_bytes"] = sum(
        len(text.encode("utf-8")) for row in snapshot._history for text in row
    )
    return result


@_guard
def inspect_configuration_recovery(
    root,
    *,
    acknowledgement_uncertain=False,
    expected_installation_id=None,
    expected_decision_id=None,
    expected_current_revision=None,
):
    """Read-only observation: never creates even lock files or repairs journals.

    Acknowledgement receipt is unknowable from disk; uncertainty is explicit
    operator context. Healthy committed data is never permission to retry.
    """
    if type(acknowledgement_uncertain) is not bool or any(
        value is not None and (type(value) is not str or not authority._ID.fullmatch(value))
        for value in (expected_installation_id, expected_decision_id, expected_current_revision)
    ):
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    root = checked_root(root)
    if not root.is_dir():
        return _result("installation_missing")
    try:
        return _inspect(
            root,
            acknowledgement_uncertain=acknowledgement_uncertain,
            expected_installation_id=expected_installation_id,
            expected_decision_id=expected_decision_id,
            expected_current_revision=expected_current_revision,
        )
    except ConfigurationStoreError as error:
        state = {
            "STORE_MISSING": "store_missing",
            "INSTALLATION_MISMATCH": "identity_conflict",
            "UNSUPPORTED_PROTOCOL": "unsupported_state",
            "UNSUPPORTED_STORAGE": "unsupported_state",
            "STORE_BUSY": "inspection_busy",
            "RECOVERY_REQUIRED": "sqlite_recovery_required",
        }.get(error.code, "invalid_or_incomplete_state")
        return _result(state)


def _bound_store(root, record, installation_id):
    handle = ConfigurationStore._handle(root, 5)
    handle._installation_id = installation_id
    handle._anchor, handle._anchor_kinds = record["revision"], record["evidence"]["selection_kinds"]
    return handle


def _validate_recovered(snapshot, record, expected_revision):
    if snapshot.installation_id != record["installation_id"]:
        raise ConfigurationStoreError("INSTALLATION_MISMATCH")
    if snapshot.revision != expected_revision:
        raise ConfigurationStoreError("REVISION_CONFLICT")
    ProfileSnapshot(snapshot, {})


def _verify_node(root, snapshot, record, node):
    program = migration._NODE.replace(
        "new ConfigurationStoreReader(expected.root)",
        "new ConfigurationStoreReader(expected.root, { activation: expected.activation })",
    )
    expected = {
        "root": str(root),
        "protocol_version": snapshot.protocol_version,
        "installation_id": snapshot.installation_id,
        "revision": snapshot.revision,
        "documents": {key: snapshot.documents[key] for key in ("models", "search")},
        "selections": {key: snapshot.selections[key] for key in ("models", "search")},
        "activation": json.dumps(record),
    }
    result = subprocess.run(
        [str(node), "--input-type=module", "--eval", program],
        input=json.dumps(expected).encode(),
        capture_output=True,
        timeout=15,
        **migration._subprocess_options(),
    )
    if result.returncode or result.stdout != b"verified":
        raise ConfigurationStoreError("STORE_UNAVAILABLE")


def _audit_final_authority(root, record, restored, expected_revision):
    """Read-only postconditions under the coordinator's exclusive leases.

    Runtime authority readers must still reject our workspace. Inspecting the
    exact record directly here grants no runtime capability and never removes
    or bypasses a fence for another caller.
    """
    expected = {"decision.json": record}
    if _records(root)[0] != expected or record["state"] != "committed":
        raise ConfigurationStoreError("REVISION_CONFLICT")
    if os.path.lexists(root / authority.BACKUP_FENCE):
        raise ConfigurationStoreError("RECOVERY_REQUIRED")
    authority._directory(root / authority.RECOVERY_NAMESPACE)
    handle = _bound_store(root, record, record["installation_id"])
    if any(
        os.path.lexists(handle._path.with_name(handle._path.name + suffix))
        for suffix in ("-journal", "-wal", "-shm")
    ):
        raise ConfigurationStoreError("RECOVERY_REQUIRED")

    def audit():
        with handle._session(time.monotonic() + 5) as session:
            session.execute("BEGIN")
            snapshot = handle._current(session)
            if session.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ConfigurationStoreError("STORE_CORRUPT")
            session.execute("COMMIT")
        return snapshot

    snapshot = _safe_call(audit, read_only=True)
    _validate_recovered(snapshot, record, expected_revision)
    if snapshot._texts != restored._texts or snapshot._history != restored._history:
        raise ConfigurationStoreError("UPDATE_FAILED")
    deployment.require_idle(root)
    if os.path.lexists(root / "conexus/project/host.lock"):
        raise ConfigurationStoreError("STORE_BUSY")
    if _records(root)[0] != expected:
        raise ConfigurationStoreError("REVISION_CONFLICT")


def _finish_workspace(workspace):
    # Only files created by this operation, never recursive deletion, real
    # history pruning, ambiguous witnesses, or Windows permission changes.
    allowed = {"configuration-store.sqlite3", "request.json"}
    if workspace.resolve() != workspace or {p.name for p in workspace.iterdir()} != allowed:
        raise ConfigurationStoreError("UNSAFE_STORAGE")
    for name in sorted(allowed):
        path = workspace / name
        deployment._regular(path)
        path.unlink()
    workspace.rmdir()
    authority._sync_directory(workspace.parent)


@_guard
def recover_configuration_journal(
    root,
    *,
    installation_id,
    decision_id,
    current_revision,
    node_executable,
    acknowledge_offline=False,
    acknowledge_current_revision=False,
):
    """Recover one interrupted physical transaction, not an older logical head.

    The expected CURRENT revision must come from the administrator's trusted
    pre-interruption record, never a backup guess or activation anchor shortcut.
    Private staging is not a usable backup. Failed audits leave a deny-only
    witness. Once fence removal begins, errors mean uncertain publication and
    require explicit inspection, never cleanup/retry/authority repair.
    """
    if (
        acknowledge_offline is not True
        or acknowledge_current_revision is not True
        or any(
            type(value) is not str or not authority._ID.fullmatch(value)
            for value in (installation_id, decision_id, current_revision)
        )
    ):
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    root = checked_root(root)
    record = authority.read_decision_record(root)
    if record is None or record.as_dict()["state"] != "committed":
        raise ConfigurationStoreError("RECOVERY_REQUIRED")
    value = record.as_dict()
    if value["installation_id"] != installation_id or value["decision_id"] != decision_id:
        raise ConfigurationStoreError("INSTALLATION_MISMATCH")
    return _recover_offline(root, value, current_revision, node_executable)


def _recover_offline(root, value, current_revision, node_executable):
    publication_started = False
    try:
        with ExitStack() as leases:
            leases.enter_context(execution_access(root, exclusive=True))
            leases.enter_context(agent_access(root, exclusive=True))
            leases.enter_context(configuration_access(root, exclusive=True))
            for name in ("sync-job-queue", "captured-rq-execution"):
                leases.enter_context(runtime_execution_lock(root, name, timeout_seconds=0))
            deployment.require_idle(root)
            if os.path.lexists(root / "conexus/project/host.lock"):
                raise ConfigurationStoreError("STORE_BUSY")
            if authority.read_decision_record(root).as_dict() != value:
                raise ConfigurationStoreError("REVISION_CONFLICT")
            migration._require_node(node_executable)
            original = _bound_store(root, value, value["installation_id"])
            if not original._has_recovery_journal():
                raise ConfigurationStoreError("RECOVERY_REQUIRED")
            original._check_file(recovering=True)
            journal = original._path.with_name(original._path.name + "-journal")
            files = (original._path, journal)
            if sum(deployment._regular(path).st_size for path in files) > _MAX_RECOVERY_BYTES:
                raise ConfigurationStoreError("UNSUPPORTED_STORAGE")
            fingerprints = [deployment._digest(path) for path in files]
            workspace = root / authority.RECOVERY_NAMESPACE
            workspace.mkdir(mode=0o777 if os.name == "nt" else 0o700)
            authority._sync_directory(workspace.parent)
            request = workspace / "request.json"
            fd = os.open(
                request, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666 if os.name == "nt" else 0o600
            )
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "installation_id": value["installation_id"],
                        "decision_id": value["decision_id"],
                        "current_revision": current_revision,
                    },
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            for path in files:
                deployment._copy_file(path, workspace / path.name)
            staged = _bound_store(root, value, value["installation_id"])
            staged._path = workspace / original._path.name
            candidate = _safe_call(staged._recover)
            _validate_recovered(candidate, value, current_revision)
            if [deployment._digest(path) for path in files] != fingerprints or _records(root)[
                0
            ] != {"decision.json": value}:
                raise ConfigurationStoreError("UPDATE_FAILED")
            deployment.require_idle(root)
            restored = _safe_call(original._recover)
            _validate_recovered(restored, value, current_revision)
            if (
                restored.documents != candidate.documents
                or restored.selections != candidate.selections
                or restored._history != candidate._history
            ):
                raise ConfigurationStoreError("UPDATE_FAILED")
            _verify_node(root, restored, value, node_executable)
            _audit_final_authority(root, value, restored, current_revision)
            result = _result(
                "journal_recovered",
                action="normal_startup",
                backend="sqlite",
                record={**value, "revision": restored.revision},
            )
            publication_started = True
            _finish_workspace(workspace)
        return result
    except Exception:
        if publication_started:
            # Includes a directory-sync or lease-release error after rmdir.
            # Do not recreate a witness or claim that publication rolled back.
            raise ConfigurationStoreError("RECOVERY_PUBLICATION_UNCERTAIN") from None
        raise
