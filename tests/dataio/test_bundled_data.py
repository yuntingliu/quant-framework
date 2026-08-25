from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd


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
        "ep", "bp", "roe", "gross_margin", "leverage", "profit_growth", "revenue_growth"
    }.issubset(fundamentals.columns)

    factors = pd.read_parquet(immutable[2])
    assert list(factors.columns) == ["MKT", "SMB", "HML", "MOM", "RMW", "rf"]
    assert len(factors) == 60


def test_seed_database_is_complete():
    db_path = ROOT / "data" / "app" / "alphalab.db"
    with sqlite3.connect(db_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "pipeline_components",
                "pipeline_component_versions",
                "pipeline_projects",
                "pipeline_project_versions",
                "backtests",
                "backtest_returns",
                "backtest_weights",
                "orders",
            )
        }
        authorable_stages = {
            row[0]
            for row in connection.execute(
                """SELECT DISTINCT pc.stage
                   FROM pipeline_projects pp,
                            json_each(pp.component_refs_json) ref
                   JOIN pipeline_components pc ON pc.id = json_extract(ref.value, '$.component_id')
                   WHERE pp.id = 'three-stage-default'"""
            )
        }
        default_project = connection.execute(
            "SELECT built_in, revision FROM pipeline_projects WHERE id = 'three-stage-default'"
        ).fetchone()
    assert counts["pipeline_components"] >= 9
    assert counts["pipeline_component_versions"] >= counts["pipeline_components"]
    assert counts["pipeline_projects"] >= 1
    assert counts["pipeline_project_versions"] >= counts["pipeline_projects"]
    assert authorable_stages == {"selection", "portfolio", "execution"}
    assert default_project is not None
    assert default_project[0] == 1
    assert default_project[1] >= 1
    assert counts["backtests"] >= 6
    assert counts["backtest_returns"] >= 360
    assert counts["backtest_weights"] > 0
    assert counts["orders"] > 0
