"""Isolated, private SQLite configuration generations (storage protocol 1).

No live profile services, environment resolution, legacy adoption, or provider
connections belong here. The caller supplies validated native documents and a
pure update callback. Structural checks below do not replace provider validation
or Phase 4A's separate, credential-free transfer schema.

    store = ConfigurationStore.initialize(root, documents=docs, selections=choices)
    snapshot = ConfigurationStore.open_existing(root).read_snapshot()

    def change(current):
        documents = current.documents
        documents["models"]["profiles"][0]["name"] = "Research"
        return documents, current.selections

    next_snapshot = store.update(change, expected_revision=snapshot.revision)

``change(snapshot)`` returns ``(documents, selections)``. Returned dictionaries
are fresh copies; snapshots are immutable and hide their payload in printable
representations. Explicit document access still returns credentials: never pass
these private values to public serializers, diagnostic locals, or loggers.

Reads use SQLite mode=ro, never create directories or databases, and do not
recover hot journals. Explicit ``recover_existing`` aborts interrupted SQLite
transactions; it never restores an older committed configuration. Initialization
uses exclusive file creation and never overwrites an existing file. A failed
initialization can leave an uninitialized private file; automatic replacement,
repair, and cleanup of such files are deliberately not provided.

Only local filesystems with working SQLite locking and flush semantics are
supported. DELETE journaling and synchronous=EXTRA are required for writers.
POSIX private-directory/file permissions are checked; Windows inherits existing
directory protection without ACL or chmod changes. Windows/POSIX power-loss
durability still depends on the OS, filesystem, and storage hardware.

All generations contain secrets. Retention, cleanup, backups, relocation,
credential revocation across history, and logical rollback require a separately
reviewed cutover policy. There is no history/rollback API or automatic pruning.
Callbacks must not reenter any store, resolve live settings, or perform I/O.
Lock timeouts bound waiting, not arbitrary callback execution or filesystem I/O.
After an uncertain commit acknowledgement, read the current revision rather
than automatically replaying a callback.

Interruptions propagate as fresh, redacted standard exceptions, without the
original args, custom attributes, traceback, or exception chain. Standard
cancellation/interrupt families are preserved; unknown BaseException subclasses
become BaseException. SystemExit retains only a plain integer or None status;
other exit payloads become status 1. Caller-supplied diagnostic locals remain
private and must never be logged.

Exception classification uses actual built-in types and native exception storage,
never Python instance descriptors. If classification fails, a fresh redacted
exception still propagates, preserving the original interruption family.

Explicit recovery checks a journal's private regular-file location and initial
signature before deferring database-header validation. SQLite alone interprets
and replays rollback records, including incomplete trailing segments. Afterwards,
the restored header, integrity, metadata and installation binding must pass
validation. This interface never creates attached-database/super-journal
transactions. Corruption rejected by SQLite or restored-state validation fails
closed; this is not authentication against malicious journal/database edits.
Externally removed journals and tampering that leaves a consistent SQLite state
cannot always be detected. A trusted, private local directory is required.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
from asyncio import CancelledError as AsyncCancelledError
from concurrent.futures import CancelledError as FutureCancelledError
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, TypeVar
from uuid import uuid4

PROTOCOL_VERSION = 1
_APPLICATION_ID = 0x414C4346  # ALCF; SQLite header offset 68.
_RELATIVE_PATH = Path("app/secrets/configuration-store.sqlite3")
_SERVICES = ("models", "search", "rqdata")
_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_OPAQUE_ID = re.compile(r"[0-9a-f]{32}")
_MAX_BYTES = 4 * 1024 * 1024
_MAX_NODES = 32768
_MAX_DEPTH = 32
_INTERRUPTED = "Configuration-store operation interrupted."
_EXCEPTION_GROUP = getattr(builtins, "BaseExceptionGroup", None)  # Python 3.11+.
_EXCEPTION_ATTRIBUTES = BaseException.__dict__["__dict__"]
_JOURNAL_MAGIC = b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"
_MESSAGES = {
    "INVALID_ARGUMENT": "Check the configuration-store arguments.",
    "INVALID_CONFIGURATION": "Provide complete validated native settings and selection intents.",
    "STORE_EXISTS": "The configuration store already exists.",
    "STORE_MISSING": "The configuration store has not been initialized.",
    "STORE_CORRUPT": "The configuration store is invalid or incomplete.",
    "UNSUPPORTED_PROTOCOL": "The configuration-store protocol is not supported.",
    "UNSUPPORTED_STORAGE": "The required SQLite storage settings are unavailable.",
    "UNSAFE_STORAGE": "The configuration-store location is not private or supported.",
    "STORE_BUSY": "The configuration store is busy. Retry the operation.",
    "STORE_UNAVAILABLE": "The configuration store could not be accessed.",
    "RECOVERY_REQUIRED": "The configuration store requires explicit transaction recovery.",
    "REVISION_CONFLICT": "The configuration revision has changed.",
    "INSTALLATION_MISMATCH": "The configuration store belongs to another installation.",
    "UPDATE_FAILED": "The configuration update was rejected.",
    "REENTRANT_ACCESS": "Configuration-store callbacks must not reenter storage.",
}


class ConfigurationStoreError(RuntimeError):
    """A static code/message only: no paths, payloads, or underlying exceptions."""

    def __init__(self, code: str):
        self.code = code if type(code) is str and code in _MESSAGES else "STORE_UNAVAILABLE"
        super().__init__(_MESSAGES[self.code])

    def __reduce__(self):
        return type(self), (self.code,)


def _fail(code: str):
    raise ConfigurationStoreError(code)


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_LOCAL = threading.local()
_T = TypeVar("_T")


def _after_fork():
    global _LOCKS, _LOCKS_GUARD, _LOCAL
    _LOCKS, _LOCKS_GUARD, _LOCAL = {}, threading.Lock(), threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _exception_field(exc: BaseException, name: str, default=None):
    # Bypass __getattribute__, properties, shadow __dict__ and dict-subclass
    # overrides. Compare only exact strings so hostile dictionary keys cannot
    # run __eq__ or __hash__ during classification either.
    for key, value in dict.items(_EXCEPTION_ATTRIBUTES.__get__(exc)):
        if type(key) is str and key == name:
            return value
    return default


def _sqlite_error(exc: sqlite3.Error, *, read_only: bool = False) -> str:
    code = _exception_field(exc, "sqlite_errorcode", 0)
    arguments = BaseException.args.__get__(exc)
    if type(code) is not int:
        code = 0
    if code in {264, 776}:  # READONLY_RECOVERY / READONLY_ROLLBACK.
        return "RECOVERY_REQUIRED"
    if code & 255 in {5, 6}:  # BUSY / LOCKED, including extended codes.
        return "STORE_BUSY"
    if not code and len(arguments) == 1 and type(arguments[0]) is str:
        # Python 3.10 has no sqlite_errorcode attribute. Inspect known SQLite
        # diagnostics only for classification; never retain or echo them.
        message = arguments[0]
        if message in {"database is locked", "database table is locked"}:
            return "STORE_BUSY"
        if read_only and message == "attempt to write a readonly database":
            return "RECOVERY_REQUIRED"
        if message in {
            "database disk image is malformed",
            "file is not a database",
        } or message.startswith(("no such table: ", "no such column: ")):
            return "STORE_CORRUPT"
    if code & 255 in {1, 11, 17, 24, 26}:
        return "STORE_CORRUPT"
    return "STORE_UNAVAILABLE"


def _interruption_family(exception_type):
    """Inspect the actual type, never an instance's spoofable __class__."""
    for kind in (
        AsyncCancelledError,
        FutureCancelledError,
        KeyboardInterrupt,
        SystemExit,
        GeneratorExit,
        InterruptedError,
    ):
        if issubclass(exception_type, kind):
            return kind
    return None if issubclass(exception_type, Exception) else BaseException


def _redacted_interruption(exc: BaseException, depth=0) -> BaseException | None:
    """Return only fresh standard exceptions; never keep the original object."""
    if depth > _MAX_DEPTH:
        return BaseException(_INTERRUPTED)
    if _EXCEPTION_GROUP is not None and issubclass(type(exc), _EXCEPTION_GROUP):
        children = [
            _redacted_interruption(child, depth + 1)
            for child in _EXCEPTION_GROUP.exceptions.__get__(exc)
        ]
        if any(child is not None for child in children):
            return _EXCEPTION_GROUP(
                _INTERRUPTED,
                [child if child is not None else Exception(_INTERRUPTED) for child in children],
            )
        return None
    kind = _interruption_family(type(exc))
    if kind is SystemExit:
        status = SystemExit.code.__get__(exc)
        return SystemExit(status if status is None or type(status) is int else 1)
    return None if kind is None else kind(_INTERRUPTED)


def _raise_redacted(error: BaseException):
    try:
        raise error from None
    except BaseException:
        # Python also attaches a caller's active exception as context. Clear it
        # after raising, then use bare re-raise so it cannot be reattached.
        error.__context__ = None
        raise


def _safe_call(action: Callable[[], _T], *, read_only: bool = False) -> _T:
    if getattr(_LOCAL, "active", False):
        _fail("REENTRANT_ACCESS")
    _LOCAL.active = True
    code = None
    interruption = None
    try:
        try:
            result = action()
        except BaseException as exc:
            kind = _interruption_family(type(exc))
            fallback = None if kind is None else kind(1 if kind is SystemExit else _INTERRUPTED)
            try:
                interruption = _redacted_interruption(exc)
                if interruption is None:
                    if issubclass(type(exc), ConfigurationStoreError):
                        value = _exception_field(exc, "code")
                        code = (
                            value
                            if type(value) is str and value in _MESSAGES
                            else "STORE_UNAVAILABLE"
                        )
                    elif issubclass(type(exc), sqlite3.Error):
                        code = _sqlite_error(exc, read_only=read_only)
                    else:
                        code = "STORE_UNAVAILABLE"
            except BaseException:
                # A classifier failure must never escape with the original
                # sensitive exception as its context. Preserve the actual
                # interruption category even when richer inspection fails.
                interruption, code = fallback, "STORE_UNAVAILABLE"
    finally:
        _LOCAL.active = False
    if interruption is not None:
        # Raise outside the handler so no raw exception survives in the chain.
        _raise_redacted(interruption)
    if code is not None:
        # Raise outside except: decoder errors retain documents, and callbacks
        # and filesystem/SQLite exceptions may contain credentials or paths.
        _raise_redacted(ConfigurationStoreError(code))
    return result


def _check_json(value: object):
    pending = [(value, 0, frozenset())]
    count = size = 0
    while pending:
        item, depth, ancestors = pending.pop()
        count += 1
        if count > _MAX_NODES or depth > _MAX_DEPTH:
            _fail("INVALID_CONFIGURATION")
        if type(item) in {dict, list}:
            if id(item) in ancestors or len(item) > _MAX_NODES:
                _fail("INVALID_CONFIGURATION")
            ancestors = ancestors | {id(item)}
            children = list(item.keys()) + list(item.values()) if type(item) is dict else item
            if type(item) is dict and any(type(key) is not str for key in item):
                _fail("INVALID_CONFIGURATION")
            pending.extend((child, depth + 1, ancestors) for child in children)
            size += len(item) * 2 + 2
        elif type(item) is str:
            if len(item) > _MAX_BYTES or any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                _fail("INVALID_CONFIGURATION")
            size += len(item.encode("utf-8"))
        elif type(item) is int:
            if item.bit_length() > 64:
                _fail("INVALID_CONFIGURATION")
        elif type(item) is float:
            if not math.isfinite(item):
                _fail("INVALID_CONFIGURATION")
        elif type(item) not in {bool, type(None)}:
            _fail("INVALID_CONFIGURATION")
        if size > _MAX_BYTES:
            _fail("INVALID_CONFIGURATION")


def _identifier(value: object) -> bool:
    return type(value) is str and _PROFILE_ID.fullmatch(value) is not None


def _encode(documents: object, selections: object) -> tuple[str, ...]:
    _check_json([documents, selections])
    if (
        type(documents) is not dict
        or set(documents) != set(_SERVICES)
        or type(selections) is not dict
        or set(selections) != set(_SERVICES)
    ):
        _fail("INVALID_CONFIGURATION")
    for service in _SERVICES:
        document, choice = documents[service], selections[service]
        if type(document) is not dict or type(document.get("version")) is not int:
            _fail("INVALID_CONFIGURATION")
        if document["version"] not in ((1, 2) if service == "search" else (1,)):
            _fail("INVALID_CONFIGURATION")
        if service == "search" and document["version"] == 1:
            if type(document.get("provider")) is not str or document["provider"] not in {
                "brave",
                "openrouter",
                "off",
            }:
                _fail("INVALID_CONFIGURATION")
            identifiers, active = {"default"}, "default"
        else:
            profiles = document.get("profiles")
            if type(profiles) is not list or "active_id" not in document:
                _fail("INVALID_CONFIGURATION")
            identifiers = set()
            for profile in profiles:
                if type(profile) is not dict or not _identifier(profile.get("id")):
                    _fail("INVALID_CONFIGURATION")
                if profile["id"] in identifiers:
                    _fail("INVALID_CONFIGURATION")
                identifiers.add(profile["id"])
            active = document["active_id"]
            if active is not None and (not _identifier(active) or active not in identifiers):
                _fail("INVALID_CONFIGURATION")
        if type(choice) is not dict or type(choice.get("kind")) is not str:
            _fail("INVALID_CONFIGURATION")
        kind = choice["kind"]
        if kind == "profile":
            if (
                set(choice) != {"kind", "id"}
                or not _identifier(choice["id"])
                or choice["id"] not in identifiers
                or choice["id"] != active
            ):
                _fail("INVALID_CONFIGURATION")
        elif kind not in {"environment", "none"} or set(choice) != {"kind"} or active is not None:
            _fail("INVALID_CONFIGURATION")
    texts = tuple(
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        for value in [*(documents[service] for service in _SERVICES), selections]
    )
    if sum(len(text.encode("utf-8")) for text in texts) > _MAX_BYTES:
        _fail("INVALID_CONFIGURATION")
    return texts


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("STORE_CORRUPT")
        result[key] = value
    return result


def _decode(text):
    if type(text) is not str or len(text) > _MAX_BYTES:
        _fail("STORE_CORRUPT")
    return json.loads(text, object_pairs_hook=_pairs)


class ConfigurationSnapshot:
    """Immutable private snapshot; document/selection properties return fresh copies."""

    __slots__ = ("_installation_id", "_revision", "_texts")

    def __init__(self, installation_id: str, revision: str, texts: tuple[str, ...]):
        object.__setattr__(self, "_installation_id", installation_id)
        object.__setattr__(self, "_revision", revision)
        object.__setattr__(self, "_texts", tuple(texts))

    def __setattr__(self, name, value):
        raise AttributeError("Configuration snapshots are immutable.")

    def __repr__(self):
        return "ConfigurationSnapshot(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Private configuration snapshots cannot be serialized implicitly.")

    @property
    def protocol_version(self) -> int:
        return PROTOCOL_VERSION

    @property
    def installation_id(self) -> str:
        return self._installation_id

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def documents(self) -> dict:
        return {service: json.loads(text) for service, text in zip(_SERVICES, self._texts)}

    @property
    def selections(self) -> dict:
        return json.loads(self._texts[3])


class _Session:
    def __init__(self, connection: sqlite3.Connection, deadline: float):
        self.connection, self.deadline = connection, deadline

    def execute(self, sql: str, parameters=()):
        remaining_ms = max(0, math.ceil((self.deadline - time.monotonic()) * 1000))
        self.connection.execute(f"PRAGMA busy_timeout={remaining_ms}")
        return self.connection.execute(sql, parameters)


_SCHEMA = (
    """CREATE TABLE generations (
        ordinal INTEGER PRIMARY KEY,
        revision TEXT NOT NULL UNIQUE,
        parent_revision TEXT REFERENCES generations(revision),
        models TEXT NOT NULL, search TEXT NOT NULL, rqdata TEXT NOT NULL,
        selections TEXT NOT NULL
    )""",
    """CREATE TABLE store_metadata (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        protocol_version INTEGER NOT NULL,
        installation_id TEXT NOT NULL,
        root_key TEXT NOT NULL,
        current_revision TEXT NOT NULL REFERENCES generations(revision)
    )""",
)


class ConfigurationStore:
    """Explicit installation-bound handle; no persistent connection or live integration.

    Use initialize/open_existing/recover_existing rather than constructing a
    handle directly. Ordinary open/read are read-only; explicit recovery and
    updates use mode=rw, which also refuses a missing database.
    """

    __slots__ = (
        "_root",
        "_path",
        "_root_key",
        "_timeout",
        "_installation_id",
        "_anchor",
        "_anchor_kinds",
    )

    def __init__(self):
        raise TypeError("Use explicit configuration-store initialization or opening.")

    def __repr__(self):
        return "ConfigurationStore(<private>)"

    @classmethod
    def _handle(cls, root, timeout):
        if (
            not isinstance(root, (str, Path))
            or (isinstance(root, str) and not root.strip())
            or type(timeout) not in {int, float}
            or not math.isfinite(timeout)
            or not 0 <= timeout <= 30
        ):
            _fail("INVALID_ARGUMENT")
        store = object.__new__(cls)
        store._root = Path(root).resolve()
        store._path = store._root / _RELATIVE_PATH
        key = os.path.normcase(str(store._root))
        store._root_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
        store._timeout, store._installation_id = float(timeout), None
        store._anchor, store._anchor_kinds = None, None
        return store

    def bind_activation(self, revision, selection_kinds):
        """Return a detached handle that audits protocol-1 ancestry on every use.

        The immutable activation anchor is external authority, not a request to
        select history. No database/schema change or recovery is performed.
        """
        if (
            type(revision) is not str
            or not _OPAQUE_ID.fullmatch(revision)
            or type(selection_kinds) is not dict
            or set(selection_kinds) != set(_SERVICES)
            or any(
                type(kind) is not str or kind not in {"profile", "environment", "none"}
                for kind in selection_kinds.values()
            )
        ):
            _fail("INVALID_ARGUMENT")
        bound = self._handle(self._root, self._timeout)
        bound._installation_id = self._installation_id
        bound._anchor, bound._anchor_kinds = revision, dict(selection_kinds)
        bound.read_snapshot()
        return bound

    @classmethod
    def initialize(cls, root, *, documents, selections, timeout: float = 5.0):
        """Create a private new store exclusively, publishing its first generation."""

        def action():
            texts = _encode(documents, selections)
            store = cls._handle(root, timeout)
            with store._writer() as deadline:
                store._check_parent(create=True)
                try:
                    descriptor = os.open(
                        store._path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o666 if os.name == "nt" else 0o600,
                    )
                except FileExistsError:
                    _fail("STORE_EXISTS")
                else:
                    os.close(descriptor)
                with store._session(deadline, write=True, initializing=True) as session:
                    session.execute("BEGIN IMMEDIATE")
                    for statement in _SCHEMA:
                        session.execute(statement)
                    session.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                    session.execute(f"PRAGMA user_version={PROTOCOL_VERSION}")
                    installation_id, revision = uuid4().hex, uuid4().hex
                    store._insert(session, revision, None, texts)
                    session.execute(
                        "INSERT INTO store_metadata VALUES (1, ?, ?, ?, ?)",
                        (PROTOCOL_VERSION, installation_id, store._root_key, revision),
                    )
                    session.execute("COMMIT")
                store._installation_id = installation_id
            return store

        return _safe_call(action)

    @classmethod
    def open_existing(cls, root, *, timeout: float = 5.0):
        """Validate and open read-only, without creation, adoption, or recovery."""

        def action():
            store = cls._handle(root, timeout)
            snapshot = store._read(time.monotonic() + store._timeout)
            store._installation_id = snapshot.installation_id
            return store

        return _safe_call(action, read_only=True)

    @classmethod
    def recover_existing(cls, root, *, timeout: float = 5.0):
        """Explicit SQLite crash recovery; never select a historical generation."""

        def action():
            store = cls._handle(root, timeout)
            with store._writer() as deadline:
                with store._session(deadline, write=True, recovering=True) as session:
                    session.execute("BEGIN IMMEDIATE")
                    store._check_file()
                    if session.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        _fail("STORE_CORRUPT")
                    snapshot = store._current(session)
                    session.execute("COMMIT")
                store._installation_id = snapshot.installation_id
            return store

        return _safe_call(action)

    def read_snapshot(self) -> ConfigurationSnapshot:
        """Read all documents and selection intents from one read transaction."""
        return _safe_call(lambda: self._read(time.monotonic() + self._timeout), read_only=True)

    def update(self, mutate, *, expected_revision: str | None = None) -> ConfigurationSnapshot:
        """Run one trusted pure mutation under BEGIN IMMEDIATE and publish atomically.

        The callback returns (documents, selections). Stale revisions reject the
        operation before the callback runs. No automatic callback retries occur.
        """

        def action():
            if not callable(mutate) or (
                expected_revision is not None
                and (
                    type(expected_revision) is not str
                    or not _OPAQUE_ID.fullmatch(expected_revision)
                )
            ):
                _fail("INVALID_ARGUMENT")
            with self._writer() as deadline:
                with self._session(deadline, write=True) as session:
                    session.execute("BEGIN IMMEDIATE")
                    current = self._current(session)
                    if expected_revision is not None and expected_revision != current.revision:
                        _fail("REVISION_CONFLICT")
                    try:
                        candidate = mutate(current)
                    except ConfigurationStoreError:
                        raise
                    except BaseException as exc:
                        if _interruption_family(type(exc)) is not None or (
                            _EXCEPTION_GROUP is not None and issubclass(type(exc), _EXCEPTION_GROUP)
                        ):
                            raise
                        _fail("UPDATE_FAILED")
                    if type(candidate) is not tuple or len(candidate) != 2:
                        _fail("INVALID_CONFIGURATION")
                    texts = _encode(*candidate)
                    revision = uuid4().hex
                    self._insert(session, revision, current.revision, texts)
                    if self._anchor is not None:
                        self._ancestry(
                            session, ConfigurationSnapshot(current.installation_id, revision, texts)
                        )
                    published = session.execute(
                        "UPDATE store_metadata SET current_revision=? "
                        "WHERE singleton=1 AND current_revision=?",
                        (revision, current.revision),
                    )
                    if published.rowcount != 1:
                        _fail("STORE_CORRUPT")
                    session.execute("COMMIT")
                    return ConfigurationSnapshot(current.installation_id, revision, texts)

        if self._anchor is not None:
            from alphalab.utils.configuration_maintenance import execution_access

            with execution_access(self._root):
                return _safe_call(action)
        return _safe_call(action)

    @contextmanager
    def _writer(self):
        deadline = time.monotonic() + self._timeout
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(self._root_key, threading.Lock())
        if not lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            _fail("STORE_BUSY")
        try:
            yield deadline
        finally:
            lock.release()

    def _check_parent(self, *, create=False):
        parent = self._path.parent
        if parent.resolve() != parent:
            _fail("UNSAFE_STORAGE")
        if create:
            parent.mkdir(parents=True, exist_ok=True, mode=0o777 if os.name == "nt" else 0o700)
        if parent.exists() and os.name != "nt" and parent.stat().st_mode & 0o077:
            _fail("UNSAFE_STORAGE")

    def _has_recovery_journal(self):
        journal = self._path.with_name(self._path.name + "-journal")
        try:
            info = journal.lstat()
        except FileNotFoundError:
            return False
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (os.name != "nt" and info.st_mode & 0o077)
        ):
            _fail("UNSAFE_STORAGE")
        with journal.open("rb") as stream:
            prefix = stream.read(12)
            if prefix == b"\0" * len(prefix):
                # A live writer may not have synced its first journal header.
                # Do not defer database validation; let SQLite's locks decide
                # whether the writer is active before examining current state.
                return False
            if info.st_size <= 512 or prefix[:8] != _JOURNAL_MAGIC:
                _fail("STORE_CORRUPT")
        return True

    def _check_file(self, *, initializing=False, recovering=False):
        self._check_parent()
        try:
            info = self._path.lstat()
        except FileNotFoundError:
            _fail("STORE_MISSING")
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("UNSAFE_STORAGE")
        if os.name != "nt" and info.st_mode & 0o077:
            _fail("UNSAFE_STORAGE")
        if not initializing:
            if recovering and self._has_recovery_journal():
                return  # Only explicit recovery may defer header validation.
            with self._path.open("rb") as stream:
                header = stream.read(100)
            if (
                len(header) != 100
                or header[:16] != b"SQLite format 3\0"
                or int.from_bytes(header[68:72], "big") != _APPLICATION_ID
            ):
                _fail("STORE_CORRUPT")
            if int.from_bytes(header[60:64], "big") != PROTOCOL_VERSION:
                _fail("UNSUPPORTED_PROTOCOL")
            if header[18:20] != b"\x01\x01":
                # Reject WAL before opening SQLite, so even a read cannot create
                # WAL/SHM sidecars for an unsupported storage mode.
                _fail("UNSUPPORTED_STORAGE")

    @contextmanager
    def _session(self, deadline, *, write=False, initializing=False, recovering=False):
        self._check_file(initializing=initializing, recovering=recovering)
        uri = self._path.as_uri() + ("?mode=rw" if write else "?mode=ro")
        connection = sqlite3.connect(
            uri, uri=True, isolation_level=None, timeout=max(0.0, deadline - time.monotonic())
        )
        try:
            session = _Session(connection, deadline)
            session.execute("PRAGMA trusted_schema=OFF")
            session.execute("PRAGMA foreign_keys=ON")
            if not write:
                session.execute("PRAGMA query_only=ON")
            if initializing:
                session.execute("PRAGMA journal_mode=DELETE")
            if session.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                _fail("UNSUPPORTED_STORAGE")
            if write:
                session.execute("PRAGMA synchronous=EXTRA")
                if session.execute("PRAGMA synchronous").fetchone() != (3,):
                    _fail("UNSUPPORTED_STORAGE")
            yield session
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()

    @staticmethod
    def _insert(session, revision, parent_revision, texts):
        session.execute(
            "INSERT INTO generations (revision, parent_revision, models, search, rqdata, selections) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (revision, parent_revision, *texts),
        )

    def _read(self, deadline):
        with self._session(deadline) as session:
            session.execute("BEGIN")
            snapshot = self._current(session)
            session.execute("COMMIT")
            return snapshot

    def _current(self, session):
        rows = session.execute(
            "SELECT singleton, protocol_version, installation_id, root_key, current_revision "
            "FROM store_metadata"
        ).fetchall()
        if len(rows) != 1:
            _fail("STORE_CORRUPT")
        singleton, protocol, installation_id, root_key, revision = rows[0]
        if type(singleton) is not int or singleton != 1:
            _fail("STORE_CORRUPT")
        if protocol != PROTOCOL_VERSION or type(protocol) is not int:
            _fail("UNSUPPORTED_PROTOCOL")
        if any(
            type(value) is not str or not _OPAQUE_ID.fullmatch(value)
            for value in (installation_id, revision)
        ):
            _fail("STORE_CORRUPT")
        if root_key != self._root_key or self._installation_id not in {None, installation_id}:
            _fail("INSTALLATION_MISMATCH")
        row = session.execute(
            "SELECT models, search, rqdata, selections FROM generations "
            "WHERE revision=? AND ordinal=(SELECT max(ordinal) FROM generations)",
            (revision,),
        ).fetchone()
        if row is None:
            _fail("STORE_CORRUPT")
        try:
            documents = {service: _decode(text) for service, text in zip(_SERVICES, row)}
            texts = _encode(documents, _decode(row[3]))
        except Exception:
            _fail("STORE_CORRUPT")
        snapshot = ConfigurationSnapshot(installation_id, revision, texts)
        if self._anchor is not None:
            self._ancestry(session, snapshot)
        return snapshot

    def _ancestry(self, session, current):
        if any(choice["kind"] == "environment" for choice in current.selections.values()):
            _fail("INVALID_CONFIGURATION")
        # Protocol 1 never deletes generations. Requiring the complete ordinal
        # sequence and one linear chain rejects missing parents, cycles, forks,
        # disconnected records and metadata rollback without choosing history.
        rows = session.execute(
            "SELECT ordinal, revision, parent_revision, length(CAST(models AS BLOB)), "
            "length(CAST(search AS BLOB)), length(CAST(rqdata AS BLOB)), "
            "length(CAST(selections AS BLOB)) FROM generations ORDER BY ordinal LIMIT 4097"
        ).fetchall()
        if not rows or len(rows) > 4096:
            _fail("STORE_CORRUPT")
        previous, total = None, 0
        for ordinal, row in enumerate(rows, 1):
            index, revision, parent, *lengths = row
            if (
                type(index) is not int
                or index != ordinal
                or type(revision) is not str
                or not _OPAQUE_ID.fullmatch(revision)
                or parent != previous
                or any(type(length) is not int or length < 1 for length in lengths)
            ):
                _fail("STORE_CORRUPT")
            total += sum(lengths)
            if total > 64 * 1024 * 1024 or sum(lengths) > _MAX_BYTES:
                _fail("STORE_CORRUPT")
            if ordinal == 1 and revision != self._anchor:
                _fail("REVISION_CONFLICT")
            fields = session.execute(
                "SELECT models, search, rqdata, selections FROM generations WHERE revision=?",
                (revision,),
            ).fetchone()
            documents = {name: _decode(text) for name, text in zip(_SERVICES, fields)}
            choices = _decode(fields[3])
            _encode(documents, choices)
            if any(choice["kind"] == "environment" for choice in choices.values()):
                _fail("INVALID_CONFIGURATION")
            if (
                ordinal == 1
                and {name: choice["kind"] for name, choice in choices.items()} != self._anchor_kinds
            ):
                _fail("INVALID_CONFIGURATION")
            previous = revision
        if previous != current.revision:
            _fail("REVISION_CONFLICT")


__all__ = [
    "PROTOCOL_VERSION",
    "ConfigurationSnapshot",
    "ConfigurationStore",
    "ConfigurationStoreError",
]
