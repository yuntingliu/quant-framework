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
from typing import Any, Mapping

import pandas as pd

from alphalab.strategy.builtins import DEFAULT_STRATEGY_SOURCE
from alphalab.strategy.factor_templates import get_factor_template
from alphalab.strategy.sdk_runtime import load_strategy_module, probe_sdk_operation
from alphalab.strategy.source import SourceInspection, StrategySourceError, inspect_strategy_source
from alphalab.utils.paths import APP_DATA_DIR


_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"
_DEFAULT_DB = APP_DATA_DIR / "alphalab.db"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_DEFAULT_PROJECT_ID = "sdk-v1-default"
_MIGRATION_NAME = "strategy-sdk-v1-cutover"
_FACTOR_REPAIR_MIGRATION = "strategy-sdk-v1-factor-repair"
_RQ_PROFILE_MIGRATION = "strategy-sdk-v1-rq-profile"


def normalize_project_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if not _ID.fullmatch(normalized):
        raise ValueError(
            "project id must be 2-64 lowercase letters, numbers, underscores, or hyphens"
        )
    return normalized


class StrategyRepository:
    """Own the one mutable draft and immutable SDK source packages per project."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path) if db_path else _DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._seed_default()
        self._migrate_pipeline_projects()
        self._repair_legacy_factor_migrations()
        self._migrate_projects_to_rq_profile()

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
            self._insert_package(
                _DEFAULT_PROJECT_ID,
                1,
                None,
                DEFAULT_STRATEGY_SOURCE,
                inspection,
            )
            self._conn.commit()

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
        normalized = normalize_project_id(project_id)
        if not str(name).strip():
            raise ValueError("project name must not be empty")
        if profile != "runtime":
            raise ValueError("strategy projects use the RQ runtime profile")
        if self.get_project(normalized, include_source=False) is not None:
            raise FileExistsError(normalized)
        inspection = inspect_strategy_source(source)
        self._probe_source(source, inspection)
        with self._lock:
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
            self._insert_package(normalized, 1, None, source, inspection)
            self._conn.commit()
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
        row = self._editable_row(project_id)
        if expected_source_sha256 and row["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        inspection = inspect_strategy_source(source)
        with self._lock:
            self._conn.execute(
                """UPDATE strategy_projects
                   SET draft_source = ?, draft_source_sha256 = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (source, inspection.source_sha256, row["id"]),
            )
            self._conn.commit()
        return self.get_project(row["id"]) or {}

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
        row = self._editable_row(project_id)
        if expected_source_sha256 and row["draft_source_sha256"] != expected_source_sha256:
            raise RuntimeError("draft changed since it was inspected")
        source = str(row["draft_source"])
        inspection = inspect_strategy_source(source)
        self._probe_source(source, inspection)
        current = self.get_package(row["id"], int(row["current_revision"]))
        if current and current["source_sha256"] == inspection.source_sha256:
            return current
        revision = int(row["current_revision"]) + 1
        parent = int(row["current_revision"]) or None
        with self._lock:
            self._insert_package(row["id"], revision, parent, source, inspection)
            self._conn.execute(
                """UPDATE strategy_projects
                   SET current_revision = ?, draft_parent_revision = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (revision, revision, row["id"]),
            )
            self._conn.commit()
        return self.get_package(row["id"], revision) or {}

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

    def _project_payload(self, row: sqlite3.Row, *, include_source: bool) -> dict[str, Any]:
        current = self.get_package(row["id"], int(row["current_revision"]), include_source=False)
        dirty = not current or current["source_sha256"] != row["draft_source_sha256"]
        payload = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "profile": row["profile"],
            "current_revision": int(row["current_revision"]),
            "draft_parent_revision": row["draft_parent_revision"],
            "draft_source_sha256": row["draft_source_sha256"],
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
            payload["inspection"] = inspect_strategy_source(row["draft_source"]).to_dict()
        return payload

    @staticmethod
    def _package_payload(row: sqlite3.Row, *, include_source: bool) -> dict[str, Any]:
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
                            if field in {"open", "high", "low", "close"}
                            else 1_000_000.0
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
            "portfolio": {},
            "state": {},
            "force_signal": True,
            "limits": {"max_weight": 1.0, "max_gross_exposure": 1.0},
        }
        probe_sdk_operation(source, "execution", payload)
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
                f"""@factor(id={factor_id!r}, label={f"迁移待复核：{factor_id}"!r}, inputs=[])
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
