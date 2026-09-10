"""FastAPI app for the barebone workstation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from dashboard.backend.config import LOADED_ENV_FILES
from dashboard.backend.routers import (
    agent_context,
    agent_tools,
    backtests,
    compat,
    conexus,
    data,
    data_sync,
    market,
    paper,
    python_editor,
    sdk_docs,
    strategy,
    system,
    validation,
)

app = FastAPI(
    title="AlphaLab Barebone API",
    description="Provider-first quant framework workstation API",
    version="0.6.1",
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


@app.get("/")
def root() -> dict:
    return {"status": "ok", "name": "AlphaLab Barebone API"}


@app.post("/api/cache/clear")
def clear_cache() -> dict:
    # Engines are short-lived in the barebone backend, so there is no global
    # process cache to clear yet. Keep the endpoint for frontend compatibility.
    return {"status": "ok"}


FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
