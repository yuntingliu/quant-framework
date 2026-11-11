"""Disposable Node ancestry fixture; real Python activation, substituted core."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
base = Path(sys.argv[1]).resolve()
assert base.parent == Path(tempfile.gettempdir()).resolve()
assert base.name.startswith("alphalab-activation-node-")
for key in ("ALPHALAB_RUNTIME_DIR", "ALPHALAB_APP_DATA_DIR", "ALPHALAB_CACHE_DIR"):
    os.environ[key] = str(base / key)
import pytest  # noqa: E402

from tests.api.test_configuration_activation import activate, candidate  # noqa: E402

changes = pytest.MonkeyPatch()
root, store, owner, prepared = setup = candidate.__wrapped__(base, changes)
record = activate(setup, sys.argv[2])
latest = store.read_snapshot()
fault = sys.argv[3]
if fault != "anchor":
    for counter in (1, 2):

        def update(old):
            documents = old.documents
            for document in documents.values():
                document["counter"] = counter
            return documents, old.selections

        latest = store.update(update)
    with sqlite3.connect(root / "app/secrets/configuration-store.sqlite3") as db:
        if fault == "fork":
            db.execute(
                "UPDATE generations SET parent_revision=? WHERE revision=?",
                (record["revision"], latest.revision),
            )
        elif fault == "missing":
            db.execute(
                "UPDATE generations SET parent_revision=? WHERE revision=?",
                ("0" * 32, latest.revision),
            )
        elif fault == "cycle":
            db.execute(
                "UPDATE generations SET parent_revision=? WHERE revision=?",
                (latest.revision, record["revision"]),
            )
        elif fault == "rollback":
            db.execute("UPDATE store_metadata SET current_revision=?", (record["revision"],))
print(json.dumps({"root": str(root), "record": record, "revision": latest.revision}))
