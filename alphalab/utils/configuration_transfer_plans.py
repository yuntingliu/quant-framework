"""Private, credential-free transfer plans; not configuration-store protocol v1.

Every transition holds a real installation OS lease. Atomic private JSON
publication persists one-use claims BEFORE settings writes. An interrupted
pending publication is preserved and blocks further plan operations. No plan
ever contains native documents, credentials or credential fingerprints.
"""

from __future__ import annotations

import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from alphalab.utils.configuration_decision import _directory, _sync_directory
from alphalab.utils.configuration_maintenance import _access, checked_root
from alphalab.utils.configuration_store import ConfigurationStoreError

MAX_PLANS = 256
MAX_PLAN_BYTES = 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}")
_DIRECTORY = Path("app/secrets/configuration-transfers")
_FIELDS = {
    "version",
    "id",
    "kind",
    "state",
    "expires",
    "binding",
    "revision",
    "configuration",
    "before",
    "services",
    "mode",
    "apply_selections",
    "policy",
    "consents",
    "origin",
    "result_revision",
}


class TransferPlanError(RuntimeError):
    def __init__(self, code):
        self.code = (
            code if code in {"PLAN_LIMIT", "UNKNOWN_RECEIPT", "INVALID_PLAN"} else "INVALID_PLAN"
        )
        super().__init__("Configuration transfer plan is unavailable.")


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise TransferPlanError("INVALID_PLAN")
        result[key] = value
    return result


def _regular(path):
    value = path.lstat()
    if (
        path.resolve() != path
        or not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or (os.name != "nt" and value.st_mode & 0o077)
    ):
        raise ConfigurationStoreError("UNSAFE_STORAGE")
    return value


class TransferPlans:
    __slots__ = ("directory",)

    def __init__(self, directory):
        self.directory = directory

    def __repr__(self):
        return "TransferPlans(<private>)"

    def __reduce_ex__(self, _protocol):
        raise TypeError("Transfer plans cannot be serialized implicitly.")

    def read(self, identifier):
        if type(identifier) is not str or not _ID.fullmatch(identifier):
            raise TransferPlanError("UNKNOWN_RECEIPT")
        path = self.directory / (identifier + ".json")
        if not os.path.lexists(path):
            raise TransferPlanError("UNKNOWN_RECEIPT")
        original = _regular(path)
        with path.open("rb") as stream:
            actual = os.fstat(stream.fileno())
            raw = stream.read(MAX_PLAN_BYTES + 1)
            after = os.fstat(stream.fileno())
        named = _regular(path)

        def identity(value):
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

        if len(raw) > MAX_PLAN_BYTES or any(
            identity(info) != identity(original) for info in (actual, after, named)
        ):
            raise TransferPlanError("INVALID_PLAN")
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if (
            type(value) is not dict
            or set(value) != _FIELDS
            or value["version"] != 1
            or type(value["version"]) is not int
            or value["id"] != identifier
            or value["kind"] not in {"import", "rollback"}
            or value["state"] not in {"pending", "claimed", "committed", "rejected", "uncertain"}
            or type(value["expires"]) is not int
        ):
            raise TransferPlanError("INVALID_PLAN")
        binding = value["binding"]
        if (
            type(binding) is not dict
            or set(binding) != {"root_key", "backend", "installation_id", "decision_id"}
            or binding["backend"] != "sqlite"
            or type(binding["root_key"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", binding["root_key"])
            or any(
                type(binding[key]) is not str or not _ID.fullmatch(binding[key])
                for key in ("installation_id", "decision_id")
            )
            or type(value["revision"]) is not str
            or not _ID.fullmatch(value["revision"])
            or (
                value["result_revision"] is not None
                and (
                    type(value["result_revision"]) is not str
                    or not _ID.fullmatch(value["result_revision"])
                )
            )
            or (
                value["origin"] is not None
                and (type(value["origin"]) is not str or not _ID.fullmatch(value["origin"]))
            )
            or (value["kind"] == "rollback") != (value["origin"] is not None)
            or (value["state"] == "committed") != (value["result_revision"] is not None)
            or value["expires"] <= 0
            or type(value["configuration"]) is not str
            or type(value["before"]) is not str
        ):
            raise TransferPlanError("INVALID_PLAN")
        return value

    def write(self, value, *, new=False):
        identifier = value["id"]
        if type(identifier) is not str or not _ID.fullmatch(identifier):
            raise TransferPlanError("INVALID_PLAN")
        path = self.directory / (identifier + ".json")
        if new:
            if len(list(self.directory.iterdir())) >= MAX_PLANS:
                raise TransferPlanError("PLAN_LIMIT")
            if os.path.lexists(path):
                raise TransferPlanError("INVALID_PLAN")
        else:
            _regular(path)
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
        if len(raw) > MAX_PLAN_BYTES:
            raise TransferPlanError("INVALID_PLAN")
        pending = self.directory / ("." + identifier + "." + uuid4().hex + ".pending")
        fd = os.open(
            pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666 if os.name == "nt" else 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        _sync_directory(self.directory)

    def claim(self, identifier):
        value = self.read(identifier)
        if value["state"] != "pending":
            raise TransferPlanError("INVALID_PLAN")
        value["state"] = "claimed"
        self.write(value)
        return value


@contextmanager
def transfer_plans(root, *, create=False):
    """No lease upgrade; callers hold backend/maintenance use before this lease."""
    root = checked_root(root)
    directory = root / _DIRECTORY
    if not create and not os.path.lexists(directory):
        raise TransferPlanError("UNKNOWN_RECEIPT")
    with _access(
        root, Path("app/secrets/configuration-transfer.lock"), exclusive=True, timeout=0.0
    ):
        if create and not os.path.lexists(directory):
            directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
            _sync_directory(directory.parent)
        _directory(directory)
        for index, entry in enumerate(directory.iterdir(), 1):
            if index > MAX_PLANS:
                raise TransferPlanError("PLAN_LIMIT")
            if not re.fullmatch(r"[0-9a-f]{32}\.json", entry.name):
                raise TransferPlanError("INVALID_PLAN")
            _regular(entry)
        yield TransferPlans(directory)
