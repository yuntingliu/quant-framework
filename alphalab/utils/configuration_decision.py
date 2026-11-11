"""Inactive durable-decision protocol. No launcher imports or activation writer.

An absent namespace is legacy JSON compatibility, never SQLite discovery. Once
the namespace exists, a missing/partial record is an error, not JSON fallback.
Only prepared records can be published by this module. Committed records are
read/validation protocol for future integration, not permission to cut over.
Local trusted storage and cooperative maintenance leases are prerequisites.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

from alphalab.utils.configuration_agent import _auth_json, _owned_call
from alphalab.utils.configuration_maintenance import checked_root
from alphalab.utils.configuration_store import (
    ConfigurationStore,
    ConfigurationStoreError,
    _safe_call,
)

PROTOCOL_VERSION = 1
NAMESPACE = Path("app/secrets/configuration-decision")
RECOVERY_NAMESPACE = Path("app/secrets/configuration-recovery")
BACKUP_FENCE = Path("app/secrets/configuration-backup-only.json")
OWNER_WITNESS = Path("app/secrets/configuration-owner.json")
_RECORD = "decision.json"
_PENDING = "decision.pending"
_LIMIT = 16384
_ID = re.compile(r"[0-9a-f]{32}")
_HASH = re.compile(r"[0-9a-f]{64}")
_SERVICES = {"models", "search", "rqdata"}


def _fail(code="INVALID_CONFIGURATION"):
    raise ConfigurationStoreError(code)


def _root(root):
    location = checked_root(root)
    if not location.is_dir():
        _fail("UNSAFE_STORAGE")
    return location


def root_identity(root):
    """Bind a canonical explicit root without serializing its private path."""
    return _safe_call(lambda: _root_key(_root(root)))


def _root_key(root):
    return hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()


class DecisionRecord:
    """Detached non-secret metadata; construction requires strict decoding."""

    __slots__ = ("_wire",)

    def __init__(self):
        raise TypeError("Use parse_decision_record().")

    def __repr__(self):
        return "DecisionRecord(<validated metadata>)"

    def as_dict(self):
        return json.loads(self._wire)


def _parse(data, root_key):
    if type(root_key) is not str or not _HASH.fullmatch(root_key):
        _fail("INVALID_ARGUMENT")
    if type(data) not in {str, bytes} or len(data) > _LIMIT:
        _fail()
    value = _auth_json(data)
    fields = {
        "protocol_version",
        "state",
        "backend",
        "root_key",
        "installation_id",
        "decision_id",
        "revision",
        "evidence",
    }
    if type(value) is not dict or set(value) != fields:
        _fail()
    if type(value["protocol_version"]) is not int or value["protocol_version"] != PROTOCOL_VERSION:
        _fail("UNSUPPORTED_PROTOCOL")
    if value["state"] not in {"prepared", "committed"} or value["backend"] != "sqlite":
        _fail()
    if type(value["root_key"]) is not str or value["root_key"] != root_key:
        _fail("INSTALLATION_MISMATCH")
    if any(
        type(value[key]) is not str or not _ID.fullmatch(value[key])
        for key in ("installation_id", "decision_id", "revision")
    ):
        _fail()
    evidence = value["evidence"]
    if type(evidence) is not dict or set(evidence) != {
        "kind",
        "verification_id",
        "migration_revision",
        "store_protocol_version",
        "selection_kinds",
    }:
        _fail()
    if (
        evidence["kind"] != "legacy-exact-v1"
        or type(evidence["verification_id"]) is not str
        or not _ID.fullmatch(evidence["verification_id"])
        or type(evidence["migration_revision"]) is not str
        or evidence["migration_revision"] != value["revision"]
        or type(evidence["store_protocol_version"]) is not int
        or evidence["store_protocol_version"] != 1
    ):
        _fail()
    choices = evidence["selection_kinds"]
    if (
        type(choices) is not dict
        or set(choices) != _SERVICES
        or any(
            type(kind) is not str or kind not in {"profile", "environment", "none"}
            for kind in choices.values()
        )
    ):
        _fail()
    result = object.__new__(DecisionRecord)
    result._wire = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return result


def parse_decision_record(data, *, root_key):
    return _safe_call(lambda: _parse(data, root_key))


def _directory(path):
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.resolve() != path
        or (os.name != "nt" and info.st_mode & 0o077)
    ):
        _fail("UNSAFE_STORAGE")


def _file(path):
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or path.resolve() != path
        or before.st_size > _LIMIT
        or (os.name != "nt" and before.st_mode & 0o077)
    ):
        _fail("UNSAFE_STORAGE")
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        actual = os.fstat(stream.fileno())
        data = stream.read(_LIMIT + 1)
        after = os.fstat(stream.fileno())

    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    if (
        identity(before) != identity(actual)
        or identity(actual) != identity(after)
        or identity(after) != identity(path.lstat())
        or len(data) > _LIMIT
    ):
        _fail("UPDATE_FAILED")
    return data


def _read_unlocked(root):
    # Deny-only witnesses: neither file presence nor a pending recovery selects
    # a backend. Never turn damaged/backup authority into JSON compatibility.
    if any(os.path.lexists(root / path) for path in (RECOVERY_NAMESPACE, BACKUP_FENCE)):
        _fail("RECOVERY_REQUIRED")
    directory = root / NAMESPACE
    try:
        directory.lstat()
    except FileNotFoundError:
        if os.path.lexists(root / OWNER_WITNESS):
            _fail("RECOVERY_REQUIRED")
        return None
    _directory(directory)
    if {path.name for path in directory.iterdir()} != {_RECORD}:
        _fail("STORE_CORRUPT")
    return _parse(_file(directory / _RECORD), _root_key(root))


def read_decision_record(root):
    """Strict filesystem reader; caller coordinates leases and store validation.

    None means the namespace never existed under the legacy contract. It never
    means an invalid/partial namespace was repaired or replaced. No cache, file
    creation, SQLite inspection, or backend construction occurs in this reader.
    """
    return _safe_call(lambda: _read_unlocked(_root(root)))


def validate_committed_authority(root, *, installation_id=None, decision_id=None):
    """Audit the immutable anchor and its complete latest chain in one snapshot.

    Callers retain their actual leases through use. This does not authorize a
    restored database or attest provider-side revocation/whole-root provenance.
    """
    if any(
        expected is not None and (type(expected) is not str or not _ID.fullmatch(expected))
        for expected in (installation_id, decision_id)
    ):
        _fail("INVALID_ARGUMENT")
    record = read_decision_record(root)
    if record is None or record.as_dict()["state"] != "committed":
        _fail("INVALID_CONFIGURATION")
    value = record.as_dict()
    if installation_id is not None and value["installation_id"] != installation_id:
        _fail("INSTALLATION_MISMATCH")
    if decision_id is not None and value["decision_id"] != decision_id:
        _fail("REVISION_CONFLICT")

    def unchanged_anchor():
        store = ConfigurationStore._handle(root, 5)
        store._anchor = value["revision"]
        store._anchor_kinds = value["evidence"]["selection_kinds"]
        with store._session(time.monotonic() + 5) as session:
            session.execute("BEGIN")
            snapshot = store._current(session)
            if session.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                _fail("STORE_CORRUPT")
            return snapshot

    snapshot = _safe_call(unchanged_anchor, read_only=True)
    if snapshot.protocol_version != value["evidence"]["store_protocol_version"]:
        _fail("UNSUPPORTED_PROTOCOL")
    if snapshot.installation_id != value["installation_id"]:
        _fail("INSTALLATION_MISMATCH")

    def validate_native():
        # Integration validation over the SAME detached three-document snapshot,
        # not independent service reads. The storage protocol remains API-free.
        from apps.api.services.configuration_profile_service import ProfileSnapshot

        ProfileSnapshot(snapshot, {})

    _owned_call(validate_native)
    return value


def check_backend_authority(root, *, backend):
    """Fence JSON and explicitly inactive SQLite use under a caller-owned lease.

    A prepared record only preserves JSON authority; it is not a freshness
    receipt and legitimate JSON edits remain allowed. Every committed record
    blocks these compositions, including a matching inactive SQLite owner.
    Durable production composition is admitted separately by the actual owner.
    """
    if type(backend) is not str or backend not in {"json", "inactive_sqlite", "sqlite"}:
        _fail("INVALID_ARGUMENT")
    record = read_decision_record(root)
    if record is not None and record.as_dict()["state"] == "committed":
        validate_committed_authority(root)
        if backend != "sqlite":
            _fail("UNSUPPORTED_STORAGE")
    elif backend == "sqlite":
        _fail("INVALID_CONFIGURATION")


def _sync_directory(path):
    # Windows file fsync is supported; portable directory fsync is not. NTFS
    # atomic visibility is tested, physical power-loss durability is not claimed.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _require_absent(root):
    if _read_unlocked(root) is not None:
        _fail("REVISION_CONFLICT")


def _publish_prepared(offline, record, *, expected_decision_id=None):
    """Write once under an actual offline capability; committed writes forbidden.

    No effective activation primitive exists. Temporary remnants are retained
    as fail-closed evidence, never deleted/recovered automatically on failure.
    """

    from alphalab.utils.configuration_deployment import OfflineInstallation

    def publish():
        if type(offline) is not OfflineInstallation or type(record) is not DecisionRecord:
            _fail("INVALID_ARGUMENT")
        offline._check()
        root = _root(offline.root)
        value = _parse(record._wire, _root_key(root)).as_dict()
        if value["state"] != "prepared" or expected_decision_id is not None:
            _fail("REVISION_CONFLICT")
        _require_absent(root)
        directory = root / NAMESPACE
        if directory.parent.resolve() != directory.parent:
            _fail("UNSAFE_STORAGE")
        # Exclusive mkdir is also the durable namespace witness: any later
        # missing record or incomplete write cannot be mistaken for legacy JSON.
        directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        _directory(directory)
        _sync_directory(directory.parent)
        pending, target = directory / _PENDING, directory / _RECORD
        wire = record._wire.encode("utf-8")
        descriptor = os.open(
            pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(wire)
            stream.flush()
            os.fsync(stream.fileno())
        if _file(pending) != wire:
            _fail("UPDATE_FAILED")
        _parse(wire, _root_key(root))
        # Atomic, no-replace link publication. A crash between link and unlink
        # leaves a pending file and is rejected, including if final bytes exist.
        os.link(pending, target, follow_symlinks=False)
        _sync_directory(directory)
        pending.unlink()
        _sync_directory(directory)
        observed = _read_unlocked(root)
        if observed.as_dict() != value:
            _fail("UPDATE_FAILED")
        return observed

    return _safe_call(publish)


def _publish_committed(offline, expected):
    """Atomic CAS under exclusive offline leases; never erase the namespace.

    A pending file is fail-closed evidence. Do not automatically retry, remove
    remnants, or roll back if fsync/readback fails after the atomic replacement.
    """
    from alphalab.utils.configuration_deployment import OfflineInstallation

    def publish():
        if type(offline) is not OfflineInstallation or type(expected) is not DecisionRecord:
            _fail("INVALID_ARGUMENT")
        offline._check()
        root = _root(offline.root)
        current = _read_unlocked(root)
        if (
            current is None
            or current._wire != expected._wire
            or current.as_dict()["state"] != "prepared"
            or offline.backend != "json"
        ):
            _fail("REVISION_CONFLICT")
        original = _file(root / NAMESPACE / _RECORD)
        if _parse(original, _root_key(root))._wire != current._wire:
            _fail("REVISION_CONFLICT")
        value = current.as_dict()
        value["state"] = "committed"
        record = _parse(json.dumps(value), _root_key(root))
        directory = root / NAMESPACE
        pending, target = directory / _PENDING, directory / _RECORD
        descriptor = os.open(
            pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(record._wire.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(directory)
        if _file(target) != original or _file(pending) != record._wire.encode("utf-8"):
            _fail("REVISION_CONFLICT")
        os.replace(pending, target)
        _sync_directory(directory)
        observed = _read_unlocked(root)
        if observed is None or observed._wire != record._wire:
            _fail("UPDATE_FAILED")
        return observed

    return _safe_call(publish)
