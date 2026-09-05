from __future__ import annotations

import pandas as pd

from alphalab.dataio import (
    DataEngine,
    LocalParquetInstrumentProvider,
    create_default_engine,
    create_runtime_engine,
)
from alphalab.dataio.runtime import RuntimeStore


def test_default_engine_reads_generic_local_parquet(tmp_path):
    market = tmp_path / "market"
    fundamentals = tmp_path / "fundamentals"
    factors = tmp_path / "factors"
    market.mkdir()
    fundamentals.mkdir()
    factors.mkdir()

    pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=3),
            "symbol": ["AAA", "AAA", "AAA"],
            "open": [10, 11, 12],
            "high": [11, 12, 13],
            "low": [9, 10, 11],
            "close": [10.5, 11.5, 12.5],
            "volume": [100, 110, 120],
        }
    ).to_parquet(market / "bars.parquet")
    pd.DataFrame(
        {
            "quarter": ["2023q4", "2024q1"],
            "available_date": pd.to_datetime(["2024-03-15", "2024-05-01"]),
            "symbol": ["AAA", "AAA"],
            "ep": [0.08, 0.10],
            "roe": [0.15, 0.17],
        }
    ).to_parquet(fundamentals / "fundamentals.parquet")
    pd.DataFrame({"MKT": [0.01, 0.02]}, index=pd.to_datetime(["2024-01-31", "2024-02-29"])).to_parquet(
        factors / "factor_returns.parquet"
    )

    engine = create_default_engine(tmp_path)
    assert engine.providers() == {
        "market": ["local"],
        "instrument": ["local"],
        "fundamental": ["local"],
        "factor": ["local"],
        "research": [],
    }
    assert engine.get_symbols() == ["AAA"]
    assert not engine.get_bars(["AAA"], "2024-01-01", "2024-01-03").empty
    before_release = engine.get_fundamentals(
        ["AAA"], ["ep"], "2023q1", "2024q4", asof_date="2024-04-30"
    )
    assert before_release.iloc[-1]["ep"] == 0.08
    after_release = engine.get_fundamentals(
        ["AAA"], ["ep"], "2023q1", "2024q4", asof_date="2024-05-02"
    )
    assert after_release.iloc[-1]["ep"] == 0.10
    assert list(engine.get_factors(["MKT"], "2024-01-01", "2024-12-31").columns) == ["MKT"]


def test_instrument_master_keeps_asset_slices_from_different_snapshots(tmp_path):
    path = tmp_path / "instruments.parquet"
    pd.DataFrame(
        {
            "snapshot_date": pd.to_datetime(["2026-08-12", "2026-08-25"]),
            "symbol": ["510300.SH", "000001.SZ"],
            "asset_type": ["ETF", "CS"],
            "listed_date": pd.to_datetime(["2012-05-28", "1991-04-03"]),
            "de_listed_date": [pd.NaT, pd.NaT],
        }
    ).to_parquet(path)
    engine = DataEngine().register_instrument(
        "runtime", LocalParquetInstrumentProvider(tmp_path), default=True
    )

    assert engine.get_instruments(None)["symbol"].tolist() == ["000001.SZ"]
    assert set(engine.get_instrument_master()["symbol"]) == {"510300.SH", "000001.SZ"}


def test_runtime_instrument_provider_uses_rq_type_when_asset_type_is_null(tmp_path):
    RuntimeStore(tmp_path).write(
        "rq.instruments",
        pd.DataFrame(
            {
                "snapshot_date": pd.to_datetime(["2026-08-25", "2026-08-25"]),
                "symbol": ["000001.SZ", "510300.SH"],
                "asset_type": [None, "ETF"],
                "type": ["CS", "ETF"],
                "listed_date": pd.to_datetime(["1991-04-03", "2012-05-28"]),
                "de_listed_date": [pd.NaT, pd.NaT],
            }
        ),
    )

    instruments = create_runtime_engine(tmp_path).get_instrument_master()

    assert instruments.set_index("symbol")["asset_type"].to_dict() == {
        "000001.SZ": "CS",
        "510300.SH": "ETF",
    }
