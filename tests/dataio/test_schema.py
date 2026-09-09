from __future__ import annotations

import pandas as pd

from alphalab.dataio.schema import DatasetField, discover_parquet_fields
from apps.api.routers import strategy


def test_parquet_schema_discovery_unions_partition_columns_without_loading_rows(tmp_path):
    first = tmp_path / "part-1.parquet"
    second = tmp_path / "part-2.parquet"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02"]),
            "close": [10.0],
        }
    ).to_parquet(first, index=False)
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-03"]),
            "close": [11.0],
            "raw_close": [10.5],
        }
    ).to_parquet(second, index=False)

    fields = discover_parquet_fields([first, second])

    assert [field.name for field in fields] == ["date", "close", "raw_close"]
    assert [field.data_type for field in fields] == ["date", "number", "number"]


def test_strategy_field_catalog_is_generated_from_discovered_schema(monkeypatch):
    schemas = {
        "market_bars": (
            DatasetField("date", "date", True),
            DatasetField("symbol", "string", True),
            DatasetField("close", "number", True),
            DatasetField("vendor_new_field", "number", True),
        ),
        "fundamentals": (
            DatasetField("quarter", "string", True),
            DatasetField("symbol", "string", True),
        ),
    }
    monkeypatch.setattr(
        strategy,
        "research_dataset_schema",
        lambda _profile, dataset: schemas[dataset],
    )
    monkeypatch.setattr(strategy, "_profile_range", lambda _profile: ("2024-01-01", "2025-01-01"))

    catalog = strategy.fields("demo")
    fields = {item["name"]: item for item in catalog["datasets"]["market_bars"]}

    assert set(fields) == {"close", "vendor_new_field"}
    assert fields["close"]["data_type"] == "number"
    assert catalog["start_date"] == "2024-01-01"
