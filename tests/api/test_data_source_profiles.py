from __future__ import annotations

import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphalab.dataio import rq_profiles
from apps.api.routers import data_source_profiles


@contextmanager
def runtime_dir() -> Path:
    path = Path(".tmp") / f"api-rq-profile-test-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def client(monkeypatch, runtime_path: Path):
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(runtime_path))
    monkeypatch.delenv("RQ_USER", raising=False)
    monkeypatch.delenv("RQ_PASSWORD", raising=False)
    monkeypatch.delenv("RQ_HOST", raising=False)
    app = FastAPI()
    app.include_router(data_source_profiles.router)
    return TestClient(app)


def profile(**changes):
    return {
        "id": "research",
        "name": "Research",
        "user": "demo-user",
        "host": "rq.example:16011",
        "password": "private-rq-password",
        **changes,
    }


def test_rq_profiles_persist_privately_and_preserve_password_only_for_same_destination(
    monkeypatch,
):
    with runtime_dir() as path:
        api = client(monkeypatch, path)
        response = api.put("/api/data-source-profiles", json=profile())
        assert response.status_code == 200
        assert "private-rq-password" not in response.text
        assert response.json()["profiles"][0]["has_password"] is True
        assert response.json()["active_id"] == "research"

        keep = api.put(
            "/api/data-source-profiles",
            json=profile(password="", name="Renamed Research"),
        )
        assert keep.status_code == 200
        assert rq_profiles.active_profile().password == "private-rq-password"

        changed = api.put(
            "/api/data-source-profiles",
            json=profile(password="", host="other.example:16011"),
        )
        assert changed.status_code == 422
        assert "private-rq-password" not in changed.text
        assert rq_profiles.active_config()["RQ_HOST"] == "rq.example:16011"

        stored = json.loads(rq_profiles.settings_path().read_text(encoding="utf-8"))
        assert stored["version"] == 1
        if os.name != "nt":
            assert rq_profiles.settings_path().stat().st_mode & 0o077 == 0


def test_rq_profiles_require_explicit_password_clear_and_return_only_safe_metadata(
    monkeypatch,
):
    with runtime_dir() as path:
        api = client(monkeypatch, path)
        api.put("/api/data-source-profiles", json=profile())

        rejected = api.put(
            "/api/data-source-profiles",
            json=profile(
                password="",
                clear_password=False,
                user="new-user",
                host="new.example:16011",
            ),
        )
        assert rejected.status_code == 422

        cleared = api.put(
            "/api/data-source-profiles",
            json=profile(password="", clear_password=True),
        )
        assert cleared.status_code == 200
        assert cleared.json()["profiles"][0]["has_password"] is False
        assert "password" not in cleared.json()["profiles"][0]

        read_back = api.get("/api/data-source-profiles")
        assert read_back.status_code == 200
        assert read_back.json() == {
            "provider": "rqdata",
            "active_id": "research",
            "profiles": [
                {
                    "id": "research",
                    "name": "Research",
                    "provider": "rqdata",
                    "user": "demo-user",
                    "host": "rq.example:16011",
                    "has_password": False,
                }
            ],
            "environment_configured": False,
            "configured": False,
            "source": "saved",
        }


def test_rq_profiles_validate_input_and_sanitize_invalid_settings_file(
    monkeypatch,
):
    with runtime_dir() as path:
        api = client(monkeypatch, path)
        response = api.put(
            "/api/data-source-profiles",
            json=profile(host="https://rq.example:16011/path"),
        )
        assert response.status_code == 422
        assert "https://rq.example:16011/path" not in response.text

        rq_profiles.settings_path().parent.mkdir(parents=True, exist_ok=True)
        rq_profiles.settings_path().write_text(
            json.dumps({"version": 999, "active_id": None, "profiles": []}),
            encoding="utf-8",
        )
        read_back = api.get("/api/data-source-profiles")
        assert read_back.status_code == 503
        assert read_back.json() == {"detail": "Data-source settings could not be read."}


def test_rq_profile_validation_errors_are_sanitized(monkeypatch):
    with runtime_dir() as path:
        api = client(monkeypatch, path)
        response = api.put(
            "/api/data-source-profiles",
            json=profile(password="sensitive-rq-secret" * 1000),
        )

        assert response.status_code == 422
        assert response.json() == {
            "detail": "Check the profile name, user, host, and password length."
        }
        assert "sensitive-rq-secret" not in response.text
