from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pyarrow.parquet as pq

from alphalab import create_runtime_engine
from alphalab.dataio.catalog import DataCatalog


def _partition(root, filename, frame, dataset="rq.bars"):
    path = DataCatalog(root).path(dataset) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def test_coverage_reuses_reads_and_tracks_replaced_added_deleted_partitions(tmp_path, monkeypatch):
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02", None]),
        "symbol": ["000001.sz", "000001.SZ", "000002.SZ"],
        "close": [10.0, 11.0, 12.0],
    })
    first = _partition(tmp_path, "first.parquet", frame)
    catalog = DataCatalog(tmp_path)
    reads = []
    original = pq.ParquetFile

    def opened(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pq, "ParquetFile", opened)
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: catalog.status("rq.bars"), range(4)))
    assert all(row["rows"] == 3 and row["symbol_count"] == 2 for row in statuses)
    assert statuses[0]["date_start"] == "2026-01-01"
    assert statuses[0]["date_end"] == "2026-01-02"
    assert len(reads) == 1
    engine = create_runtime_engine(tmp_path)
    assert engine.get_symbols() == ["000001.SZ"]
    assert engine.get_latest_date() == "2026-01-02"
    assert len(reads) == 1

    replacement = frame.iloc[:1].assign(date=pd.Timestamp("2026-02-01"), symbol="000003.SZ")
    temporary = first.with_suffix(".replacement")
    replacement.to_parquet(temporary, index=False)
    temporary.replace(first)
    assert catalog.status("rq.bars")["date_end"] == "2026-02-01"
    assert engine.get_symbols() == ["000003.SZ"]
    second = _partition(tmp_path, "second.parquet", frame.iloc[:1])
    assert engine.get_symbols() == ["000001.SZ", "000003.SZ"]
    first.unlink()
    assert catalog.status("rq.bars")["rows"] == 1
    second.unlink()
    assert catalog.status("rq.bars")["status"] == "missing"
    assert engine.get_symbols() == []


def test_range_queries_filter_before_pandas_and_preserve_state_and_schema(tmp_path, monkeypatch):
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2025-12-31", "2026-01-01", "2026-01-02", "2026-01-02"]),
        "symbol": ["000001.SZ", "000001.sz", "000001.SZ", "000002.SZ"],
        "open": [9.0, 10.0, 11.0, 20.0],
        "high": [10.0, 11.0, 12.0, 21.0],
        "low": [8.0, 9.0, 10.0, 19.0],
        "close": [9.5, 10.5, 11.5, 20.5],
        "volume": [10.0, 20.0, 30.0, 40.0],
    })
    _partition(tmp_path, "year=2026/month=1/first.parquet", frame)
    _partition(tmp_path, "year=2026/month=1/second.parquet", frame.iloc[[2]].assign(close=12.5, extra=7.0))
    _partition(tmp_path, "state.parquet", pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-02"]),
        "symbol": ["000001.sz", "000001.SZ", "000002.SZ"],
        "paused": [False, True, False],
    }), "rq.paused")
    decoded = []
    original = pq.read_table

    def read(path, *args, **kwargs):
        table = original(path, *args, **kwargs)
        decoded.append((path, table.num_rows, kwargs.get("filters")))
        return table

    monkeypatch.setattr(pq, "read_table", read)
    engine = create_runtime_engine(tmp_path)
    result = engine.get_bars(["000001.SZ"], "2026-01-01", "2026-01-02", use_cache=False)
    assert result["symbol"].tolist() == ["000001.SZ", "000001.SZ"]
    assert result["close"].tolist() == [10.5, 12.5]
    assert result["is_suspended"].tolist() == [False, True]
    assert "year" not in result and "month" not in result
    assert pd.isna(result.iloc[0]["extra"])
    assert result.iloc[1]["extra"] == 7.0
    assert all(count <= 2 and predicate is not None for _, count, predicate in decoded)
    projected = engine.get_bars(["000001.SZ"], "2026-01-01", "2026-01-02", fields=["close"], use_cache=False)
    assert list(projected.columns) == ["date", "symbol", "close"]
    weekly = engine.get_bars(["000001.SZ"], "2026-01-01", "2026-01-02", freq="1w", use_cache=False)
    assert weekly.iloc[0]["open"] == 10.0
    assert weekly.iloc[0]["close"] == 12.5
    assert weekly.iloc[0]["volume"] == 50.0
    other = engine.get_bars(["000002.SZ"], "2026-01-02", "2026-01-02", use_cache=False)
    assert other["close"].tolist() == [20.5]


def test_coverage_does_not_hide_invalid_or_newly_repaired_files(tmp_path):
    frame = pd.DataFrame({"date": ["2026-01-01", "invalid"], "symbol": ["A", "B"]})
    path = _partition(tmp_path, "first.parquet", frame)
    catalog = DataCatalog(tmp_path)
    assert catalog.status("rq.bars")["date_end"] == "2026-01-01"
    assert catalog.symbols("rq.bars", dated_only=True) == ["A"]
    path.write_bytes(b"broken")
    assert catalog.status("rq.bars")["status"] == "invalid"
    frame.iloc[:1].to_parquet(path, index=False)
    assert catalog.status("rq.bars")["status"] == "ready"
