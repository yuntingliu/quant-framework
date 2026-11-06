from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from alphalab.utils import private_settings
from apps.api.services import model_provider_service as models
from apps.api.services import search_provider_service as service


def configure_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ALPHALAB_AGENT_MODE", "local")
    monkeypatch.setenv("ALPHALAB_WEB_SEARCH_PROVIDER", "openrouter")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("CONEXUS_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("CONEXUS_MODEL_ID", raising=False)
    monkeypatch.delenv("CONEXUS_MODEL_API_KEY", raising=False)
    models.save_profile(
        {
            "id": "search-account",
            "name": "Search account",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "test-model",
            "api_key": "fixture-model-key",
        },
        activate=True,
    )


def read_raw_settings() -> dict:
    return json.loads(service.settings_path().read_text(encoding="utf-8"))


def test_named_profiles_keep_safe_public_metadata(monkeypatch, tmp_path):
    configure_runtime(monkeypatch, tmp_path)

    response = service.save_profile(
        {
            "id": "brave-work",
            "name": "Brave Work",
            "provider": "brave",
            "brave_api_key": "fixture-brave-key",
        },
        activate=False,
    )

    assert response["active_id"] is None
    assert response["source"] == "environment"
    assert response["provider"] == "openrouter"
    assert "fixture-brave-key" not in json.dumps(response)
    assert response["profiles"] == [
        {
            "id": "brave-work",
            "name": "Brave Work",
            "provider": "brave",
            "model_profile_id": None,
            "has_brave_api_key": True,
            "configured": True,
        }
    ]


def test_brave_key_retention_replacement_deletion_and_profile_isolation(monkeypatch, tmp_path):
    configure_runtime(monkeypatch, tmp_path)

    service.save_profile(
        {"id": "brave-a", "name": "Brave A", "provider": "brave", "brave_api_key": "alpha"},
        activate=True,
    )
    assert (
        next(row for row in read_raw_settings()["profiles"] if row["id"] == "brave-a")[
            "brave_api_key"
        ]
        == "alpha"
    )

    service.save_profile({"id": "brave-a", "name": "Brave A", "provider": "brave"}, activate=True)
    assert (
        next(row for row in read_raw_settings()["profiles"] if row["id"] == "brave-a")[
            "brave_api_key"
        ]
        == "alpha"
    )

    service.save_profile(
        {"id": "brave-a", "name": "Brave A", "provider": "brave", "brave_api_key": "beta"},
        activate=True,
    )
    assert (
        next(row for row in read_raw_settings()["profiles"] if row["id"] == "brave-a")[
            "brave_api_key"
        ]
        == "beta"
    )

    cleared = service.save_profile(
        {"id": "brave-a", "name": "Brave A", "provider": "brave"},
        activate=True,
        clear_brave_api_key=True,
    )
    assert (
        next(row for row in read_raw_settings()["profiles"] if row["id"] == "brave-a")[
            "brave_api_key"
        ]
        == ""
    )
    assert cleared["active_id"] == "brave-a"
    assert cleared["has_brave_api_key"] is False
    assert cleared["configured"] is False
    assert next(row for row in cleared["profiles"] if row["id"] == "brave-a")["configured"] is False

    with pytest.raises(ValueError, match="Enter a Brave Search API key."):
        service.resolve_settings({"id": "brave-a", "provider": "brave"})

    service.save_profile(
        {"id": "brave-a", "name": "Brave A", "provider": "brave", "brave_api_key": "gamma"},
        activate=True,
    )
    with pytest.raises(ValueError, match="Enter a Brave Search API key."):
        service.save_profile(
            {"id": "brave-b", "name": "Brave B", "provider": "brave"}, activate=False
        )

    rows = {row["id"]: row for row in read_raw_settings()["profiles"]}
    assert rows["brave-a"]["brave_api_key"] == "gamma"
    assert "brave-b" not in rows


def test_v1_migration_does_not_rewrite_files_and_invalid_writes_preserve_existing_settings(
    monkeypatch, tmp_path
):
    configure_runtime(monkeypatch, tmp_path)
    legacy = {"version": 1, "provider": "openrouter", "model_profile_id": "search-account"}
    service.settings_path().parent.mkdir(parents=True, exist_ok=True)
    service.settings_path().write_text(json.dumps(legacy), encoding="utf-8")

    assert service.read_settings() == {
        "version": 2,
        "active_id": "default",
        "profiles": [
            {
                "id": "default",
                "name": "Default",
                "provider": "openrouter",
                "model_profile_id": "search-account",
                "brave_api_key": "",
            }
        ],
    }
    assert json.loads(service.settings_path().read_text(encoding="utf-8")) == legacy

    with pytest.raises(ValueError, match="Enter a Brave Search API key."):
        service.save_profile(
            {"id": "broken", "name": "Broken", "provider": "brave"}, activate=False
        )
    assert json.loads(service.settings_path().read_text(encoding="utf-8")) == legacy


def test_invalid_legacy_settings_are_not_overwritten(monkeypatch, tmp_path):
    configure_runtime(monkeypatch, tmp_path)
    invalid = {"version": 1, "provider": "invalid-provider", "brave_api_key": "fixture-brave-key"}
    service.settings_path().parent.mkdir(parents=True, exist_ok=True)
    service.settings_path().write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported search settings format."):
        service.read_settings()
    assert json.loads(service.settings_path().read_text(encoding="utf-8")) == invalid


@pytest.mark.parametrize("provider", ["openrouter", "brave", "off"])
def test_legacy_migration_is_read_only_until_an_explicit_save(monkeypatch, tmp_path, provider):
    configure_runtime(monkeypatch, tmp_path)
    legacy = {
        "version": 1,
        "provider": provider,
        "model_profile_id": "search-account",
        "brave_api_key": "legacy-key",
    }
    path = service.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    assert service.public_settings()["active_id"] == "default"
    assert path.read_bytes() == before
    service.save_settings({"provider": provider, "name": None})
    assert read_raw_settings()["version"] == 2
    assert read_raw_settings()["profiles"][0]["brave_api_key"] == "legacy-key"


def test_cleared_saved_key_never_borrows_environment_credentials(monkeypatch, tmp_path):
    configure_runtime(monkeypatch, tmp_path)
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "environment-secret")
    service.save_profile(
        {"id": "a", "name": "A", "provider": "brave", "brave_api_key": "a-key"}, activate=True
    )
    service.save_profile({"id": "a", "provider": "brave"}, activate=True, clear_brave_api_key=True)
    assert service.public_settings()["configured"] is False
    with pytest.raises(ValueError, match="Enter a Brave Search API key"):
        service.save_profile({"id": "b", "name": "B", "provider": "brave"}, activate=False)


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        {},
        {"base_url": "https://other.example", "model": "m", "api_key": "key"},
        {"base_url": "https://openrouter.ai/api/v1", "model": "", "api_key": "key"},
    ],
)
def test_explicit_model_reference_never_uses_another_profile(monkeypatch, tmp_path, candidate):
    configure_runtime(monkeypatch, tmp_path)
    if candidate is not None:
        original = models.read_settings
        monkeypatch.setattr(
            models,
            "read_settings",
            lambda: {
                **original(),
                "profiles": [*original()["profiles"], {"id": "missing", **candidate}],
            },
        )
    with pytest.raises(ValueError, match="saved OpenRouter"):
        service.resolve_settings({"provider": "openrouter", "model_profile_id": "missing"})


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"version": True},
        {"version": 3},
        {"version": 2, "profiles": {}},
        {"version": 2, "active_id": "missing", "profiles": []},
        {"version": 1, "provider": "off", "model_profile_id": []},
        {"version": 1, "provider": "brave", "brave_api_key": {"secret": "value"}},
        {"version": 2, "profiles": [{"id": "x", "name": ["invalid"], "provider": "off"}]},
    ],
)
def test_malformed_settings_fail_with_safe_validation_errors(value):
    with pytest.raises(ValueError):
        service.validate_settings(value)


def test_failed_atomic_replace_preserves_settings_and_removes_only_its_temporary_file(
    monkeypatch, tmp_path
):
    configure_runtime(monkeypatch, tmp_path)
    service.save_settings({"provider": "off"})
    before = service.settings_path().read_bytes()

    def fail_replace(*args):
        raise OSError("Simulated replace failure")

    monkeypatch.setattr(private_settings.os, "replace", fail_replace)
    with pytest.raises(OSError, match="Simulated replace failure"):
        service.save_profile({"id": "new", "name": "New", "provider": "off"}, activate=True)
    assert service.settings_path().read_bytes() == before
    assert not list(service.settings_path().parent.glob(".search-providers-*.tmp"))


def test_concurrent_thread_updates_preserve_every_profile(monkeypatch, tmp_path):
    configure_runtime(monkeypatch, tmp_path)
    original = service.write_json_settings

    def slow_write(*args, **kwargs):
        time.sleep(0.02)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "write_json_settings", slow_write)

    def save(index):
        return service.save_profile(
            {"id": f"p{index}", "name": f"P{index}", "provider": "off"}, activate=False
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save, range(8)))
    assert {row["id"] for row in service.read_settings()["profiles"]} == {f"p{i}" for i in range(8)}


def test_concurrent_process_updates_preserve_every_profile(monkeypatch, tmp_path):
    configure_runtime(monkeypatch, tmp_path)
    # Each process imports its own service and therefore has an independent RLock.
    script = """
import sys, time
from pathlib import Path
from apps.api.services import search_provider_service as service
root, identifier = Path(sys.argv[1]), sys.argv[2]
original = service.write_json_settings
def slow_write(*args, **kwargs):
    time.sleep(0.05)
    return original(*args, **kwargs)
service.write_json_settings = slow_write
(root / identifier).touch()
deadline = time.monotonic() + 30
while not (root / "start").exists():
    if time.monotonic() > deadline:
        raise RuntimeError("Process start timed out")
    time.sleep(0.01)
service.save_profile({"id": identifier, "name": identifier, "provider": "off"}, activate=False)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), f"p{i}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
        )
        for i in range(4)
    ]
    try:
        deadline = time.monotonic() + 30
        while not all((tmp_path / f"p{i}").exists() for i in range(4)):
            assert time.monotonic() < deadline, "Child processes did not become ready"
            time.sleep(0.01)
        (tmp_path / "start").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stdout + stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
    assert {row["id"] for row in service.read_settings()["profiles"]} == {f"p{i}" for i in range(4)}


def test_independent_installations_keep_separate_profiles(monkeypatch, tmp_path):
    for installation in ("one", "two"):
        configure_runtime(monkeypatch, tmp_path / installation)
        service.save_profile(
            {"id": "same", "name": "Same", "provider": "brave", "brave_api_key": installation},
            activate=True,
        )
    for installation in ("one", "two"):
        monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path / installation))
        assert service.resolve_settings({"provider": "brave"})["brave_api_key"] == installation
