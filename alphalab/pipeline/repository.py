"""SQLite repository for Python components and versioned pipeline projects."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping

from alphalab.pipeline.builtins import BUILTIN_COMPONENTS, DEFAULT_PROJECT
from alphalab.pipeline.compose import ComposedStrategy, compose_strategy
from alphalab.pipeline.models import (
    ComponentRef,
    PipelineProject,
    STAGE_ENTRYPOINTS,
    STAGE_NAMES,
    normalize_id,
)
from alphalab.strategy.python_runtime import python_source_sha256, validate_python_source
from alphalab.utils.paths import APP_DATA_DIR


_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"
_DEFAULT_DB = APP_DATA_DIR / "alphalab.db"


class PipelineRepository:
    """Persist immutable component versions and revision-pinned projects."""

    def __init__(self, db_path: str | Path | None = None):
        self.path = Path(db_path) if db_path else _DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        from alphalab.pipeline.contract_migration import migrate_pipeline_contracts

        migrate_pipeline_contracts(self._conn)
        self._seed_builtins()

    def close(self) -> None:
        self._conn.close()

    def _seed_builtins(self) -> None:
        with self._lock:
            for item in BUILTIN_COMPONENTS:
                component_id = str(item["id"])
                source = str(item["source"])
                entrypoint = STAGE_ENTRYPOINTS[str(item["stage"])]
                validation = validate_python_source(source, entrypoint)
                self._conn.execute(
                    """INSERT OR IGNORE INTO pipeline_components
                       (id, stage, name, description, built_in)
                       VALUES (?, ?, ?, ?, 1)""",
                    (component_id, item["stage"], item["name"], item["description"]),
                )
                self._conn.execute(
                    """INSERT OR IGNORE INTO pipeline_component_versions
                       (component_id, version, entrypoint, source, source_sha256,
                        parameters_json, notes)
                       VALUES (?, 1, ?, ?, ?, ?, 'built-in preset')""",
                    (
                        component_id,
                        entrypoint,
                        source,
                        validation["sha256"],
                        _json(item.get("parameters") or {}),
                    ),
                )
            if self._conn.execute(
                "SELECT 1 FROM pipeline_projects WHERE id = ?",
                (DEFAULT_PROJECT["id"],),
            ).fetchone() is None:
                project = PipelineProject(
                    id=str(DEFAULT_PROJECT["id"]),
                    name=str(DEFAULT_PROJECT["name"]),
                    description=str(DEFAULT_PROJECT["description"]),
                    components={
                        stage: ComponentRef(**ref)
                        for stage, ref in dict(DEFAULT_PROJECT["components"]).items()
                    },
                    settings=dict(DEFAULT_PROJECT["settings"]),
                    built_in=True,
                )
                self._insert_project(project)
            from alphalab.pipeline.legacy_migration import (
                migrate_legacy_local_projects,
                pending_migrated_projects,
            )

            migrate_legacy_local_projects(self._conn)
            for project_id in pending_migrated_projects(self._conn):
                row = self._conn.execute(
                    "SELECT * FROM pipeline_projects WHERE id = ?", (project_id,)
                ).fetchone()
                if row is not None:
                    project = self._project_from_row(row)
                    self._insert_project_version(project, self.compose(project))
            self._conn.commit()

    def list_components(self, stage: str | None = None) -> list[dict[str, Any]]:
        if stage is not None and stage not in STAGE_NAMES:
            raise ValueError(f"stage must be one of {STAGE_NAMES}")
        query = """
            SELECT c.*, MAX(v.version) AS latest_version, COUNT(v.version) AS version_count
            FROM pipeline_components c
            JOIN pipeline_component_versions v ON v.component_id = c.id
        """
        placeholders = ",".join("?" for _ in STAGE_NAMES)
        query += f" WHERE c.stage IN ({placeholders})"
        params: tuple[Any, ...] = tuple(STAGE_NAMES)
        if stage:
            query += " AND c.stage = ?"
            params = (*params, stage)
        query += " GROUP BY c.id ORDER BY c.stage, c.built_in DESC, c.name"
        return [self._component_summary(row) for row in self._conn.execute(query, params)]

    def get_component(self, component_id: str, version: int | None = None) -> dict[str, Any] | None:
        normalized = normalize_id(component_id, label="component id")
        component = self._conn.execute(
            "SELECT * FROM pipeline_components WHERE id = ?", (normalized,)
        ).fetchone()
        if component is None:
            return None
        if str(component["stage"]) not in STAGE_NAMES:
            return None
        if version is None:
            version_row = self._conn.execute(
                """SELECT * FROM pipeline_component_versions
                   WHERE component_id = ? ORDER BY version DESC LIMIT 1""",
                (normalized,),
            ).fetchone()
        else:
            version_row = self._conn.execute(
                """SELECT * FROM pipeline_component_versions
                   WHERE component_id = ? AND version = ?""",
                (normalized, int(version)),
            ).fetchone()
        if version_row is None:
            return None
        versions = [
            int(row["version"])
            for row in self._conn.execute(
                """SELECT version FROM pipeline_component_versions
                   WHERE component_id = ? ORDER BY version DESC""",
                (normalized,),
            )
        ]
        return {
            "id": normalized,
            "stage": component["stage"],
            "name": component["name"],
            "description": component["description"],
            "built_in": bool(component["built_in"]),
            "editable": not bool(component["built_in"]),
            "version": int(version_row["version"]),
            "versions": versions,
            "entrypoint": version_row["entrypoint"],
            "source": version_row["source"],
            "source_sha256": version_row["source_sha256"],
            "parameters": _load_json(version_row["parameters_json"], {}),
            "notes": version_row["notes"],
            "created_at": version_row["created_at"],
        }

    def create_component(
        self,
        component_id: str,
        *,
        stage: str,
        name: str,
        description: str,
        source: str,
        parameters: Mapping[str, Any] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        normalized = normalize_id(component_id, label="component id")
        if stage not in STAGE_NAMES:
            raise ValueError(f"stage must be one of {STAGE_NAMES}")
        validation = validate_python_source(source, STAGE_ENTRYPOINTS[stage])
        with self._lock:
            if self._conn.execute(
                "SELECT 1 FROM pipeline_components WHERE id = ?", (normalized,)
            ).fetchone():
                raise FileExistsError(normalized)
            self._conn.execute(
                """INSERT INTO pipeline_components
                   (id, stage, name, description, built_in)
                   VALUES (?, ?, ?, ?, 0)""",
                (normalized, stage, str(name).strip() or normalized, description),
            )
            self._conn.execute(
                """INSERT INTO pipeline_component_versions
                   (component_id, version, entrypoint, source, source_sha256,
                    parameters_json, notes)
                   VALUES (?, 1, ?, ?, ?, ?, ?)""",
                (
                    normalized,
                    STAGE_ENTRYPOINTS[stage],
                    source,
                    validation["sha256"],
                    _json(parameters or {}),
                    notes,
                ),
            )
            self._conn.commit()
        return self.get_component(normalized) or {}

    def clone_component(self, source_id: str, target_id: str, name: str | None = None) -> dict[str, Any]:
        source = self.get_component(source_id)
        if source is None:
            raise KeyError(source_id)
        return self.create_component(
            target_id,
            stage=source["stage"],
            name=name or f"{source['name']} 副本",
            description=source["description"],
            source=source["source"],
            parameters=source["parameters"],
            notes=f"cloned from {source['id']}@{source['version']}",
        )

    def save_component_version(
        self,
        component_id: str,
        *,
        source: str,
        parameters: Mapping[str, Any] | None = None,
        name: str | None = None,
        description: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        current = self.get_component(component_id)
        if current is None:
            raise KeyError(component_id)
        if current["built_in"]:
            raise PermissionError("built-in components are immutable; clone before editing")
        validation = validate_python_source(source, current["entrypoint"])
        next_version = int(current["version"]) + 1
        with self._lock:
            self._conn.execute(
                """UPDATE pipeline_components
                   SET name = ?, description = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (name or current["name"], description if description is not None else current["description"], current["id"]),
            )
            self._conn.execute(
                """INSERT INTO pipeline_component_versions
                   (component_id, version, entrypoint, source, source_sha256,
                    parameters_json, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    current["id"],
                    next_version,
                    current["entrypoint"],
                    source,
                    validation["sha256"],
                    _json(parameters if parameters is not None else current["parameters"]),
                    notes,
                ),
            )
            self._conn.commit()
        return self.get_component(current["id"], next_version) or {}

    def delete_component(self, component_id: str) -> bool:
        item = self.get_component(component_id)
        if item is None:
            return False
        if item["built_in"]:
            raise PermissionError("built-in components are immutable")
        used = self._projects_using_component(item["id"])
        if used:
            raise ValueError(f"component is used by projects: {', '.join(used)}")
        with self._lock:
            self._conn.execute("DELETE FROM pipeline_components WHERE id = ?", (item["id"],))
            self._conn.commit()
        return True

    def list_projects(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM pipeline_projects ORDER BY built_in DESC, name"
        ).fetchall()
        return [self._project_from_row(row).to_dict() for row in rows]

    def get_project(self, project_id: str, *, include_source: bool = True) -> dict[str, Any] | None:
        normalized = normalize_id(project_id, label="project id")
        row = self._conn.execute(
            "SELECT * FROM pipeline_projects WHERE id = ?", (normalized,)
        ).fetchone()
        if row is None:
            return None
        project = self._project_from_row(row)
        payload = project.to_dict()
        composed = self.compose(project)
        payload.update(
            {
                "source_sha256": composed.source_sha256,
                "component_manifest": list(composed.manifest),
            }
        )
        if include_source:
            payload["composed_source"] = composed.source
        return payload

    def create_project(
        self,
        project_id: str,
        *,
        name: str,
        description: str,
        components: Mapping[str, Mapping[str, Any] | ComponentRef],
        settings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_id(project_id, label="project id")
        if self._conn.execute(
            "SELECT 1 FROM pipeline_projects WHERE id = ?", (normalized,)
        ).fetchone():
            raise FileExistsError(normalized)
        project = PipelineProject(
            id=normalized,
            name=name,
            description=description,
            components={
                stage: value if isinstance(value, ComponentRef) else ComponentRef(**dict(value))
                for stage, value in components.items()
            },
            settings=settings or {},
        )
        with self._lock:
            self._insert_project(project)
            self._conn.commit()
        return self.get_project(normalized) or {}

    def clone_project(self, source_id: str, target_id: str, name: str | None = None) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM pipeline_projects WHERE id = ?",
            (normalize_id(source_id, label="project id"),),
        ).fetchone()
        if row is None:
            raise KeyError(source_id)
        source = self._project_from_row(row)
        return self.create_project(
            target_id,
            name=name or f"{source.name} 副本",
            description=source.description,
            components=source.components,
            settings=source.settings,
        )

    def update_project(
        self,
        project_id: str,
        *,
        name: str,
        description: str,
        components: Mapping[str, Mapping[str, Any] | ComponentRef],
        settings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_id(project_id, label="project id")
        row = self._conn.execute(
            "SELECT * FROM pipeline_projects WHERE id = ?", (normalized,)
        ).fetchone()
        if row is None:
            raise KeyError(normalized)
        if bool(row["built_in"]):
            raise PermissionError("built-in projects are immutable; clone before editing")
        project = PipelineProject(
            id=normalized,
            name=name,
            description=description,
            components={
                stage: value if isinstance(value, ComponentRef) else ComponentRef(**dict(value))
                for stage, value in components.items()
            },
            settings=settings or {},
            revision=int(row["revision"]) + 1,
        )
        composed = self.compose(project)
        refs = {stage: project.components[stage].to_dict() for stage in STAGE_NAMES}
        with self._lock:
            self._conn.execute(
                """UPDATE pipeline_projects SET name = ?, description = ?, revision = ?,
                   component_refs_json = ?, settings_json = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (project.name, project.description, project.revision, _json(refs), _json(project.settings), normalized),
            )
            self._insert_project_version(project, composed)
            self._conn.commit()
        return self.get_project(normalized) or {}

    def delete_project(self, project_id: str) -> bool:
        normalized = normalize_id(project_id, label="project id")
        row = self._conn.execute(
            "SELECT built_in FROM pipeline_projects WHERE id = ?", (normalized,)
        ).fetchone()
        if row is None:
            return False
        if bool(row["built_in"]):
            raise PermissionError("built-in projects are immutable")
        with self._lock:
            self._conn.execute("DELETE FROM pipeline_projects WHERE id = ?", (normalized,))
            self._conn.commit()
        return True

    def compose(self, project: PipelineProject | str) -> ComposedStrategy:
        if isinstance(project, str):
            row = self._conn.execute(
                "SELECT * FROM pipeline_projects WHERE id = ?",
                (normalize_id(project, label="project id"),),
            ).fetchone()
            if row is None:
                raise KeyError(project)
            project = self._project_from_row(row)
        resolved: dict[str, dict[str, Any]] = {}
        overrides = dict(project.settings.get("stage_parameters") or {})
        for stage in STAGE_NAMES:
            ref = project.components[stage]
            item = self.get_component(ref.component_id, ref.version)
            if item is None:
                raise ValueError(f"missing component version: {ref.component_id}@{ref.version}")
            if item["stage"] != stage:
                raise ValueError(f"component {ref.component_id} belongs to {item['stage']}, not {stage}")
            resolved[stage] = {
                "component_id": ref.component_id,
                "version": ref.version,
                "source": item["source"],
                "parameters": {**item["parameters"], **dict(overrides.get(stage) or {})},
            }
        return compose_strategy(resolved)

    def stage_parameters(self, project_id: str) -> dict[str, dict[str, Any]]:
        project_payload = self.get_project(project_id, include_source=False)
        if project_payload is None:
            raise KeyError(project_id)
        return {
            item["stage"]: dict(item["parameters"])
            for item in project_payload["component_manifest"]
        }

    def _insert_project(self, project: PipelineProject) -> None:
        composed = self.compose(project)
        refs = {stage: project.components[stage].to_dict() for stage in STAGE_NAMES}
        self._conn.execute(
            """INSERT INTO pipeline_projects
               (id, name, description, revision, component_refs_json, settings_json, built_in)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project.id, project.name, project.description, project.revision, _json(refs), _json(project.settings), int(project.built_in)),
        )
        self._insert_project_version(project, composed)

    def _insert_project_version(self, project: PipelineProject, composed: ComposedStrategy) -> None:
        refs = {stage: project.components[stage].to_dict() for stage in STAGE_NAMES}
        self._conn.execute(
            """INSERT INTO pipeline_project_versions
               (project_id, revision, component_refs_json, settings_json,
                composed_source, source_sha256)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project.id, project.revision, _json(refs), _json(project.settings), composed.source, composed.source_sha256),
        )

    @staticmethod
    def _component_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "stage": row["stage"],
            "name": row["name"],
            "description": row["description"],
            "built_in": bool(row["built_in"]),
            "editable": not bool(row["built_in"]),
            "latest_version": int(row["latest_version"]),
            "version_count": int(row["version_count"]),
        }

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> PipelineProject:
        refs = _load_json(row["component_refs_json"], {})
        return PipelineProject(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            revision=int(row["revision"]),
            built_in=bool(row["built_in"]),
            components={stage: ComponentRef(**refs[stage]) for stage in STAGE_NAMES},
            settings=_load_json(row["settings_json"], {}),
        )

    def _projects_using_component(self, component_id: str) -> list[str]:
        used: list[str] = []
        for row in self._conn.execute("SELECT id, component_refs_json FROM pipeline_projects"):
            refs = _load_json(row["component_refs_json"], {})
            if any(ref.get("component_id") == component_id for ref in refs.values()):
                used.append(str(row["id"]))
        return used


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


__all__ = ["PipelineRepository"]
