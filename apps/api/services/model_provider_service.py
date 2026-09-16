"""Private local model profiles; public responses contain no API key values."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from alphalab.utils.paths import RUNTIME_DIR

_LOCK = threading.RLock()


def settings_path() -> Path:
    root = Path(os.getenv("ALPHALAB_RUNTIME_DIR", str(RUNTIME_DIR))).expanduser()
    return root / "conexus" / "secrets" / "model-providers.json"


def read_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return {"version": 1, "active_id": None, "profiles": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("version") != 1 or not isinstance(value.get("profiles"), list):
        raise ValueError("Unsupported model settings format.")
    return value


def validate_profile(profile: dict) -> dict:
    result = dict(profile)
    result["base_url"] = str(result.get("base_url", "")).strip().rstrip("/")
    result["model"] = str(result.get("model", "")).strip()
    result["api_key"] = str(result.get("api_key", "")).strip()
    parsed = urlsplit(result["base_url"])
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))):
        raise ValueError("Use an HTTPS API base URL or loopback HTTP, without URL credentials, query, or fragment.")
    if not result["model"]:
        raise ValueError("Enter a model ID.")
    if not loopback and not result["api_key"]:
        raise ValueError("A remote provider requires an API key.")
    return result


def active_profile() -> dict | None:
    settings = read_settings()
    for row in settings["profiles"]:
        if row["id"] == settings["active_id"]:
            return validate_profile(row)
    base = os.getenv("CONEXUS_MODEL_BASE_URL", "").strip()
    model = os.getenv("CONEXUS_MODEL_ID", "").strip()
    if base and model:
        return validate_profile({
            "id": "environment", "name": "Environment", "base_url": base,
            "model": model, "api_key": os.getenv("CONEXUS_MODEL_API_KEY", ""),
        })
    return None


def public_settings() -> dict:
    settings = read_settings()
    profile = active_profile()
    return {
        "mode": os.getenv("ALPHALAB_AGENT_MODE", "remote"),
        "active_id": profile["id"] if profile else None,
        "profiles": [{key: value for key, value in row.items() if key != "api_key"}
                     | {"has_api_key": bool(row.get("api_key"))} for row in settings["profiles"]],
        "environment_configured": bool(os.getenv("CONEXUS_MODEL_BASE_URL") and os.getenv("CONEXUS_MODEL_ID")),
    }


def save_profile(profile: dict, *, activate: bool) -> dict:
    with _LOCK:
        settings = read_settings()
        previous = next((row for row in settings["profiles"] if row["id"] == profile["id"]), None)
        # Empty input preserves a stored key only when the destination is unchanged.
        # A provider switch must never send an old provider's key to another origin.
        if not profile.get("api_key") and previous and profile["base_url"].strip().rstrip("/") == previous["base_url"]:
            profile = {**profile, "api_key": previous.get("api_key", "")}
        profile = validate_profile(profile)
        rows = [row for row in settings["profiles"] if row["id"] != profile["id"]]
        if len(rows) >= 20:
            raise ValueError("At most 20 provider profiles are supported.")
        settings["profiles"] = rows + [profile]
        if activate:
            settings["active_id"] = profile["id"]
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(settings, stream)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    return public_settings()


def resolve_test_profile(profile: dict) -> dict:
    previous = next((row for row in read_settings()["profiles"] if row["id"] == profile["id"]), None)
    if not profile.get("api_key") and previous and profile["base_url"].strip().rstrip("/") == previous["base_url"]:
        profile = {**profile, "api_key": previous.get("api_key", "")}
    return validate_profile(profile)
