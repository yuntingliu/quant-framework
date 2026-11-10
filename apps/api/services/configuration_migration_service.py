"""Explicit offline legacy adoption; no route/startup invokes this module.

The operator must stop older/unmanaged processes and deployment operations.
Current API/launcher lifetimes and JSON saves honor the maintenance fence.
This is cooperative exclusion, not protection against a privileged local editor.
No environment resolution, provider calls, plaintext backups, or activation.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import stat
import subprocess
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from alphalab.dataio import rq_profiles
from alphalab.utils import configuration_store as storage
from alphalab.utils.configuration_deployment import OfflineInstallation
from alphalab.utils.configuration_maintenance import checked_root, configuration_access
from apps.api.services import configuration_schema as schema
from apps.api.services import search_provider_service as search
from apps.api.services.configuration_profile_service import (
    _credential_values,
    _guard,
    _scan_profile_metadata,
)

_SOURCES = {
    "models": Path("conexus/secrets/model-providers.json"),
    "search": Path("conexus/secrets/search-providers.json"),
    "rqdata": Path("app/secrets/rqdata-profiles.json"),
}
_DESTINATION = Path("app/secrets/configuration-store.sqlite3")
_MAX_SOURCE_BYTES = 1024 * 1024
_READER = Path(__file__).resolve().parents[3] / "integrations/conexus/configuration-store.mjs"
_NODE = """
import { isDeepStrictEqual } from 'node:util';
let input = '';
for await (const part of process.stdin) input += part;
let reader;
try {
  const expected = JSON.parse(input);
  const { ConfigurationStoreReader } = await import(READER_URL);
  reader = new ConfigurationStoreReader(expected.root);
  const snapshot = await reader.readSnapshot();
  const valid = snapshot.protocol_version === expected.protocol_version
    && snapshot.installation_id === expected.installation_id
    && snapshot.revision === expected.revision
    && isDeepStrictEqual(snapshot.documents, expected.documents)
    && isDeepStrictEqual(snapshot.selections, expected.selections);
  if (!valid) throw 0;
  await reader.close();
  process.stdout.write('verified');
} catch {
  try { await reader?.close() } catch {}
  process.exitCode = 1;
}
""".replace("READER_URL", json.dumps(_READER.as_uri()))


@dataclass(frozen=True)
class MigrationReport:
    """Public system identifiers/counts only; no source values or secret hashes."""

    installation_id: str
    revision: str
    profile_counts: tuple[int, int, int]
    selection_kinds: tuple[str, str, str]

    def as_dict(self):
        return {
            "protocol_version": storage.PROTOCOL_VERSION,
            "installation_id": self.installation_id,
            "revision": self.revision,
            "profile_counts": dict(zip(_SOURCES, self.profile_counts)),
            "selection_kinds": dict(zip(_SOURCES, self.selection_kinds)),
            "backend_activated": False,
        }


def _invalid():
    raise storage.ConfigurationStoreError("INVALID_CONFIGURATION")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _constant(_value):
    _invalid()


def _read_source(path):
    if path.parent.resolve() != path.parent:
        raise storage.ConfigurationStoreError("UNSAFE_STORAGE")
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise storage.ConfigurationStoreError("UNSAFE_STORAGE")
    if before.st_size > _MAX_SOURCE_BYTES:
        _invalid()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        actual = os.fstat(stream.fileno())
        raw = stream.read(_MAX_SOURCE_BYTES + 1)
        after = os.fstat(stream.fileno())

    def identity(info):
        # Windows path stat and fd stat can expose different legacy ctime
        # meanings (birth time versus change time). Use file ID/size/mtime and
        # byte rechecks there; POSIX ctime is a consistent extra change detector.
        common = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
        return common if os.name == "nt" else (*common, info.st_ctime_ns)

    if (
        identity(before) != identity(actual)
        or identity(actual) != identity(after)
        or identity(after) != identity(path.lstat())
    ):
        raise storage.ConfigurationStoreError("UPDATE_FAILED")
    if len(raw) > _MAX_SOURCE_BYTES:
        _invalid()
    return identity(after), raw


def _read_sources(root):
    return {service: _read_source(root / relative) for service, relative in _SOURCES.items()}


def _prepare(sources, unselected_intents):
    overrides = {} if unselected_intents is None else unselected_intents
    if type(overrides) is not dict or set(overrides) - set(_SOURCES):
        _invalid()
    documents, choices = {}, {}
    for service, source in sources.items():
        value = (
            {"version": 2 if service == "search" else 1, "active_id": None, "profiles": []}
            if source is None
            else json.loads(
                source[1].decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
            )
        )
        storage._check_json(value)
        if type(value) is not dict:
            _invalid()
        if service == "search" and value.get("version") == 1:
            if set(value) - {"version", "provider", "model_profile_id", "brave_api_key"}:
                _invalid()
            original = value
            value = search.validate_settings(value)
            value["profiles"][0]["brave_api_key"] = original.get("brave_api_key", "")
        if set(value) != {"version", "active_id", "profiles"}:
            _invalid()
        value = copy.deepcopy(value)
        if type(value["profiles"]) is not list:
            _invalid()
        secret = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}[service]
        for row in value["profiles"]:
            if type(row) is not dict:
                _invalid()
            row.setdefault(secret, "")
            if type(row[secret]) is not str or len(row[secret]) > 8192:
                _invalid()
            if service == "search":
                row.setdefault("model_profile_id", None)
            if service == "rqdata":
                row.setdefault("provider", "rqdata")
        active = value["active_id"]
        choice = {"kind": "environment"} if active is None else {"kind": "profile", "id": active}
        if service in overrides:
            if (
                active is not None
                or type(overrides[service]) is not str
                or overrides[service] not in {"environment", "none"}
            ):
                _invalid()
            choice = {"kind": overrides[service]}
        documents[service], choices[service] = value, choice
    # Pure Phase 4A projection validates strict metadata, IDs, selections,
    # cross-service references and known secrets. Nothing exported is persisted.
    _scan_profile_metadata(documents, _credential_values(documents))
    projected = schema.project_settings_snapshots(**documents, selections=choices)
    schema.export_configuration(**projected)
    search.validate_settings(documents["search"])
    rq_profiles.validate_settings(documents["rqdata"])
    storage._encode(documents, choices)
    return documents, choices


def _check_offline(root, acknowledge_offline):
    if acknowledge_offline is not True:
        raise storage.ConfigurationStoreError("INVALID_ARGUMENT")
    root = checked_root(root)
    if os.path.lexists(root / "conexus/project/host.lock"):
        raise storage.ConfigurationStoreError("STORE_BUSY")
    return root


def _require_node(node_executable):
    if not isinstance(node_executable, (str, Path)) or not str(node_executable).strip():
        raise storage.ConfigurationStoreError("INVALID_ARGUMENT")
    result = subprocess.run(
        [str(node_executable), "--version"],
        capture_output=True,
        timeout=10,
        check=True,
        **_subprocess_options(),
    )
    match = re.fullmatch(rb"v22\.(\d+)\.\d+\s*", result.stdout)
    if not match or int(match[1]) < 18 or not _READER.is_file():
        raise storage.ConfigurationStoreError("UNSUPPORTED_PROTOCOL")


def _subprocess_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _verify_node(expected, node_executable):
    # Private expectations use a pipe, never argv, environment, or temp reports.
    result = subprocess.run(
        [str(node_executable), "--input-type=module", "--eval", _NODE],
        input=json.dumps(expected).encode("utf-8"),
        capture_output=True,
        timeout=15,
        **_subprocess_options(),
    )
    if result.returncode != 0 or result.stdout != b"verified":
        raise storage.ConfigurationStoreError("STORE_UNAVAILABLE")


def _verify(root, documents, choices, node_executable):
    store = storage.ConfigurationStore.open_existing(root)
    snapshot = store.read_snapshot()
    if snapshot.documents != documents or snapshot.selections != choices:
        raise storage.ConfigurationStoreError("UPDATE_FAILED")
    with closing(
        sqlite3.connect((root / _DESTINATION).as_uri() + "?mode=ro", uri=True, timeout=5)
    ) as connection:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("SELECT count(*) FROM generations").fetchone() != (1,):
            raise storage.ConfigurationStoreError("UPDATE_FAILED")
        if connection.execute("SELECT revision, parent_revision FROM generations").fetchall() != [
            (snapshot.revision, None)
        ]:
            raise storage.ConfigurationStoreError("UPDATE_FAILED")
    expected = {
        "root": str(root),
        "protocol_version": snapshot.protocol_version,
        "installation_id": snapshot.installation_id,
        "revision": snapshot.revision,
        "documents": {key: documents[key] for key in ("models", "search")},
        "selections": {key: choices[key] for key in ("models", "search")},
    }
    _verify_node(expected, node_executable)
    latest = store.read_snapshot()
    if latest.revision != snapshot.revision:
        raise storage.ConfigurationStoreError("REVISION_CONFLICT")
    return MigrationReport(
        snapshot.installation_id,
        snapshot.revision,
        tuple(len(documents[key]["profiles"]) for key in _SOURCES),
        tuple(choices[key]["kind"] for key in _SOURCES),
    )


@_guard
def migrate_legacy_configuration(
    root, *, node_executable, acknowledge_offline=False, unselected_intents=None
):
    """Initialize once under an offline fence; never run on live user settings.

    The operator must explicitly stop old/unmanaged writers and deployment tools.
    Shared leases reject live current APIs/launchers and supported JSON saves.
    Existing/partial destinations and orphan SQLite sidecars are never repaired.
    Any error after initialization may leave a private, inactive destination;
    use verify_legacy_migration for acknowledgement recovery, never retry/reset.
    """
    root = _check_offline(root, acknowledge_offline)
    with configuration_access(root, exclusive=True):
        _check_offline(root, acknowledge_offline)
        destination = root / _DESTINATION
        if os.path.lexists(destination):
            raise storage.ConfigurationStoreError("STORE_EXISTS")
        if any(
            os.path.lexists(str(destination) + suffix) for suffix in ("-journal", "-wal", "-shm")
        ):
            raise storage.ConfigurationStoreError("STORE_CORRUPT")
        sources = _read_sources(root)
        documents, choices = _prepare(sources, unselected_intents)
        _require_node(node_executable)
        if _read_sources(root) != sources:
            raise storage.ConfigurationStoreError("UPDATE_FAILED")
        storage.ConfigurationStore.initialize(root, documents=documents, selections=choices)
        report = _verify(root, documents, choices, node_executable)
        if _read_sources(root) != sources:
            raise storage.ConfigurationStoreError("UPDATE_FAILED")
        return report


@_guard
def verify_legacy_migration(
    root, *, node_executable, acknowledge_offline=False, unselected_intents=None
):
    """Read-only acknowledgement recovery for an unchanged first generation.

    Does not initialize, repair, recover journals, replace files, or select history.
    A store edited after migration is deliberately not considered resumable.
    """
    root = _check_offline(root, acknowledge_offline)
    if not os.path.lexists(root / _DESTINATION):
        raise storage.ConfigurationStoreError("STORE_MISSING")
    with configuration_access(root, exclusive=True):
        return _verify_locked(root, node_executable, unselected_intents)


def _verify_locked(root, node_executable, unselected_intents):
    _check_offline(root, True)
    sources = _read_sources(root)
    documents, choices = _prepare(sources, unselected_intents)
    _require_node(node_executable)
    report = _verify(root, documents, choices, node_executable)
    if _read_sources(root) != sources:
        raise storage.ConfigurationStoreError("UPDATE_FAILED")
    return report


@_guard
def verify_offline_migration(offline, *, node_executable, unselected_intents=None):
    """Reuse verification inside the stronger deployment/offline fence.

    Only a live thread-bound OfflineInstallation can avoid lease reacquisition.
    No arbitrary identity dictionary, closed backend or environment can do so.
    """
    if type(offline) is not OfflineInstallation:
        raise storage.ConfigurationStoreError("INVALID_ARGUMENT")
    offline._check()
    if offline.backend != "json":
        raise storage.ConfigurationStoreError("INVALID_CONFIGURATION")
    return _verify_locked(offline.root, node_executable, unselected_intents)
