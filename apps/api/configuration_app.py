"""Explicit model/search API composition; never installed as the production app.

The caller owns the backend lease until API and Agent shutdown. Deferred RQData,
recipe and deployment routes are deliberately absent, preventing partial full
workbench activation or jobs that still depend on legacy JSON configuration.
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


def create_configuration_app(backend, *, environment=None, agent_identity=None):
    """Prepare profile routes; agent_identity is a consistency assertion only.

    Non-Off SQLite search probes remain unavailable until an authenticated owned
    endpoint can supply a complete private connection bound to the owner lifetime.
    An arbitrary identity/connection dictionary never authorizes a probe.
    """
    binding = ModelSearchBindings(backend, environment=environment, agent_identity=agent_identity)

    @asynccontextmanager
    async def lifespan(_app):
        with backend.borrow():
            # Recheck identity/readability before accepting requests, not just
            # when an application object was constructed earlier.
            if backend.mode == "sqlite":
                binding._sql.read_snapshot()
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.configuration_bindings = binding
    app.include_router(model_providers.router)
    app.include_router(search_providers.router)

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
