from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pandas as pd

from alphalab.strategy.repository import StrategyRepository

ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_bundled_real_data_contract():
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["symbol_count"] == 300
    assert manifest["realtime"] == "not_configured"

    immutable = [
        ROOT / "data" / "market" / "bars.parquet",
        ROOT / "data" / "fundamentals" / "fundamentals.parquet",
        ROOT / "data" / "factors" / "factor_returns.parquet",
    ]
    for path in immutable:
        relative = path.relative_to(ROOT).as_posix()
        assert path.stat().st_size == manifest["files"][relative]["bytes"]
        assert _sha256(path) == manifest["files"][relative]["sha256"]

    bars = pd.read_parquet(immutable[0])
    assert bars["symbol"].nunique() == 300
    assert not bars.duplicated(["date", "symbol"]).any()
    assert bars[["open", "high", "low", "close", "volume", "amount"]].notna().all().all()
    assert bars["high"].ge(bars[["open", "close", "low"]].max(axis=1)).all()
    assert bars["low"].le(bars[["open", "close", "high"]].min(axis=1)).all()

    fundamentals = pd.read_parquet(immutable[1])
    assert fundamentals["symbol"].nunique() == 300
    assert fundamentals["available_date"].notna().all()
    assert {
        "ep",
        "bp",
        "roe",
        "gross_margin",
        "leverage",
        "profit_growth",
        "revenue_growth",
    }.issubset(fundamentals.columns)

    factors = pd.read_parquet(immutable[2])
    assert list(factors.columns) == ["MKT", "SMB", "HML", "MOM", "RMW", "rf"]
    assert len(factors) == 60


def test_bundled_database_upgrades_to_the_sdk_contract(tmp_path):
    # The tracked database is a frozen historical fixture. Current tables are
    # created and old custom projects migrated only in a writable local copy.
    db_path = tmp_path / "alphalab.db"
    shutil.copy2(ROOT / "data" / "app" / "alphalab.db", db_path)
    repository = StrategyRepository(db_path)
    try:
        project = repository.get_project("sdk-v1-default")
        package = repository.get_package("sdk-v1-default", 1)
    finally:
        repository.close()
    assert project is not None and project["built_in"] is True
    assert package is not None and package["sdk_version"] == 1

    with sqlite3.connect(db_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "strategy_projects",
                "strategy_source_packages",
                "backtests",
                "backtest_returns",
                "backtest_weights",
                "orders",
            )
        }
        migration = connection.execute(
            "SELECT detail_json FROM strategy_contract_migrations "
            "WHERE name = 'strategy-sdk-v1-cutover'"
        ).fetchone()
    assert counts["strategy_projects"] >= 1
    assert counts["strategy_source_packages"] >= counts["strategy_projects"]
    assert migration is not None
    assert counts["backtests"] >= 6
    assert counts["backtest_returns"] >= 360
    assert counts["backtest_weights"] > 0
    assert counts["orders"] > 0
