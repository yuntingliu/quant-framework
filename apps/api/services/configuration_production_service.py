"""Full-workbench composition over one explicitly owned installation.

Request/background contexts carry the actual owner, never a backend flag. The
owner lives in ASGI application state and must outlive data and Agent children.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.services.configuration_binding_service import ModelSearchBindings
from apps.api.services.configuration_data_service import ConfigurationDataServices

_CURRENT = ContextVar("owned_production_configuration", default=None)


class ProductionConfiguration:
    def __init__(self, backend, *, agent=None, data_options=None):
        if type(backend) is not ConfigurationBackend:
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        self.backend, self.agent, self.data = backend, agent, None
        self.bindings = ModelSearchBindings(backend, environment={}, agent_connection=agent)
        if backend.mode == "sqlite":
            self.data = ConfigurationDataServices(backend, environment={}, **(data_options or {}))

    def __repr__(self):
        return "ProductionConfiguration(<private>)"

    def __reduce_ex__(self, _protocol):
        raise TypeError("Private production ownership cannot be serialized.")

    def close(self):
        from alphalab.utils.configuration_agent import _owned_call
        from alphalab.utils.configuration_store import _interruption_family, _raise_redacted

        errors = []
        for component in (self.agent, self.data):
            if component is not None:
                try:
                    _owned_call(component.close)
                except BaseException as error:
                    errors.append(error)  # Already sanitized; attempt every closer.
        if errors:
            error = next(
                (item for item in errors if _interruption_family(type(item)) is not None), errors[0]
            )
            _raise_redacted(error)


def current_configuration():
    return _CURRENT.get()


def current_data():
    value = current_configuration()
    return None if value is None else value.data


@contextmanager
def production_scope(value, *, admit=True):
    if type(value) is not ProductionConfiguration:
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    from alphalab.utils.configuration_maintenance import execution_access

    with execution_access(value.backend.root) if admit else nullcontext():
        with value.backend.borrow():
            token = _CURRENT.set(value)
            try:
                yield value
            finally:
                _CURRENT.reset(token)


class ProductionConfigurationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        value = getattr(scope["app"].state, "configuration_runtime", None)
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse

        from alphalab.utils.configuration_maintenance import json_configuration_access
        from alphalab.utils.private_settings import runtime_root

        started = False

        async def forward(message):
            nonlocal started
            if message["type"] in {"http.response.start", "websocket.accept"}:
                started = True
            await send(message)

        try:
            admit = scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
            context = (
                json_configuration_access(runtime_root())
                if value is None
                else production_scope(value, admit=admit)
            )
            with context:
                return await self.app(scope, receive, forward)
        except ConfigurationStoreError:
            if started:
                raise
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1011})
        else:
            await JSONResponse(
                {"detail": "Configuration authority is unavailable."}, status_code=503
            )(scope, receive, send)
