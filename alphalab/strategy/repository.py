"""SQLite repository for SDK v1 project drafts and immutable source packages."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from alphalab.strategy.builtins import DEFAULT_STRATEGY_SOURCE
from alphalab.strategy.factor_templates import get_factor_template
from alphalab.strategy.sdk_runtime import load_strategy_module, probe_sdk_operation
from alphalab.strategy.source import (
    SourceInspection,
    StrategySourceError,
    assemble_strategy_source,
    ensure_default_risk_handler,
    inspect_strategy_source,
    remove_factor_inputs_arguments,
    split_strategy_source,
)
from alphalab.utils.paths import APP_DATA_DIR

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"
_DEFAULT_DB = APP_DATA_DIR / "alphalab.db"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_DEFAULT_PROJECT_ID = "sdk-v1-default"
_MIGRATION_NAME = "strategy-sdk-v1-cutover"
_FACTOR_REPAIR_MIGRATION = "strategy-sdk-v1-factor-repair"
_RQ_PROFILE_MIGRATION = "strategy-sdk-v1-rq-profile"
_FACTOR_INPUTS_MIGRATION = "strategy-sdk-v1-remove-factor-inputs"
_DEFAULT_RISK_MIGRATION = "strategy-sdk-v1-default-risk-event"


def normalize_project_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if not _ID.fullmatch(normalized):
        raise ValueError(
            "project id must be 2-64 lowercase letters, numbers, underscores, or hyphens"
        )
    return normalized


class StrategyRepository:
    """Own mutable source units, their runtime bundle, and immutable packages."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path) if db_path else _DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._lock = threading.RLock()
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._seed_default()
        self._migrate_pipeline_projects()
        self._repair_legacy_factor_migrations()
        self._migrate_projects_to_rq_profile()
        self._remove_factor_inputs_from_drafts()
        self._ensure_default_risk_in_drafts()
        self._refresh_builtin_default()
        self._ensure_source_units()

    def close(self) -> None:
        self._conn.close()

    def _seed_default(self) -> None:
        if self._conn.execute(
            "SELECT 1 FROM strategy_projects WHERE id = ?", (_DEFAULT_PROJECT_ID,)
        ).fetchone():
            return
        inspection = inspect_strategy_source(DEFAULT_STRATEGY_SOURCE)
        with self._lock:
            self._conn.execute(
                """INSERT INTO strategy_projects
                   (id, name, description, profile, current_revision,
                    draft_parent_revision, draft_source, draft_source_sha256,
                    settings_json, built_in)
                   VALUES (?, ?, ?, 'runtime', 1, 1, ?, ?, ?, 1)""",
                (
                    _DEFAULT_PROJECT_ID,
                    "SDK v1 默认策略",
                    "统一源码：研究标的池 → 月末动量 → 等权组合 → 下一交易日开盘执行",
                    DEFAULT_STRATEGY_SOURCE,
                    inspection.source_sha256,
                    _json(_default_settings()),
                ),
            )
            self._replace_source_units(_DEFAULT_PROJECT_ID, DEFAULT_STRATEGY_SOURCE)
            self._insert_package(
                _DEFAULT_PROJECT_ID,
                1,
                None,
                DEFAULT_STRATEGY_SOURCE,
                inspection,
            )
            self._conn.commit()

    def _refresh_builtin_default(self) -> None:
        """Advance the immutable built-in project when its shipped source changes."""

        inspection = inspect_strategy_source(DEFAULT_STRATEGY_SOURCE)
        row = self._conn.execute(
            "SELECT * FROM strategy_projects WHERE id = ? AND built_in = 1",
            (_DEFAULT_PROJECT_ID,),
        ).fetchone()
        if row is None or (
            row["draft_source"] == DEFAULT_STRATEGY_SOURCE
            and row["draft_source_sha256"] == inspection.source_sha256
        ):
            return

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                locked = self._conn.execute(
                    "SELECT * FROM strategy_projects WHERE id = ? AND built_in = 1",
                    (_DEFAULT_PROJECT_ID,),
                ).fetchone()
                if locked is None or (
                    locked["draft_source"] == DEFAULT_STRATEGY_SOURCE
                    and locked["draft_source_sha256"] == inspection.source_sha256
                ):
                    self._conn.commit()
                    return
                existing = self._conn.execute(
                    """SELECT revision FROM strategy_source_packages
                       WHERE project_id = ? AND source_sha256 = ?""",
                    (_DEFAULT_PROJECT_ID, inspection.source_sha256),
                ).fetchone()
                if existing is not None:
                    revision = int(existing["revision"])
                else:
                    revision = int(locked["current_revision"]) + 1
                    self._insert_package(
                        _DEFAULT_PROJECT_ID,
                        revision,
                        int(locked["current_revision"]) or None,
                        DEFAULT_STRATEGY_SOURCE,
                        inspection,
                    )
                self._conn.execute(
                    """UPDATE strategy_projects
                       SET current_revision = ?, draft_parent_revision = ?,
                           draft_source = ?, draft_source_sha256 = ?,
                           updated_at = datetime('now')
                       WHERE id = ? AND built_in = 1""",
                    (
                        revision,
                        revision,
                        DEFAULT_STRATEGY_SOURCE,
                        inspection.source_sha256,
                        _DEFAULT_PROJECT_ID,
                    ),
                )
                self._replace_source_units(_DEFAULT_PROJECT_ID, DEFAULT_STRATEGY_SOURCE)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def list_projects(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM strategy_projects ORDER BY built_in DESC, name"
        ).fetchall()
        return [self._project_payload(row, include_source=False) for row in rows]

    def get_project(self, project_id: str, *, include_source: bool = True) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM strategy_projects WHERE id = ?",
            (normalize_project_id(project_id),),
        ).fetchone()
        return self._project_payload(row, include_source=include_source) if row else None

    def create_project(
        self,
        project_id: str,
        *,
        name: str,
        description: str = "",
        source: str = DEFAULT_STRATEGY_SOURCE,
        profile: str = "runtime",
        settings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        strategy_source, factor_units = split_strategy_source(source)
        return self.create_project_from_units(
            project_id,
            name=name,
            description=description,
            strategy_source=strategy_source,
            factor_sources=[item.source for item in factor_units],
            profile=profile,
            settings=settings,
        )

    def create_project_from_units(
        self,
        project_id: str,
        *,
        name: str,
        strategy_source: str,
        factor_sources: Iterable[str] = (),
        description: str = "",
        profile: str = "runtime",
        settings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate all source units, then create exactly one initial package."""

        normalized = normalize_project_id(project_id)
        if not str(name).strip():
            raise ValueError("project name must not be empty")
        if profile != "runtime":
            raise ValueError("strategy projects use the RQ runtime profile")
        if self.get_project(normalized, include_source=False) is not None:
            raise FileExistsError(normalized)
        factors = [str(item) for item in factor_sources]
        source, inspection = assemble_strategy_source(strategy_source, factors)
        self._probe_source(source, inspection)
        factor_ids = [item.id for item in inspection.entrypoints if item.kind == "factor"]
        if len(factor_ids) != len(factors):
            raise StrategySourceError(
                "assembled factor inventory is inconsistent", phase="register"
            )
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    """INSERT INTO strategy_projects
                       (id, name, description, profile, current_revision,
                        draft_parent_revision, draft_source, draft_source_sha256,
                        settings_json, built_in)
                       VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?, 0)""",
                    (
                        normalized,
                        str(name).strip(),
                        str(description),
                        profile,
                        source,
                        inspection.source_sha256,
                        _json({**_default_settings(), **dict(settings or {})}),
                    ),
                )
                self._write_source_units(normalized, strategy_source, factors, factor_ids)
                self._insert_package(normalized, 1, None, source, inspection)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_project(normalized) or {}

    def clone_project(
        self,
        source_id: str,
        target_id: str,
        *,
        name: str | None = None,
    ) -> dict[str, Any]:
        source = self.get_project(source_id)
        if source is None:
            raise KeyError(source_id)
        return self.create_project(
            target_id,
            name=name or f"{source['name']} 副本",
            description=source["description"],
            source=source["draft_source"],
            profile="runtime",
            settings=source["settings"],
        )

    def update_draft(
        self,
        project_id: str,
        source: str,
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        strategy_source, factor_units = split_strategy_source(source)
        return self._commit_source_units(
            project_id,
            strategy_source,
            [item.source for item in factor_units],
            expected_source_sha256=expected_source_sha256,
        )

    def update_strategy_source(
        self,
        project_id: str,
        strategy_source: str,
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        row = self._editable_row(project_id)
        if expected_source_sha256 and row["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        factors = [
            str(item["source"])
            for item in self._source_unit_rows(str(row["id"]))
            if item["kind"] == "factor"
        ]
        return self._commit_source_units(
            str(row["id"]),
            strategy_source,
            factors,
            expected_source_sha256=str(row["draft_source_sha256"]),
        )

    def add_factor_source(
        self,
        project_id: str,
        factor_source: str,
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        row = self._editable_row(project_id)
        if expected_source_sha256 and row["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        units = self._source_unit_rows(str(row["id"]))
        strategy = next(str(item["source"]) for item in units if item["kind"] == "strategy")
        factors = [str(item["source"]) for item in units if item["kind"] == "factor"]
        return self._commit_source_units(
            str(row["id"]),
            strategy,
            [*factors, factor_source],
            expected_source_sha256=str(row["draft_source_sha256"]),
        )

    def get_factor_source(self, project_id: str, factor_id: str) -> dict[str, Any]:
        normalized = normalize_project_id(project_id)
        project = self.get_project(normalized, include_source=False)
        if project is None:
            raise KeyError(normalized)
        row = self._conn.execute(
            """SELECT * FROM strategy_source_units
               WHERE project_id = ? AND path = ? AND kind = 'factor'""",
            (normalized, f"factors/{factor_id}.py"),
        ).fetchone()
        if row is None:
            raise KeyError(factor_id)
        return {
            "path": row["path"],
            "kind": row["kind"],
            "source": row["source"],
            "source_sha256": row["source_sha256"],
            "position": int(row["position"]),
        }

    def replace_factor_source(
        self,
        project_id: str,
        factor_id: str,
        factor_source: str,
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        row = self._editable_row(project_id)
        if expected_source_sha256 and row["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        units = self._source_unit_rows(str(row["id"]))
        strategy = next(str(item["source"]) for item in units if item["kind"] == "strategy")
        factors = [str(item["source"]) for item in units if item["kind"] == "factor"]
        paths = [str(item["path"]) for item in units if item["kind"] == "factor"]
        try:
            factor_index = paths.index(f"factors/{factor_id}.py")
        except ValueError:
            raise KeyError(factor_id) from None
        factors[factor_index] = factor_source
        return self._commit_source_units(
            str(row["id"]),
            strategy,
            factors,
            expected_source_sha256=str(row["draft_source_sha256"]),
        )

    def delete_factor_source(
        self,
        project_id: str,
        factor_id: str,
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        row = self._editable_row(project_id)
        if expected_source_sha256 and row["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        units = self._source_unit_rows(str(row["id"]))
        strategy = next(str(item["source"]) for item in units if item["kind"] == "strategy")
        factor_rows = [item for item in units if item["kind"] == "factor"]
        factors = [
            str(item["source"]) for item in factor_rows if item["path"] != f"factors/{factor_id}.py"
        ]
        if len(factors) == len(factor_rows):
            raise KeyError(factor_id)
        return self._commit_source_units(
            str(row["id"]),
            strategy,
            factors,
            expected_source_sha256=str(row["draft_source_sha256"]),
        )

    def update_metadata(
        self,
        project_id: str,
        *,
        name: str,
        description: str,
        profile: str,
        settings: Mapping[str, Any],
    ) -> dict[str, Any]:
        row = self._editable_row(project_id)
        if not str(name).strip():
            raise ValueError("project name must not be empty")
        if profile != "runtime":
            raise ValueError("strategy projects use the RQ runtime profile")
        with self._lock:
            self._conn.execute(
                """UPDATE strategy_projects
                   SET name = ?, description = ?, profile = ?, settings_json = ?,
                       updated_at = datetime('now') WHERE id = ?""",
                (str(name).strip(), str(description), profile, _json(dict(settings)), row["id"]),
            )
            self._conn.commit()
        return self.get_project(row["id"]) or {}

    def save_revision(
        self,
        project_id: str,
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        observed = self._editable_row(project_id)
        if expected_source_sha256 and observed["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        source = str(observed["draft_source"])
        inspection = inspect_strategy_source(source)
        self._probe_source(source, inspection)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT * FROM strategy_projects WHERE id = ?",
                    (observed["id"],),
                ).fetchone()
                if row is None:
                    raise KeyError(str(observed["id"]))
                compare_hash = expected_source_sha256 or str(observed["draft_source_sha256"])
                if row["draft_source_sha256"] != compare_hash or row["draft_source"] != source:
                    raise RuntimeError("draft changed while the revision was being saved")
                current = self._conn.execute(
                    """SELECT * FROM strategy_source_packages
                       WHERE project_id = ? AND revision = ?""",
                    (row["id"], int(row["current_revision"])),
                ).fetchone()
                if current is not None and current["source_sha256"] == inspection.source_sha256:
                    revision = int(row["current_revision"])
                else:
                    revision = int(row["current_revision"]) + 1
                    parent = int(row["current_revision"]) or None
                    self._insert_package(row["id"], revision, parent, source, inspection)
                    self._conn.execute(
                        """UPDATE strategy_projects
                           SET current_revision = ?, draft_parent_revision = ?,
                               updated_at = datetime('now') WHERE id = ?""",
                        (revision, revision, row["id"]),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_package(str(observed["id"]), revision) or {}

    def list_packages(self, project_id: str) -> list[dict[str, Any]]:
        normalized = normalize_project_id(project_id)
        rows = self._conn.execute(
            """SELECT * FROM strategy_source_packages
               WHERE project_id = ? ORDER BY revision DESC""",
            (normalized,),
        ).fetchall()
        return [self._package_payload(row, include_source=False) for row in rows]

    def get_package(
        self,
        project_id: str,
        revision: int | None = None,
        *,
        include_source: bool = True,
    ) -> dict[str, Any] | None:
        normalized = normalize_project_id(project_id)
        if revision is None:
            project = self._conn.execute(
                "SELECT current_revision FROM strategy_projects WHERE id = ?", (normalized,)
            ).fetchone()
            if project is None:
                return None
            revision = int(project["current_revision"])
        row = self._conn.execute(
            """SELECT * FROM strategy_source_packages
               WHERE project_id = ? AND revision = ?""",
            (normalized, int(revision)),
        ).fetchone()
        return self._package_payload(row, include_source=include_source) if row else None

    def delete_project(self, project_id: str) -> bool:
        normalized = normalize_project_id(project_id)
        row = self._conn.execute(
            "SELECT built_in FROM strategy_projects WHERE id = ?", (normalized,)
        ).fetchone()
        if row is None:
            return False
        if bool(row["built_in"]):
            raise PermissionError("built-in projects are immutable")
        with self._lock:
            self._conn.execute("DELETE FROM strategy_projects WHERE id = ?", (normalized,))
            self._conn.commit()
        return True

    def _editable_row(self, project_id: str) -> sqlite3.Row:
        normalized = normalize_project_id(project_id)
        row = self._conn.execute(
            "SELECT * FROM strategy_projects WHERE id = ?", (normalized,)
        ).fetchone()
        if row is None:
            raise KeyError(normalized)
        if bool(row["built_in"]):
            raise PermissionError("built-in projects are immutable; clone before editing")
        return row

    def _source_unit_rows(self, project_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            """SELECT * FROM strategy_source_units
               WHERE project_id = ?
               ORDER BY CASE kind WHEN 'strategy' THEN 0 ELSE 1 END, position, path""",
            (normalize_project_id(project_id),),
        ).fetchall()

    def list_source_units(self, project_id: str) -> list[dict[str, Any]]:
        normalized = normalize_project_id(project_id)
        if self.get_project(normalized, include_source=False) is None:
            raise KeyError(normalized)
        return [
            {
                "path": row["path"],
                "kind": row["kind"],
                "source": row["source"],
                "source_sha256": row["source_sha256"],
                "position": int(row["position"]),
            }
            for row in self._source_unit_rows(normalized)
        ]

    def _replace_source_units(self, project_id: str, bundled_source: str) -> None:
        strategy_source, factor_units = split_strategy_source(bundled_source)
        self._write_source_units(
            project_id,
            strategy_source,
            [item.source for item in factor_units],
            [item.path.removeprefix("factors/").removesuffix(".py") for item in factor_units],
        )

    def _write_source_units(
        self,
        project_id: str,
        strategy_source: str,
        factor_sources: list[str],
        factor_ids: list[str],
    ) -> None:
        if any(
            factor_id in {".", ".."} or "/" in factor_id or "\\" in factor_id
            for factor_id in factor_ids
        ):
            raise StrategySourceError(
                "factor public IDs cannot contain path separators", phase="register"
            )
        strategy_hash = hashlib.sha256(strategy_source.encode("utf-8")).hexdigest()
        rows = [
            (project_id, "strategy.py", "strategy", strategy_source, strategy_hash, 0),
            *[
                (
                    project_id,
                    f"factors/{factor_id}.py",
                    "factor",
                    factor_source,
                    hashlib.sha256(factor_source.encode("utf-8")).hexdigest(),
                    position,
                )
                for position, (factor_id, factor_source) in enumerate(
                    zip(factor_ids, factor_sources, strict=True)
                )
            ],
        ]
        self._conn.execute("DELETE FROM strategy_source_units WHERE project_id = ?", (project_id,))
        self._conn.executemany(
            """INSERT INTO strategy_source_units
               (project_id, path, kind, source, source_sha256, position)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def _commit_source_units(
        self,
        project_id: str,
        strategy_source: str,
        factor_sources: list[str],
        *,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        row = self._editable_row(project_id)
        if expected_source_sha256 and row["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        bundled, inspection = assemble_strategy_source(strategy_source, factor_sources)
        # Probe the fully assembled cross-file package before opening the write
        # transaction.  A failed import/register probe must never leave a dirty
        # draft or consume a revision number.
        self._probe_source(bundled, inspection)
        factor_ids = [item.id for item in inspection.entrypoints if item.kind == "factor"]
        if len(factor_ids) != len(factor_sources):
            raise StrategySourceError(
                "assembled factor inventory is inconsistent", phase="register"
            )
        if any(
            factor_id in {".", ".."} or "/" in factor_id or "\\" in factor_id
            for factor_id in factor_ids
        ):
            raise StrategySourceError(
                "factor public IDs cannot contain path separators", phase="register"
            )
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                locked = self._conn.execute(
                    "SELECT * FROM strategy_projects WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                if locked is None:
                    raise KeyError(str(row["id"]))
                compare_hash = expected_source_sha256 or str(row["draft_source_sha256"])
                if locked["draft_source_sha256"] != compare_hash:
                    raise RuntimeError("draft changed while the update was being prepared")
                current_revision = int(locked["current_revision"])
                current = self._conn.execute(
                    """SELECT source_sha256 FROM strategy_source_packages
                       WHERE project_id = ? AND revision = ?""",
                    (row["id"], current_revision),
                ).fetchone()
                if current is not None and current["source_sha256"] == inspection.source_sha256:
                    revision = current_revision
                else:
                    revision = current_revision + 1
                    self._insert_package(
                        str(row["id"]),
                        revision,
                        current_revision or None,
                        bundled,
                        inspection,
                    )
                self._conn.execute(
                    """UPDATE strategy_projects
                       SET current_revision = ?, draft_parent_revision = ?,
                           draft_source = ?, draft_source_sha256 = ?,
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (
                        revision,
                        revision,
                        bundled,
                        inspection.source_sha256,
                        row["id"],
                    ),
                )
                self._write_source_units(
                    str(row["id"]), strategy_source, factor_sources, factor_ids
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_project(str(row["id"])) or {}

    def _insert_package_units(self, project_id: str, revision: int, bundled_source: str) -> None:
        strategy_source, factor_units = split_strategy_source(bundled_source)
        strategy_hash = hashlib.sha256(strategy_source.encode("utf-8")).hexdigest()
        rows = [
            (project_id, revision, "strategy.py", "strategy", strategy_source, strategy_hash, 0),
            *[
                (
                    project_id,
                    revision,
                    unit.path,
                    unit.kind,
                    unit.source,
                    unit.source_sha256,
                    unit.position,
                )
                for unit in factor_units
            ],
        ]
        self._conn.executemany(
            """INSERT INTO strategy_source_package_units
               (project_id, revision, path, kind, source, source_sha256, position)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def _insert_package(
        self,
        project_id: str,
        revision: int,
        parent_revision: int | None,
        source: str,
        inspection: SourceInspection,
    ) -> None:
        manifest = [
            {
                "kind": item.kind,
                "id": item.id,
                "function": item.function,
                "label": item.label,
                "event": item.event,
                "metadata": item.metadata,
            }
            for item in inspection.entrypoints
        ]
        parameters = {
            item.id: {
                parameter.name: parameter.default
                for parameter in item.parameters
                if parameter.editable
            }
            for item in inspection.entrypoints
        }
        self._conn.execute(
            """INSERT INTO strategy_source_packages
               (project_id, revision, parent_revision, source, source_sha256,
                sdk_version, validator_version, manifest_json, parameters_json,
                data_requirements_json, runtime_requirements_json, environment_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                revision,
                parent_revision,
                source,
                inspection.source_sha256,
                inspection.sdk_version,
                inspection.validator_version,
                _json(manifest),
                _json(parameters),
                _json(inspection.data_requirements),
                _json(inspection.runtime_requirements),
                _json(_environment_fingerprint()),
            ),
        )
        self._insert_package_units(project_id, revision, source)

    def _project_payload(self, row: sqlite3.Row, *, include_source: bool) -> dict[str, Any]:
        current = self.get_package(row["id"], int(row["current_revision"]), include_source=False)
        dirty = not current or current["source_sha256"] != row["draft_source_sha256"]
        unit_rows = self._source_unit_rows(str(row["id"]))
        strategy_unit = next((item for item in unit_rows if item["kind"] == "strategy"), None)
        payload = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "profile": row["profile"],
            "current_revision": int(row["current_revision"]),
            "draft_parent_revision": row["draft_parent_revision"],
            "draft_source_sha256": row["draft_source_sha256"],
            "strategy_source_sha256": (
                strategy_unit["source_sha256"] if strategy_unit is not None else ""
            ),
            "source_units": [
                {
                    "path": item["path"],
                    "kind": item["kind"],
                    "source_sha256": item["source_sha256"],
                    "position": int(item["position"]),
                }
                for item in unit_rows
            ],
            "dirty": dirty,
            "settings": _load_json(row["settings_json"], {}),
            "built_in": bool(row["built_in"]),
            "editable": not bool(row["built_in"]),
            "current_package": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if include_source:
            payload["draft_source"] = row["draft_source"]
            payload["strategy_source"] = (
                strategy_unit["source"] if strategy_unit is not None else row["draft_source"]
            )
            payload["inspection"] = inspect_strategy_source(row["draft_source"]).to_dict()
        return payload

    def _package_payload(self, row: sqlite3.Row, *, include_source: bool) -> dict[str, Any]:
        payload = {
            "project_id": row["project_id"],
            "revision": int(row["revision"]),
            "parent_revision": row["parent_revision"],
            "source_sha256": row["source_sha256"],
            "sdk_version": int(row["sdk_version"]),
            "validator_version": row["validator_version"],
            "manifest": _load_json(row["manifest_json"], []),
            "parameters": _load_json(row["parameters_json"], {}),
            "data_requirements": _load_json(row["data_requirements_json"], {}),
            "runtime_requirements": _load_json(row["runtime_requirements_json"], {}),
            "environment": _load_json(row["environment_json"], {}),
            "created_at": row["created_at"],
        }
        if include_source:
            payload["source"] = row["source"]
            unit_rows = self._conn.execute(
                """SELECT path, kind, source, source_sha256, position
                   FROM strategy_source_package_units
                   WHERE project_id = ? AND revision = ?
                   ORDER BY CASE kind WHEN 'strategy' THEN 0 ELSE 1 END, position, path""",
                (row["project_id"], int(row["revision"])),
            ).fetchall()
            payload["source_units"] = [
                {
                    "path": item["path"],
                    "kind": item["kind"],
                    "source": item["source"],
                    "source_sha256": item["source_sha256"],
                    "position": int(item["position"]),
                }
                for item in unit_rows
            ]
        return payload

    @staticmethod
    def _probe_source(source: str, inspection: SourceInspection) -> None:
        # Import/registration is an execution boundary.  The API requires the
        # caller's explicit save confirmation before reaching this method.
        load_strategy_module(source)
        fields = set(inspection.data_requirements.get("bars") or ()) | {
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "raw_close",
            "is_suspended",
            "is_st",
            "limit_up",
            "limit_down",
        }
        dates = pd.bdate_range("2024-01-01", periods=260)
        bars = pd.DataFrame(
            [
                {
                    "date": date,
                    "symbol": symbol,
                    **{
                        field: (
                            100.0 + index * 0.1
                            if field in {"open", "high", "low", "close", "raw_close"}
                            else (
                                False
                                if field in {"is_suspended", "is_st"}
                                else (
                                    200.0
                                    if field == "limit_up"
                                    else 1.0 if field == "limit_down" else 1_000_000.0
                                )
                            )
                        )
                        for field in fields
                    },
                }
                for index, date in enumerate(dates)
                for symbol in ("TEST_A", "TEST_B", "511260")
            ]
        )
        fundamentals_fields = set(inspection.data_requirements.get("fundamentals") or ())
        instrument_fields = set(inspection.data_requirements.get("instruments") or ())
        daily_factor_fields = set(inspection.data_requirements.get("daily_factors") or ())
        index_component_ids = set(inspection.data_requirements.get("index_components") or ())
        fundamentals = pd.DataFrame(
            [
                {
                    "available_date": dates[-1],
                    "quarter": "2024q4",
                    "symbol": symbol,
                    **{field: 1.0 for field in fundamentals_fields},
                }
                for symbol in ("TEST_A", "TEST_B", "511260")
            ]
        )
        payload = {
            "event": "session_close",
            "as_of": dates[-1],
            "sessions": list(dates),
            "available_symbols": ["TEST_A", "TEST_B", "511260"],
            "bars": bars,
            "instruments": pd.DataFrame(
                {
                    "snapshot_date": [dates[-1]] * 3,
                    "symbol": ["TEST_A", "TEST_B", "511260"],
                    "asset_type": ["ETF", "ETF", "ETF"],
                    **{
                        field: ["TEST"] * 3
                        for field in instrument_fields
                        if field not in {"snapshot_date", "symbol", "asset_type"}
                    },
                }
            ),
            "fundamentals": fundamentals,
            "daily_factors": pd.DataFrame(
                [
                    {
                        "date": dates[-1],
                        "symbol": symbol,
                        "field": field,
                        "value": 1.0,
                    }
                    for symbol in ("TEST_A", "TEST_B", "511260")
                    for field in daily_factor_fields
                ]
            ),
            "index_components": pd.DataFrame(
                [
                    {
                        "date": dates[-1],
                        "index_symbol": index_symbol,
                        "symbol": symbol,
                    }
                    for index_symbol in index_component_ids
                    for symbol in ("TEST_A", "TEST_B", "511260")
                ]
            ),
            "portfolio": {},
            "state": {},
            "force_signal": True,
            "limits": {"max_weight": 1.0, "max_gross_exposure": 1.0},
        }
        probe_sdk_operation(source, "execution", payload)
        if any(item.kind == "execution_data_fill" for item in inspection.entrypoints):
            execution_rows = bars.loc[bars["date"].eq(dates[-1])].copy()
            execution_rows[["is_suspended", "limit_up", "limit_down"]] = pd.NA
            probe_sdk_operation(
                source,
                "execution_data_fill",
                {
                    **payload,
                    "event": "session_open",
                    "execution_state_rows": execution_rows[
                        ["date", "symbol", "is_suspended", "limit_up", "limit_down"]
                    ].to_dict(orient="records"),
                },
            )
        for entrypoint in inspection.entrypoints:
            if entrypoint.kind == "factor":
                probe_sdk_operation(
                    source,
                    "factor",
                    {**payload, "factor_id": entrypoint.id, "parameters": {}},
                )
        for entrypoint in inspection.entrypoints:
            if entrypoint.kind == "event" and entrypoint.event:
                probe_sdk_operation(
                    source,
                    "event",
                    {**payload, "event": entrypoint.event, "force_signal": False},
                )

    def _migrate_pipeline_projects(self) -> None:
        if self._conn.execute(
            "SELECT 1 FROM strategy_contract_migrations WHERE name = ?", (_MIGRATION_NAME,)
        ).fetchone():
            return
        migrated: list[str] = []
        table = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipeline_projects'"
        ).fetchone()
        if table:
            rows = self._conn.execute(
                "SELECT * FROM pipeline_projects WHERE built_in = 0 ORDER BY id"
            ).fetchall()
            for row in rows:
                project_id = normalize_project_id(str(row["id"]))
                if self.get_project(project_id, include_source=False) is not None:
                    continue
                settings = _load_json(row["settings_json"], {})
                source = _legacy_source(settings)
                inspection = inspect_strategy_source(source)
                self._conn.execute(
                    """INSERT INTO strategy_projects
                       (id, name, description, profile, current_revision,
                        draft_parent_revision, draft_source, draft_source_sha256,
                        settings_json, built_in)
                       VALUES (?, ?, ?, 'runtime', 1, 1, ?, ?, ?, 0)""",
                    (
                        project_id,
                        row["name"],
                        f"{row['description']}（已从三阶段项目转换，请复核自定义逻辑）",
                        source,
                        inspection.source_sha256,
                        _json(
                            {**_default_settings(), **settings, "migration_review_required": True}
                        ),
                    ),
                )
                self._insert_package(project_id, 1, None, source, inspection)
                migrated.append(project_id)
        self._conn.execute(
            "INSERT INTO strategy_contract_migrations (name, detail_json) VALUES (?, ?)",
            (_MIGRATION_NAME, _json({"migrated_projects": migrated})),
        )
        self._conn.commit()

    def _repair_legacy_factor_migrations(self) -> None:
        if self._conn.execute(
            "SELECT 1 FROM strategy_contract_migrations WHERE name = ?",
            (_FACTOR_REPAIR_MIGRATION,),
        ).fetchone():
            return
        table = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipeline_projects'"
        ).fetchone()
        repaired: list[str] = []
        skipped_dirty: list[str] = []
        if table:
            rows = self._conn.execute(
                "SELECT id, settings_json FROM pipeline_projects WHERE built_in = 0 ORDER BY id"
            ).fetchall()
            for row in rows:
                project_id = normalize_project_id(str(row["id"]))
                project = self._conn.execute(
                    "SELECT * FROM strategy_projects WHERE id = ?", (project_id,)
                ).fetchone()
                if project is None:
                    continue
                legacy_settings = _load_json(row["settings_json"], {})
                expected_ids = {
                    str(item.get("name") or "").strip()
                    for item in legacy_settings.get("factors") or []
                    if isinstance(item, Mapping) and str(item.get("name") or "").strip()
                }
                current_inspection = inspect_strategy_source(str(project["draft_source"]))
                current_ids = {
                    item.id for item in current_inspection.entrypoints if item.kind == "factor"
                }
                if not expected_ids - current_ids:
                    continue
                package = self.get_package(
                    project_id, int(project["current_revision"]), include_source=False
                )
                if not package or package["source_sha256"] != project["draft_source_sha256"]:
                    skipped_dirty.append(project_id)
                    continue
                source = _legacy_source(legacy_settings)
                inspection = inspect_strategy_source(source)
                self._conn.execute(
                    """UPDATE strategy_projects
                       SET draft_source = ?, draft_source_sha256 = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (source, inspection.source_sha256, project_id),
                )
                repaired.append(project_id)
        self._conn.execute(
            "INSERT INTO strategy_contract_migrations (name, detail_json) VALUES (?, ?)",
            (
                _FACTOR_REPAIR_MIGRATION,
                _json({"repaired_projects": repaired, "skipped_dirty_projects": skipped_dirty}),
            ),
        )
        self._conn.commit()

    def _migrate_projects_to_rq_profile(self) -> None:
        if self._conn.execute(
            "SELECT 1 FROM strategy_contract_migrations WHERE name = ?",
            (_RQ_PROFILE_MIGRATION,),
        ).fetchone():
            return
        project_ids = [
            str(row["id"])
            for row in self._conn.execute(
                "SELECT id FROM strategy_projects WHERE profile != 'runtime' ORDER BY id"
            ).fetchall()
        ]
        self._conn.execute(
            "UPDATE strategy_projects SET profile = 'runtime', updated_at = datetime('now') "
            "WHERE profile != 'runtime'"
        )
        self._conn.execute(
            "INSERT INTO strategy_contract_migrations (name, detail_json) VALUES (?, ?)",
            (_RQ_PROFILE_MIGRATION, _json({"updated_projects": project_ids})),
        )
        self._conn.commit()

    def _remove_factor_inputs_from_drafts(self) -> None:
        if self._conn.execute(
            "SELECT 1 FROM strategy_contract_migrations WHERE name = ?",
            (_FACTOR_INPUTS_MIGRATION,),
        ).fetchone():
            return
        updated_projects: list[str] = []
        rows = self._conn.execute(
            "SELECT id, draft_source FROM strategy_projects ORDER BY id"
        ).fetchall()
        for row in rows:
            current = str(row["draft_source"])
            updated, inspection = remove_factor_inputs_arguments(current)
            if updated == current:
                continue
            self._conn.execute(
                """UPDATE strategy_projects
                   SET draft_source = ?, draft_source_sha256 = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (updated, inspection.source_sha256, str(row["id"])),
            )
            updated_projects.append(str(row["id"]))
        self._conn.execute(
            "INSERT INTO strategy_contract_migrations (name, detail_json) VALUES (?, ?)",
            (_FACTOR_INPUTS_MIGRATION, _json({"updated_projects": updated_projects})),
        )
        self._conn.commit()

    def _ensure_default_risk_in_drafts(self) -> None:
        if self._conn.execute(
            "SELECT 1 FROM strategy_contract_migrations WHERE name = ?",
            (_DEFAULT_RISK_MIGRATION,),
        ).fetchone():
            return
        updated_projects: list[str] = []
        skipped_dirty_projects: list[str] = []
        rows = self._conn.execute("SELECT * FROM strategy_projects ORDER BY id").fetchall()
        with self._lock:
            try:
                for row in rows:
                    current = str(row["draft_source"])
                    current_inspection = inspect_strategy_source(current)
                    if any(item.kind == "event" for item in current_inspection.entrypoints):
                        continue
                    package = self.get_package(
                        str(row["id"]), int(row["current_revision"]), include_source=False
                    )
                    dirty = not package or package["source_sha256"] != row["draft_source_sha256"]
                    if dirty and not bool(row["built_in"]):
                        skipped_dirty_projects.append(str(row["id"]))
                        continue
                    updated, inspection = ensure_default_risk_handler(current)
                    revision = int(row["current_revision"]) + 1
                    self._insert_package(
                        str(row["id"]),
                        revision,
                        int(row["current_revision"]) or None,
                        updated,
                        inspection,
                    )
                    self._conn.execute(
                        """UPDATE strategy_projects
                           SET current_revision = ?, draft_parent_revision = ?,
                               draft_source = ?, draft_source_sha256 = ?,
                               updated_at = datetime('now')
                           WHERE id = ?""",
                        (
                            revision,
                            revision,
                            updated,
                            inspection.source_sha256,
                            str(row["id"]),
                        ),
                    )
                    self._replace_source_units(str(row["id"]), updated)
                    updated_projects.append(str(row["id"]))
                self._conn.execute(
                    "INSERT INTO strategy_contract_migrations (name, detail_json) VALUES (?, ?)",
                    (
                        _DEFAULT_RISK_MIGRATION,
                        _json(
                            {
                                "updated_projects": updated_projects,
                                "skipped_dirty_projects": skipped_dirty_projects,
                            }
                        ),
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _ensure_source_units(self) -> None:
        """Backfill canonical source files for pre-unit SDK projects and packages."""

        projects = self._conn.execute(
            "SELECT id, draft_source FROM strategy_projects ORDER BY id"
        ).fetchall()
        for project in projects:
            exists = self._conn.execute(
                """SELECT 1 FROM strategy_source_units
                   WHERE project_id = ? AND kind = 'strategy'""",
                (project["id"],),
            ).fetchone()
            if not exists:
                self._replace_source_units(str(project["id"]), str(project["draft_source"]))
        packages = self._conn.execute(
            """SELECT project_id, revision, source
               FROM strategy_source_packages ORDER BY project_id, revision"""
        ).fetchall()
        for package in packages:
            exists = self._conn.execute(
                """SELECT 1 FROM strategy_source_package_units
                   WHERE project_id = ? AND revision = ?""",
                (package["project_id"], package["revision"]),
            ).fetchone()
            if not exists:
                self._insert_package_units(
                    str(package["project_id"]),
                    int(package["revision"]),
                    str(package["source"]),
                )
        self._conn.commit()


def _legacy_source(settings: Mapping[str, Any]) -> str:
    universe = dict(settings.get("universe") or {})
    symbols = tuple(str(value).upper() for value in universe.get("symbols") or ())
    raw_factors = [
        dict(item) for item in settings.get("factors") or [] if isinstance(item, Mapping)
    ]
    if not raw_factors:
        raw_factors = [{"name": "momentum_20d", "weight": 1.0, "direction": "long"}]
    stage_parameters = dict(settings.get("stage_parameters") or {})
    selection = dict(settings.get("selection") or stage_parameters.get("selection") or {})
    selection_weights = dict(selection.get("factor_weights") or {})
    requirements: dict[str, list[str]] = {
        "bars": ["open", "high", "low", "close", "volume", "amount"]
    }
    definitions: list[str] = []
    weights: dict[str, float] = {}
    used_ids: set[str] = set()
    used_functions: set[str] = set()

    for raw_factor in raw_factors:
        factor_id = str(raw_factor.get("name") or "").strip()
        if not factor_id or factor_id in used_ids:
            continue
        used_ids.add(factor_id)
        function_name = re.sub(r"\W+", "_", factor_id).strip("_") or "migrated_factor"
        while function_name in used_functions:
            function_name += "_migrated"
        used_functions.add(function_name)
        try:
            template = get_factor_template(factor_id)
        except StrategySourceError:
            expression = str(raw_factor.get("expression") or "").strip()
            message = (
                f"Legacy expression factor {factor_id!r} requires manual Python conversion"
                + (f": {expression}" if expression else "")
            )
            definitions.append(
                f"""@factor(id={factor_id!r}, label={f"迁移待复核：{factor_id}"!r})
def {function_name}(context):
    raise RuntimeError({message!r})"""
            )
        else:
            definitions.append(template.source.strip())
            for dataset, fields in template.requirements.items():
                current = requirements.setdefault(dataset, [])
                current.extend(field for field in fields if field not in current)

        raw_weight = selection_weights.get(factor_id, raw_factor.get("weight", 1.0))
        weight = float(raw_weight)
        if weight > 0:
            weights[factor_id] = -weight if raw_factor.get("direction") == "short" else weight

    if not used_ids:
        template = get_factor_template("momentum_20d")
        used_ids.add(template.id)
        definitions.append(template.source.strip())
        for dataset, fields in template.requirements.items():
            current = requirements.setdefault(dataset, [])
            current.extend(field for field in fields if field not in current)
    if not weights:
        weights[next(iter(used_ids))] = 1.0

    frequency = str(selection.get("signal_frequency") or "monthly")
    schedule = {
        "daily": 'Daily.at("close")',
        "weekly": 'Weekly.last_trading_day(at="close")',
        "monthly": 'Monthly.last_trading_day(at="close")',
    }.get(frequency, 'Monthly.last_trading_day(at="close")')
    normalization = "zscore" if selection.get("normalization") == "zscore" else "rank"
    top_n = max(1, int(selection.get("n_stocks") or selection.get("count") or 20))
    factor_source = "\n\n\n".join(definitions)
    return f"""from alphalab.sdk.v1 import (Daily, ExecutionPolicy, Monthly, PortfolioDecision, SignalResult, UniverseResult, Weekly, execution, factor, portfolio, signal, universe)

SDK_VERSION = 1
MIGRATED_SYMBOLS = {symbols!r}
DATA_REQUIREMENTS = {requirements!r}

@universe(id="migrated_universe")
def migrated_universe(context):
    return UniverseResult(symbols=MIGRATED_SYMBOLS or context.universe)

{factor_source}

@signal(id="migrated_signal", schedule={schedule})
def migrated_signal(context, state, *, top_n: int = {top_n}):
    scores = context.combine_factors(weights={weights!r}, normalization={normalization!r}).dropna()
    selected = list(scores.nlargest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)

@portfolio(id="migrated_portfolio")
def migrated_portfolio(context, signal, state, *, max_weight: float = 0.10):
    count = len(signal.selected)
    weight = min(max_weight, 1.0 / count) if count else 0.0
    return PortfolioDecision(target_weights={{symbol: weight for symbol in signal.selected}}, state=state)

@execution(id="migrated_execution")
def migrated_execution(context, decision):
    return ExecutionPolicy(activation="next_session_open")
"""


def _default_settings() -> dict[str, Any]:
    return {
        "lookback_days": 260,
        "max_weight": 1.0,
        "max_gross_exposure": 1.0,
        "portfolio_value": 1_000_000.0,
        "research_thresholds": {
            "min_sharpe": 0.5,
            "max_drawdown": -0.35,
            "max_turnover": 1.0,
        },
    }


def _environment_fingerprint() -> dict[str, Any]:
    packages = {}
    for name in ("alphalab", "numpy", "pandas", "scipy"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "workspace"
    payload = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable_name": Path(sys.executable).name,
        "packages": packages,
    }
    payload["sha256"] = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
    return payload


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


__all__ = ["StrategyRepository", "normalize_project_id"]
