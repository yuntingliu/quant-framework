"""Saved workstation-local RQData profiles with private password storage."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from alphalab.dataio.errors import DataLoadError
from alphalab.utils.env import load_env_files
from alphalab.utils.private_settings import (
    app_secret_path,
    read_json_settings,
    write_json_settings,
)

_ENV_KEYS = ("RQ_USER", "RQ_PASSWORD", "RQ_HOST")
_PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"


@dataclass(frozen=True)
class SavedRQProfile:
    id: str
    name: str
    provider: str
    user: str
    password: str
    host: str

    def to_config(self) -> dict[str, str]:
        return {"RQ_USER": self.user, "RQ_PASSWORD": self.password, "RQ_HOST": self.host}


def settings_path() -> Path:
    return app_secret_path("rqdata-profiles.json")


def default_settings() -> dict[str, Any]:
    return {"version": 1, "active_id": None, "profiles": []}


def read_settings() -> dict[str, Any]:
    return read_json_settings(settings_path(), default=default_settings, validate=validate_settings)


def validate_settings(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported RQData settings format.")
    profiles = value.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("Unsupported RQData settings format.")
    active_id = value.get("active_id")
    if active_id is not None:
        active_id = _validate_profile_id(active_id)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in profiles:
        validated = validate_profile_row(row)
        if validated["id"] in seen:
            raise ValueError("Unsupported RQData settings format.")
        seen.add(validated["id"])
        normalized.append(validated)
    if active_id is not None and active_id not in seen:
        raise ValueError("Unsupported RQData settings format.")
    return {"version": 1, "active_id": active_id, "profiles": normalized}


def validate_profile_input(profile: Mapping[str, object]) -> dict[str, str]:
    profile_id = _validate_profile_id(profile.get("id"))
    name = str(profile.get("name", "")).strip()
    user = str(profile.get("user", "")).strip()
    host = _validate_host(profile.get("host", ""))
    password = str(profile.get("password", "")).strip()
    if not name or len(name) > 100:
        raise ValueError("Enter a profile name.")
    if not user or len(user) > 200:
        raise ValueError("Enter an RQData user name.")
    if len(password) > 8192:
        raise ValueError("Password is too long.")
    return {
        "id": profile_id,
        "name": name,
        "provider": "rqdata",
        "user": user,
        "password": password,
        "host": host,
    }


def validate_profile_row(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("Unsupported RQData settings format.")
    profile = validate_profile_input(value)
    if str(value.get("provider", "rqdata")).strip() != "rqdata":
        raise ValueError("Unsupported RQData settings format.")
    return profile


def public_profile(row: Mapping[str, object]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "provider": "rqdata",
        "user": str(row["user"]),
        "host": str(row["host"]),
        "has_password": bool(str(row.get("password", "")).strip()),
    }


def public_settings() -> dict[str, Any]:
    settings = read_settings()
    resolved = active_profile()
    return {
        "provider": "rqdata",
        "active_id": settings["active_id"] if settings["active_id"] is not None else resolved.id if resolved is not None else None,
        "profiles": [public_profile(row) for row in settings["profiles"]],
        "environment_configured": _environment_is_configured(),
        "configured": resolved is not None,
        "source": "saved" if _saved_active_profile(settings) is not None else "environment",
    }


def save_profile(
    profile: Mapping[str, object],
    *,
    activate: bool,
    clear_password: bool = False,
) -> dict[str, Any]:
    settings = read_settings()
    normalized = validate_profile_input(profile)
    previous = next((row for row in settings["profiles"] if row["id"] == normalized["id"]), None)
    normalized["password"] = _resolve_password(normalized, previous, clear_password=clear_password)
    rows = [row for row in settings["profiles"] if row["id"] != normalized["id"]]
    if previous is None and len(rows) >= 20:
        raise ValueError("At most 20 RQData profiles are supported.")
    rows.append(normalized)
    next_settings = {"version": 1, "active_id": settings["active_id"], "profiles": rows}
    if activate:
        next_settings["active_id"] = normalized["id"]
    write_json_settings(settings_path(), next_settings, validate=validate_settings)
    return public_settings()


def active_profile() -> SavedRQProfile | None:
    settings = read_settings()
    saved = _saved_active_profile(settings)
    if saved is not None:
        if not saved.get("password", ""):
            return None
        return SavedRQProfile(
            id=saved["id"],
            name=saved["name"],
            provider=saved["provider"],
            user=saved["user"],
            password=saved["password"],
            host=saved["host"],
        )
    load_env_files()
    values = {key: str(os.environ.get(key, "")).strip() for key in _ENV_KEYS}
    if any(not value for value in values.values()):
        return None
    return SavedRQProfile(
        id="environment",
        name="Environment",
        provider="rqdata",
        user=values["RQ_USER"],
        password=values["RQ_PASSWORD"],
        host=values["RQ_HOST"],
    )


def active_config() -> dict[str, str]:
    settings = read_settings()
    saved = _saved_active_profile(settings)
    if saved is not None:
        if not saved.get("password", ""):
            raise DataLoadError(
                "The active saved RQData profile is incomplete. Update or clear the saved profile explicitly."
            )
        return {
            "RQ_USER": saved["user"],
            "RQ_PASSWORD": saved["password"],
            "RQ_HOST": saved["host"],
        }
    missing = missing_environment_keys()
    if missing:
        raise DataLoadError(
            "RQData is not configured. Set these values in the local .env: "
            + ", ".join(missing)
        )
    load_env_files()
    return {
        "RQ_USER": str(os.environ["RQ_USER"]).strip(),
        "RQ_PASSWORD": str(os.environ["RQ_PASSWORD"]).strip(),
        "RQ_HOST": str(os.environ["RQ_HOST"]).strip(),
    }


def resolve_connection_profile(profile: Mapping[str, object]) -> SavedRQProfile:
    settings = read_settings()
    normalized = validate_profile_input(profile)
    previous = next((row for row in settings["profiles"] if row["id"] == normalized["id"]), None)
    normalized["password"] = _resolve_password(normalized, previous, clear_password=False)
    return SavedRQProfile(
        id=normalized["id"],
        name=normalized["name"],
        provider=normalized["provider"],
        user=normalized["user"],
        password=normalized["password"],
        host=normalized["host"],
    )


def missing_environment_keys(env: Mapping[str, str] | None = None) -> list[str]:
    if env is None:
        load_env_files()
        env = os.environ
    values = {key: str(env.get(key, "")).strip() for key in _ENV_KEYS}
    return [key for key, value in values.items() if not value]


def _saved_active_profile(settings: Mapping[str, Any] | None = None) -> dict[str, str] | None:
    current = settings if settings is not None else read_settings()
    active_id = current.get("active_id")
    if not active_id:
        return None
    return next((row for row in current["profiles"] if row["id"] == active_id), None)


def _resolve_password(
    profile: Mapping[str, str],
    previous: Mapping[str, str] | None,
    *,
    clear_password: bool,
) -> str:
    password = str(profile.get("password", "")).strip()
    if clear_password:
        if password:
            raise ValueError("Password replacement and clearing must be requested separately.")
        if previous is None:
            raise ValueError("Cannot clear a password for a new RQData profile.")
        return ""
    if password:
        return password
    if previous is None:
        raise ValueError("Enter an RQData password.")
    if _same_destination(profile, previous):
        return str(previous.get("password", "")).strip()
    raise ValueError(
        "Blank password keeps the existing secret only when the profile user and host are unchanged."
    )


def _same_destination(current: Mapping[str, str], previous: Mapping[str, str]) -> bool:
    return (
        str(current.get("provider", "rqdata")).strip() == "rqdata"
        and str(previous.get("provider", "rqdata")).strip() == "rqdata"
        and str(current.get("user", "")).strip() == str(previous.get("user", "")).strip()
        and str(current.get("host", "")).strip() == str(previous.get("host", "")).strip()
    )


def _environment_is_configured() -> bool:
    return not missing_environment_keys()


def _validate_profile_id(value: object) -> str:
    profile_id = str(value or "").strip()
    if not re.fullmatch(_PROFILE_ID_PATTERN, profile_id):
        raise ValueError(
            "Profile ID must use 1-64 lowercase letters, numbers, underscores, or hyphens."
        )
    return profile_id


def _validate_host(value: object) -> str:
    host = str(value or "").strip()
    if not host or len(host) > 255:
        raise ValueError("Enter an RQData host.")
    if any(character.isspace() for character in host):
        raise ValueError("Enter an RQData host without spaces.")
    parsed = urlsplit(f"//{host}")
    if (
        parsed.scheme
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Use an RQData host like host:port, without a URL scheme, credentials, path, query, or fragment."
        )
    return host


__all__ = [
    "SavedRQProfile",
    "active_config",
    "active_profile",
    "missing_environment_keys",
    "public_settings",
    "read_settings",
    "resolve_connection_profile",
    "save_profile",
    "settings_path",
    "validate_profile_input",
    "validate_settings",
]
