"""Persistence for mutable validation.py files and immutable run packages."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from alphalab.strategy.repository import normalize_project_id
from alphalab.utils.paths import APP_DATA_DIR
from alphalab.validation.builtins import DEFAULT_VALIDATION_SOURCE
from alphalab.validation.source import (
    inspect_validation_source,
    update_validation_parameters,
)

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"
_DEFAULT_DB = APP_DATA_DIR / "alphalab.db"


class ValidationRepository:
    """Own the project's validation source independently from strategy.py."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path) if db_path else _DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))

    def close(self) -> None:
        self._conn.close()

    def get_or_create(self, project_id: str) -> dict[str, Any]:
        normalized = normalize_project_id(project_id)
        project = self._project(normalized)
        row = self._conn.execute(
            "SELECT * FROM validation_sources WHERE project_id = ?", (normalized,)
        ).fetchone()
        if row is None:
            inspection = inspect_validation_source(DEFAULT_VALIDATION_SOURCE)
            with self._lock:
                self._conn.execute(
                    """INSERT OR IGNORE INTO validation_sources
                       (project_id, source, source_sha256, current_revision)
                       VALUES (?, ?, ?, 1)""",
                    (normalized, DEFAULT_VALIDATION_SOURCE, inspection.source_sha256),
                )
                self._conn.execute(
                    """INSERT OR IGNORE INTO validation_source_packages
                       (project_id, revision, source, source_sha256, inspection_json)
                       VALUES (?, 1, ?, ?, ?)""",
                    (
                        normalized,
                        DEFAULT_VALIDATION_SOURCE,
                        inspection.source_sha256,
                        json.dumps(inspection.to_dict(), sort_keys=True),
                    ),
                )
                self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM validation_sources WHERE project_id = ?", (normalized,)
            ).fetchone()
        assert row is not None
        return self._payload(row, project)

    def get_package(self, project_id: str, revision: int | None = None) -> dict[str, Any]:
        current = self.get_or_create(project_id)
        selected = int(revision or current["current_revision"])
        row = self._conn.execute(
            """SELECT * FROM validation_source_packages
               WHERE project_id = ? AND revision = ?""",
            (normalize_project_id(project_id), selected),
        ).fetchone()
        if row is None:
            raise KeyError(f"{project_id}@validation-{selected}")
        return {
            "project_id": row["project_id"],
            "revision": int(row["revision"]),
            "source": row["source"],
            "source_sha256": row["source_sha256"],
            "inspection": json.loads(row["inspection_json"]),
            "created_at": row["created_at"],
        }

    def update_source(
        self,
        project_id: str,
        source: str,
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_project_id(project_id)
        project = self._project(normalized)
        if bool(project["built_in"]):
            raise PermissionError("built-in project validation source is read-only")
        current = self.get_or_create(normalized)
        if expected_source_sha256 and current["source_sha256"] != expected_source_sha256:
            raise RuntimeError("validation source changed since it was inspected")
        inspection = inspect_validation_source(source)
        if inspection.source_sha256 == current["source_sha256"]:
            return current
        revision = int(current["current_revision"]) + 1
        with self._lock:
            self._conn.execute(
                """INSERT INTO validation_source_packages
                   (project_id, revision, source, source_sha256, inspection_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    normalized,
                    revision,
                    source,
                    inspection.source_sha256,
                    json.dumps(inspection.to_dict(), sort_keys=True),
                ),
            )
            self._conn.execute(
                """UPDATE validation_sources
                   SET source = ?, source_sha256 = ?, current_revision = ?,
                       updated_at = datetime('now')
                   WHERE project_id = ?""",
                (source, inspection.source_sha256, revision, normalized),
            )
            self._conn.commit()
        return self.get_or_create(normalized)

    def update_parameters(
        self,
        project_id: str,
        edits: Iterable[dict[str, Any]],
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_or_create(project_id)
        if expected_source_sha256 and current["source_sha256"] != expected_source_sha256:
            raise RuntimeError("validation source changed since it was inspected")
        source, _ = update_validation_parameters(current["source"], edits)
        return self.update_source(
            project_id,
            source,
            expected_source_sha256=current["source_sha256"],
        )

    def clone_source(self, source_project_id: str, target_project_id: str) -> dict[str, Any]:
        source = self.get_or_create(source_project_id)
        target = normalize_project_id(target_project_id)
        self._project(target)
        inspection = inspect_validation_source(source["source"])
        with self._lock:
            self._conn.execute(
                "DELETE FROM validation_source_packages WHERE project_id = ?", (target,)
            )
            self._conn.execute("DELETE FROM validation_sources WHERE project_id = ?", (target,))
            self._conn.execute(
                """INSERT INTO validation_sources
                   (project_id, source, source_sha256, current_revision)
                   VALUES (?, ?, ?, 1)""",
                (target, source["source"], inspection.source_sha256),
            )
            self._conn.execute(
                """INSERT INTO validation_source_packages
                   (project_id, revision, source, source_sha256, inspection_json)
                   VALUES (?, 1, ?, ?, ?)""",
                (
                    target,
                    source["source"],
                    inspection.source_sha256,
                    json.dumps(inspection.to_dict(), sort_keys=True),
                ),
            )
            self._conn.commit()
        return self.get_or_create(target)

    def _project(self, project_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT id, built_in FROM strategy_projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return row

    @staticmethod
    def _payload(row: sqlite3.Row, project: sqlite3.Row) -> dict[str, Any]:
        inspection = inspect_validation_source(str(row["source"]))
        return {
            "project_id": row["project_id"],
            "source": row["source"],
            "source_sha256": row["source_sha256"],
            "current_revision": int(row["current_revision"]),
            "editable": not bool(project["built_in"]),
            "inspection": inspection.to_dict(),
            "updated_at": row["updated_at"],
        }


__all__ = ["ValidationRepository"]
