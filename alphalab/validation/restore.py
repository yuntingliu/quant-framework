"""Read-only previews and transactional restoration of validation.py revisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from alphalab.validation.source import inspect_validation_source


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _state(db: sqlite3.Connection, project: str, revision: int) -> tuple[sqlite3.Row, str]:
    owner = db.execute(
        "SELECT built_in FROM strategy_projects WHERE id=?", (project,)
    ).fetchone()
    if owner is None:
        raise KeyError(project)
    if bool(owner["built_in"]):
        raise PermissionError("built-in project validation source is read-only")
    row = db.execute(
        """SELECT s.source, s.source_sha256 AS current_sha256, s.current_revision,
                  p.built_in, t.source AS target, t.source_sha256 AS target_sha256
           FROM validation_sources s
           JOIN strategy_projects p ON p.id=s.project_id
           JOIN validation_source_packages t
             ON t.project_id=s.project_id AND t.revision=?
           WHERE s.project_id=?""",
        (revision, project),
    ).fetchone()
    if row is None:
        raise KeyError("validation revision unavailable")
    if bool(row["built_in"]):
        raise PermissionError("built-in project validation source is read-only")
    if _digest(row["source"]) != row["current_sha256"]:
        raise ValueError("current validation source checksum mismatch")
    if _digest(row["target"]) != row["target_sha256"]:
        raise ValueError("historical validation source checksum mismatch")
    token = _digest(json.dumps([
        project,
        "validation",
        revision,
        row["current_revision"],
        row["current_sha256"],
        row["target_sha256"],
    ]))
    return row, token


def preview(db: sqlite3.Connection, project: str, revision: int) -> dict:
    from alphalab.strategy.history import source_changes

    row, token = _state(db, project, revision)
    current = {
        "validation.py": {
            "kind": "validation",
            "source": row["source"],
            "source_sha256": row["current_sha256"],
        }
    }
    target = {
        "validation.py": {
            "kind": "validation",
            "source": row["target"],
            "source_sha256": row["target_sha256"],
        }
    }
    return {
        "token": token,
        "affected_strategies": [],
        "changes": source_changes(current, target, "current", revision),
        "metadata_changes": [],
    }


def _receipt(
    db: sqlite3.Connection,
    project: str,
    request_id: str,
    revision: int,
    token: str,
) -> dict | None:
    row = db.execute(
        """SELECT revision, source_sha256, restored_from, previous_revision, preview_token
           FROM validation_source_packages WHERE project_id=? AND request_id=?""",
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
    }


def restore(repo, project: str, revision: int, token: str, request_id: str) -> dict:
    db = repo._conn
    with repo._lock, db:
        db.execute("BEGIN")
        project = repo._project(project)["id"]
        replay = _receipt(db, project, request_id, revision, token)
        if replay:
            return replay
        row, expected = _state(db, project, revision)
        if token != expected:
            raise RuntimeError("validation source changed since restore preview")
        if row["source"] == row["target"]:
            raise ValueError("current validation source already matches this revision")

    inspection = inspect_validation_source(row["target"])

    with repo._lock, db:
        db.execute("BEGIN IMMEDIATE")
        replay = _receipt(db, project, request_id, revision, token)
        if replay:
            return replay
        row, expected = _state(db, project, revision)
        if token != expected:
            raise RuntimeError("validation source changed since restore preview")
        next_revision = int(row["current_revision"]) + 1
        db.execute(
            """INSERT INTO validation_source_packages(
                   project_id, revision, source, source_sha256, inspection_json,
                   restored_from, previous_revision, request_id, preview_token
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project,
                next_revision,
                row["target"],
                row["target_sha256"],
                json.dumps(inspection.to_dict(), sort_keys=True),
                revision,
                row["current_revision"],
                request_id,
                token,
            ),
        )
        changed = db.execute(
            """UPDATE validation_sources
               SET source=?, source_sha256=?, current_revision=?, updated_at=datetime('now')
               WHERE project_id=? AND current_revision=? AND source_sha256=?""",
            (
                row["target"],
                row["target_sha256"],
                next_revision,
                project,
                row["current_revision"],
                row["current_sha256"],
            ),
        )
        if changed.rowcount != 1:
            raise RuntimeError("validation source changed during restore")
        return _receipt(db, project, request_id, revision, token)
