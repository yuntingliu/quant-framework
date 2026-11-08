"""Synthetic real-store fixture for inactive Agent runtime integration tests."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from alphalab.utils.configuration_store import ConfigurationStore  # noqa: E402
from apps.api.services.configuration_profile_service import ConfigurationProfileServices  # noqa: E402


def documents():
    return {
        "models": {
            "version": 1,
            "active_id": "model",
            "profiles": [
                {
                    "id": "model",
                    "name": "Model",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "provider/model-v0",
                    "api_key": "runtime-model-secret-4101",
                },
                {
                    "id": "alternate",
                    "name": "Alternate",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "provider/alternate-v0",
                    "api_key": "runtime-reference-secret-4102",
                },
            ],
        },
        "search": {
            "version": 2,
            "active_id": "search",
            "profiles": [
                {
                    "id": "search",
                    "name": "Search",
                    "provider": "brave",
                    "model_profile_id": "alternate",
                    "brave_api_key": "runtime-brave-secret-4103",
                },
            ],
        },
        "rqdata": {
            "version": 1,
            "active_id": "rq",
            "profiles": [
                {
                    "id": "rq",
                    "name": "RQ",
                    "provider": "rqdata",
                    "user": "fixture-user",
                    "host": "rq.example:16011",
                    "password": "runtime-rq-secret-4104",
                },
            ],
        },
    }


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    root, command = Path(sys.argv[1]), sys.argv[2]
    options = json.loads(sys.stdin.readline() or "{}")
    if command == "init":
        native = documents()
        selections = {
            key: {"kind": "profile", "id": value["active_id"]} for key, value in native.items()
        }
        store = ConfigurationStore.initialize(root, documents=native, selections=selections)
    else:
        store = ConfigurationStore.open_existing(root)
    services = ConfigurationProfileServices(store)
    if command == "edit":
        if options.get("profiles"):
            services.save_profiles(
                options["profiles"],
                activate=options.get("activate", True),
                clear_secrets=set(options.get("clear", [])),
            )
        for service, choice in options.get("selections", {}).items():
            services.select(service, choice)
    elif command == "patch":
        # Deliberately exercise incomplete but structurally valid native states.
        def patch(current):
            native = current.documents
            for service, changes in options.items():
                native[service]["profiles"][0].update(changes)
            return native, current.selections

        store.update(patch)
    elif command == "loop":
        emit({"ready": True})
        for index in range(1, options.get("count", 6) + 1):
            native = documents()
            model = native["models"]["profiles"][0]
            model.update(model=f"provider/model-v{index}", api_key=f"runtime-model-key-{index}")
            search = native["search"]["profiles"][0]
            search.update(provider="openrouter", model_profile_id="model")
            services.save_profiles({"models": model, "search": search})
            time.sleep(0.02)
    elif command == "lock":
        with closing(
            sqlite3.connect(root / "app/secrets/configuration-store.sqlite3")
        ) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            emit({"ready": True})
            sys.stdin.readline()
            connection.rollback()
    elif command not in {"init", "edit", "patch"}:
        raise ValueError("Unknown synthetic fixture command.")
    snapshot = store.read_snapshot()
    emit({"revision": snapshot.revision, "installation_id": snapshot.installation_id})


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    try:
        main()
    except BaseException:
        emit({"fixture_error": True})
        raise SystemExit(1) from None
