"""Portable transfer over actual backend ownership. No activation or probes.

Private values exist only in detached in-memory candidates. Durable plans and
logical undo descriptions contain Phase 4A metadata, never native snapshots.
"""

from __future__ import annotations

import copy
import os
import time
from functools import wraps
from uuid import uuid4

from alphalab.utils import configuration_store as storage
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_maintenance import configuration_snapshot_access, execution_access
from alphalab.utils.configuration_transfer_plans import TransferPlanError, transfer_plans
from apps.api.services import configuration_migration_service as migration
from apps.api.services import configuration_schema as schema
from apps.api.services.configuration_profile_service import (
    ConfigurationProfileServices,
    ProfileSnapshot,
    _credential_values,
    _snapshot_credential_values,
)
from apps.api.services.configuration_profile_service import (
    _guard as _profile_guard,
)

SERVICES = schema.SERVICES
SECRET = schema._SECRET_FIELD
FIELDS = schema._PROFILE_FIELDS
TTL_SECONDS = 600
_MESSAGES = {
    **schema._MESSAGES,
    "INVALID_REQUEST": "Check the bounded configuration-transfer request.",
    "INVALID_TRANSFER": "Check the portable configuration document and references.",
    "UNSUPPORTED_VERSION": "The configuration schema version is unsupported.",
    "UNSAFE_METADATA": "Configuration metadata contains an unsafe or private value.",
    "UNSUPPORTED_JSON_APPLY": "Atomic import and logical rollback require an already selected SQLite backend.",
    "UNSUPPORTED_NATIVE_FIELDS": "The selected service has native fields unsupported by portable transfer.",
    "ENVIRONMENT_SELECTION_UNSUPPORTED": "Durable SQLite import cannot select environment fallback.",
    "CREDENTIAL_DECISION_REQUIRED": "Choose a credential policy for every imported profile.",
    "RETENTION_NOT_ELIGIBLE": "Credential retention requires the same existing profile and destination identity.",
    "PRIVATE_INPUT_REQUIRED": "Supply the requested fresh credentials through the private apply input.",
    "REVOKED_CREDENTIAL": "A known historical credential cannot be reactivated.",
    "CONSENT_REQUIRED": "Confirm the exact operations and consent requirements from the preview.",
    "STALE_PREVIEW": "Configuration changed after preview; create a new preview.",
    "RECEIPT_USED": "This receipt was already attempted. Inspect its operation; do not replay it.",
    "RECEIPT_EXPIRED": "The preview expired; create a new preview.",
    "WRONG_INSTALLATION": "The receipt does not belong to the current installation and authority.",
    "ROLLBACK_CONFLICT": "The originating operation is unconfirmed or no longer the current configuration.",
    "APPLICATION_UNCERTAIN": "Application was not confirmed. Inspect the operation and current revision; do not replay it.",
    "APPLICATION_REJECTED": "No new revision was observed. The receipt was consumed; create a new preview before another attempt.",
    "PLAN_LIMIT": "Transfer plan capacity is exhausted; administrative review is required.",
    "UNKNOWN_RECEIPT": "The transfer receipt or originating operation is unavailable.",
    "INVALID_PLAN": "Transfer records are incomplete or invalid; administrative review is required.",
    "HISTORY_CAPACITY": "Configuration history capacity is exhausted; no transfer was published.",
    "BUSY": "Configuration transfer or maintenance is busy.",
    "UNAVAILABLE": "Configuration transfer is unavailable.",
}


class TransferError(RuntimeError):
    def __init__(self, code="UNAVAILABLE"):
        self.code = code if type(code) is str and code in _MESSAGES else "UNAVAILABLE"
        super().__init__(_MESSAGES[self.code])


def _guard(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        code, interruption = "UNAVAILABLE", None
        try:
            return function(*args, **kwargs)
        except BaseException as error:
            family = storage._interruption_family(type(error))
            fallback = (
                None
                if family is None
                else family(1 if family is SystemExit else "Configuration transfer interrupted.")
            )
            try:
                interruption = storage._redacted_interruption(error)
                kind = type(error)
                if issubclass(kind, (TransferError, TransferPlanError)):
                    code = storage._exception_field(error, "code")
                elif issubclass(kind, schema.TransferValidationError):
                    value = storage._exception_field(error, "code")
                    code = value if value in schema._MESSAGES else "INVALID_TRANSFER"
                elif issubclass(kind, storage.ConfigurationStoreError):
                    value = storage._exception_field(error, "code")
                    code = {
                        "STORE_BUSY": "BUSY",
                        "REVISION_CONFLICT": "STALE_PREVIEW",
                        "HISTORY_CAPACITY": "HISTORY_CAPACITY",
                        "INVALID_CONFIGURATION": "UNSAFE_METADATA",
                    }.get(value, "UNAVAILABLE")
            except BaseException:
                interruption = fallback
        storage._raise_redacted(interruption if interruption is not None else TransferError(code))

    return guarded


def _fail(code):
    raise TransferError(code)


def _keys(value, allowed, required=None):
    if (
        type(value) is not dict
        or set(value) - set(allowed)
        or not set(required or ()).issubset(value)
    ):
        _fail("INVALID_REQUEST")
    return value


def _project(documents, selections):
    snapshots = schema.project_settings_snapshots(**documents, selections=selections)
    return schema.export_configuration(**snapshots)


def _view(documents, selections):
    """Comparison view only. Unknown native fields are NEVER silently exported.

    They remain raw/untouched; selecting their service is unsupported. This
    permits an unrelated service edit without rewriting private extensions.
    """
    clean, unmanaged = {}, set()
    for service, original in documents.items():
        fields = FIELDS[service] | {SECRET[service]}
        if service == "search" and original["version"] == 1:
            allowed = {"version", "provider", "model_profile_id", "brave_api_key"}
            if set(original) - allowed:
                unmanaged.add(service)
            clean[service] = {key: value for key, value in original.items() if key in allowed}
            continue
        if set(original) - {"version", "active_id", "profiles"}:
            unmanaged.add(service)
        rows = []
        for row in original["profiles"]:
            if set(row) - fields:
                unmanaged.add(service)
            rows.append({key: value for key, value in row.items() if key in fields})
        clean[service] = {
            "version": original["version"],
            "active_id": original["active_id"],
            "profiles": rows,
        }
    return _project(clean, selections), unmanaged


def _scan(document, values):
    for service in SERVICES:
        schema._reject_known_credentials(service, document[service], list(values))


def _identity(service, row):
    fields = {
        "models": ("base_url", "model"),
        "search": ("provider", "model_profile_id"),
        "rqdata": ("provider", "user", "host"),
    }[service]
    return tuple(row.get(key) for key in fields)


def _rows(document, service):
    native = document[service]
    if service == "search" and native["version"] == 1:
        from apps.api.services.search_provider_service import validate_settings

        native = validate_settings(native)
    return {row["id"]: row for row in native["profiles"]}


def _options(services, mode, apply_selections, policy):
    if (
        type(services) is not list
        or not services
        or any(type(key) is not str or key not in SERVICES for key in services)
        or len(services) != len(set(services))
        or type(mode) is not str
        or mode not in {"merge", "replace"}
        or type(apply_selections) is not bool
        or type(policy) is not list
        or len(policy) > 60
    ):
        _fail("INVALID_REQUEST")
    result = {}
    for item in policy:
        _keys(item, {"service", "id", "action"}, {"service", "id", "action"})
        if (
            type(item["service"]) is not str
            or item["service"] not in services
            or not storage._identifier(item["id"])
            or type(item["action"]) is not str
            or item["action"] not in {"retain", "clear", "replace"}
        ):
            _fail("INVALID_REQUEST")
        key = (item["service"], item["id"])
        if key in result:
            _fail("INVALID_REQUEST")
        result[key] = item["action"]
    return result


def _native(current, document, services, mode, apply_selections, decisions, fresh=None):
    documents, choices = current["documents"], current["selections"]
    result, selections = copy.deepcopy(documents), copy.deepcopy(choices)
    expected_policy = {
        (service, row["id"]) for service in services for row in document[service]["profiles"]
    }
    if set(decisions) - expected_policy:
        _fail("INVALID_REQUEST")
    undecided = expected_policy - set(decisions)
    for service in services:
        previous = _rows(documents, service)
        rows = copy.deepcopy(previous) if mode == "merge" else {}
        for metadata in document[service]["profiles"]:
            identifier = metadata["id"]
            action = decisions.get((service, identifier))
            old = previous.get(identifier)
            value = ""
            if action == "retain":
                if old is None or _identity(service, old) != _identity(service, metadata):
                    _fail("RETENTION_NOT_ELIGIBLE")
                value = old.get(SECRET[service], "")
            elif action == "replace" and fresh is not None:
                value = fresh[(service, identifier)]
            rows[identifier] = {**metadata, SECRET[service]: value}
        selection = document[service]["selection"] if apply_selections else choices[service]
        selections[service] = copy.deepcopy(selection)
        result[service] = {
            "version": 2 if service == "search" else 1,
            "active_id": selection.get("id") if selection["kind"] == "profile" else None,
            "profiles": list(rows.values()),
        }
    storage._encode(result, selections)
    _view(result, selections)  # Complete prospective metadata and reference validation.
    return result, selections, undecided


def _missing(document, documents, decisions):
    """Read actual merged credentials; replacements await their private input."""
    availability = {}
    for service in SERVICES:
        for row in _rows(documents, service).values():
            key = (service, row["id"])
            availability[key] = decisions.get(key) == "replace" or bool(
                row.get(SECRET[service], "").strip()
            )
    missing = []
    for service in SERVICES:
        for row in document[service]["profiles"]:
            needed = False
            if service == "models":
                from urllib.parse import urlsplit

                needed = (
                    urlsplit(row["base_url"]).hostname not in {"localhost", "127.0.0.1", "::1"}
                    and not availability[(service, row["id"])]
                )
            elif service == "rqdata":
                needed = not availability[(service, row["id"])]
            elif row["provider"] == "brave":
                needed = not availability[(service, row["id"])]
            elif row["provider"] == "openrouter":
                reference = row["model_profile_id"] or document["models"]["selection"].get("id")
                needed = reference is None or not availability.get(("models", reference), False)
            if needed:
                missing.append({"service": service, "id": row["id"], "code": "CREDENTIAL_REQUIRED"})
            if decisions.get((service, row["id"])) == "replace":
                missing.append(
                    {"service": service, "id": row["id"], "code": "PRIVATE_INPUT_REQUIRED"}
                )
    return missing


class ConfigurationTransferService:
    __slots__ = ("backend",)

    def __init__(self, backend):
        if type(backend) is not ConfigurationBackend:
            _fail("INVALID_REQUEST")
        self.backend = backend

    def __repr__(self):
        return "ConfigurationTransferService(<private>)"

    def __reduce_ex__(self, _protocol):
        raise TypeError("Private transfer services cannot be serialized.")

    def _current(self):
        if self.backend.mode == "sqlite":
            snapshot = self.backend.store.with_history_checks().read_snapshot()
            profiles = ProfileSnapshot(snapshot, {})
            return {
                "documents": snapshot.documents,
                "selections": snapshot.selections,
                "revision": snapshot.revision,
                "values": _snapshot_credential_values(snapshot),
                "profile_snapshot": profiles,
            }
        # The caller holds backend use; every supported JSON writer holds the
        # shared snapshot lease for its complete read-modify-write operation.
        with configuration_snapshot_access(self.backend.root, exclusive=True):
            sources = migration._read_sources(self.backend.root)
            documents, selections = migration._prepare(sources, {})
            if migration._read_sources(self.backend.root) != sources:
                _fail("BUSY")
            values = _credential_values(documents)
            staged = self.backend.root / "app/secrets/configuration-store.sqlite3"
            if os.path.lexists(staged):
                snapshot = (
                    storage.ConfigurationStore.open_existing(self.backend.root)
                    .with_history_checks()
                    .read_snapshot()
                )
                values.update(_snapshot_credential_values(snapshot))
            return {
                "documents": documents,
                "selections": selections,
                "revision": None,
                "values": values,
            }

    def _binding(self):
        from alphalab.utils.configuration_decision import root_identity

        value = self.backend.public_identity()
        return {
            "root_key": root_identity(self.backend.root),
            "backend": self.backend.mode,
            "installation_id": value["installation_id"],
            "decision_id": value["decision_id"],
        }

    @_guard
    def export(self):
        with self.backend.borrow():
            current = self._current()
            document = _project(current["documents"], current["selections"])
            _scan(document, current["values"])
            return schema.serialize_transfer_document(document)

    @_guard
    def preview(self, configuration, *, services, mode, apply_selections, credential_policy):
        document = (
            schema.parse_transfer_json(configuration)
            if type(configuration) in {str, bytes}
            else schema.validate_transfer_document(configuration)
        )
        source = (
            schema._decode_json(configuration)
            if type(configuration) in {str, bytes}
            else configuration
        )
        migrations = [
            key.upper() + "_ACTIVE_ID_SELECTION" for key in SERVICES if "active_id" in source[key]
        ]
        if source["search"]["version"] == 1:
            migrations.append("SEARCH_V1_TO_V2")
        with self.backend.borrow(), execution_access(self.backend.root):
            current = self._current()
            return self._preview(
                current,
                document,
                services,
                mode,
                apply_selections,
                credential_policy,
                kind="import",
                origin=None,
                migrations=migrations,
            )

    def _preview(
        self,
        current,
        document,
        services,
        mode,
        apply_selections,
        policy,
        *,
        kind,
        origin,
        migrations=(),
    ):
        decisions = _options(services, mode, apply_selections, policy)
        _scan(document, current["values"])
        before, unmanaged = _view(current["documents"], current["selections"])
        documents, selections, undecided = _native(
            current, document, services, mode, apply_selections, decisions
        )
        after, _ = _view(documents, selections)
        _scan(after, current["values"])
        changes, consent = {}, {"APPLY_CONFIGURATION"}
        for service in SERVICES:
            old = {row["id"]: row for row in before[service]["profiles"]}
            new = {row["id"]: row for row in after[service]["profiles"]}
            changes[service] = {
                "added": sorted(new.keys() - old.keys()),
                "removed": sorted(old.keys() - new.keys()),
                "changed": sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
                "unchanged": sorted(key for key in old.keys() & new.keys() if old[key] == new[key]),
                "selection_before": before[service]["selection"],
                "selection_after": after[service]["selection"],
            }
            if changes[service]["removed"]:
                consent.add("REMOVE_PROFILES")
            if before[service]["selection"] != after[service]["selection"]:
                consent.add("CHANGE_SELECTION")
        if "clear" in decisions.values():
            consent.add("CLEAR_CREDENTIALS")
        missing = _missing(after, documents, decisions)
        if any(row["code"] == "CREDENTIAL_REQUIRED" for row in missing):
            consent.add("ALLOW_UNCONFIGURED")
        unsupported = []
        if self.backend.mode != "sqlite":
            unsupported.append("UNSUPPORTED_JSON_APPLY")
        if unmanaged & set(services):
            unsupported.append("UNSUPPORTED_NATIVE_FIELDS")
        if self.backend.mode == "sqlite" and any(
            choice["kind"] == "environment" for choice in selections.values()
        ):
            unsupported.append("ENVIRONMENT_SELECTION_UNSUPPORTED")
        if undecided:
            unsupported.append("CREDENTIAL_DECISION_REQUIRED")
        result = {
            "kind": kind,
            "origin_operation": origin,
            "backend": self.backend.mode,
            "can_apply": not unsupported,
            "revision": current["revision"],
            "changes": changes,
            "configuration": after,
            "missing_credentials": missing,
            "credential_actions": copy.deepcopy(policy),
            "selection_explanations": {
                key: {
                    "kind": selections[key]["kind"],
                    "message": {
                        "profile": "Saved profile only; missing credentials do not enable fallback.",
                        "environment": "Environment intent only; this operation does not resolve environment values.",
                        "none": "Explicit disablement or unselection; no environment fallback.",
                    }[selections[key]["kind"]],
                }
                for key in SERVICES
            },
            "required_consents": sorted(consent),
            "diagnostics": [{"code": code, "message": _MESSAGES[code]} for code in unsupported],
            "migrations": list(migrations),
            "receipt": None,
        }
        if not unsupported:
            plan = {
                "version": 1,
                "id": uuid4().hex,
                "kind": kind,
                "state": "pending",
                "expires": int(time.time()) + TTL_SECONDS,
                "binding": self._binding(),
                "revision": current["revision"],
                "configuration": schema.serialize_transfer_document(document),
                "before": schema.serialize_transfer_document(before),
                "services": services,
                "mode": mode,
                "apply_selections": apply_selections,
                "policy": policy,
                "consents": sorted(consent),
                "origin": origin,
                "result_revision": None,
            }
            with transfer_plans(self.backend.root, create=True) as plans:
                plans.write(plan, new=True)
            result.update(receipt=plan["id"], expires=plan["expires"])
        return result

    def _check_binding(self, plan, current):
        if plan["binding"] != self._binding():
            _fail("WRONG_INSTALLATION")
        if plan["state"] != "pending":
            _fail("RECEIPT_USED")
        if int(time.time()) >= plan["expires"]:
            _fail("RECEIPT_EXPIRED")
        if plan["revision"] != current["revision"]:
            _fail("STALE_PREVIEW")

    def _fresh(self, values, decisions, current, document):
        if type(values) is not list or len(values) > 60:
            _fail("INVALID_REQUEST")
        fresh = {}
        for row in values:
            _keys(row, {"service", "id", "value"}, {"service", "id", "value"})
            if (
                type(row["service"]) is not str
                or row["service"] not in SERVICES
                or not storage._identifier(row["id"])
            ):
                _fail("PRIVATE_INPUT_REQUIRED")
            key = (row["service"], row["id"])
            value = row["value"]
            if (
                key in fresh
                or type(value) is not str
                or not value.strip()
                or len(value) > 8192
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
            ):
                _fail("PRIVATE_INPUT_REQUIRED")
            fresh[key] = value.strip()
        if set(fresh) != {key for key, action in decisions.items() if action == "replace"}:
            _fail("PRIVATE_INPUT_REQUIRED")
        for (service, identifier), value in fresh.items():
            old = _rows(current["documents"], service).get(identifier)
            metadata = next(row for row in document[service]["profiles"] if row["id"] == identifier)
            authorized_current = (
                old is not None
                and _identity(service, old) == _identity(service, metadata)
                and old.get(SECRET[service], "").strip() == value
            )
            if value in current["values"] and not authorized_current:
                _fail("REVOKED_CREDENTIAL")
        _scan(document, current["values"] | set(fresh.values()))
        return fresh

    def _publish(self, documents, selections, revision):
        return self.backend.store.with_history_checks().update(
            lambda previous: (documents, selections), expected_revision=revision
        )

    @_profile_guard
    def _preflight_publication(self, current, documents, selections):
        """Use the ordinary writers' full public/secret contract before claim."""
        previous = current["profile_snapshot"]
        proposed = ProfileSnapshot(
            storage.ConfigurationSnapshot(
                previous.installation_id,
                current["revision"],
                storage._encode(documents, selections),
            ),
            {},
        )
        ConfigurationProfileServices(self.backend.store)._check_publication(previous, proposed)

    @_guard
    def apply(self, receipt, *, expected_revision, confirm, consents, credentials, kind="import"):
        if (
            confirm is not True
            or type(consents) is not list
            or any(type(code) is not str for code in consents)
            or len(consents) != len(set(consents))
        ):
            _fail("CONSENT_REQUIRED")
        if self.backend.mode != "sqlite":
            _fail("UNSUPPORTED_JSON_APPLY")
        with (
            self.backend.borrow(),
            execution_access(self.backend.root),
            transfer_plans(self.backend.root) as plans,
        ):
            plan = plans.read(receipt)
            current = self._current()
            self._check_binding(plan, current)
            if (
                plan["kind"] != kind
                or expected_revision != plan["revision"]
                or set(consents) != set(plan["consents"])
            ):
                _fail("CONSENT_REQUIRED")
            document = schema.parse_transfer_json(plan["configuration"])
            decisions = _options(
                plan["services"], plan["mode"], plan["apply_selections"], plan["policy"]
            )
            _, existing_unmanaged = _view(current["documents"], current["selections"])
            if existing_unmanaged & set(plan["services"]):
                _fail("UNSUPPORTED_NATIVE_FIELDS")
            fresh = self._fresh(credentials, decisions, current, document)
            _scan(
                schema.parse_transfer_json(plan["before"]), current["values"] | set(fresh.values())
            )
            documents, selections, undecided = _native(
                current,
                document,
                plan["services"],
                plan["mode"],
                plan["apply_selections"],
                decisions,
                fresh,
            )
            if undecided:
                _fail("CREDENTIAL_DECISION_REQUIRED")
            view, unmanaged = _view(documents, selections)
            if unmanaged & set(plan["services"]):
                _fail("UNSUPPORTED_NATIVE_FIELDS")
            if any(value["kind"] == "environment" for value in selections.values()):
                _fail("ENVIRONMENT_SELECTION_UNSUPPORTED")
            _scan(view, current["values"] | set(fresh.values()))
            self._preflight_publication(current, documents, selections)
            if kind == "rollback":
                self._rollback_source(plans, plan["origin"], current)
            plans.claim(receipt)  # Durable, one use even after process death.
            try:
                result = self._publish(documents, selections, current["revision"])
            except BaseException as error:
                interruption = storage._redacted_interruption(error)
                observed = None
                try:
                    observed = self.backend.store.read_snapshot().revision
                    plan["state"] = "rejected" if observed == current["revision"] else "uncertain"
                    plans.write(plan)
                except BaseException:
                    pass
                if interruption is not None:
                    storage._raise_redacted(interruption)
                if observed == current["revision"] and issubclass(
                    type(error), storage.ConfigurationStoreError
                ):
                    code = storage._exception_field(error, "code")
                    if code == "HISTORY_CAPACITY":
                        _fail("HISTORY_CAPACITY")
                    if code == "REVISION_CONFLICT":
                        _fail("STALE_PREVIEW")
                if observed == current["revision"]:
                    _fail("APPLICATION_REJECTED")
                _fail("APPLICATION_UNCERTAIN")
            plan["state"], plan["result_revision"] = "committed", result.revision
            try:
                plans.write(plan)
            except Exception:
                _fail("APPLICATION_UNCERTAIN")
            return {
                "operation": plan["id"],
                "state": "committed",
                "revision": result.revision,
                "kind": kind,
            }

    def _rollback_source(self, plans, identifier, current):
        original = plans.read(identifier)
        if (
            original["binding"] != self._binding()
            or original["state"] != "committed"
            or original["kind"] != "import"
            or original["result_revision"] != current["revision"]
        ):
            _fail("ROLLBACK_CONFLICT")
        return original

    @_guard
    def rollback_preview(self, operation, *, credential_policy):
        if self.backend.mode != "sqlite":
            _fail("UNSUPPORTED_JSON_APPLY")
        with self.backend.borrow(), execution_access(self.backend.root):
            current = self._current()
            with transfer_plans(self.backend.root) as plans:
                original = self._rollback_source(plans, operation, current)
            document = schema.parse_transfer_json(original["before"])
            return self._preview(
                current,
                document,
                original["services"],
                "replace",
                True,
                credential_policy,
                kind="rollback",
                origin=operation,
            )

    @_guard
    def operation(self, identifier):
        with self.backend.borrow(), transfer_plans(self.backend.root) as plans:
            value = plans.read(identifier)
            if value["binding"] != self._binding():
                _fail("WRONG_INSTALLATION")
            return {
                "operation": value["id"],
                "state": value["state"],
                "kind": value["kind"],
                "revision": value["result_revision"],
                "current_revision": self._current()["revision"],
            }
