from __future__ import annotations

import pandas as pd
import pytest

from alphalab import create_runtime_engine
from alphalab.dataio import MissingDataError, RQDataClient, RQDataConfig
from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.fundamentals import (
    INCOME_FIELDS,
    build_canonical_fundamentals,
    first_disclosures,
)
from alphalab.dataio.quality import validate_dataset
from alphalab.dataio.rq_sync import RQAcquirer
from alphalab.dataio.runtime import OperationsStore, RuntimeStore
from alphalab.dataio.sync import RQSyncService, SyncRequest, build_sync_plan
from alphalab.tools import create_data_tool_registry


def _financial_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    quarters = [f"{year}q{quarter}" for year in (2023, 2024) for quarter in range(1, 5)]
    info_dates = pd.to_datetime(
        [
            "2023-04-30",
            "2023-08-30",
            "2023-10-30",
            "2024-03-30",
            "2024-04-30",
            "2024-08-30",
            "2024-10-30",
            "2025-03-30",
        ]
    )
    income = pd.DataFrame(
        {
            "symbol": ["000001.SZ"] * 8,
            "quarter": quarters,
            "info_date": info_dates,
            "if_adjusted": [0] * 8,
            "revenue": [100, 220, 360, 500, 130, 280, 450, 650],
            "operating_revenue": [100, 220, 360, 500, 130, 280, 450, 650],
            "cost_of_goods_sold": [60, 132, 216, 300, 78, 168, 270, 390],
            "gross_profit": [40, 88, 144, 200, 52, 112, 180, 260],
            "net_profit_parent_company": [10, 22, 36, 50, 13, 28, 45, 65],
        }
    )
    balance = pd.DataFrame(
        {
            "symbol": ["000001.SZ"] * 8,
            "quarter": quarters,
            "info_date": info_dates,
            "if_adjusted": [0] * 8,
            "total_assets": [1000] * 8,
            "total_liabilities": [400] * 8,
            "equity_parent_company": [600, 610, 620, 630, 640, 650, 660, 670],
            "paid_in_capital": [100] * 8,
        }
    )
    bars = pd.DataFrame(
        {
            "date": info_dates,
            "symbol": ["000001.SZ"] * 8,
            "open": [10.0] * 8,
            "high": [10.5] * 8,
            "low": [9.5] * 8,
            "close": [10.2] * 8,
            "raw_close": [10.0] * 8,
            "volume": [1000.0] * 8,
            "amount": [10000.0] * 8,
        }
    )
    return income, balance, bars


def test_runtime_store_deduplicates_and_rejects_empty_overwrite(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    _, _, bars = _financial_frames()
    store.write("rq.bars", bars.iloc[:2])
    revised = bars.iloc[[1]].copy()
    revised["close"] = 10.3
    store.write("rq.bars", revised)

    saved = store.read("rq.bars")
    assert len(saved) == 2
    assert saved.loc[saved["date"].eq(bars.iloc[1]["date"]), "close"].item() == 10.3
    assert validate_dataset("rq.bars", tmp_path)["status"] == "passed"
    with pytest.raises(MissingDataError, match="empty response"):
        store.write("rq.bars", pd.DataFrame())


def test_first_disclosure_and_canonical_fundamentals_are_point_in_time() -> None:
    income, balance, bars = _financial_frames()
    revision = income.iloc[[0]].copy()
    revision["info_date"] = pd.Timestamp("2025-01-01")
    revision["if_adjusted"] = 1
    revision["net_profit_parent_company"] = 999
    income = pd.concat([income, revision], ignore_index=True)

    first = first_disclosures(income)
    assert first.loc[first["quarter"].eq("2023q1"), "net_profit_parent_company"].item() == 10
    canonical = build_canonical_fundamentals(
        income,
        balance,
        bars,
        asof_date="2024-08-30",
    )
    assert canonical["available_date"].max() <= pd.Timestamp("2024-08-30")
    assert canonical["quarter"].max() == "2024q2"
    assert canonical["ep"].notna().any()


class _FakeAcquirer:
    def __init__(self) -> None:
        self.income, self.balance, self.bars = _financial_frames()

    def instruments(self, snapshot_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "snapshot_date": [pd.Timestamp(snapshot_date)],
                "symbol": ["000001.SZ"],
                "name": ["Example"],
                "listed_date": [pd.Timestamp("1991-01-01")],
                "de_listed_date": [pd.NaT],
                "status": ["Active"],
                "is_st": [False],
                "industry": ["Bank"],
            }
        )

    def daily_bars(self, *args, **kwargs) -> pd.DataFrame:
        return self.bars.copy()

    def financials(self, symbols, fields, *args, **kwargs) -> pd.DataFrame:
        source = self.income if set(fields) == set(INCOME_FIELDS) else self.balance
        return source.copy()


def test_sync_service_builds_runtime_engine_and_records_job(tmp_path) -> None:
    request = SyncRequest(
        datasets=["instruments", "bars", "fundamentals"],
        symbols=["000001.SZ"],
        start="2023-01-01",
        end="2025-03-31",
        force=True,
    )
    operations = OperationsStore(tmp_path)
    job_id = operations.create_job(request.model_dump(mode="json"))
    result = RQSyncService(tmp_path, acquirer=_FakeAcquirer()).run(job_id)

    assert result["status"] == "succeeded"
    assert result["progress"] == result["total"] == 5
    assert DataCatalog(tmp_path).status("canonical.fundamentals")["status"] == "ready"
    engine = create_runtime_engine(tmp_path)
    assert engine.get_symbols() == ["000001.SZ"]
    fundamentals = engine.get_fundamentals(
        ["000001.SZ"],
        ["ep", "roe", "profit_growth"],
        "2023q1",
        "2024q4",
        asof_date="2025-03-31",
        use_cache=False,
    )
    assert not fundamentals.empty


def test_sync_plan_uses_incremental_bar_and_financial_lookbacks(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    income, balance, bars = _financial_frames()
    store.write("rq.bars", bars)
    canonical = build_canonical_fundamentals(income, balance, bars)
    store.write("canonical.fundamentals", canonical)
    plan = build_sync_plan(
        SyncRequest(
            datasets=["bars", "fundamentals"],
            symbols=["000001.SZ"],
            start="2020-01-01",
            end="2025-03-31",
        ),
        root=tmp_path,
    )
    bar_step = next(item for item in plan["steps"] if item["dataset"] == "rq.bars")
    income_step = next(
        item for item in plan["steps"] if item["dataset"] == "rq.financials.income"
    )
    assert bar_step["mode"] == "incremental"
    assert bar_step["start"] == "2025-03-23"
    assert income_step["mode"] == "revision_lookback"
    assert income_step["start_quarter"] == "2023q2"


class _FakeRQModule:
    def __init__(self) -> None:
        self.price_calls: list[dict] = []
        self.financial_calls: list[dict] = []

    def init(self, *args, **kwargs) -> None:
        pass

    def get_price(self, order_book_ids, **kwargs):
        self.price_calls.append({"order_book_ids": order_book_ids, **kwargs})
        index = pd.MultiIndex.from_tuples(
            [(symbol, pd.Timestamp("2025-01-02")) for symbol in order_book_ids],
            names=["order_book_id", "date"],
        )
        columns = {
            "open": [10.0] * len(index),
            "high": [10.5] * len(index),
            "low": [9.5] * len(index),
            "close": [10.2] * len(index),
            "volume": [1000.0] * len(index),
            "total_turnover": [10200.0] * len(index),
        }
        return pd.DataFrame(columns, index=index)

    def get_pit_financials_ex(self, **kwargs):
        self.financial_calls.append(kwargs)
        symbols = kwargs["order_book_ids"]
        index = pd.MultiIndex.from_tuples(
            [(symbol, kwargs["start_quarter"]) for symbol in symbols],
            names=["order_book_id", "quarter"],
        )
        values = {
            "info_date": [pd.Timestamp("2025-03-20")] * len(index),
            "if_adjusted": [0] * len(index),
        }
        values.update({field: [1.0] * len(index) for field in kwargs["fields"]})
        return pd.DataFrame(values, index=index)


def test_rq_acquirer_enforces_stock_and_quarter_batch_limits() -> None:
    module = _FakeRQModule()
    client = RQDataClient(
        RQDataConfig(user="demo", password="secret", host="example:16011"),
        module=module,
    )
    acquirer = RQAcquirer(client, retries=1)
    symbols = [f"{value:06d}.SZ" for value in range(201)]
    bars = acquirer.daily_bars(symbols, "2025-01-01", "2025-01-03")
    financials = acquirer.financials(
        symbols,
        ["revenue"],
        "2012q2",
        "2024q4",
    )

    assert len(module.price_calls) == 4
    assert max(len(call["order_book_ids"]) for call in module.price_calls) <= 200
    assert len(module.financial_calls) == 4
    assert all(call["statements"] == "all" for call in module.financial_calls)
    assert "raw_close" in bars
    assert not financials.empty


def test_failed_sync_records_an_honest_error(tmp_path) -> None:
    request = SyncRequest(
        datasets=["bars"],
        symbols=["000001.SZ"],
        start="2025-01-01",
        end="2025-01-03",
    )

    class EmptyAcquirer(_FakeAcquirer):
        def daily_bars(self, *args, **kwargs) -> pd.DataFrame:
            return pd.DataFrame()

    operations = OperationsStore(tmp_path)
    job_id = operations.create_job(request.model_dump(mode="json"))
    result = RQSyncService(tmp_path, acquirer=EmptyAcquirer()).run(job_id)
    assert result["status"] == "failed"
    assert "empty response" in result["error"]


def test_data_tool_registry_returns_bounded_structured_rows(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    _, _, bars = _financial_frames()
    store.write("rq.bars", bars)
    registry = create_data_tool_registry(tmp_path)

    assert {item["name"] for item in registry.describe()} == {
        "data.catalog",
        "data.status",
        "data.plan_sync",
        "data.run_sync",
        "data.validate",
        "data.query",
    }
    result = registry.invoke(
        "data.query",
        {"dataset": "rq.bars", "symbols": ["000001.SZ"], "limit": 2},
    )
    assert result["returned_rows"] == 2
    assert result["truncated"] is True
