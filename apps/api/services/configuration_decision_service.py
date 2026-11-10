"""Explicit inactive preparation only; no API, CLI or production startup hook."""

from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from alphalab.utils import configuration_decision as protocol
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_deployment import DeploymentPreparationError, coordinated_offline
from alphalab.utils.configuration_maintenance import configuration_access
from alphalab.utils.configuration_store import ConfigurationStore, ConfigurationStoreError
from apps.api.services import configuration_migration_service as migration
from apps.api.services.configuration_profile_service import ProfileSnapshot, _guard

_NODE_MODULE = (
    Path(__file__).resolve().parents[3] / "integrations/conexus/configuration-decision.mjs"
)
_NODE_CHECK = """
let input = '';
for await (const part of process.stdin) input += part;
try {
  const value = JSON.parse(input);
  const { validateDecisionForRoot } = await import(MODULE_URL);
  await validateDecisionForRoot(value.record, value.root);
  process.stdout.write('verified');
} catch { process.exitCode = 1; }
""".replace("MODULE_URL", json.dumps(_NODE_MODULE.as_uri()))


def _verify_record_node(record, root, node_executable):
    result = subprocess.run(
        [str(node_executable), "--input-type=module", "--eval", _NODE_CHECK],
        input=json.dumps({"root": str(root), "record": record._wire}).encode("utf-8"),
        capture_output=True,
        timeout=15,
        **migration._subprocess_options(),
    )
    if result.returncode != 0 or result.stdout != b"verified":
        raise ConfigurationStoreError("STORE_UNAVAILABLE")


def _identity(record, snapshot):
    value = record.as_dict()
    if value["installation_id"] != snapshot.installation_id:
        raise ConfigurationStoreError("INSTALLATION_MISMATCH")
    if value["revision"] != snapshot.revision:
        # Only the unchanged activation anchor is supported in this foundation.
        # Descendant validation for live saves is an explicit cutover prerequisite.
        raise ConfigurationStoreError("REVISION_CONFLICT")
    kinds = {key: choice["kind"] for key, choice in snapshot.selections.items()}
    if value["evidence"]["selection_kinds"] != kinds:
        raise ConfigurationStoreError("INVALID_CONFIGURATION")


def _legacy_equivalent(root, snapshot):
    overrides = {
        key: choice["kind"]
        for key, choice in snapshot.selections.items()
        if choice["kind"] != "profile"
    }
    sources = migration._read_sources(root)
    documents, selections = migration._prepare(sources, overrides)
    if documents != snapshot.documents or selections != snapshot.selections:
        raise ConfigurationStoreError("UPDATE_FAILED")
    if migration._read_sources(root) != sources:
        raise ConfigurationStoreError("UPDATE_FAILED")


@contextmanager
def _offline(decision):
    code = None
    try:
        with coordinated_offline(decision, stop_services=lambda: None, timeout=0) as offline:
            yield offline
    except DeploymentPreparationError as error:
        code = "STORE_BUSY" if error.code == "BUSY" else "STORE_UNAVAILABLE"
    if code is not None:
        raise ConfigurationStoreError(code)


@_guard
def inspect_configuration_decision(root):
    """Strict observation, never a runtime backend constructor or activation.

    JSON compatibility is returned only for a genuinely absent namespace.
    Existing records require an existing matching SQLite snapshot. No repairs,
    history selection, environment lookup or fallback follows any read failure.
    """
    protocol.root_identity(root)
    with configuration_access(root):
        record = protocol.read_decision_record(root)
        if record is None:
            return {"state": "legacy", "backend": "json"}
        snapshot = ConfigurationStore.open_existing(root).read_snapshot()
        ProfileSnapshot(snapshot, {})
        _identity(record, snapshot)
        if record.as_dict()["state"] == "prepared":
            _legacy_equivalent(root, snapshot)
        return record.as_dict()


@_guard
def prepare_sqlite_candidate(
    decision,
    *,
    node_executable,
    expected_installation_id,
    expected_revision,
    acknowledge_offline=False,
    expected_decision_id=None,
    unselected_intents=None,
):
    """Verify a first-generation staged store and publish inactive metadata.

    The caller must stop all unmanaged processes before acknowledging offline.
    The closed JSON decision supplies installation scope, not activation power.
    No process is stopped here. There is intentionally no committed publication
    primitive: every supported startup still needs the next integration phase.
    """
    if (
        acknowledge_offline is not True
        or type(decision) is not ConfigurationBackend
        or decision.mode != "json"
        or not decision._closed
    ):
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    if any(
        type(value) is not str or not protocol._ID.fullmatch(value)
        for value in (expected_installation_id, expected_revision)
    ):
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    if expected_decision_id is not None and (
        type(expected_decision_id) is not str or not protocol._ID.fullmatch(expected_decision_id)
    ):
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    # Existing order: execution admission -> maintenance -> legacy child locks.
    # No upgrades, owner bypass, second SQLite owner or claim of Node OS leases.
    with _offline(decision) as offline:
        current = protocol.read_decision_record(offline.root)
        if current is not None:
            value = current.as_dict()
            if value["state"] != "prepared" or value["decision_id"] != expected_decision_id:
                raise ConfigurationStoreError("REVISION_CONFLICT")
        elif expected_decision_id is not None:
            raise ConfigurationStoreError("REVISION_CONFLICT")
        sources = migration._read_sources(offline.root)
        report = migration.verify_offline_migration(
            offline, node_executable=node_executable, unselected_intents=unselected_intents
        )
        if report.installation_id != expected_installation_id:
            raise ConfigurationStoreError("INSTALLATION_MISMATCH")
        if report.revision != expected_revision:
            raise ConfigurationStoreError("REVISION_CONFLICT")
        snapshot = ConfigurationStore.open_existing(offline.root).read_snapshot()
        view = ProfileSnapshot(snapshot, {})
        for service, selection in snapshot.selections.items():
            if selection["kind"] == "environment":
                # No ambient credentials are evidence of portable readiness.
                # First-use environment admission remains a cutover prerequisite.
                raise ConfigurationStoreError("INVALID_CONFIGURATION")
            if selection["kind"] == "profile":
                view.active_config(service)
        record = current or protocol.parse_decision_record(
            json.dumps(
                {
                    "protocol_version": 1,
                    "state": "prepared",
                    "backend": "sqlite",
                    "root_key": protocol.root_identity(offline.root),
                    "installation_id": expected_installation_id,
                    "decision_id": uuid4().hex,
                    "revision": expected_revision,
                    "evidence": {
                        "kind": "legacy-exact-v1",
                        "verification_id": uuid4().hex,
                        "migration_revision": expected_revision,
                        "store_protocol_version": 1,
                        "selection_kinds": {
                            key: choice["kind"] for key, choice in snapshot.selections.items()
                        },
                    },
                }
            ),
            root_key=protocol.root_identity(offline.root),
        )
        _identity(record, snapshot)
        _verify_record_node(record, offline.root, node_executable)
        _check_sources(offline.root, sources, expected_revision)
        if current is not None:
            return current.as_dict()  # Explicit expected-ID acknowledgement only.
        published = protocol._publish_prepared(offline, record, expected_decision_id=None)
        _check_sources(offline.root, sources, expected_revision)
        return published.as_dict()


def _check_sources(root, sources, revision):
    if migration._read_sources(root) != sources:
        raise ConfigurationStoreError("UPDATE_FAILED")
    if ConfigurationStore.open_existing(root).read_snapshot().revision != revision:
        raise ConfigurationStoreError("REVISION_CONFLICT")
