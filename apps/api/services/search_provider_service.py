"""Workstation-local named search profiles with private credential storage."""

from __future__ import annotations

import errno
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from alphalab.utils.configuration_maintenance import legacy_settings_operation
from alphalab.utils.private_settings import read_json_settings, runtime_root, write_json_settings
from apps.api.services import model_provider_service as models

_DEFAULT_PROFILE_ID = "default"
_DEFAULT_PROFILE_NAME = "Default"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_VALID_PROVIDERS = {"openrouter", "brave", "off"}
_LOCK = threading.RLock()


@contextmanager
def _settings_lock():
    """Serialize the whole update across threads and API processes."""
    with _LOCK:
        path = settings_path().with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            path.parent.chmod(0o700)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        acquired = False
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if time.monotonic() >= deadline:
                        raise OSError("Search settings are busy. Retry the update.") from None
                    time.sleep(0.01)
            yield
        finally:
            try:
                if acquired:
                    if os.name == "nt":
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def settings_path() -> Path:
    return runtime_root() / "conexus" / "secrets" / "search-providers.json"


def default_settings() -> dict[str, Any]:
    return {"version": 2, "active_id": None, "profiles": []}


@legacy_settings_operation(lambda: settings_path())
def read_settings() -> dict[str, Any]:
    with _LOCK:
        return read_json_settings(
            settings_path(), default=default_settings, validate=validate_settings
        )


def validate_settings(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Unsupported search settings format.")
    version = value.get("version")
    if type(version) is not int:
        raise ValueError("Unsupported search settings format.")
    if version == 1:
        return _migrate_v1_settings(value)
    if version != 2:
        raise ValueError("Unsupported search settings format.")
    profiles = value.get("profiles")
    if not isinstance(profiles, list) or len(profiles) > 20:
        raise ValueError("Unsupported search settings format.")
    active_id = value.get("active_id")
    if active_id is not None:
        active_id = _validate_profile_id(active_id)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in profiles:
        validated = validate_profile_row(row)
        if validated["id"] in seen:
            raise ValueError("Unsupported search settings format.")
        seen.add(validated["id"])
        normalized.append(validated)
    if active_id is not None and active_id not in seen:
        raise ValueError("Unsupported search settings format.")
    return {"version": 2, "active_id": active_id, "profiles": normalized}


def validate_profile_row(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Unsupported search settings format.")
    return {
        "id": _validate_profile_id(value.get("id")),
        "name": _validate_profile_name(value.get("name")),
        "provider": _validate_provider(
            value.get("provider"), message="Unsupported search settings format."
        ),
        "model_profile_id": _normalize_model_profile_id(value.get("model_profile_id")),
        "brave_api_key": _normalize_brave_api_key(value.get("brave_api_key", "")),
    }


def public_profile(row: Mapping[str, object]) -> dict[str, Any]:
    candidate = validate_profile_row(row)
    return {
        "id": candidate["id"],
        "name": candidate["name"],
        "provider": candidate["provider"],
        "model_profile_id": candidate["model_profile_id"],
        "has_brave_api_key": bool(candidate["brave_api_key"]),
        "configured": _profile_is_configured(candidate),
    }


def public_settings() -> dict[str, Any]:
    settings = read_settings()
    saved = _saved_active_profile(settings)
    active = saved if saved is not None else _environment_profile()
    return {
        "mode": os.getenv("ALPHALAB_AGENT_MODE", "remote"),
        "scope": "workstation",
        "active_id": settings["active_id"],
        "profiles": [public_profile(row) for row in settings["profiles"]],
        "provider": active["provider"],
        "model_profile_id": active["model_profile_id"],
        "has_brave_api_key": bool(active.get("brave_api_key")),
        "configured": _profile_is_configured(active),
        "source": "saved" if saved is not None else "environment",
    }


@legacy_settings_operation(lambda: settings_path())
def save_profile(
    profile: Mapping[str, object],
    *,
    activate: bool,
    clear_brave_api_key: bool = False,
) -> dict[str, Any]:
    with _settings_lock():
        settings = read_settings()
        normalized = _prepare_profile(profile, settings, clear_brave_api_key=clear_brave_api_key)
        allow_incomplete_brave = clear_brave_api_key and normalized["provider"] == "brave"
        _validate_runtime_profile(normalized, allow_incomplete_brave=allow_incomplete_brave)
        previous = next(
            (row for row in settings["profiles"] if row["id"] == normalized["id"]), None
        )
        rows = [row for row in settings["profiles"] if row["id"] != normalized["id"]]
        if previous is None and len(rows) >= 20:
            raise ValueError("At most 20 search profiles are supported.")
        rows.append(normalized)
        next_settings = {"version": 2, "active_id": settings["active_id"], "profiles": rows}
        if activate:
            next_settings["active_id"] = normalized["id"]
        write_json_settings(settings_path(), next_settings, validate=validate_settings)
        return public_settings()


def save_settings(settings: Mapping[str, object]) -> dict[str, Any]:
    activate = bool(settings.get("activate", True))
    clear_brave_api_key = bool(settings.get("clear_brave_api_key", False))
    payload = {
        key: value
        for key, value in settings.items()
        if key not in {"activate", "clear_brave_api_key"}
    }
    return save_profile(payload, activate=activate, clear_brave_api_key=clear_brave_api_key)


def resolve_settings(
    settings: Mapping[str, object],
    *,
    clear_brave_api_key: bool = False,
) -> dict[str, Any]:
    normalized = _prepare_profile(
        settings, read_settings(), clear_brave_api_key=clear_brave_api_key
    )
    _validate_runtime_profile(normalized, allow_incomplete_brave=False)
    provider = normalized["provider"]
    if provider == "off":
        return {"provider": "off"}
    if provider == "openrouter":
        return {"provider": "openrouter", "model_profile_id": normalized["model_profile_id"]}
    return {"provider": "brave", "brave_api_key": normalized["brave_api_key"]}


def _prepare_profile(
    profile: Mapping[str, object],
    settings: Mapping[str, Any],
    *,
    clear_brave_api_key: bool,
) -> dict[str, Any]:
    return prepare_profile(
        profile,
        settings,
        clear_brave_api_key=clear_brave_api_key,
        initial_environment=_environment_profile()
        if _may_capture_environment(profile, settings)
        else None,
    )


def _may_capture_environment(profile, settings) -> bool:
    profile_id, _ = _target_profile_identity(profile, settings)
    return (
        profile_id == _DEFAULT_PROFILE_ID
        and profile.get("id") is None
        and profile.get("name") is None
        and not any(row["id"] == profile_id for row in settings["profiles"])
    )


def prepare_profile(
    profile: Mapping[str, object],
    settings: Mapping[str, Any],
    *,
    clear_brave_api_key: bool = False,
    initial_environment: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Pure profile preparation; environment capture is an explicit caller input."""
    profile_id, default_name = _target_profile_identity(profile, settings)
    previous = next((row for row in settings["profiles"] if row["id"] == profile_id), None)
    if previous is None and profile.get("id") is not None and profile.get("name") is None:
        raise ValueError("Enter a profile name.")
    key_source = previous
    # The legacy form edits the effective environment configuration before its
    # first save. Only that implicit default may capture the environment key.
    # An existing profile, including one explicitly cleared, owns its secret.
    if (
        previous is None
        and profile_id == _DEFAULT_PROFILE_ID
        and profile.get("id") is None
        and profile.get("name") is None
    ):
        if initial_environment and initial_environment.get("brave_api_key"):
            key_source = initial_environment
    return {
        "id": profile_id,
        "name": _resolve_profile_name(profile, previous, default_name=default_name),
        "provider": _validate_provider(
            profile.get("provider"), message="Choose OpenRouter, Brave or Off for search."
        ),
        "model_profile_id": _resolve_model_profile_id(profile, previous),
        "brave_api_key": _resolve_brave_api_key(
            profile, key_source, clear_brave_api_key=clear_brave_api_key
        ),
    }


def _target_profile_identity(
    profile: Mapping[str, object],
    settings: Mapping[str, Any],
) -> tuple[str, str | None]:
    raw = profile.get("id")
    if raw is not None:
        return _validate_profile_id(raw), None
    saved = _saved_active_profile(settings)
    if saved is not None:
        return str(saved["id"]), None
    if any(row["id"] == _DEFAULT_PROFILE_ID for row in settings["profiles"]):
        return _DEFAULT_PROFILE_ID, None
    return _DEFAULT_PROFILE_ID, _DEFAULT_PROFILE_NAME


def _resolve_profile_name(
    profile: Mapping[str, object],
    previous: Mapping[str, object] | None,
    *,
    default_name: str | None,
) -> str:
    # Older clients omit the name; Pydantic also serializes that default as None.
    if profile.get("name") is not None:
        return _validate_profile_name(profile.get("name"))
    if previous is not None:
        return str(previous["name"])
    if default_name is not None:
        return default_name
    raise ValueError("Enter a profile name.")


def _resolve_model_profile_id(
    profile: Mapping[str, object],
    previous: Mapping[str, object] | None,
) -> str | None:
    if "model_profile_id" in profile:
        return _normalize_model_profile_id(profile.get("model_profile_id"))
    if previous is not None:
        return _normalize_model_profile_id(previous.get("model_profile_id"))
    return None


def _resolve_brave_api_key(
    profile: Mapping[str, object],
    previous: Mapping[str, object] | None,
    *,
    clear_brave_api_key: bool,
) -> str:
    if clear_brave_api_key:
        if _normalize_brave_api_key(profile.get("brave_api_key", "")):
            raise ValueError("Brave key replacement and deletion must be requested separately.")
        if previous is None:
            raise ValueError("Cannot clear the Brave Search key for a new profile.")
        return ""
    if "brave_api_key" not in profile:
        return _normalize_brave_api_key(
            previous.get("brave_api_key", "") if previous is not None else ""
        )
    value = _normalize_brave_api_key(profile.get("brave_api_key", ""))
    if value:
        return value
    if previous is not None:
        return _normalize_brave_api_key(previous.get("brave_api_key", ""))
    return ""


def _profile_is_configured(profile: Mapping[str, object] | None) -> bool:
    if profile is None:
        return False
    try:
        _validate_runtime_profile(profile, allow_incomplete_brave=False)
        return True
    except ValueError:
        return False


def _validate_runtime_profile(
    profile: Mapping[str, object],
    *,
    allow_incomplete_brave: bool,
) -> None:
    is_openrouter = (
        _validate_provider(
            profile.get("provider"), message="Choose OpenRouter, Brave or Off for search."
        )
        == "openrouter"
    )
    identifier = (
        _normalize_model_profile_id(profile.get("model_profile_id")) if is_openrouter else None
    )
    validate_runtime_profile(
        profile,
        allow_incomplete_brave=allow_incomplete_brave,
        model_settings=models.read_settings() if is_openrouter and identifier is not None else None,
        active_model=models.active_profile() if is_openrouter and identifier is None else None,
    )


def validate_runtime_profile(
    profile: Mapping[str, object],
    *,
    allow_incomplete_brave: bool,
    model_settings: Mapping[str, Any] | None = None,
    active_model: Mapping[str, object] | None = None,
) -> None:
    """Validate readiness using models from the same caller-supplied snapshot."""
    provider = _validate_provider(
        profile.get("provider"), message="Choose OpenRouter, Brave or Off for search."
    )
    if provider == "openrouter":
        identifier = _normalize_model_profile_id(profile.get("model_profile_id"))
        if identifier is not None:
            candidate = next(
                (
                    row
                    for row in (model_settings or {"profiles": []})["profiles"]
                    if isinstance(row, Mapping) and row.get("id") == identifier
                ),
                None,
            )
            if not _is_openrouter_model_profile(candidate):
                raise ValueError("Choose a saved OpenRouter model profile with an API key.")
            return
        if not _is_openrouter_model_profile(active_model):
            raise ValueError("Choose an active OpenRouter model profile with an API key.")
        return
    if provider == "brave" and not _normalize_brave_api_key(profile.get("brave_api_key", "")):
        if allow_incomplete_brave:
            return
        raise ValueError("Enter a Brave Search API key.")


def _is_openrouter_model_profile(profile: Mapping[str, object] | None) -> bool:
    if profile is None:
        return False
    if any(not isinstance(profile.get(key), str) for key in ("base_url", "api_key", "model")):
        return False
    base_url = profile["base_url"].strip().rstrip("/")
    api_key = profile["api_key"].strip()
    model = profile["model"].strip()
    return base_url == _OPENROUTER_BASE_URL and bool(api_key) and bool(model)


def _saved_active_profile(settings: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    current = settings if settings is not None else read_settings()
    active_id = current.get("active_id")
    if not active_id:
        return None
    return next((row for row in current["profiles"] if row["id"] == active_id), None)


def _environment_profile() -> dict[str, Any]:
    provider = str(os.getenv("ALPHALAB_WEB_SEARCH_PROVIDER", "brave")).strip().lower()
    if provider not in _VALID_PROVIDERS:
        provider = "brave"
    return {
        "id": "environment",
        "name": "Environment",
        "provider": provider,
        "model_profile_id": None,
        "brave_api_key": _normalize_brave_api_key(os.getenv("BRAVE_SEARCH_API_KEY", "")),
    }


def _migrate_v1_settings(value: Mapping[str, object]) -> dict[str, Any]:
    migrated = {
        "id": _DEFAULT_PROFILE_ID,
        "name": _DEFAULT_PROFILE_NAME,
        "provider": _validate_provider(
            value.get("provider"), message="Unsupported search settings format."
        ),
        "model_profile_id": _normalize_model_profile_id(value.get("model_profile_id")),
        "brave_api_key": _normalize_brave_api_key(value.get("brave_api_key", "")),
    }
    return {"version": 2, "active_id": _DEFAULT_PROFILE_ID, "profiles": [migrated]}


def _validate_provider(value: object, *, message: str) -> str:
    provider = value.strip().lower() if isinstance(value, str) else ""
    if provider not in _VALID_PROVIDERS:
        raise ValueError(message)
    return provider


def _validate_profile_id(value: object) -> str:
    profile_id = value.strip() if isinstance(value, str) else ""
    if not _PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError(
            "Profile ID must use 1-64 lowercase letters, numbers, underscores, or hyphens."
        )
    return profile_id


def _validate_profile_name(value: object) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > 100:
        raise ValueError("Enter a profile name.")
    return name


def _normalize_model_profile_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    return _validate_profile_id(value)


def _normalize_brave_api_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Unsupported search settings format.")
    key = value.strip()
    if len(key) > 8192:
        raise ValueError("Brave Search API key is too long.")
    return key
