"""FastAPI app for the barebone workstation."""

from __future__ import annotations

import re
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from alphalab import ResultStore
from alphalab.dataio.catalog import DataCatalog
from alphalab.utils.build_info import build_info
from alphalab.utils.configuration_agent import managed_configuration_agent
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_maintenance import checked_root
from alphalab.utils.configuration_store import ConfigurationStoreError
from alphalab.utils.paths import RUNTIME_DIR
from alphalab.utils.private_settings import runtime_root
from apps.api.auth import HttpBasicAuthMiddleware
from apps.api.config import LOADED_ENV_FILES
from apps.api.routers import (
    agent_context,
    agent_tools,
    backtests,
    compat,
    conexus,
    configuration_transfer,
    data,
    data_source_profiles,
    data_sync,
    market,
    model_providers,
    paper,
    python_editor,
    sdk_docs,
    search_providers,
    strategy,
    system,
    validation,
)
from apps.api.services.configuration_production_service import (
    ProductionConfiguration,
    ProductionConfigurationMiddleware,
    production_scope,
)

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "build" / "web"


@asynccontextmanager
async def configuration_lifespan(_app):
    supplied = getattr(_app.state, "configuration_backend", None)
    with ExitStack() as lifetime:
        if supplied is None:
            owner = lifetime.enter_context(ConfigurationBackend.production(runtime_root()))
        else:
            if (type(supplied) is not ConfigurationBackend
                or supplied.root != checked_root(runtime_root())
                or (supplied.mode == "sqlite" and not supplied.durable)):
                raise ConfigurationStoreError("INVALID_CONFIGURATION")
            owner = supplied
        lifetime.enter_context(owner.borrow())
        agent = getattr(_app.state, "configuration_agent", None)
        if owner.mode == "sqlite":
            if owner.root != RUNTIME_DIR.resolve():
                raise ConfigurationStoreError("INSTALLATION_MISMATCH")
            if agent is None:
                agent = lifetime.enter_context(managed_configuration_agent(
                    owner, api_origin=getattr(_app.state, "configuration_api_origin", None)
                ))
            agent.require_owner(owner)
        runtime = ProductionConfiguration(
            owner, agent=agent, data_options=getattr(_app.state, "configuration_data_options", None)
        )
        _app.state.configuration_bindings = runtime.bindings
        _app.state.configuration_runtime = runtime
        try:
            with production_scope(runtime, admit=False):
                if owner.mode == "sqlite":
                    from apps.api.services.agent_run_service import run_recovery_lifespan
                    async with run_recovery_lifespan(_app):
                        yield
                else:
                    yield
        finally:
            import anyio
            # Stop admission and wait for every owned SDK child before the
            # Agent connection and finally the backend lease may be released.
            await anyio.to_thread.run_sync(runtime.close)
            del _app.state.configuration_runtime
            del _app.state.configuration_bindings


app = FastAPI(
    title="AlphaLab Barebone API",
    description="Provider-first quant framework workstation API",
    version="0.7.0",
    lifespan=configuration_lifespan,
)
app.state.loaded_env_files = tuple(str(path) for path in LOADED_ENV_FILES)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "null",
    ],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(HttpBasicAuthMiddleware)
app.add_middleware(ProductionConfigurationMiddleware)


@app.exception_handler(ConfigurationStoreError)
async def configuration_unavailable(_request, error):
    from apps.api.services.configuration_binding_service import configuration_http_error

    public = configuration_http_error(error)
    return JSONResponse(status_code=public.status_code, content={"detail": public.detail})


_PRIVATE_ERROR_KEYS = {
    "environment",
    "environment_hash",
    "environment_sha256",
    "path",
    "release_dir",
    "root",
    "source",
    "source_hash",
    "source_sha256",
    "stack",
    "stack_trace",
    "state_sha256",
    "stderr",
    "stdout",
    "traceback",
}


def _public_error_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.splitlines()[0]
        text = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<internal-path>", text)
        text = re.sub(
            r"(?<![:/\w])/(?!/)(?:[^/\s]+/)+[^\s\"']+",
            "<internal-path>",
            text,
        )
        text = re.sub(r"\b[a-fA-F0-9]{40,64}\b", "<internal-id>", text)
        return text[:500]
    if isinstance(value, list):
        return [_public_error_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {
            key: _public_error_value(item)
            for key, item in value.items()
            if str(key).lower() not in _PRIVATE_ERROR_KEYS
        }
    return value


@app.exception_handler(HTTPException)
async def sanitized_http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    """Keep public errors useful without exposing local runtime internals."""

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": _public_error_value(exc.detail)},
        headers=exc.headers,
    )


@app.middleware("http")
async def prevent_stale_frontend_shell(request: Request, call_next):
    """Make each deployment's hashed frontend bundle discoverable immediately."""

    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if request.url.path.startswith("/app") and "text/html" in content_type:
        response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.include_router(data.router)
app.include_router(agent_context.router)
app.include_router(agent_tools.router)
app.include_router(data_source_profiles.router)
app.include_router(data_sync.router)
app.include_router(market.router)
app.include_router(strategy.router)
app.include_router(validation.router)
app.include_router(backtests.router)
app.include_router(paper.router)
app.include_router(python_editor.router)
app.include_router(sdk_docs.router)
app.include_router(system.router)
app.include_router(compat.router)
app.include_router(conexus.router)
app.include_router(model_providers.router)
app.include_router(search_providers.router)
app.include_router(configuration_transfer.router)


@app.get("/", response_model=None)
def root() -> dict | RedirectResponse:
    if FRONTEND_DIST.is_dir():
        return RedirectResponse(url="/app/")
    return {"status": "ok", "name": "AlphaLab Barebone API"}


@app.get("/api/health")
def health() -> dict:
    store = ResultStore()
    try:
        stats = store.stats()
    finally:
        store.close()
    value = {
        "status": "ok",
        "name": "AlphaLab Barebone API",
        **build_info(),
        "frontend": "ready" if FRONTEND_DIST.is_dir() else "not_built",
        # Detailed coverage scans read market datasets and are inappropriate
        # for a frequent deployment probe with a bounded request timeout.
        "runtime_profile": "not_checked",
        "data_status_url": "/api/data/providers",
        "store": {
            "status": "ready",
            "backtests": stats["backtests"],
            "strategies": stats["strategies"],
        },
    }
    runtime = getattr(app.state, "configuration_runtime", None)
    if runtime is not None:
        with runtime.backend.borrow():
            value["configuration"] = runtime.backend.public_identity()
    return value


@app.post("/api/cache/clear")
def clear_cache() -> dict:
    DataCatalog.clear_cache()
    return {"status": "ok"}

if FRONTEND_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
