"""Read-only previews and transactional restoration of data recipe revisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from alphalab.dataio.recipes import builtin_recipe_catalog, inspect_data_recipe_source


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _template_available(db: sqlite3.Connection, template_id: str | None) -> bool:
    if template_id is None:
        return True
    built_in = {
        item["id"]
        for item in builtin_recipe_catalog(start="2020-01-01", end="2020-12-31")
    }
    if template_id in built_in:
        return True
    return db.execute(
        "SELECT 1 FROM data_recipe_templates WHERE id=?", (template_id,)
    ).fetchone() is not None


def _state(db: sqlite3.Connection, project: str, revision: int) -> tuple[dict, str]:
    current = db.execute(
        "SELECT * FROM data_recipe_drafts WHERE project_id=?", (project,)
    ).fetchone()
    target = db.execute(
        """SELECT * FROM data_recipe_history WHERE project_id=? AND revision=?""",
        (project, revision),
    ).fetchone()
    latest = db.execute(
        """SELECT revision FROM data_recipe_history WHERE project_id=?
           ORDER BY revision DESC LIMIT 1""",
        (project,),
    ).fetchone()
    if current is None or target is None or latest is None:
        raise KeyError("recipe revision unavailable")
    if _digest(current["source"]) != current["source_sha256"]:
        raise ValueError("current recipe source checksum mismatch")
    if _digest(target["source"]) != target["source_sha256"]:
        raise ValueError("historical recipe source checksum mismatch")
    if not _template_available(db, target["selected_template_id"]):
        raise ValueError("historical recipe template is unavailable")
    token = _digest(json.dumps([
        project,
        "recipe",
        revision,
        latest["revision"],
        current["source_sha256"],
        current["selected_template_id"],
        target["source_sha256"],
        target["selected_template_id"],
    ]))
    return {
        "source": current["source"],
        "source_sha256": current["source_sha256"],
        "selected_template_id": current["selected_template_id"],
        "latest_revision": int(latest["revision"]),
        "target": target["source"],
        "target_sha256": target["source_sha256"],
        "target_template_id": target["selected_template_id"],
    }, token


def preview(db: sqlite3.Connection, project: str, revision: int) -> dict:
    from alphalab.strategy.history import source_changes

    state, token = _state(db, project, revision)
    current = {
        "recipe.py": {
            "kind": "recipe",
            "source": state["source"],
            "source_sha256": state["source_sha256"],
        }
    }
    target = {
        "recipe.py": {
            "kind": "recipe",
            "source": state["target"],
            "source_sha256": state["target_sha256"],
        }
    }
    metadata_changes = []
    if state["selected_template_id"] != state["target_template_id"]:
        metadata_changes.append({
            "field": "selected_template_id",
            "before": state["selected_template_id"],
            "after": state["target_template_id"],
        })
    return {
        "token": token,
        "affected_strategies": [],
        "changes": source_changes(current, target, "current", revision),
        "metadata_changes": metadata_changes,
    }


def _receipt(
    db: sqlite3.Connection,
    project: str,
    request_id: str,
    revision: int,
    token: str,
) -> dict | None:
    row = db.execute(
        """SELECT revision, source_sha256, selected_template_id,
                  restored_from, previous_revision, preview_token
           FROM data_recipe_history WHERE project_id=? AND request_id=?""",
        (project, request_id),
    ).fetchone()
    if row is None:
        return None
    if (row["restored_from"], row["preview_token"]) != (revision, token):
        raise RuntimeError("request changed since first restore")
    return {
        "revision": int(row["revision"]),
        "restored_from": int(row["restored_from"]),
        "previous_revision": int(row["previous_revision"]),
        "source_sha256": row["source_sha256"],
        "selected_template_id": row["selected_template_id"],
    }


def restore(operations, project: str, revision: int, token: str, request_id: str) -> dict:
    with operations._connect() as db:
        db.execute("BEGIN")
        replay = _receipt(db, project, request_id, revision, token)
        if replay:
            return replay
        state, expected = _state(db, project, revision)
        if token != expected:
            raise RuntimeError("data recipe changed since restore preview")
        if (
            state["source"] == state["target"]
            and state["selected_template_id"] == state["target_template_id"]
        ):
            raise ValueError("current recipe already matches this revision")

    inspect_data_recipe_source(state["target"])
    from alphalab.dataio.runtime import _now

    with operations._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        replay = _receipt(db, project, request_id, revision, token)
        if replay:
            return replay
        state, expected = _state(db, project, revision)
        if token != expected:
            raise RuntimeError("data recipe changed since restore preview")
        next_revision = state["latest_revision"] + 1
        db.execute(
            """INSERT INTO data_recipe_history(
                   project_id, revision, source, source_sha256, selected_template_id,
                   created_at, origin, restored_from, previous_revision, request_id, preview_token
               ) VALUES (?, ?, ?, ?, ?, ?, 'save', ?, ?, ?, ?)""",
            (
                project,
                next_revision,
                state["target"],
                state["target_sha256"],
                state["target_template_id"],
                _now(),
                revision,
                state["latest_revision"],
                request_id,
                token,
            ),
        )
        changed = db.execute(
            """UPDATE data_recipe_drafts
               SET source=?, source_sha256=?, selected_template_id=?, updated_at=?
               WHERE project_id=? AND source_sha256=? AND selected_template_id IS ?""",
            (
                state["target"],
                state["target_sha256"],
                state["target_template_id"],
                _now(),
                project,
                state["source_sha256"],
                state["selected_template_id"],
            ),
        )
        if changed.rowcount != 1:
            raise RuntimeError("data recipe changed during restore")
        return _receipt(db, project, request_id, revision, token)
