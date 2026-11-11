"""Cooperative, installation-wide exclusion for explicit offline maintenance.

Normal JSON writers and application lifetimes take shared leases. Maintenance
takes an exclusive lease, without changing the selected storage backend. This
does not fence old releases, editors, or deployment programs that ignore this
protocol: the operator must stop those before acknowledging offline access.
Locks are bounded, local-filesystem only, released by the OS after process exit,
and never removed or repaired automatically. Windows uses LockFileEx, not ACLs.
"""

from __future__ import annotations

import errno
import math
import os
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

from alphalab.utils.configuration_store import ConfigurationStoreError, _safe_call

_LOCAL = threading.local()
_RELATIVE_LOCK = Path("app/secrets/configuration-maintenance.lock")
_WORKER_LEASES = []


@dataclass
class _HeldLease:
    pid: int
    exclusive: bool
    descriptor: int
    users: int = 1


def _windows_lock(descriptor, exclusive, unlock=False):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("internal", ctypes.c_size_t),
            ("internal_high", ctypes.c_size_t),
            ("offset", wintypes.DWORD),
            ("offset_high", wintypes.DWORD),
            ("event", wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle, overlap = msvcrt.get_osfhandle(descriptor), Overlapped()
    if unlock:
        operation = kernel.UnlockFileEx
        operation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(Overlapped),
        ]
        arguments = (handle, 0, 1, 0, ctypes.byref(overlap))
    else:
        operation = kernel.LockFileEx
        operation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(Overlapped),
        ]
        arguments = (handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlap))
    operation.restype = wintypes.BOOL
    if not operation(*arguments):
        code = ctypes.get_last_error()
        if code in {32, 33, 158}:
            raise BlockingIOError(errno.EAGAIN, "Configuration maintenance is busy.")
        raise OSError("Configuration maintenance lock is unavailable.")


def _lock(descriptor, exclusive, *, unlock=False):
    if os.name == "nt":
        _windows_lock(descriptor, exclusive, unlock)
    else:
        import fcntl

        operation = (
            fcntl.LOCK_UN
            if unlock
            else ((fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        )
        fcntl.flock(descriptor, operation)


def checked_root(root):
    """Validate explicit lexical/resolved location without reading settings."""
    if type(root) not in {str, Path} and not isinstance(root, Path):
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    if not str(root).strip() or "\0" in str(root):
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    path = Path(root).absolute()
    if os.name == "nt" and str(path).startswith("\\\\"):
        raise ConfigurationStoreError("UNSAFE_STORAGE")
    if os.path.normcase(str(path.resolve())) != os.path.normcase(str(path)):
        raise ConfigurationStoreError("UNSAFE_STORAGE")
    return path


def configuration_access(root, *, exclusive=False, timeout=0.0):
    """Shared use or exclusive offline lease; never upgrade a shared lease.

    Same-thread shared nesting is allowed. Exclusive reentry is refused, so a
    migration cannot accidentally invoke live readers/writers inside its fence.
    Acquire this lease before provider-local locks, never from store callbacks.
    """

    return _access(root, _RELATIVE_LOCK, exclusive=exclusive, timeout=timeout)


@contextmanager
def json_configuration_access(root):
    """Read authority and use JSON within the same actual maintenance lease."""
    from alphalab.utils.configuration_decision import check_backend_authority

    with configuration_access(root):
        check_backend_authority(root, backend="json")
        yield


def execution_access(root, *, exclusive=False, timeout=0.0):
    """Cooperative job/child admission, independent of the backend owner's lease.

    Shared admission never waits: shutdown may hold this exclusive gate while
    waiting for API maintenance lifetimes to close, without a lock-order cycle.
    A child holds its own shared lease until actual process completion.
    """
    if not exclusive and timeout != 0:
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    return _access(
        root,
        Path("app/secrets/configuration-execution.lock"),
        exclusive=exclusive,
        timeout=timeout,
    )


def agent_access(root, *, exclusive=False, timeout=0.0):
    """Independent idle Agent lifetime, distinct from active-work admission.

    Offline coordination gates execution first, stops only owned listeners, then
    requires this exclusive lease to prove their helper processes have exited.
    """
    return _access(
        root,
        Path("app/secrets/configuration-agent.lock"),
        exclusive=exclusive,
        timeout=timeout,
    )


def execution_operation(function):
    """Fence scheduler admission and direct sync operations with an explicit root."""

    @wraps(function)
    def guarded(self, *args, **kwargs):
        from alphalab.utils.configuration_decision import check_backend_authority

        with execution_access(self.root):
            owner = getattr(self, "_configuration_backend", None)
            if owner is None:
                captured = getattr(self, "_captured_execution", None)
                if captured is not None:
                    if type(captured) is not _CapturedExecution:
                        raise ConfigurationStoreError("INVALID_ARGUMENT")
                    captured.require(self.root)
                    return function(self, *args, **kwargs)
                check_backend_authority(self.root, backend="json")
                return function(self, *args, **kwargs)
            from alphalab.utils.configuration_backend import ConfigurationBackend

            if type(owner) is not ConfigurationBackend or owner.root != checked_root(self.root):
                raise ConfigurationStoreError("INSTALLATION_MISMATCH")
            with owner.borrow():
                return function(self, *args, **kwargs)

    return guarded


def hold_worker_execution(root):
    """Retain the raw OS lease through worker thread/finalizer teardown.

    Do not close at target-function return: SDK threads can outlive that return.
    Spawned workers use os._exit after bootstrap; the OS releases these retained
    descriptors on actual process exit, including crash/forced termination.
    """
    import multiprocessing

    if multiprocessing.parent_process() is None:
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    lease = execution_access(root)
    lease.__enter__()
    _WORKER_LEASES.append(lease)


class _CapturedExecution:
    """Private process-bound capability; a dictionary or flag cannot replace it."""

    __slots__ = ("root", "installation_id", "revision", "_pid", "_descriptor")

    def __init__(self):
        raise TypeError("Use validated captured worker admission.")

    def __repr__(self):
        return "CapturedExecution(<private>)"

    def __reduce_ex__(self, _protocol):
        raise TypeError("Captured execution cannot be serialized.")

    def require(self, root):
        location = checked_root(root)
        key = os.path.normcase(str(location / "app/secrets/configuration-execution.lock"))
        held = getattr(_LOCAL, "held", {}).get(key)
        if (
            location != self.root
            or self._pid != os.getpid()
            or held is None
            or held.pid != os.getpid()
            or held.exclusive
            or held.descriptor != self._descriptor
        ):
            raise ConfigurationStoreError("INSTALLATION_MISMATCH")
        os.fstat(self._descriptor)
        from alphalab.utils.configuration_decision import (
            read_decision_record,
            validate_committed_authority,
        )

        record = read_decision_record(location)
        if record is not None and record.as_dict()["state"] == "committed":
            from alphalab.utils.configuration_owner import verify_worker_owner

            identity = validate_committed_authority(location, installation_id=self.installation_id)
            verify_worker_owner(location, identity)


def captured_worker_admission(root, installation_id, revision):
    """Only after private-pipe validation, with a real retained child OS lease."""
    import multiprocessing

    from alphalab.utils.configuration_decision import read_decision_record
    from alphalab.utils.configuration_store import ConfigurationStore

    location = checked_root(root)
    key = os.path.normcase(str(location / "app/secrets/configuration-execution.lock"))
    held = getattr(_LOCAL, "held", {}).get(key)
    if multiprocessing.parent_process() is None or not _WORKER_LEASES or held is None:
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    value = object.__new__(_CapturedExecution)
    value.root, value.installation_id, value.revision = location, installation_id, revision
    value._pid, value._descriptor = os.getpid(), held.descriptor
    value.require(location)
    record = read_decision_record(location)

    def validate():
        store = ConfigurationStore._handle(location, 5)
        if record is not None and record.as_dict()["state"] == "committed":
            data = record.as_dict()
            store._anchor, store._anchor_kinds = (
                data["revision"],
                data["evidence"]["selection_kinds"],
            )
        with store._session(time.monotonic() + 5) as session:
            session.execute("BEGIN")
            snapshot = store._current(session)
            if snapshot.installation_id != installation_id or session.execute(
                "SELECT 1 FROM generations WHERE revision=?", (revision,)
            ).fetchall() != [(1,)]:
                raise ConfigurationStoreError("INSTALLATION_MISMATCH")

    _safe_call(validate, read_only=True)
    return value


@contextmanager
def _access(root, relative_lock, *, exclusive, timeout):
    def acquire():
        if (
            type(exclusive) is not bool
            or type(timeout) not in {int, float}
            or (not math.isfinite(timeout) or not 0 <= timeout <= 5)
        ):
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        location = checked_root(root)
        key = os.path.normcase(str(location / relative_lock))
        held = getattr(_LOCAL, "held", {})
        previous = held.get(key)
        if previous is not None and previous.pid == os.getpid():
            if exclusive or previous.exclusive:
                raise ConfigurationStoreError("STORE_BUSY")
            previous.users += 1
            return previous, key
        path = location / relative_lock
        if path.parent.resolve() != path.parent:
            raise ConfigurationStoreError("UNSAFE_STORAGE")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o777 if os.name == "nt" else 0o700)
        if os.name != "nt" and path.parent.stat().st_mode & 0o077:
            raise ConfigurationStoreError("UNSAFE_STORAGE")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            actual, named = os.fstat(descriptor), path.lstat()
            if (
                not stat.S_ISREG(actual.st_mode)
                or actual.st_nlink != 1
                or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
                or not stat.S_ISREG(named.st_mode)
                or (os.name != "nt" and actual.st_mode & 0o077)
            ):
                raise ConfigurationStoreError("UNSAFE_STORAGE")
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _lock(descriptor, exclusive)
                    break
                except (BlockingIOError, PermissionError):
                    if time.monotonic() >= deadline:
                        raise ConfigurationStoreError("STORE_BUSY") from None
                    time.sleep(min(0.01, max(0, deadline - time.monotonic())))
            held = dict(held)
            lease = _HeldLease(os.getpid(), exclusive, descriptor)
            held[key] = lease
            _LOCAL.held = held
            return lease, key
        except BaseException:
            os.close(descriptor)
            raise

    lease, key = _safe_call(acquire)
    try:
        yield
    finally:
        lease.users -= 1
        if not lease.users:
            current = dict(_LOCAL.held)
            current.pop(key, None)
            _LOCAL.held = current

            def release():
                try:
                    _lock(lease.descriptor, lease.exclusive, unlock=True)
                finally:
                    os.close(lease.descriptor)

            _safe_call(release)


def legacy_settings_operation(path_factory):
    """Fence a supported JSON read/modify/write before its existing locks."""

    def decorate(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            with json_configuration_access(path_factory().parents[2]):
                return function(*args, **kwargs)

        return guarded

    return decorate
