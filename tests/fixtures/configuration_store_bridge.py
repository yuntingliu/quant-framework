"""Test-owned Python/Node interoperability fixture; no providers or live settings."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from alphalab.utils import configuration_store as implementation  # noqa: E402
from alphalab.utils.configuration_store import ConfigurationStore  # noqa: E402


def emit(value):
    print(json.dumps(value), flush=True)


def documents(counter=0, *, intent="profile", search_version=2, empty_secrets=False):
    settings = {
        "models": {
            "version": 1,
            "active_id": "model" if intent == "profile" else None,
            "profiles": [
                {
                    "id": "model",
                    "name": "Model \u03a9 \U0001f680",
                    "base_url": "https://model.example/v1",
                    "model": "provider/model-name",
                    "api_key": "fixture-node-model-secret-1102",
                },
                {"id": "inactive", "api_key": "fixture-node-inactive-secret-2203"},
            ],
        },
        "search": {
            "version": 2,
            "active_id": "search" if intent == "profile" else None,
            "profiles": [
                {
                    "id": "search",
                    "provider": "brave",
                    "model_profile_id": "model",
                    "brave_api_key": "fixture-node-brave-secret-3304",
                }
            ],
        },
        "rqdata": {
            "version": 1,
            "active_id": "rq",
            "profiles": [{"id": "rq", "password": "fixture-node-rqdata-secret-4405"}],
        },
    }
    choices = {
        "models": {"kind": "profile", "id": "model"} if intent == "profile" else {"kind": intent},
        "search": {"kind": "profile", "id": "search"} if intent == "profile" else {"kind": intent},
        "rqdata": {"kind": "profile", "id": "rq"},
    }
    if search_version == 1:
        settings["search"] = {
            "version": 1,
            "provider": "brave",
            "brave_api_key": "fixture-node-brave-secret-3304",
            "model_profile_id": "model",
        }
        choices["search"] = {"kind": "profile", "id": "default"}
    if empty_secrets:
        settings["models"]["profiles"][0]["api_key"] = ""
        if search_version == 1:
            settings["search"]["brave_api_key"] = ""
        else:
            settings["search"]["profiles"][0]["brave_api_key"] = ""
    for document in settings.values():
        document["counter"] = counter
    return settings, choices


def advance(snapshot):
    settings = snapshot.documents
    for document in settings.values():
        document["counter"] += 1
    return settings, snapshot.selections


def metadata(snapshot):
    return {
        "revision": snapshot.revision,
        "installation_id": snapshot.installation_id,
        "counter": snapshot.documents["models"]["counter"],
    }


def main():
    root, command = Path(sys.argv[1]), sys.argv[2]
    options = json.loads(sys.stdin.readline() or "{}")
    path = root / "app/secrets/configuration-store.sqlite3"
    if command == "init":
        settings, choices = documents(**options)
        settings["models"]["large_integer"] = 2**64 - 1
        settings["models"]["fraction"] = 1.5
        settings["models"]["__proto__"] = {"polluted": "fixture-only"}
        store = ConfigurationStore.initialize(root, documents=settings, selections=choices)
        emit(metadata(store.read_snapshot()))
    elif command == "update":
        store = ConfigurationStore.open_existing(root)
        emit(metadata(store.update(advance)))
    elif command == "root_keys":
        emit(
            {
                "keys": [
                    hashlib.sha256(os.path.normcase(value).encode("utf-8")).hexdigest()
                    for value in options["values"]
                ]
            }
        )
    elif command == "update_gate":
        store = ConfigurationStore.open_existing(root, timeout=15)

        def gated(snapshot):
            emit({"ready": True})
            return advance(snapshot)

        emit(metadata(store.update(gated)))
    elif command == "loop":
        store = ConfigurationStore.open_existing(root, timeout=15)
        emit({"ready": True})
        for _ in range(options.get("count", 20)):
            emit(metadata(store.update(advance)))
            time.sleep(0.015)
    elif command == "lock":
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            connection.execute("BEGIN " + options.get("mode", "EXCLUSIVE"))
            connection.execute("SELECT current_revision FROM store_metadata").fetchone()
            emit({"ready": True})
            sys.stdin.readline()
            connection.rollback()
    elif command == "crash":
        store = ConfigurationStore.open_existing(root, timeout=15)
        execute = implementation._Session.execute

        def interrupted(session, sql, parameters=()):
            if sql == "BEGIN IMMEDIATE":
                execute(session, "PRAGMA cache_size=1")
                execute(session, "PRAGMA cache_spill=ON")
            result = execute(session, sql, parameters)
            if sql.startswith("UPDATE store_metadata"):
                emit({"ready": True})
                sys.stdin.readline()
            return result

        def mutate(snapshot):
            settings, choices = advance(snapshot)
            for document in settings.values():
                document["padding"] = "x" * 128000
            return settings, choices

        with patch.object(implementation._Session, "execute", interrupted):
            store.update(mutate)
    elif command == "tamper":
        with closing(sqlite3.connect(path)) as connection:
            case = options["case"]
            if case == "protocol":
                connection.execute("PRAGMA user_version=999")
            elif case == "metadata_protocol":
                connection.execute("UPDATE store_metadata SET protocol_version=999")
            elif case == "singleton":
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute("UPDATE store_metadata SET singleton=2")
            elif case == "missing_current":
                connection.execute("UPDATE store_metadata SET current_revision=?", ("a" * 32,))
            elif case == "history":
                connection.execute(
                    "UPDATE store_metadata SET current_revision=(SELECT revision FROM generations ORDER BY ordinal LIMIT 1)"
                )
            elif case == "wal":
                connection.execute("PRAGMA journal_mode=WAL")
            elif case == "missing_table":
                connection.execute("DROP TABLE generations")
            elif case == "model_text":
                connection.execute("UPDATE generations SET models=?", (options["text"],))
            elif case == "selection_text":
                connection.execute("UPDATE generations SET selections=?", (options["text"],))
            elif case == "search_text":
                connection.execute("UPDATE generations SET search=?", (options["text"],))
            else:
                raise ValueError("Unknown fixture operation")
            connection.commit()
        emit({"ok": True})
    else:
        raise ValueError("Unknown fixture operation")


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    try:
        main()
    except BaseException:
        emit({"fixture_error": True})
        raise SystemExit(1) from None
