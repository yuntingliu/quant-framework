"""Explicit offline administration; never mounted as an HTTP activation route."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from alphalab.utils import configuration_decision as protocol
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStore, ConfigurationStoreError
from apps.api.services import configuration_decision_service as preparation
from apps.api.services import configuration_migration_service as migration
from apps.api.services.configuration_profile_service import ProfileSnapshot, _guard

_REPOSITORY = Path(__file__).resolve().parents[3]
_CORE = _REPOSITORY / "build/conexus/apps/local-host/src/index.mjs"
_MANIFEST = _REPOSITORY / "build/conexus/UPSTREAM.json"
_LOCK = _REPOSITORY / "integrations/conexus/runtime.lock.json"


def require_agent_runtime(node_executable):
    """Check the installed pinned launch contract, without installing/probing providers.

    Import/export compatibility is not real-bundle execution certification.
    Tests may substitute these private paths, never an HTTP/environment option.
    """
    migration._require_node(node_executable)
    if not _CORE.is_file() or not _MANIFEST.is_file():
        raise ConfigurationStoreError("STORE_UNAVAILABLE")
    if json.loads(_MANIFEST.read_bytes()) != json.loads(_LOCK.read_bytes()):
        raise ConfigurationStoreError("UNSUPPORTED_PROTOCOL")
    program = """
try {
 const core = await import(CORE);
 if (!['createLocalHost','createLocalRuntime','createModelCompletion','compileHarnessWorkspace'].every(key => typeof core[key] === 'function')) throw 0;
 process.stdout.write('compatible');
} catch { process.exitCode=1; }
""".replace("CORE", json.dumps(_CORE.as_uri()))
    result = subprocess.run(
        [str(node_executable), "--input-type=module", "--eval", program],
        capture_output=True,
        timeout=15,
        **migration._subprocess_options(),
    )
    if result.returncode or result.stdout != b"compatible":
        raise ConfigurationStoreError("STORE_UNAVAILABLE")


@_guard
def activate_sqlite_candidate(
    decision,
    *,
    node_executable,
    expected_installation_id,
    expected_decision_id,
    expected_revision,
    acknowledge_offline=False,
    acknowledge_current_credentials=False,
):
    """Commit a fresh prepared candidate, with no repair, retries or rollback.

    The administrator attests current native files are current credentials, not
    restored history. Source equivalence is verified independently under actual
    exclusive leases. Provider revocation and whole-root provenance are not
    knowable offline; historical restore remains prohibited pending Phase 3C.
    """
    if (
        type(decision) is not ConfigurationBackend
        or decision.mode != "json"
        or not decision._closed
        or acknowledge_offline is not True
        or acknowledge_current_credentials is not True
        or any(
            type(value) is not str or not protocol._ID.fullmatch(value)
            for value in (expected_installation_id, expected_decision_id, expected_revision)
        )
    ):
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    with preparation._offline(decision) as offline:
        record = protocol.read_decision_record(offline.root)
        if record is None or record.as_dict()["state"] != "prepared":
            raise ConfigurationStoreError("REVISION_CONFLICT")
        value = record.as_dict()
        if value["decision_id"] != expected_decision_id or value["revision"] != expected_revision:
            raise ConfigurationStoreError("REVISION_CONFLICT")
        if value["installation_id"] != expected_installation_id:
            raise ConfigurationStoreError("INSTALLATION_MISMATCH")
        snapshot = ConfigurationStore.open_existing(offline.root).read_snapshot()
        preparation._identity(record, snapshot)
        choices = snapshot.selections
        if any(choice["kind"] == "environment" for choice in choices.values()):
            raise ConfigurationStoreError("INVALID_CONFIGURATION")
        sources = migration._read_sources(offline.root)
        migration.verify_offline_migration(
            offline,
            node_executable=node_executable,
            unselected_intents={
                key: "none" for key, choice in choices.items() if choice["kind"] == "none"
            },
        )
        view = ProfileSnapshot(snapshot, {})
        for name, choice in choices.items():
            if choice["kind"] == "profile":
                view.active_config(name)
        require_agent_runtime(node_executable)
        preparation._verify_record_node(record, offline.root, node_executable)
        preparation._legacy_equivalent(offline.root, snapshot)
        preparation._check_sources(offline.root, sources, expected_revision)
        # This is the sole publication point. Every check above and the CAS
        # below run inside the same execution/Agent/maintenance-exclusive fence.
        return protocol._publish_committed(offline, record).as_dict()
