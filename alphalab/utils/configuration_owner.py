"""Private, revocable proof of an actual SQLite owner and its owned Agent.

This is an ephemeral launch witness, never a backend-selection marker. Only
the durable decision selects SQLite. A replacement owner must first prove OS
execution, Agent-lifetime and maintenance exclusion; it never adopts old tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from alphalab.utils.configuration_maintenance import configuration_access
from alphalab.utils.configuration_store import ConfigurationStoreError

_PATH = Path("app/secrets/configuration-owner.json")
_HEX = re.compile(r"[0-9a-f]{64}")


def _fail():
    raise ConfigurationStoreError("STORE_UNAVAILABLE")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail()
        result[key] = value
    return result


def _process_identity(pid):
    """Observe an exact live owner instance; never signal a Windows process."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            *(ctypes.POINTER(wintypes.FILETIME),) * 4,
        )
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            _fail()
        try:
            code = wintypes.DWORD()
            times = [wintypes.FILETIME() for _ in range(4)]
            if (
                not kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                or code.value != 259
                or not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times))
            ):
                _fail()
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    os.kill(pid, 0)  # POSIX liveness query only; never used on Windows.
    proc = Path(f"/proc/{pid}/stat")
    if proc.exists():
        return proc.read_text().rpartition(")")[2].split()[19]
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, timeout=1)
    if result.returncode or not result.stdout.strip():
        _fail()
    return result.stdout.decode("ascii").strip()


def read_owner(root):
    path = root / _PATH
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > 4096
        or path.resolve() != path
        or (os.name != "nt" and before.st_mode & 0o077)
    ):
        _fail()
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        actual = os.fstat(stream.fileno())
        wire = stream.read(4097)
        after = os.fstat(stream.fileno())

    def identity(item):
        return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns

    if any(identity(item) != identity(before) for item in (actual, after, path.lstat())):
        _fail()
    value = json.loads(wire, object_pairs_hook=_pairs)
    if (
        type(value) is not dict
        or set(value)
        != {
            "version",
            "installation_id",
            "decision_id",
            "owner_pid",
            "owner_birth",
            "child_pid",
            "secret_digest",
        }
        or value["version"] != 1
        or type(value["version"]) is not int
        or any(
            type(value[key]) is not str or not re.fullmatch(r"[0-9a-f]{32}", value[key])
            for key in ("installation_id", "decision_id")
        )
        or type(value["owner_pid"]) is not int
        or value["owner_pid"] <= 0
        or type(value["owner_birth"]) is not str
        or not 0 < len(value["owner_birth"]) <= 128
        or (
            value["child_pid"] is not None
            and (type(value["child_pid"]) is not int or value["child_pid"] <= 0)
        )
        or (
            value["secret_digest"] is not None
            and (
                type(value["secret_digest"]) is not str
                or not _HEX.fullmatch(value["secret_digest"])
            )
        )
        or ((value["child_pid"] is None) != (value["secret_digest"] is None))
    ):
        _fail()
    return value


def publish_owner(root, identity, *, child_pid=None, secret=None):
    """Caller holds the actual SQLite owner lease; never print the witness."""
    if secret is not None and (type(secret) is not str or not _HEX.fullmatch(secret)):
        _fail()
    value = {
        "version": 1,
        "installation_id": identity["installation_id"],
        "decision_id": identity["decision_id"],
        "owner_pid": os.getpid(),
        "owner_birth": _process_identity(os.getpid()),
        "child_pid": child_pid,
        "secret_digest": None
        if secret is None
        else hashlib.sha256(bytes.fromhex(secret)).hexdigest(),
    }
    path = root / _PATH
    if os.path.lexists(path):
        read_owner(root)  # Never overwrite an ambiguous/corrupt witness.
    pending = path.with_suffix(".pending")
    descriptor = os.open(
        pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666 if os.name == "nt" else 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def verify_agent_owner(root, identity, secret, *, child_pid):
    value = read_owner(root)
    if (
        type(secret) is not str
        or not _HEX.fullmatch(secret)
        or value["owner_birth"] != _process_identity(value["owner_pid"])
        or value["child_pid"] != child_pid
        or value["secret_digest"] is None
        or any(value[key] != identity[key] for key in ("installation_id", "decision_id"))
        or not hmac.compare_digest(
            value["secret_digest"], hashlib.sha256(bytes.fromhex(secret)).hexdigest()
        )
    ):
        _fail()
    require_exclusive_owner(root)


def require_exclusive_owner(root):
    # A file/dictionary alone cannot prove ownership: an exclusive OS owner must
    # still exclude shared maintenance. Agent lifetime fencing prevents another
    # owner from replacing this witness while the helper is alive.
    try:
        with configuration_access(root):
            pass
    except ConfigurationStoreError as error:
        if error.code == "STORE_BUSY":
            return
        raise
    _fail()


def verify_worker_owner(root, identity):
    value = read_owner(root)
    if (
        value["owner_pid"] != os.getppid()
        or value["owner_birth"] != _process_identity(value["owner_pid"])
        or any(value[key] != identity[key] for key in ("installation_id", "decision_id"))
    ):
        _fail()
    require_exclusive_owner(root)
