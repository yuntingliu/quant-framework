"""FastAPI app for the barebone workstation."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from alphalab import ResultStore
from alphalab.dataio.catalog import DataCatalog
from alphalab.utils.build_info import build_info
from dashboard.backend.auth import HttpBasicAuthMiddleware
from dashboard.backend.config import LOADED_ENV_FILES
from dashboard.backend.routers import (
    backtests,
    compat,
    conexus,
    data,
    data_sync,
    market,
    paper,
    research,
    signals,
    strategies,
    system,
)

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"

app = FastAPI(
    title="AlphaLab Barebone API",
    description="Provider-first quant framework workstation API",
    version="0.4.0",
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

app.include_router(data.router)
app.include_router(data_sync.router)
app.include_router(market.router)
app.include_router(strategies.router)
app.include_router(backtests.router)
app.include_router(signals.router)
app.include_router(paper.router)
app.include_router(research.router)
app.include_router(system.router)
app.include_router(compat.router)
app.include_router(conexus.router)


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
    return {
        "status": "ok",
        "name": "AlphaLab Barebone API",
        **build_info(),
        "frontend": "ready" if FRONTEND_DIST.is_dir() else "not_built",
        "runtime_profile": DataCatalog().summary()["status"],
        "store": {
            "status": "ready",
            "backtests": stats["backtests"],
            "strategies": stats["strategies"],
        },
    }


@app.post("/api/cache/clear")
def clear_cache() -> dict:
    # Engines are short-lived in the barebone backend, so there is no global
    # process cache to clear yet. Keep the endpoint for frontend compatibility.
    return {"status": "ok"}

if FRONTEND_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
