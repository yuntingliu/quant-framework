"""SQLite persistence for reusable safe-expression factor definitions."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from alphalab.factors.expression import factor_dependencies
from alphalab.store import ResultStore
from alphalab.strategy.config import FactorSpec
from alphalab.utils.paths import RUNTIME_APP_DIR

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"
_DEFAULT_DB = RUNTIME_APP_DIR / "alphalab.db"
_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")


class FactorDefinitionRepository:
    """Store user-authored expressions independently from project adoption."""

    def __init__(self, db_path: str | Path | None = None):
        self.path = Path(db_path) if db_path else _DEFAULT_DB
        if db_path is None and not self.path.exists():
            ResultStore().close()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))

    def close(self) -> None:
        self._conn.close()

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT name, description, expression, direction, winsorize,
                      neutralize_json, created_at, updated_at
               FROM factor_definitions
               ORDER BY updated_at DESC, name"""
        ).fetchall()
        return [self._as_dict(row) for row in rows]

    def get(self, name: str) -> dict | None:
        row = self._conn.execute(
            """SELECT name, description, expression, direction, winsorize,
                      neutralize_json, created_at, updated_at
               FROM factor_definitions WHERE name = ?""",
            (str(name).strip(),),
        ).fetchone()
        return self._as_dict(row) if row is not None else None

    def save(
        self,
        name: str,
        *,
        description: str = "",
        expression: str,
        direction: str = "long",
        winsorize: float = 0.01,
        neutralize: Iterable[str] = (),
        reserved_names: Iterable[str] = (),
    ) -> dict:
        normalized_name = str(name).strip()
        if not _NAME_PATTERN.fullmatch(normalized_name):
            raise ValueError("factor name must be a Python-style identifier up to 80 characters")
        if normalized_name in set(reserved_names):
            raise ValueError("custom factor name must not shadow a built-in factor")
        normalized_description = str(description).strip()
        if len(normalized_description) > 240:
            raise ValueError("factor description must be at most 240 characters")
        factor = FactorSpec(
            name=normalized_name,
            source="expression",
            expression=str(expression).strip(),
            direction=direction,
            weight=1.0,
            winsorize=float(winsorize),
            neutralize=tuple(neutralize),
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO factor_definitions
                   (name, description, expression, direction, winsorize, neutralize_json)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       description = excluded.description,
                       expression = excluded.expression,
                       direction = excluded.direction,
                       winsorize = excluded.winsorize,
                       neutralize_json = excluded.neutralize_json,
                       updated_at = datetime('now')""",
                (
                    factor.name,
                    normalized_description,
                    factor.expression,
                    factor.direction,
                    factor.winsorize,
                    json.dumps(list(factor.neutralize), ensure_ascii=False),
                ),
            )
            self._conn.commit()
        return self.get(factor.name) or {}

    def delete(self, name: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM factor_definitions WHERE name = ?",
                (str(name).strip(),),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _as_dict(row: sqlite3.Row) -> dict:
        expression = str(row["expression"])
        return {
            "name": str(row["name"]),
            "description": str(row["description"]),
            "expression": expression,
            "direction": str(row["direction"]),
            "winsorize": float(row["winsorize"]),
            "neutralize": json.loads(row["neutralize_json"]),
            "input_fields": list(factor_dependencies(expression)),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }


__all__ = ["FactorDefinitionRepository"]
