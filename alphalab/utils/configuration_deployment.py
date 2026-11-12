"""Offline deployment preparation, never a backend activation or restore API.

The caller supplies a verified, now closed ConfigurationBackend and stops only
its owned services. Cooperative admission is fenced before stopping services;
maintenance and legacy execution locks must then be acquired before mutation.
Older releases/editors remain an operator responsibility. All backups, including
SQLite history, are private. Restore is deliberately unavailable in this phase.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
from contextlib import ExitStack, closing, contextmanager
from functools import wraps
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from alphalab.dataio.runtime import runtime_execution_lock
from alphalab.utils import configuration_store as storage
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_maintenance import (
    agent_access,
    checked_root,
    configuration_access,
    execution_access,
)

_CONFIG = Path("app/secrets/configuration-store.sqlite3")
_MANIFEST = "configuration-backup.json"
_MESSAGES = {
    "UNAVAILABLE": "Deployment preparation could not be completed.",
    "BUSY": "Execution or maintenance remains active; no offline operation is permitted.",
    "INVALID_BACKUP": "The backup is invalid, incomplete, or belongs to another installation.",
    "SOURCE_CHANGED": "The offline source changed; the backup was not published.",
    "BACKEND_MISMATCH": "A verified JSON backend is required for legacy deployment writes.",
    "INVALID_METADATA": "Provide supported, non-secret deployment metadata.",
    "RESTORE_REQUIRES_REVIEW": "Historical configuration restore requires a reviewed credential-revocation policy.",
}
_TERMINAL = {"succeeded", "completed", "failed", "cancelled"}


class DeploymentPreparationError(RuntimeError):
    def __init__(self, code="UNAVAILABLE"):
        self.code = code if type(code) is str and code in _MESSAGES else "UNAVAILABLE"
        super().__init__(_MESSAGES[self.code])


def protected(function):
    """Fresh static errors and the existing redacted interruption semantics."""

    @wraps(function)
    def guarded(*args, **kwargs):
        code, interruption = "UNAVAILABLE", None
        try:
            return function(*args, **kwargs)
        except BaseException as error:
            family = storage._interruption_family(type(error))
            interruption = (
                None
                if family is None
                else family(1 if family is SystemExit else "Deployment preparation interrupted.")
            )
            try:
                interruption = storage._redacted_interruption(error)
                if issubclass(type(error), DeploymentPreparationError):
                    code = storage._exception_field(error, "code")
                elif issubclass(type(error), storage.ConfigurationStoreError):
                    code = (
                        "BUSY"
                        if storage._exception_field(error, "code") == "STORE_BUSY"
                        else "UNAVAILABLE"
                    )
            except BaseException:
                pass
        storage._raise_redacted(interruption or DeploymentPreparationError(code))

    return guarded


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise DeploymentPreparationError("INVALID_BACKUP")
        result[key] = value
    return result


def _json(path):
    _regular(path)
    with path.open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise DeploymentPreparationError("INVALID_BACKUP")
    return json.loads(
        raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
    )


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.resolve() != path:
        raise DeploymentPreparationError("UNAVAILABLE")
    return info


@protected
def require_idle(root):
    """Read-only checks; unknown, queued and interrupted states are not offline."""
    root = checked_root(root)
    for name, table in (("alphalab.db", "backtest_jobs"), ("dataio.db", "sync_jobs")):
        path = root / "app" / name
        if not path.exists():
            continue
        _regular(path)
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists and any(
                row[0] not in _TERMINAL
                for row in connection.execute(f'SELECT status FROM "{table}"')
            ):
                raise DeploymentPreparationError("BUSY")
    state = root / "conexus/project/state.json"
    if state.exists():
        _regular(state)
        value = _json(state)
        runs = value.get("runs")
        if type(runs) is not list or any(
            type(run) is not dict or run.get("status") not in _TERMINAL for run in runs
        ):
            raise DeploymentPreparationError("BUSY")


class OfflineInstallation:
    """Thread-bound capability issued only while all offline leases are held."""

    __slots__ = ("_root", "_backend", "_installation_id", "_active", "_thread")

    def __init__(self):
        raise TypeError("Use coordinated_offline().")

    def __repr__(self):
        return "OfflineInstallation(<private>)"

    def __reduce_ex__(self, _protocol):
        raise TypeError("Offline deployment capabilities cannot be serialized.")

    @property
    def root(self):
        return self._root

    @property
    def backend(self):
        return self._backend

    @property
    def installation_id(self):
        return self._installation_id

    def _check(self):
        if not self._active or self._thread != threading.get_ident():
            raise DeploymentPreparationError("UNAVAILABLE")

    @protected
    def require_json_writer(self):
        from alphalab.utils.configuration_decision import check_backend_authority

        self._check()
        if self.backend != "json":
            raise DeploymentPreparationError("BACKEND_MISMATCH")
        check_backend_authority(self.root, backend="json")


@contextmanager
def coordinated_offline(decision, *, stop_services, timeout=5.0):
    """Gate new work, stop owned listeners, then require maintenance exclusion.

    An active SQLite owner is never closed or bypassed here. The explicit closed
    decision, not database presence or environment, determines writer policy.
    Shared job admission is nonblocking, so gate -> maintenance cannot deadlock
    a service attempting to admit a new job while it is shutting down.
    """
    with ExitStack() as stack:

        @protected
        def acquire():
            if type(decision) is not ConfigurationBackend or not decision._closed:
                raise DeploymentPreparationError("BUSY")
            if (
                type(timeout) not in {int, float}
                or not math.isfinite(timeout)
                or not 0 <= timeout <= 5
            ):
                raise ValueError
            root = checked_root(decision.root)
            deadline = time.monotonic() + timeout

            def remaining():
                # Same-tick floating-point subtraction may round above timeout.
                return min(timeout, max(0, deadline - time.monotonic()))

            stack.enter_context(execution_access(root, exclusive=True, timeout=remaining()))

            decision._check_authority()
            require_idle(root)
            stop_services()
            # Idle Agents do not hold execution admission. Their independent
            # lifetime fence must nevertheless be gone before any mutation.
            stack.enter_context(agent_access(root, exclusive=True, timeout=remaining()))
            stack.enter_context(configuration_access(root, exclusive=True, timeout=remaining()))
            decision._check_authority()
            # Old 2A children also retain these locks after API/owner death.
            for name in ("sync-job-queue", "captured-rq-execution"):
                stack.enter_context(runtime_execution_lock(root, name, timeout_seconds=remaining()))
            require_idle(root)
            if decision.mode == "sqlite":
                snapshot = storage.ConfigurationStore.open_existing(root).read_snapshot()
                if snapshot.installation_id != decision.public_identity()["installation_id"]:
                    raise DeploymentPreparationError("INVALID_BACKUP")
            value = object.__new__(OfflineInstallation)
            value._root, value._backend = root, decision.mode
            value._installation_id = decision.public_identity()["installation_id"]
            value._active, value._thread = True, threading.get_ident()
            return value

        token = acquire()
        try:
            yield token
        finally:
            token._active = False


def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o777 if os.name == "nt" else 0o700)
    if path.resolve() != path or (os.name != "nt" and path.stat().st_mode & 0o077):
        raise DeploymentPreparationError("UNAVAILABLE")


def _digest(path):
    _regular(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root):
    if root.resolve() != root or not stat.S_ISDIR(root.stat().st_mode):
        raise DeploymentPreparationError("INVALID_BACKUP")

    def enumeration_failed(error):
        raise error

    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=enumeration_failed):
        parent = Path(directory)
        for name in dirs[:]:
            path = parent / name
            if path.is_symlink() or path.resolve() != path:
                raise DeploymentPreparationError("UNAVAILABLE")
            if name in {".locks", "__pycache__"}:
                dirs.remove(name)
        for name in names:
            path = parent / name
            info = _regular(path)
            if name.endswith(("-wal", "-shm", "-journal")):
                base = path.with_name(name.rsplit("-", 1)[0])
                if not base.exists() or (name.endswith("-journal") and info.st_size):
                    raise DeploymentPreparationError("INVALID_BACKUP")
                _regular(base)
            if name.endswith((".lock", "-shm")):
                continue
            files[path.relative_to(root).as_posix()] = (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                _digest(path),
            )
    return files


def _copy_file(source, target):
    _regular(source)
    _mkdir(target.parent)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666 if os.name == "nt" else 0o600)
    with os.fdopen(fd, "wb") as output, source.open("rb") as stream:
        shutil.copyfileobj(stream, output)
        output.flush()
        os.fsync(output.fileno())


def _database(source):
    with source.open("rb") as stream:
        return (
            source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
            or stream.read(16) == b"SQLite format 3\0"
        )


def _sqlite_backup(source, target, deadline):
    _regular(source)
    _mkdir(target.parent)
    descriptor = os.open(
        target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666 if os.name == "nt" else 0o600
    )
    os.close(descriptor)
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as origin,
        closing(sqlite3.connect(target.as_uri() + "?mode=rw", uri=True, timeout=0.1)) as saved,
    ):
        origin.execute("PRAGMA query_only=ON")
        origin.execute("PRAGMA trusted_schema=OFF")
        saved.execute("PRAGMA synchronous=EXTRA")

        def progress(_status, _remaining, _total):
            if time.monotonic() >= deadline:
                raise DeploymentPreparationError("BUSY")

        origin.backup(saved, pages=128, progress=progress, sleep=0.01)
        if saved.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
            raise DeploymentPreparationError("INVALID_BACKUP")
        if (
            saved.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or saved.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise DeploymentPreparationError("INVALID_BACKUP")


def _configuration(path, original_root):
    # Keep the original root binding while validating a private backup at a
    # different path. This never opens it as an activated, relocated installation.
    handle = storage.ConfigurationStore._handle(original_root, 5)
    handle._path = path
    handle = handle.with_history_checks()
    from alphalab.utils import configuration_decision as authority

    namespace = path.parents[2] / authority.NAMESPACE
    namespace_present = os.path.lexists(namespace)
    if namespace_present:
        authority._directory(namespace)
        if {entry.name for entry in namespace.iterdir()} != {"decision.json"}:
            raise DeploymentPreparationError("INVALID_BACKUP")
        record = authority._parse(
            authority._file(namespace / "decision.json"), handle._root_key
        ).as_dict()
        if record["state"] == "committed":
            handle._anchor, handle._anchor_kinds = (
                record["revision"],
                record["evidence"]["selection_kinds"],
            )
    snapshot = handle.read_snapshot()
    if namespace_present and record["installation_id"] != snapshot.installation_id:
        raise DeploymentPreparationError("INVALID_BACKUP")
    return {
        "protocol_version": storage.PROTOCOL_VERSION,
        "installation_id": snapshot.installation_id,
        "revision": snapshot.revision,
    }


_PROFILE_CREDENTIALS = {
    "models": (Path("conexus/secrets/model-providers.json"), "api_key"),
    "search": (Path("conexus/secrets/search-providers.json"), "brave_api_key"),
    "rqdata": (Path("app/secrets/rqdata-profiles.json"), "password"),
}
_ENV_CREDENTIALS = {
    "CONEXUS_MODEL_API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "RQ_PASSWORD",
    "ALPHALAB_WEB_PASSWORD",
    "CONEXUS_PUBLICATION_WORKSPACE_TOKEN",
    "CONEXUS_WEB_TOKEN",
    "TUNNEL_TOKEN",
}


def _saved_credentials(root, config, original_root):
    """Read only explicit backup inputs, never live environment or providers.

    Retained SQLite generations and inactive native profiles are private too.
    No history values leave this helper except as in-memory matching canaries.
    """
    credentials = set()

    def add(value):
        if type(value) is str and value.strip():
            credentials.update((value, value.strip()))

    def document(service, value):
        rows = (
            [value]
            if service == "search" and value.get("version") == 1
            else value.get("profiles", [])
        )
        for row in rows:
            add(row.get(_PROFILE_CREDENTIALS[service][1], ""))

    for service, (relative, _field) in _PROFILE_CREDENTIALS.items():
        path = root / relative
        if path.exists():
            document(service, _json(path))
    path = root / _CONFIG
    if path.exists():
        handle = storage.ConfigurationStore._handle(original_root, 5)
        handle._path = path
        snapshot = handle.with_history_checks().read_snapshot()
        for documents in snapshot.extract_history_private():
            for service, value in documents.items():
                document(service, value)
    if config is not None:
        path = config / "alphalab.env"
        if path.exists():
            if _regular(path).st_size > 8 * 1024 * 1024:
                raise DeploymentPreparationError("INVALID_METADATA")
            with path.open("r", encoding="utf-8-sig") as stream:
                for line in stream:
                    key, separator, raw = line.strip().removeprefix("export ").partition("=")
                    if not separator or key.strip() not in _ENV_CREDENTIALS:
                        continue
                    raw = raw.strip()
                    try:
                        value = json.loads(raw)
                    except ValueError:
                        value = raw.strip("\"'")
                    add(value if type(value) is str else raw)
    return credentials


def _reject_metadata_credentials(metadata, credentials):
    # Same original/runtime-trimmed substring and five-decode convention as
    # configuration_profile_service. Do not exempt caller-supplied identifiers.
    if not credentials:
        return
    for value in metadata.values():
        if type(value) is not str:
            continue
        for _ in range(6):
            if any(secret in value for secret in credentials):
                raise DeploymentPreparationError("INVALID_METADATA")
            decoded = unquote(value, errors="replace")
            if decoded == value:
                break
            value = decoded
        else:
            raise DeploymentPreparationError("INVALID_METADATA")


def _prepare_metadata(metadata, root, config, original_root):
    if metadata is None:
        return None
    if type(metadata) is not dict:
        raise DeploymentPreparationError("INVALID_METADATA")
    # All allowed values are immutable primitives; detach before any I/O.
    value = metadata.copy()
    if (
        set(value) != {"release", "created_at", "tag", "old_ssh_registered", "offline"}
        or any(
            type(value[key]) is not str or len(value[key]) > 4096
            for key in ("release", "created_at", "tag")
        )
        or type(value["old_ssh_registered"]) is not bool
        or value["offline"] is not True
    ):
        raise DeploymentPreparationError("INVALID_METADATA")
    _reject_metadata_credentials(value, _saved_credentials(root, config, original_root))
    if value["tag"] not in {"manual", "before-rollback"} and not re.fullmatch(
        r"before-local-[0-9a-f]{8}", value["tag"]
    ):
        raise DeploymentPreparationError("INVALID_METADATA")
    return value


@protected
def create_backup(offline, destination, *, config=None, plists=(), metadata=None, timeout=30.0):
    """Publish a manifest last; partial private directories are never complete.

    SQLite's backup API includes WAL commits. Sidecar files are never copied as
    independent databases. Configuration protocol 1 still requires DELETE mode;
    WAL support here concerns other application databases, not a protocol change.
    """
    if type(offline) is not OfflineInstallation:
        raise ValueError
    offline._check()
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError
    require_idle(offline.root)
    destination = checked_root(destination)
    trees = {"runtime": offline.root}
    if config is not None:
        trees["config"] = checked_root(config)
    if (
        any(
            destination.is_relative_to(root) or root.is_relative_to(destination)
            for root in trees.values()
        )
        or destination.exists()
    ):
        raise DeploymentPreparationError("INVALID_BACKUP")
    before = {name: _inventory(root) for name, root in trees.items()}
    identity = (
        _configuration(offline.root / _CONFIG, offline.root)
        if (offline.root / _CONFIG).exists()
        else None
    )
    if offline.backend == "sqlite" and (
        identity is None or identity["installation_id"] != offline.installation_id
    ):
        raise DeploymentPreparationError("INVALID_BACKUP")
    metadata = _prepare_metadata(metadata, offline.root, trees.get("config"), offline.root)
    _mkdir(destination.parent)
    destination.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    deadline, entries = time.monotonic() + timeout, []
    for name, files in before.items():
        _mkdir(destination / name)
        for relative in files:
            if relative.endswith(("-wal", "-journal")):
                continue
            source, target = trees[name] / relative, destination / name / relative
            database = _database(source)
            if database:
                _sqlite_backup(source, target, deadline)
            else:
                _copy_file(source, target)
            if time.monotonic() >= deadline:
                raise DeploymentPreparationError("BUSY")
            entries.append(
                {
                    "path": target.relative_to(destination).as_posix(),
                    "sha256": _digest(target),
                    "sqlite": database,
                }
            )
    for source in plists:
        source = checked_root(source)
        if not re.fullmatch(r"net\.acetoken\.alphalab-dev3[^/\\]*\.plist", source.name):
            raise ValueError
        target = destination / "plists" / source.name
        _copy_file(source, target)
        entries.append(
            {
                "path": target.relative_to(destination).as_posix(),
                "sha256": _digest(target),
                "sqlite": False,
            }
        )
    if metadata is not None:
        path = destination / "snapshot.json"
        descriptor = os.open(
            path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666 if os.name == "nt" else 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(metadata, output, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        entries.append({"path": "snapshot.json", "sha256": _digest(path), "sqlite": False})
    after = {name: _inventory(root) for name, root in trees.items()}
    if before != after:
        raise DeploymentPreparationError("SOURCE_CHANGED")
    require_idle(offline.root)
    copied = _configuration(destination / "runtime" / _CONFIG, offline.root) if identity else None
    if copied != identity:
        raise DeploymentPreparationError("SOURCE_CHANGED")
    if identity is not None:
        from alphalab.utils.configuration_decision import BACKUP_FENCE

        marker = destination / "runtime" / BACKUP_FENCE
        _mkdir(marker.parent)
        descriptor = os.open(
            marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666 if os.name == "nt" else 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(
                {"version": 1, "purpose": "private-backup-only", "configuration": identity}, output
            )
            output.flush()
            os.fsync(output.fileno())
        entries.append(
            {
                "path": marker.relative_to(destination).as_posix(),
                "sha256": _digest(marker),
                "sqlite": False,
            }
        )
    value = {
        "version": 1,
        "backend": offline.backend,
        "root_key": hashlib.sha256(os.path.normcase(str(offline.root)).encode()).hexdigest(),
        "configuration": identity,
        "files": entries,
    }
    report = _verify_candidate(offline, destination, value)
    _publish_complete_manifest(destination, value)
    return report


def _publish_complete_manifest(destination, candidate):
    """One same-directory atomic rename publishes an already verified candidate.

    Unpublished .pending files are private incomplete artifacts, never accepted
    by verify_backup(). No power-loss/directory-fsync guarantee is implied.
    """
    target = destination / _MANIFEST
    if target.exists() or target.is_symlink():
        raise DeploymentPreparationError("INVALID_BACKUP")
    pending = destination / (".configuration-backup-" + uuid4().hex + ".pending")
    descriptor = os.open(
        pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666 if os.name == "nt" else 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump({**candidate, "complete": True}, output, allow_nan=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(pending, target)


def _verify_candidate(offline, destination, candidate):
    """Validate all candidate files without requiring or creating a completion marker."""
    return _verify_contents(offline, destination, candidate, published=False)


@protected
def verify_backup(offline, destination):
    if type(offline) is not OfflineInstallation:
        raise ValueError
    offline._check()
    destination = checked_root(destination)
    original = _digest(destination / _MANIFEST)
    manifest = _json(destination / _MANIFEST)
    if type(manifest) is not dict or manifest.get("complete") is not True:
        raise DeploymentPreparationError("INVALID_BACKUP")
    manifest.pop("complete")
    result = _verify_contents(offline, destination, manifest, published=True)
    if _digest(destination / _MANIFEST) != original:
        raise DeploymentPreparationError("SOURCE_CHANGED")
    return result


def _verify_contents(offline, destination, manifest, *, published):
    if (
        type(manifest) is not dict
        or set(manifest) != {"version", "backend", "root_key", "configuration", "files"}
        or type(manifest["version"]) is not int
        or manifest["version"] != 1
        or manifest["backend"] != offline.backend
        or manifest["root_key"]
        != hashlib.sha256(os.path.normcase(str(offline.root)).encode()).hexdigest()
    ):
        raise DeploymentPreparationError("INVALID_BACKUP")
    entries = manifest["files"]
    if type(entries) is not list:
        raise DeploymentPreparationError("INVALID_BACKUP")
    named = set()
    for entry in entries:
        if (
            type(entry) is not dict
            or set(entry) != {"path", "sha256", "sqlite"}
            or type(entry["path"]) is not str
            or type(entry["sqlite"]) is not bool
            or type(entry["sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            or "\\" in entry["path"]
            or ":" in entry["path"]
            or any(part in {"", ".", ".."} for part in entry["path"].split("/"))
        ):
            raise DeploymentPreparationError("INVALID_BACKUP")
        path = destination / entry["path"]
        if (
            entry["path"] in named
            or not path.is_relative_to(destination)
            or path.resolve() != path
            or _digest(path) != entry["sha256"]
        ):
            raise DeploymentPreparationError("INVALID_BACKUP")
        named.add(entry["path"])
        if entry["sqlite"]:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                if (
                    connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
                    or connection.execute("PRAGMA foreign_key_check").fetchone()
                ):
                    raise DeploymentPreparationError("INVALID_BACKUP")
    inventory = _inventory(destination)
    if set(inventory) != named | ({_MANIFEST} if published else set()):
        raise DeploymentPreparationError("INVALID_BACKUP")
    if any(inventory[entry["path"]][-1] != entry["sha256"] for entry in entries):
        raise DeploymentPreparationError("SOURCE_CHANGED")
    if "snapshot.json" in named:
        _prepare_metadata(
            _json(destination / "snapshot.json"),
            destination / "runtime",
            destination / "config" if (destination / "config").exists() else None,
            offline.root,
        )
    identity = manifest["configuration"]
    actual = (
        _configuration(destination / "runtime" / _CONFIG, offline.root)
        if (destination / "runtime" / _CONFIG).exists()
        else None
    )
    if actual != identity or (
        offline.backend == "sqlite"
        and (actual is None or actual["installation_id"] != offline.installation_id)
    ):
        raise DeploymentPreparationError("INVALID_BACKUP")
    from alphalab.utils.configuration_decision import BACKUP_FENCE

    fence = destination / "runtime" / BACKUP_FENCE
    if fence.exists():
        value = _json(fence)
        if (
            value != {"version": 1, "purpose": "private-backup-only", "configuration": identity}
            or type(value["version"]) is not int
        ):
            raise DeploymentPreparationError("INVALID_BACKUP")
    _reject_metadata_credentials(
        {str(index): entry["path"] for index, entry in enumerate(entries)},
        _saved_credentials(
            destination / "runtime",
            destination / "config" if (destination / "config").exists() else None,
            offline.root,
        ),
    )
    return {
        "verified": True,
        "file_count": len(named),
        "backend": offline.backend,
        "configuration": identity,
        "restorable": False,
        "freshness_verified": False,
        "credential_revocation_verified": False,
        "rollback_provenance_verified": False,
    }


@protected
def inspect_configuration_backup(root, destination, *, installation_id):
    """Read-only integrity/identity check, independent of active backend choice.

    Matching IDs/hashes never authorize restore or prove freshness. An explicit
    expected installation ID is required even when live authority is missing.
    """
    from types import SimpleNamespace

    if type(installation_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", installation_id):
        raise DeploymentPreparationError("INVALID_BACKUP")
    root, destination = checked_root(root), checked_root(destination)
    original = _digest(destination / _MANIFEST)
    manifest = _json(destination / _MANIFEST)
    if (
        type(manifest) is not dict
        or manifest.pop("complete", None) is not True
        or manifest.get("backend") not in {"json", "sqlite"}
    ):
        raise DeploymentPreparationError("INVALID_BACKUP")
    claim = SimpleNamespace(root=root, backend=manifest["backend"], installation_id=installation_id)
    result = _verify_contents(claim, destination, manifest, published=True)
    if (
        result["configuration"] is None
        or result["configuration"]["installation_id"] != installation_id
    ):
        raise DeploymentPreparationError("INVALID_BACKUP")
    if _digest(destination / _MANIFEST) != original:
        raise DeploymentPreparationError("SOURCE_CHANGED")
    return result


@protected
def reject_restore(*_args, **_kwargs):
    """No side effects, including no service stop or directory rename."""
    raise DeploymentPreparationError("RESTORE_REQUIRES_REVIEW")


@protected
def write_pending_model(offline, profile):
    """Native JSON only; a staged SQLite file never selects or receives writes."""
    if type(offline) is not OfflineInstallation:
        raise ValueError
    offline.require_json_writer()
    from apps.api.services.model_provider_service import validate_profile

    profile = dict(profile)
    profile.pop("activate", None)
    profile = validate_profile(profile)
    path = offline.root / "conexus/secrets/model-providers.json"
    _mkdir(path.parent)
    if path.exists():
        _regular(path)
    temporary = path.with_name(".pending-" + uuid4().hex)
    descriptor = os.open(
        temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666 if os.name == "nt" else 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump({"version": 1, "active_id": profile["id"], "profiles": [profile]}, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    return {"saved": True, "staged_revalidation_required": (offline.root / _CONFIG).exists()}
