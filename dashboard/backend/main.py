"""FastAPI app for the barebone workstation."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dashboard.backend.config import LOADED_ENV_FILES
from dashboard.backend.routers import (
    backtests,
    compat,
    data,
    data_sync,
    market,
    paper,
    research,
    signals,
    strategies,
    system,
)

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


@app.get("/")
def root() -> dict:
    return {"status": "ok", "name": "AlphaLab Barebone API"}


@app.post("/api/cache/clear")
def clear_cache() -> dict:
    # Engines are short-lived in the barebone backend, so there is no global
    # process cache to clear yet. Keep the endpoint for frontend compatibility.
    return {"status": "ok"}
