"""Explicit, inactive SQLite service instances; never imported by live routes.

Construct with an already opened ConfigurationStore. No environment switch,
JSON adoption, recovery, initialization, provider I/O, or production singleton
exists here. Environment values, when needed, are explicitly supplied private
inputs. Saved-state validation and transaction callbacks never resolve them.
"""

from __future__ import annotations

import copy
from functools import wraps
from urllib.parse import unquote

from alphalab.dataio import rq_profiles as rq
from alphalab.utils import configuration_store as storage
from alphalab.utils.configuration_store import (
    ConfigurationSnapshot,
    ConfigurationStore,
    ConfigurationStoreError,
)
from apps.api.services import model_provider_service as models
from apps.api.services import search_provider_service as search

_SERVICES = ("models", "search", "rqdata")
_FIELDS = {
    "models": {"id", "name", "base_url", "model", "api_key"},
    "search": {"id", "name", "provider", "model_profile_id", "brave_api_key"},
    "rqdata": {"id", "name", "provider", "user", "host", "password"},
}
_SECRET = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}
# Match Phase 4A's bounded metadata decoding convention without invoking its
# export schema or changing native profile validation/selection rules.
_MAX_METADATA_DECODINGS = 5
_ENV_KEYS = {
    "CONEXUS_MODEL_BASE_URL",
    "CONEXUS_MODEL_ID",
    "CONEXUS_MODEL_API_KEY",
    "ALPHALAB_WEB_SEARCH_PROVIDER",
    "BRAVE_SEARCH_API_KEY",
    "ALPHALAB_AGENT_MODE",
    "RQ_USER",
    "RQ_HOST",
    "RQ_PASSWORD",
}


def _guard(function):
    """Reuse the storage boundary's audited interruption/exception redaction.

    Do not wrap these operations in storage._safe_call: they legitimately call
    public store methods, which enforce their own non-reentrancy boundary.
    """

    @wraps(function)
    def guarded(*args, **kwargs):
        code, interruption = "INVALID_CONFIGURATION", None
        try:
            return function(*args, **kwargs)
        except BaseException as exc:
            kind = storage._interruption_family(type(exc))
            fallback = (
                None
                if kind is None
                else kind(1 if kind is SystemExit else "Configuration operation interrupted.")
            )
            try:
                interruption = storage._redacted_interruption(exc)
                if issubclass(type(exc), ConfigurationStoreError):
                    code = storage._exception_field(exc, "code")
            except BaseException:
                interruption = fallback
        storage._raise_redacted(
            interruption if interruption is not None else ConfigurationStoreError(code)
        )

    return guarded


def _input(service, profile):
    if type(profile) is not dict or not set(profile).issubset(_FIELDS[service]):
        raise ValueError
    if any(
        type(value) is not str
        and not (
            value is None and service == "search" and key in {"id", "name", "model_profile_id"}
        )
        for key, value in profile.items()
    ):
        raise ValueError
    if service != "search" or profile.get("id") is not None:
        search._validate_profile_id(profile.get("id"))
    if service == "models":
        search._validate_profile_name(profile.get("name"))
    return copy.deepcopy(profile)


class PrivateConfiguration:
    """Opaque private values; only extract_private() grants a detached copy.

    No Mapping/sequence/dataclass protocol or instance __dict__ is exposed to
    framework serializers. This is not a sandbox against trusted introspection.
    """

    __slots__ = ("_value",)

    def __init__(self, value):
        self._value = copy.deepcopy(value)

    def __repr__(self):
        return "PrivateConfiguration(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Private configuration cannot be serialized implicitly.")

    def extract_private(self):
        return copy.deepcopy(self._value)


def _credential_values(documents, environment=None):
    """Include original and runtime-trimmed secrets; ignore absent/empty keys."""
    result = set()
    values = []
    for service, document in documents.items():
        rows = (
            [document]
            if service == "search" and document.get("version") == 1
            else document.get("profiles", [])
        )
        values.extend(row.get(_SECRET[service], "") for row in rows)
    if environment is not None:
        values.extend(
            environment.get(key, "")
            for key in ("CONEXUS_MODEL_API_KEY", "BRAVE_SEARCH_API_KEY", "RQ_PASSWORD")
        )
    for value in values:
        if type(value) is str and value.strip():
            result.update((value, value.strip()))
    return result


def _snapshot_credential_values(snapshot, environment=None):
    """Retained secrets are redaction canaries only, never fallback values."""
    values = _credential_values(snapshot.documents, environment)
    for documents in snapshot.extract_history_private():
        values.update(_credential_values(documents))
    return values


def _scan_metadata(value, credentials):
    """Reject known secrets in original/decoded public values, never echo them."""
    if not credentials:
        return
    pending = [value]
    while pending:
        candidate = pending.pop()
        if type(candidate) is dict:
            pending.extend(candidate.values())
        elif type(candidate) is list:
            pending.extend(candidate)
        elif type(candidate) is str:
            for _ in range(_MAX_METADATA_DECODINGS + 1):
                if any(secret in candidate for secret in credentials):
                    raise ValueError
                decoded = unquote(candidate, errors="replace")
                if decoded == candidate:
                    break
                candidate = decoded
            else:
                raise ValueError


def _scan_public_metadata(value, credentials):
    # Only these top-level envelope values come from the validated store, not
    # profile input. Recheck their format before exempting random UUID substrings
    # from secret matching. Profile IDs and all configurable fields stay scanned.
    storage_fields = {"revision", "installation_id"}
    for field in storage_fields:
        identifier = value[field]
        if type(identifier) is not str or not storage._OPAQUE_ID.fullmatch(identifier):
            raise ValueError
    _scan_metadata(
        {key: item for key, item in value.items() if key not in storage_fields}, credentials
    )


def _scan_profile_metadata(documents, credentials):
    for service, document in documents.items():
        rows = (
            [document]
            if service == "search" and document.get("version") == 1
            else document["profiles"]
        )
        for row in rows:
            _scan_metadata(
                {key: row[key] for key in _FIELDS[service] - {_SECRET[service]} if key in row},
                credentials,
            )


def _documents(snapshot, *, extra_credentials=frozenset()):
    documents = snapshot.documents
    # Collect before normalizing: otherwise search/RQData validation loses the
    # original password/key spelling, including surrounding whitespace.
    credentials = _snapshot_credential_values(snapshot) | extra_credentials
    _scan_profile_metadata(documents, credentials)
    # Views normalize legacy search in memory only. Untouched native documents
    # are preserved byte-for-byte by service updates below.
    documents["search"] = search.validate_settings(documents["search"])
    documents["rqdata"] = rq.validate_settings(documents["rqdata"])
    for row in documents["models"]["profiles"]:
        if any(key in row and type(row[key]) is not str for key in _FIELDS["models"]):
            raise ValueError
    _scan_profile_metadata(documents, credentials | _credential_values(documents))
    return documents


def _connection_config(service, profile):
    """Project validated native profiles to the existing private Test contracts."""
    if service == "rqdata":
        return {
            "RQ_USER": profile["user"],
            "RQ_PASSWORD": profile["password"],
            "RQ_HOST": profile["host"],
        }
    if service == "search":
        provider = profile["provider"]
        if provider == "off":
            return {"provider": "off"}
        if provider == "openrouter":
            return {"provider": "openrouter", "model_profile_id": profile["model_profile_id"]}
        return {"provider": "brave", "brave_api_key": profile["brave_api_key"]}
    return profile


class ProfileSnapshot:
    """One pinned three-service generation, with redacted public projections."""

    __slots__ = ("_snapshot", "_environment")

    def __init__(self, snapshot, environment):
        _documents(snapshot, extra_credentials=_credential_values({}, environment))
        self._snapshot, self._environment = snapshot, environment.copy()

    def __repr__(self):
        return "ProfileSnapshot(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Private configuration cannot be serialized implicitly.")

    @property
    def revision(self):
        return self._snapshot.revision

    @property
    def installation_id(self):
        return self._snapshot.installation_id

    @property
    def protocol_version(self):
        return self._snapshot.protocol_version

    @property
    def selections(self):
        return self._snapshot.selections

    @property
    def documents(self):
        return PrivateConfiguration(self._snapshot.documents)

    def _active(self, service):
        choice = self.selections[service]
        if choice["kind"] == "none":
            return None
        if choice["kind"] == "profile":
            document = _documents(self._snapshot)[service]
            return next(row for row in document["profiles"] if row["id"] == choice["id"])
        env = {key: value.strip() for key, value in self._environment.items()}
        if service == "models":
            if not env.get("CONEXUS_MODEL_BASE_URL") or not env.get("CONEXUS_MODEL_ID"):
                return None
            return {
                "id": "environment",
                "name": "Environment",
                "base_url": env["CONEXUS_MODEL_BASE_URL"],
                "model": env["CONEXUS_MODEL_ID"],
                "api_key": env.get("CONEXUS_MODEL_API_KEY", ""),
            }
        if service == "search":
            provider = env.get("ALPHALAB_WEB_SEARCH_PROVIDER", "brave").lower()
            if provider not in {"brave", "openrouter", "off"}:
                provider = "brave"
            return {
                "id": "environment",
                "name": "Environment",
                "provider": provider,
                "model_profile_id": None,
                "brave_api_key": env.get("BRAVE_SEARCH_API_KEY", ""),
            }
        if not all(env.get(key) for key in ("RQ_USER", "RQ_HOST", "RQ_PASSWORD")):
            return None
        return {
            "id": "environment",
            "name": "Environment",
            "provider": "rqdata",
            "user": env["RQ_USER"],
            "host": env["RQ_HOST"],
            "password": env["RQ_PASSWORD"],
        }

    def _validate_runtime(self, service, profile, *, allow_clear=False):
        if profile is None:
            raise ValueError
        if service == "models":
            return models.validate_profile(profile)
        if service == "rqdata":
            result = rq.validate_profile_input(profile)
            if not result["password"]:
                raise ValueError
            return result
        search.validate_runtime_profile(
            profile,
            allow_incomplete_brave=allow_clear,
            model_settings=_documents(self._snapshot)["models"],
            active_model=self._active("models"),
        )
        return profile

    @_guard
    def active_profile(self, service):
        if service not in _SERVICES:
            raise ValueError
        candidate = self._active(service)
        try:
            return PrivateConfiguration(self._validate_runtime(service, candidate))
        except ValueError:
            return None

    @_guard
    def active_config(self, service):
        active = self.active_profile(service)
        if active is None:
            raise ValueError
        return PrivateConfiguration(_connection_config(service, active.extract_private()))

    @_guard
    def public_settings(self, service):
        if service not in _SERVICES:
            raise ValueError
        document = _documents(self._snapshot)[service]
        environment = {key: value.strip() for key, value in self._environment.items()}
        choice, active = self.selections[service], self.active_profile(service)
        profiles = []
        for row in document["profiles"]:
            public = {key: row[key] for key in _FIELDS[service] - {_SECRET[service]} if key in row}
            public[
                {"models": "has_api_key", "search": "has_brave_api_key", "rqdata": "has_password"}[
                    service
                ]
            ] = bool(row.get(_SECRET[service]))
            if service == "search":
                try:
                    self._validate_runtime(service, row)
                    public["configured"] = True
                except ValueError:
                    public["configured"] = False
            profiles.append(public)
        result = {
            "active_id": document["active_id"],
            "profiles": profiles,
            "selection": choice,
            "revision": self.revision,
            "installation_id": self.installation_id,
            "source": "saved" if choice["kind"] == "profile" else choice["kind"],
            "configured": active is not None,
        }
        if service == "models":
            result.update(
                mode=environment.get("ALPHALAB_AGENT_MODE", "remote"),
                environment_configured=bool(
                    environment.get("CONEXUS_MODEL_BASE_URL")
                    and environment.get("CONEXUS_MODEL_ID")
                ),
            )
        elif service == "search":
            selected = self._active(service)
            result.update(
                mode=environment.get("ALPHALAB_AGENT_MODE", "remote"),
                scope="workstation",
                provider=selected["provider"] if selected else "off",
                model_profile_id=selected.get("model_profile_id") if selected else None,
                has_brave_api_key=bool(selected and selected.get("brave_api_key")),
            )
        else:
            result.update(
                provider="rqdata",
                environment_configured=all(
                    environment.get(key) for key in ("RQ_USER", "RQ_HOST", "RQ_PASSWORD")
                ),
            )
        # Public metadata must not accidentally repeat supplied credentials,
        # including credentials belonging to another service/inactive profile.
        _scan_public_metadata(
            result, _snapshot_credential_values(self._snapshot, self._environment)
        )
        return result


class _Service:
    __slots__ = ("_owner", "_name")

    def __init__(self, owner, name):
        self._owner, self._name = owner, name

    def __repr__(self):
        return "StoredProfileService(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Private configuration cannot be serialized implicitly.")

    def public_settings(self):
        return self._owner.read_snapshot().public_settings(self._name)

    def read_settings(self):
        return PrivateConfiguration(
            self._owner.read_snapshot().documents.extract_private()[self._name]
        )

    def active_profile(self):
        return self._owner.read_snapshot().active_profile(self._name)

    def active_config(self):
        return self._owner.read_snapshot().active_config(self._name)

    @_guard
    def save_profile(self, profile, *, activate, expected_revision=None, **options):
        clear_secret = self._clear_option(options)
        result = self._owner.save_profiles(
            {self._name: profile},
            activate=activate,
            clear_secrets={self._name} if clear_secret else set(),
            expected_revision=expected_revision,
        )
        return result.public_settings(self._name)

    def _clear_option(self, options):
        key = {
            "models": "clear_api_key",
            "search": "clear_brave_api_key",
            "rqdata": "clear_password",
        }[self._name]
        if set(options) - {key} or type(options.get(key, False)) is not bool:
            raise ValueError
        return options.get(key, False)

    @_guard
    def resolve_test_profile(self, profile, **options):
        return self._owner.resolve_test_profile(
            self._name, profile, clear_secret=self._clear_option(options)
        )

    def resolve_settings(self, profile, **options):
        return self.resolve_test_profile(profile, **options)

    @_guard
    def save_settings(self, settings, *, expected_revision=None):
        if self._name != "search" or type(settings) is not dict:
            raise ValueError
        return self.save_profile(
            {
                key: value
                for key, value in settings.items()
                if key not in {"activate", "clear_brave_api_key"}
            },
            activate=settings.get("activate", True),
            clear_brave_api_key=settings.get("clear_brave_api_key", False),
            expected_revision=expected_revision,
        )

    def select(self, choice, *, expected_revision=None):
        return self._owner.select(
            self._name, choice, expected_revision=expected_revision
        ).public_settings(self._name)


class ConfigurationProfileServices:
    """Explicit service factory; no production caller constructs this class.

    Each edit preflights one snapshot outside the transaction, then publishes
    with that exact expected revision. Conflicts are returned, never replayed.
    The transaction's callback returns only prepared native documents/intents;
    no service readers, environment lookup, probes, or filesystem work run there.
    """

    __slots__ = ("_store", "_environment", "models", "search", "rqdata")

    @_guard
    def __init__(self, store: ConfigurationStore, *, environment: dict[str, str] | None = None):
        if type(store) is not ConfigurationStore:
            raise ValueError
        values = {} if environment is None else environment
        if type(values) is not dict or any(
            type(key) is not str or type(value) is not str for key, value in values.items()
        ):
            raise ValueError
        self._store = store.with_history_checks()
        # Keep raw credentials available to the scanner. Runtime views normalize
        # explicit environment inputs outside storage transactions.
        self._environment = {key: value for key, value in values.items() if key in _ENV_KEYS}
        self.models, self.search, self.rqdata = (_Service(self, service) for service in _SERVICES)

    def __repr__(self):
        return "ConfigurationProfileServices(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Private configuration cannot be serialized implicitly.")

    @_guard
    def read_snapshot(self):
        return ProfileSnapshot(self._store.read_snapshot(), self._environment)

    def _prepare(self, service, profile, current, *, clear_secret):
        profile = _input(service, profile)
        settings = _documents(current._snapshot)[service]
        if service == "models":
            if clear_secret:
                previous = next(
                    (row for row in settings["profiles"] if row["id"] == profile["id"]), None
                )
                if previous is None or profile.get("api_key"):
                    raise ValueError
                # A remote model may remain explicitly selected but incomplete.
                candidate = models.validate_profile(
                    {**profile, "api_key": previous.get("api_key") or "validation-only"}
                )
                return {**candidate, "api_key": ""}
            return models.prepare_profile(profile, settings)
        if service == "rqdata":
            return rq.prepare_profile(profile, settings, clear_password=clear_secret)
        initial = (
            current._active("search")
            if current.selections["search"]["kind"] == "environment"
            else None
        )
        return search.prepare_profile(
            profile, settings, clear_brave_api_key=clear_secret, initial_environment=initial
        )

    @_guard
    def resolve_test_profile(self, service, profile, *, clear_secret=False):
        if service not in _SERVICES or type(clear_secret) is not bool:
            raise ValueError
        current = self.read_snapshot()
        candidate = self._prepare(service, profile, current, clear_secret=clear_secret)
        return PrivateConfiguration(
            _connection_config(service, current._validate_runtime(service, candidate))
        )

    @_guard
    def save_profiles(
        self, profiles, *, activate=True, clear_secrets=frozenset(), expected_revision=None
    ):
        if (
            type(profiles) is not dict
            or not profiles
            or not set(profiles).issubset(_SERVICES)
            or type(activate) is not bool
        ):
            raise ValueError
        if type(clear_secrets) not in {set, frozenset} or not clear_secrets.issubset(profiles):
            raise ValueError
        current = self.read_snapshot()
        if expected_revision is not None and expected_revision != current.revision:
            raise ConfigurationStoreError("REVISION_CONFLICT")
        documents, selections = current._snapshot.documents, current.selections
        prepared = {}
        for service in _SERVICES:
            if service not in profiles:
                continue
            candidate = self._prepare(
                service, profiles[service], current, clear_secret=service in clear_secrets
            )
            prepared[service] = candidate
            settings = _documents(current._snapshot)[service]
            rows = [row for row in settings["profiles"] if row["id"] != candidate["id"]]
            if len(rows) >= 20:
                raise ValueError
            documents[service] = {
                **documents[service],
                "version": 2 if service == "search" else 1,
                "active_id": candidate["id"] if activate else settings["active_id"],
                "profiles": rows + [candidate],
            }
            if activate:
                selections[service] = {"kind": "profile", "id": candidate["id"]}
        # Validate against the prospective complete generation, including model
        # changes in the same edit, before opening a write transaction.
        proposed = ProfileSnapshot(
            ConfigurationSnapshot(
                current.installation_id, current.revision, storage._encode(documents, selections)
            ),
            self._environment,
        )
        for service, candidate in prepared.items():
            if service == "search":
                proposed._validate_runtime(service, candidate, allow_clear=service in clear_secrets)
        self._check_publication(current, proposed)
        return self._publish(current, documents, selections)

    def _check_publication(self, current, proposed):
        old_secrets = _snapshot_credential_values(current._snapshot, self._environment)
        _documents(proposed._snapshot, extra_credentials=old_secrets)
        for service in _SERVICES:
            _scan_public_metadata(proposed.public_settings(service), old_secrets)

    def _publish(self, current, documents, selections):
        snapshot = self._store.update(
            lambda previous: (documents, selections), expected_revision=current.revision
        )
        return ProfileSnapshot(snapshot, self._environment)

    @_guard
    def select(self, service, choice, *, expected_revision=None):
        if service not in _SERVICES or type(choice) is not dict:
            raise ValueError
        current = self.read_snapshot()
        if expected_revision is not None and expected_revision != current.revision:
            raise ConfigurationStoreError("REVISION_CONFLICT")
        documents, selections = current._snapshot.documents, current.selections
        document = _documents(current._snapshot)[service]
        documents[service] = {
            **document,
            "active_id": choice.get("id") if choice.get("kind") == "profile" else None,
        }
        selections[service] = choice.copy()
        proposed = ProfileSnapshot(
            ConfigurationSnapshot(
                current.installation_id, current.revision, storage._encode(documents, selections)
            ),
            self._environment,
        )
        self._check_publication(current, proposed)
        return self._publish(current, documents, selections)

    @_guard
    def delete_profile(self, service, profile_id, *, expected_revision=None):
        """Logical deletion only. History is private, retained and never resolved.

        Selected deletion means explicit none. Model references must be removed
        explicitly first, including references in inactive search profiles.
        """
        if service not in _SERVICES or not storage._identifier(profile_id):
            raise ValueError
        current = self.read_snapshot()
        if expected_revision is not None and expected_revision != current.revision:
            raise ConfigurationStoreError("REVISION_CONFLICT")
        documents, choices = current._snapshot.documents, current.selections
        validated = _documents(current._snapshot)
        document = validated[service]
        if not any(row["id"] == profile_id for row in document["profiles"]):
            raise ValueError
        if service == "models" and any(
            row.get("model_profile_id") == profile_id for row in validated["search"]["profiles"]
        ):
            raise ValueError
        document["profiles"] = [row for row in document["profiles"] if row["id"] != profile_id]
        if document["active_id"] == profile_id:
            document["active_id"] = None
            choices[service] = {"kind": "none"}
        documents[service] = document
        proposed = ProfileSnapshot(
            ConfigurationSnapshot(
                current.installation_id, current.revision, storage._encode(documents, choices)
            ),
            self._environment,
        )
        self._check_publication(current, proposed)
        return self._publish(current, documents, choices)
