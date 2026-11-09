"""Explicit configuration API composition; never installed as the production app.

The caller owns the backend lease until API and Agent shutdown. Model/search
routes are the default; isolated SQLite data routes require include_data=True.
Deployment and other full-workbench routes remain unavailable.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.routers import model_providers, search_providers
from apps.api.services.configuration_binding_service import (
    ModelSearchBindings,
    configuration_http_error,
)


def create_configuration_app(
    backend,
    *,
    environment=None,
    agent_identity=None,
    agent_connection=None,
    include_data=False,
    data_options=None,
):
    """Prepare profile routes; agent_identity is a consistency assertion only.

    Only a live private managed connection can authorize SQLite search probes.
    No identity/connection dictionary or environment value authorizes a probe.
    """
    binding = ModelSearchBindings(
        backend,
        environment=environment,
        agent_identity=agent_identity,
        agent_connection=agent_connection,
    )
    if type(include_data) is not bool or (include_data and backend.mode != "sqlite"):
        raise ConfigurationStoreError("INVALID_ARGUMENT")
    # Keep the same explicitly supplied private environment snapshot used by
    # model/search, even if the caller later mutates its construction dictionary.
    data_environment = binding._sql._environment.copy() if include_data else None
    options = dict(data_options or {})

    @asynccontextmanager
    async def lifespan(_app):
        with backend.borrow():
            # Recheck identity/readability before accepting requests, not just
            # when an application object was constructed earlier.
            if backend.mode == "sqlite":
                binding._sql.read_snapshot()
            data = None
            try:
                if include_data:
                    from apps.api.services.configuration_data_service import (
                        ConfigurationDataServices,
                    )

                    data = ConfigurationDataServices(
                        backend, environment=data_environment, **options
                    )
                    _app.state.configuration_data = data
                yield
            finally:
                if data is not None:
                    import anyio

                    await anyio.to_thread.run_sync(data.close)
                    del _app.state.configuration_data

    app = FastAPI(lifespan=lifespan)
    app.state.configuration_bindings = binding
    app.include_router(model_providers.router)
    app.include_router(search_providers.router)
    if include_data:
        from apps.api.routers.configuration_data_sync import router

        app.include_router(router)

    @app.exception_handler(ConfigurationStoreError)
    async def unavailable(_request, error):
        public = configuration_http_error(error)
        return JSONResponse(status_code=public.status_code, content={"detail": public.detail})

    @app.get("/api/health")
    async def health():
        with backend.borrow():
            configured = binding.models.public_settings()
            return {
                "ok": True,
                "configuration": backend.public_identity(),
                "modelConfigured": configured.get("configured", bool(configured.get("active_id"))),
            }

    return app
