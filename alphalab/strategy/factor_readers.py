"""Read-only factor asset and evidence projections; never initialize or migrate stores."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alphalab.strategy.factor_assets import FactorAssets
from alphalab.strategy.history import HistoryUnavailableError


class FactorAssetReader(FactorAssets):
    """FactorAssets read model with an explicitly read-only SQLite connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def __enter__(self):
        try:
            self._conn = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA query_only = ON")
            self._conn.execute("BEGIN")
            return self
        except sqlite3.Error as exc:
            raise HistoryUnavailableError("Factor assets are unavailable; no migration was attempted.") from exc

    def __exit__(self, _kind, _value, _traceback):
        self._conn.close()

    def _strategy_rows(self, project_id: str) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM project_strategies WHERE project_id=? ORDER BY id != 'main', id",
            (project_id,),
        ).fetchall()]


def factor_asset_reader(path: str | Path) -> FactorAssetReader:
    return FactorAssetReader(path)
