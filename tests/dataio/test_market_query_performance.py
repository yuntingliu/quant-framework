from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from alphalab.dataio import (
    LocalParquetMarketDataProvider,
    PartitionedParquetMarketDataProvider,
    create_runtime_engine,
)
from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.runtime import RuntimeStore


def _bars() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2025-01-02", "2025-02-03", "2025-02-03", "2025-02-04"]),
        "symbol": ["AAA", "AAA", "BBB", "AAA"],
        "open": [10., 11., 30., 12.], "high": [11., 12., 31., 13.],
        "low": [9., 10., 29., 11.], "close": [10.5, 11.5, 30.5, 12.5],
        "raw_close": [21., 23., 61., 25.], "volume": [100., 110., 300., 120.],
        "amount": [1050., 1265., 9150., 1500.],
    })


def test_catalog_reuses_coverage_across_instances_and_detects_replacement(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path)
    store.write("rq.bars", _bars())
    catalog = DataCatalog(tmp_path)
    original = catalog.status("rq.bars")
    open_parquet = pq.ParquetFile
    reads = []

    def counted(path, *args, **kwargs):
        reads.append(str(path))
        return open_parquet(path, *args, **kwargs)

    monkeypatch.setattr(pq, "ParquetFile", counted)
    assert DataCatalog(tmp_path).status("rq.bars") == original
    assert create_runtime_engine(tmp_path).get_symbols() == ["AAA", "BBB"]
    assert create_runtime_engine(tmp_path).get_latest_date() == "2025-02-04"
    assert reads == []

    # Replace an existing partition (the normal atomic publication operation).
    replacement = tmp_path / "replacement.parquet"
    _bars().iloc[:1].assign(date=pd.Timestamp("2025-01-31"), symbol="CCC").to_parquet(replacement)
    january = catalog.files("rq.bars")[0]
    replacement.replace(january)
    assert DataCatalog(tmp_path).status("rq.bars")["date_start"] == "2025-01-31"
    assert DataCatalog(tmp_path).symbols("rq.bars") == ["AAA", "BBB", "CCC"]
    assert len(reads) == 1
    january.unlink()
    assert DataCatalog(tmp_path).status("rq.bars")["rows"] == 3
    assert DataCatalog(tmp_path).symbols("rq.bars") == ["AAA", "BBB"]
    store.write("rq.bars", _bars().iloc[:1].assign(date=pd.Timestamp("2025-03-03"), symbol="DDD"))
    assert DataCatalog(tmp_path).status("rq.bars")["date_end"] == "2025-03-03"
    assert DataCatalog(tmp_path).symbols("rq.bars") == ["AAA", "BBB", "DDD"]


@pytest.mark.parametrize("frequency", ["1d", "1w", "1M"])
def test_filtered_query_matches_full_history_semantics(tmp_path, frequency):
    store = RuntimeStore(tmp_path)
    bars = _bars()
    store.write("rq.bars", bars)
    store.write("rq.paused", bars[["date", "symbol"]].assign(paused=[False, True, False, False]))
    store.write("rq.is_st", bars[["date", "symbol"]].assign(is_st=[True, False, False, True]))
    # Preserve deterministic keep-last behavior across overlapping partitions.
    duplicate = store.catalog.path("rq.bars") / "z-duplicate.parquet"
    bars.iloc[1:2].assign(close=99.).to_parquet(duplicate)
    legacy = PartitionedParquetMarketDataProvider(tmp_path)
    expected = LocalParquetMarketDataProvider.get_bars(
        legacy, ["aaa"], "2025-02-03", "2025-02-04", freq=frequency,
    )
    actual = create_runtime_engine(tmp_path).get_bars(
        ["aaa"], "2025-02-03", "2025-02-04", freq=frequency, use_cache=False,
    )
    pd.testing.assert_frame_equal(actual, expected)


def test_query_prunes_old_partitions_and_filters_before_pandas(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path)
    store.write("rq.bars", _bars())
    read_parquet = pd.read_parquet
    reads = []

    def counted(path, *args, **kwargs):
        result = read_parquet(path, *args, **kwargs)
        reads.append((Path(path), kwargs.get("filters"), len(result)))
        return result

    monkeypatch.setattr(pd, "read_parquet", counted)
    result = create_runtime_engine(tmp_path).get_bars(
        ["aaa"], "2025-02-03", "2025-02-04", use_cache=False,
    )
    assert result["close"].tolist() == [11.5, 12.5]
    assert len(reads) == 1
    assert "month=02" in str(reads[0][0])
    assert reads[0][1] is not None and reads[0][2] == 2
    # Reuse the same engine across publication; do not retain stale frames.
    engine = create_runtime_engine(tmp_path)
    engine.get_bars(["AAA"], "2025-02-03", "2025-02-04", use_cache=False)
    store.write("rq.bars", _bars().iloc[1:2].assign(close=42.))
    assert engine.get_bars(["AAA"], "2025-02-03", "2025-02-04", use_cache=False)["close"].iloc[0] == 42.


def test_string_dates_and_mixed_case_symbols_keep_input_compatibility(tmp_path):
    root = tmp_path / "rq/bars"
    root.mkdir(parents=True)
    pd.DataFrame({
        "date": ["2025-02-03", "2025-02-04", "invalid"],
        "symbol": ["AaA", "BBB", "AAA"], "close": [10., 20., 30.],
    }).to_parquet(root / "part.parquet")
    engine = create_runtime_engine(tmp_path)
    assert engine.get_symbols() == ["AAA", "BBB"]
    assert engine.get_latest_date() == "2025-02-04"
    assert engine.get_bars(["AAA"], "2025-02-03", "2025-02-04", use_cache=False)["close"].tolist() == [10.]
