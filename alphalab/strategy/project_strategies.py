"""Project strategy identity and atomic saves over the canonical source packages."""

from __future__ import annotations

import hashlib
import re

from alphalab.strategy.sdk_runtime import SdkRuntimeError
from alphalab.strategy.source import StrategySourceError, assemble_strategy_source


def strategy_id(value: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
        raise ValueError("strategy id must be 1-64 lowercase letters, digits or underscores, starting with a letter")
    return value


class ProjectStrategies:
    """Repository operations; selection belongs to this repository instance only."""

    _strategy_id = "main"

    def select_strategy(self, value: str = "main"):
        self._strategy_id = strategy_id(value)
        return self

    def _ensure_strategies(self) -> None:
        # Only mutable authoring rows are migrated. Historical packages keep their
        # original paths and hashes. strategy.py remains a compatibility alias.
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            for row in self._conn.execute("SELECT id, current_revision FROM strategy_projects").fetchall():
                unit = self._conn.execute(
                    "SELECT source FROM strategy_source_units WHERE project_id = ? AND kind = 'strategy'",
                    (row["id"],),
                ).fetchone()
                if unit is None:
                    continue
                exists = self._conn.execute(
                    "SELECT 1 FROM project_strategies WHERE project_id = ? AND id = 'main'", (row["id"],),
                ).fetchone()
                self._conn.execute(
                    """INSERT INTO project_strategies(project_id, id, name, source, current_revision)
                       VALUES (?, 'main', '主策略', ?, ?)
                       ON CONFLICT(project_id, id) DO UPDATE SET source = excluded.source,
                       current_revision = excluded.current_revision
                       WHERE source != excluded.source OR current_revision != excluded.current_revision""",
                    (row["id"], unit["source"], row["current_revision"]),
                )
                if not exists:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO project_strategy_packages
                           SELECT project_id, 'main', revision FROM strategy_source_packages WHERE project_id = ?""",
                        (row["id"],),
                    )
                self._conn.execute("INSERT OR IGNORE INTO project_strategy_packages VALUES (?, 'main', ?)",
                                   (row["id"], row["current_revision"]))

    def has_strategy_package(self, project_id: str, revision: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM project_strategy_packages WHERE project_id = ? AND strategy_id = ? AND revision = ?",
            (str(project_id).strip().lower(), self._strategy_id, revision),
        ).fetchone() is not None

    def use_run_settings(self, settings: dict):
        self._run_settings = dict(settings)
        return self

    def snapshot_strategies(self, project_id: str, ids: list[str]) -> list[dict]:
        """Read all selected source versions and common settings in one snapshot."""
        previous = self._strategy_id
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                snapshots = []
                for value in ids:
                    project = self.select_strategy(value).get_project(project_id, include_source=False)
                    if project is None:
                        raise KeyError(project_id)
                    snapshots.append(project)
                self._conn.commit()
                return snapshots
            except Exception:
                self._conn.rollback()
                raise
            finally:
                self._strategy_id = previous

    def list_strategies(self, project_id: str) -> list[dict]:
        return [
            {"id": row["id"], "name": row["name"], "path": f"strategies/{row['id']}.py",
             "current_revision": row["current_revision"]}
            for row in self._strategy_rows(project_id) if not row["archived"]
        ]

    def _strategy_rows(self, project_id: str) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM project_strategies WHERE project_id = ? ORDER BY id != 'main', id", (project_id,),
        ).fetchall()]

    def _strategy_row(self, row):
        if self._strategy_id == "main":
            return row
        selected = self._conn.execute(
            """SELECT s.*, p.source AS bundled, p.source_sha256 FROM project_strategies s
               JOIN strategy_source_packages p ON p.project_id = s.project_id AND p.revision = s.current_revision
               WHERE s.project_id = ? AND s.id = ? AND s.archived = 0""", (row["id"], self._strategy_id),
        ).fetchone()
        if selected is None:
            raise KeyError(self._strategy_id)
        return {**dict(row), "draft_source": selected["bundled"],
                "draft_source_sha256": selected["source_sha256"],
                "current_revision": selected["current_revision"],
                "draft_parent_revision": selected["current_revision"]}

    def create_strategy(self, project_id: str, value: str, *, name: str, copy_from: str = "main") -> dict:
        value, copy_from = strategy_id(value), strategy_id(copy_from)
        project_id = str(self._editable_row(project_id)["id"])
        if not name.strip():
            raise ValueError("strategy name must not be empty")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                rows = self._strategy_rows(project_id)
                if any(row["id"] == value for row in rows):
                    raise FileExistsError(value)
                if sum(not row["archived"] for row in rows) >= 20:
                    raise ValueError("a project supports at most 20 active strategies")
                original = next((row for row in rows if row["id"] == copy_from and not row["archived"]), None)
                if original is None:
                    raise KeyError(copy_from)
                self._conn.execute(
                    "INSERT INTO project_strategies(project_id,id,name,source,current_revision) VALUES (?,?,?,?,?)",
                    (project_id, value, name.strip(), original["source"], original["current_revision"]),
                )
                self._conn.execute("INSERT INTO project_strategy_packages VALUES (?,?,?)",
                                   (project_id, value, original["current_revision"]))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.select_strategy(value).get_project(project_id)

    def rename_strategy(self, project_id: str, *, name: str) -> dict:
        project_id = str(self._editable_row(project_id)["id"])
        if not name.strip():
            raise ValueError("strategy name must not be empty")
        with self._lock, self._conn:
            self._conn.execute("UPDATE project_strategies SET name = ? WHERE project_id = ? AND id = ?",
                               (name.strip(), project_id, self._strategy_id))
        return self.get_project(project_id)

    def _commit_all_sources(self, project_id, source, factors, *, expected_source_sha256=None):
        observed = self._editable_row(project_id)
        project_id = str(observed["id"])
        if expected_source_sha256 and observed["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        catalog = self._strategy_rows(project_id)
        sources = {row["id"]: row["source"] for row in catalog if not row["archived"]}
        sources[self._strategy_id] = source
        prepared = {}
        for key, strategy_source in sources.items():
            try:
                bundled, inspection = assemble_strategy_source(strategy_source, factors)
                current = next((row for row in catalog if row["id"] == key), None)
                previous = self._conn.execute(
                    "SELECT source_sha256 FROM strategy_source_packages WHERE project_id = ? AND revision = ?",
                    (project_id, current["current_revision"] if current else -1),
                ).fetchone()
                if previous is None or previous["source_sha256"] != inspection.source_sha256:
                    self._probe_source(bundled, inspection)
                prepared[key] = (bundled, inspection)
            except (StrategySourceError, SdkRuntimeError) as exc:
                raise StrategySourceError(f"strategy {key}: {exc}", phase=getattr(exc, "phase", "execute")) from exc
        factor_ids = [item.id for item in prepared[self._strategy_id][1].entrypoints if item.kind == "factor"]
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if self._strategy_rows(project_id) != catalog:
                    raise RuntimeError("a strategy changed while shared sources were being validated")
                locked = self._editable_row(project_id)
                if locked["draft_source_sha256"] != observed["draft_source_sha256"]:
                    raise RuntimeError("draft changed while the update was being prepared")
                for key, (bundled, inspection) in prepared.items():
                    old = next(row for row in catalog if row["id"] == key)
                    package = self._conn.execute(
                        "SELECT revision FROM strategy_source_packages WHERE project_id = ? AND source_sha256 = ?",
                        (project_id, inspection.source_sha256),
                    ).fetchone()
                    if package:
                        revision = int(package["revision"])
                    else:
                        revision = self._conn.execute(
                            "SELECT COALESCE(MAX(revision), 0) + 1 FROM strategy_source_packages WHERE project_id = ?",
                            (project_id,),
                        ).fetchone()[0]
                        self._insert_package(project_id, revision, old["current_revision"], bundled, inspection)
                    self._conn.execute(
                        "UPDATE project_strategies SET source = ?, current_revision = ? WHERE project_id = ? AND id = ?",
                        (sources[key], revision, project_id, key),
                    )
                    self._conn.execute("INSERT OR IGNORE INTO project_strategy_packages VALUES (?,?,?)",
                                       (project_id, key, revision))
                    if key == "main":
                        self._conn.execute(
                            """UPDATE strategy_projects SET current_revision = ?, draft_parent_revision = ?,
                               draft_source = ?, draft_source_sha256 = ?, updated_at = datetime('now') WHERE id = ?""",
                            (revision, revision, bundled, inspection.source_sha256, project_id),
                        )
                self._write_source_units(project_id, sources["main"], factors, factor_ids)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        result = self.get_project(project_id)
        result["affected_strategies"] = [key for key in prepared if any(
            row["id"] == key and row["current_revision"] != next(
                item["current_revision"] for item in result["strategies"] if item["id"] == key
            ) for row in catalog
        )]
        return result

    def _selected_units(self, rows):
        if self._strategy_id == "main":
            return rows
        if not rows:
            return rows
        selected = next((row for row in self._strategy_rows(rows[0]["project_id"])
                         if row["id"] == self._strategy_id and not row["archived"]), None)
        if selected is None:
            raise KeyError(self._strategy_id)
        return [
            {**dict(row), "path": f"strategies/{self._strategy_id}.py", "source": selected["source"],
             "source_sha256": hashlib.sha256(selected["source"].encode()).hexdigest()}
            if row["kind"] == "strategy" else row for row in rows
        ]
