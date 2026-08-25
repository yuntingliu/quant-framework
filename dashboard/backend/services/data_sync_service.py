"""Thin dashboard service over the runtime data control plane."""
from __future__ import annotations

import os
from functools import lru_cache
from importlib.util import find_spec

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.quality import validate_all, validate_dataset
from alphalab.dataio.sync import SyncJobManager, SyncRequest, build_sync_plan
from alphalab.tools import create_data_tool_registry
from alphalab.utils.env import load_env_files

_RQ_KEYS = ("RQ_USER", "RQ_PASSWORD", "RQ_HOST")


@lru_cache(maxsize=1)
def get_job_manager() -> SyncJobManager:
    return SyncJobManager()


def get_health() -> dict:
    load_env_files()
    missing = [key for key in _RQ_KEYS if not os.environ.get(key, "").strip()]
    installed = find_spec("rqdatac") is not None
    credentials_configured = not missing
    ready = installed and credentials_configured
    jobs = get_job_manager().operations.list_jobs(limit=1)
    latest = jobs[0] if jobs else None
    rq_status = (
        "not_installed"
        if not installed
        else "not_configured"
        if not credentials_configured
        else "configured"
    )
    if ready and latest and latest["status"] == "failed":
        rq_status = "unavailable"
    return {
        "status": "ok",
        "runtime": DataCatalog().summary(),
        "rq": {
            "status": rq_status,
            "installed": installed,
            "configured": credentials_configured,
            "ready": ready,
            "missing": missing,
            "connected": False,
            "last_error": latest.get("error") if latest and latest["status"] == "failed" else None,
        },
        "realtime": {"status": "not_configured"},
        "tools": {
            "status": "ready",
            "count": len(create_data_tool_registry().describe()),
        },
        "planner": {"status": "not_configured"},
    }


def catalog() -> dict:
    return DataCatalog().summary()


def plan(request: SyncRequest) -> dict:
    return build_sync_plan(request)


def submit(request: SyncRequest) -> dict:
    return get_job_manager().submit(request)


def jobs(limit: int = 50) -> list[dict]:
    return get_job_manager().operations.list_jobs(limit=limit)


def job(job_id: str) -> dict | None:
    return get_job_manager().operations.get_job(job_id)


def cancel(job_id: str) -> dict | None:
    return get_job_manager().cancel(job_id)


def validate(dataset: str | None = None) -> dict | list[dict]:
    return validate_dataset(dataset) if dataset else validate_all()
