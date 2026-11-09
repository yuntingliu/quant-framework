"""Coherent model/search bindings over one explicit backend owner.

Default router dependencies construct JSON only. The explicit SQLite API factory
mounts only these prepared routes; full-workbench/RQData cutover is deferred.
"""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import HTTPException, Request

from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStoreError
from alphalab.utils.private_settings import runtime_root
from apps.api.services import model_provider_service as models
from apps.api.services import search_provider_service as search
from apps.api.services.configuration_profile_service import ConfigurationProfileServices, _guard


def configuration_http_error(error):
    code = error.code
    status = (
        409
        if code in {"REVISION_CONFLICT", "STORE_BUSY"}
        else (422 if code in {"INVALID_CONFIGURATION", "INVALID_ARGUMENT"} else 503)
    )
    return HTTPException(status, str(ConfigurationStoreError(code)))


class _BoundService:
    __slots__ = ("_binding", "_name", "_legacy")

    def __init__(self, binding, name, legacy):
        self._binding, self._name, self._legacy = binding, name, legacy

    @contextmanager
    def _service(self):
        owner = self._binding.backend
        if owner.mode == "json":
            with owner.json_scope():
                yield self._legacy
        else:
            with owner.borrow():
                yield getattr(self._binding._sql, self._name)

    def public_settings(self):
        with self._service() as source:
            return source.public_settings()

    def save_profile(self, profile, *, activate, expected_revision=None, **clear):
        with self._service() as source:
            if self._binding.backend.mode == "json":
                if expected_revision is not None or (
                    self._name == "models" and clear.get("clear_api_key")
                ):
                    raise ConfigurationStoreError("INVALID_CONFIGURATION")
                if self._name == "models":
                    clear = {}
                return source.save_profile(profile, activate=activate, **clear)
            return source.save_profile(
                profile, activate=activate, expected_revision=expected_revision, **clear
            )

    def resolve_test_profile(self, profile, **clear):
        with self._service() as source:
            if self._binding.backend.mode == "json":
                if self._name == "models":
                    if clear.get("clear_api_key"):
                        raise ConfigurationStoreError("INVALID_CONFIGURATION")
                    return source.resolve_test_profile(profile)
                return source.resolve_settings(profile, **clear)
            return source.resolve_test_profile(profile, **clear).extract_private()

    def select(self, choice, *, expected_revision=None):
        if self._binding.backend.mode != "sqlite":
            raise ConfigurationStoreError("INVALID_CONFIGURATION")
        with self._service() as source:
            return source.select(choice, expected_revision=expected_revision)

    def __repr__(self):
        return "BoundProfileService(<private>)"


class ModelSearchBindings:
    """One backend for every GET, Save, Test and selection route in this scope."""

    __slots__ = ("backend", "_sql", "models", "search")

    @_guard
    def __init__(self, backend: ConfigurationBackend, *, environment=None, agent_identity=None):
        if type(backend) is not ConfigurationBackend:
            raise ValueError
        self.backend, self._sql = backend, None
        if agent_identity is not None:
            # Compatibility assertion only: a composition identity says nothing
            # about an HTTP endpoint. Never turn this input into authorization.
            backend.verify_agent_identity(agent_identity)
        with backend.borrow():
            if backend.mode == "sqlite":
                self._sql = ConfigurationProfileServices(backend.store, environment=environment)
                snapshot = self._sql.read_snapshot()
                # Native services preserve incomplete profiles and private
                # extension fields. Transfer/export eligibility is a separate
                # policy; do not apply it to ordinary runtime admission.
                snapshot.public_settings("models")
                snapshot.public_settings("search")
        self.models = _BoundService(self, "models", models)
        self.search = _BoundService(self, "search", search)

    def __repr__(self):
        return "ModelSearchBindings(<private>)"

    def require_search_agent(self, *, provider):
        """Fail before secret resolution until an owned endpoint can be verified.

        Current Host health/descriptor identity is public and production startup
        is JSON-only. There is no authenticated SQLite endpoint contract from
        which to mint an immutable private origin/token/identity connection. Do
        not accept a caller's identity assertion or consult ambient connection
        variables as a substitute. Off needs no connection; JSON stays legacy.
        """
        if self.backend.mode == "sqlite" and provider != "off":
            raise ConfigurationStoreError("STORE_UNAVAILABLE")


async def configuration_bindings(request: Request):
    """Async ownership keeps maintenance enter/exit on the same event-loop thread.

    An ordinary standalone router remains JSON-backed even without ASGI lifespan.
    No environment flag or request body can construct a SQLite owner.
    """
    binding = getattr(request.app.state, "configuration_bindings", None)
    if binding is not None:
        with binding.backend.borrow():
            yield binding
    else:
        with ConfigurationBackend.json(runtime_root()) as owner:
            with owner.borrow():
                yield ModelSearchBindings(owner)
