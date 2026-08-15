"""Narrow one-time importer for user-authored YAML from pre-pipeline releases.

YAML is intentionally confined to this module.  Current repositories, APIs,
runtime execution, and snapshots never serialize or interpret it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from alphalab.pipeline.builtins import DEFAULT_PROJECT
from alphalab.utils.paths import RUNTIME_APP_DIR


def migrate_legacy_local_projects(connection: sqlite3.Connection) -> int:
    migrated = 0
    roots = (
        RUNTIME_APP_DIR / "strategies",
        RUNTIME_APP_DIR / "timing_strategies",
    )
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.yaml")):
            source = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
            key = str(path.resolve())
            existing = connection.execute(
                "SELECT source_sha256 FROM legacy_strategy_migrations WHERE source_path = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                continue
            raw = yaml.safe_load(source) or {}
            if not isinstance(raw, dict):
                continue
            project_id = _available_id(connection, _normalize(path.stem))
            components = json.loads(json.dumps(DEFAULT_PROJECT["components"]))
            settings = json.loads(json.dumps(DEFAULT_PROJECT["settings"]))
            settings["stage_parameters"] = {}
            strategy_type = str(raw.get("strategy_type", "stock_selection"))
            if strategy_type == "market_timing":
                components["selection"] = {"component_id": "selection-pass-through", "version": 1}
                components["timing"] = {"component_id": "timing-trend", "version": 1}
                signal = next(iter(raw.get("signals") or []), {})
                position = dict(raw.get("position") or {})
                settings["stage_parameters"]["timing"] = {
                    "window": int(signal.get("window", 10)),
                    "min_exposure": float(position.get("min_exposure", 0.0)),
                    "max_exposure": float(position.get("max_exposure", 1.0)),
                }
                settings["factors"] = []
            else:
                settings["universe"] = dict(raw.get("universe") or settings["universe"])
                settings["factors"] = list(raw.get("factors") or [])
                selection = dict(raw.get("selection") or {})
                portfolio = dict(raw.get("portfolio") or {})
                execution = dict(raw.get("execution") or {})
                settings["stage_parameters"].update(
                    {
                        "selection": {
                            "count": int(selection.get("n_stocks", 20)),
                            "min_factor_coverage": float(selection.get("min_factor_coverage", 0.5)),
                        },
                        "risk": {
                            "max_weight": float(portfolio.get("max_weight", 0.1)),
                            "max_gross_exposure": 1.0,
                        },
                        "execution": execution,
                    }
                )
            refs_json = _json(components)
            settings_json = _json(settings)
            connection.execute(
                """INSERT INTO pipeline_projects
                   (id, name, description, revision, component_refs_json, settings_json, built_in)
                   VALUES (?, ?, ?, 1, ?, ?, 0)""",
                (
                    project_id,
                    str(raw.get("name") or project_id),
                    f"Imported once from legacy local definition: {path.name}",
                    refs_json,
                    settings_json,
                ),
            )
            # Project-version source is filled by PipelineRepository after all
            # component references are available.
            connection.execute(
                """INSERT INTO legacy_strategy_migrations
                   (source_path, source_sha256, project_id) VALUES (?, ?, ?)""",
                (key, digest, project_id),
            )
            migrated += 1
    return migrated


def pending_migrated_projects(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """SELECT p.id FROM pipeline_projects p
               LEFT JOIN pipeline_project_versions v
                 ON v.project_id = p.id AND v.revision = p.revision
               WHERE v.project_id IS NULL"""
        )
    ]


def _available_id(connection: sqlite3.Connection, base: str) -> str:
    value = base
    suffix = 2
    while connection.execute("SELECT 1 FROM pipeline_projects WHERE id = ?", (value,)).fetchone():
        value = f"{base[:58]}-{suffix}"
        suffix += 1
    return value


def _normalize(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "-_" else "-" for character in value.lower())
    cleaned = cleaned.strip("-_") or "migrated-strategy"
    if len(cleaned) < 2:
        cleaned = f"{cleaned}-strategy"
    return cleaned[:64]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["migrate_legacy_local_projects", "pending_migrated_projects"]
