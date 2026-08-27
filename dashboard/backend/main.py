"""FastAPI app for the barebone workstation."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dashboard.backend.config import LOADED_ENV_FILES
from dashboard.backend.routers import (
    agent_tools,
    backtests,
    compat,
    conexus,
    data,
    data_sync,
    market,
    paper,
    python_editor,
    reports,
    strategy,
    system,
    validation,
)

app = FastAPI(
    title="AlphaLab Barebone API",
    description="Provider-first quant framework workstation API",
    version="0.5.0",
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

app.include_router(data.router)
app.include_router(agent_tools.router)
app.include_router(data_sync.router)
app.include_router(market.router)
app.include_router(strategy.router)
app.include_router(validation.router)
app.include_router(backtests.router)
app.include_router(paper.router)
app.include_router(python_editor.router)
app.include_router(reports.router)
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
