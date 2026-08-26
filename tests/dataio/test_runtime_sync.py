from __future__ import annotations

import pandas as pd
import pytest

from alphalab import create_runtime_engine
from alphalab.dataio import (
    DataLoadError,
    DataValidationError,
    MissingDataError,
    RQDataClient,
    RQDataConfig,
)
from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.fundamentals import (
    INCOME_FIELDS,
    build_canonical_fundamentals,
    first_disclosures,
)
from alphalab.dataio.quality import validate_all, validate_dataset
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
    revision["info_date"] = pd.to_datetime(["2025-01-01"])
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


def test_incremental_bars_backfill_symbols_missing_from_local_history(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    _, _, bars = _financial_frames()
    existing = bars.iloc[[0]].copy()
    existing["date"] = pd.Timestamp("2025-01-10")
    store.write("rq.bars", existing)

    class RecordingAcquirer(_FakeAcquirer):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[list[str], str, str]] = []

        def daily_bar_chunks(self, symbols, start, end, **kwargs):
            self.calls.append((list(symbols), start, end))
            yield pd.DataFrame(
                {
                    "date": [pd.Timestamp(end)] * len(symbols),
                    "symbol": symbols,
                    "open": [10.0] * len(symbols),
                    "high": [10.5] * len(symbols),
                    "low": [9.5] * len(symbols),
                    "close": [10.2] * len(symbols),
                    "raw_close": [10.0] * len(symbols),
                    "volume": [1000.0] * len(symbols),
                    "amount": [10000.0] * len(symbols),
                }
            )

    acquirer = RecordingAcquirer()
    request = SyncRequest(
        datasets=["bars"],
        symbols=["000001.SZ", "600000.SH"],
        start="2025-01-01",
        end="2025-01-10",
    )
    operations = OperationsStore(tmp_path)
    job_id = operations.create_job(request.model_dump(mode="json"))

    result = RQSyncService(tmp_path, acquirer=acquirer).run(job_id)

    assert result["status"] == "succeeded"
    assert acquirer.calls == [
        (["600000.SH"], "2025-01-01", "2025-01-10"),
        (["000001.SZ"], "2025-01-03", "2025-01-10"),
    ]


def test_market_state_plan_backfills_new_fields_and_indexes_from_requested_start(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    store.write(
        "rq.daily_factors",
        pd.DataFrame(
            {
                "date": [pd.Timestamp("2025-01-03")],
                "symbol": ["000001.SZ"],
                "field": ["market_cap"],
                "value": [1.0],
            }
        ),
    )
    store.write(
        "rq.index_components",
        pd.DataFrame(
            {
                "date": [pd.Timestamp("2025-01-31")],
                "index_symbol": ["000300.SH"],
                "symbol": ["000001.SZ"],
            }
        ),
    )

    plan = build_sync_plan(
        SyncRequest(
            datasets=["market-state"],
            symbols=["000001.SZ"],
            start="2020-01-01",
            end="2025-02-28",
            daily_factors=["market_cap", "roe"],
            index_symbols=["000300.SH", "000905.SH"],
        ),
        root=tmp_path,
    )
    factor_step = next(item for item in plan["steps"] if item["dataset"] == "rq.daily_factors")
    component_step = next(
        item for item in plan["steps"] if item["dataset"] == "rq.index_components"
    )

    assert factor_step["field_starts"] == {
        "market_cap": "2025-01-02",
        "roe": "2020-01-01",
    }
    assert component_step["index_starts"] == {
        "000300.SH": "2025-01-31",
        "000905.SH": "2020-01-01",
    }


class _FakeRQModule:
    def __init__(self) -> None:
        self.price_calls: list[dict] = []
        self.financial_calls: list[dict] = []
        self.state_calls: list[dict] = []
        self.factor_calls: list[dict] = []

    def init(self, *args, **kwargs) -> None:
        pass

    def all_instruments(self, **kwargs):
        assert kwargs == {"type": "CS", "market": "cn"}
        return pd.DataFrame(
            {
                "order_book_id": ["000001.XSHE", "200001.XSHE", "900901.XSHG"],
                "symbol": ["Ping An Bank", "Shenzhen B", "Shanghai B"],
                "listed_date": [pd.Timestamp("1991-04-03")] * 3,
                "de_listed_date": [pd.NaT] * 3,
                "status": ["Active"] * 3,
            }
        )

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

    def get_yield_curve(self, **kwargs):
        assert kwargs["tenor"] == "1M"
        assert kwargs["market"] == "cn"
        index = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="B")
        return pd.DataFrame({"1M": 2.4}, index=index.rename("date"))

    def is_suspended(self, order_book_ids, **kwargs):
        self.state_calls.append({"kind": "paused", "order_book_ids": order_book_ids, **kwargs})
        index = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="B")
        return pd.DataFrame(False, index=index, columns=order_book_ids)

    def is_st_stock(self, order_book_ids, **kwargs):
        self.state_calls.append({"kind": "is_st", "order_book_ids": order_book_ids, **kwargs})
        index = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="B")
        return pd.DataFrame(False, index=index, columns=order_book_ids)

    def get_factor(self, **kwargs):
        self.factor_calls.append(kwargs)
        index = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="B")
        return pd.DataFrame(1.0, index=index, columns=kwargs["order_book_ids"])

    def index_components(self, index_symbol, date=None):
        return ["000001.XSHE", "600000.XSHG"]


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


def test_rq_acquirer_rejects_adjusted_and_raw_bar_key_mismatch() -> None:
    class MismatchedBarsModule(_FakeRQModule):
        def get_price(self, order_book_ids, **kwargs):
            frame = super().get_price(order_book_ids, **kwargs)
            if kwargs["adjust_type"] == "none":
                return frame.iloc[:-1]
            return frame

    module = MismatchedBarsModule()
    client = RQDataClient(
        RQDataConfig(user="demo", password="secret", host="example:16011"),
        module=module,
    )

    with pytest.raises(DataValidationError, match="adjusted/raw bars.*key mismatch"):
        RQAcquirer(client, retries=1).daily_bars(
            ["000001.SZ", "600000.SH"],
            "2025-01-02",
            "2025-01-02",
        )


def test_rq_instrument_snapshot_uses_retrieval_date() -> None:
    module = _FakeRQModule()
    client = RQDataClient(
        RQDataConfig(user="demo", password="secret", host="example:16011"),
        module=module,
    )

    instruments = RQAcquirer(client, retries=1).instruments("2020-01-01")

    assert instruments["snapshot_date"].nunique() == 1
    assert instruments["snapshot_date"].iloc[0] == pd.Timestamp.now().normalize()
    assert instruments["symbol"].tolist() == ["000001.SZ"]


def test_rq_acquirer_normalizes_annual_yield_to_monthly_return() -> None:
    module = _FakeRQModule()
    client = RQDataClient(
        RQDataConfig(user="demo", password="secret", host="example:16011"),
        module=module,
    )
    curve = RQAcquirer(client, retries=1).risk_free_curve(
        "2025-01-01",
        "2025-01-10",
    )

    assert list(curve) == ["date", "rf"]
    assert curve["rf"].iloc[0] == pytest.approx((1.024 ** (1 / 12)) - 1)


def test_rq_acquirer_normalizes_and_batches_market_state() -> None:
    module = _FakeRQModule()
    client = RQDataClient(
        RQDataConfig(user="demo", password="secret", host="example:16011"),
        module=module,
    )
    acquirer = RQAcquirer(client, retries=1)
    symbols = [f"{value:06d}.SZ" for value in range(201)]

    states = list(acquirer.market_state_chunks(symbols, "2025-01-01", "2025-01-03"))
    factors = list(
        acquirer.daily_factor_chunks(
            symbols,
            ["market_cap", "roe"],
            "2025-01-01",
            "2025-01-03",
        )
    )
    components = acquirer.index_components(["000300.SH"], ["2025-01-03"])

    paused, is_st = states[0]
    assert {"date", "symbol", "paused"} <= set(paused)
    assert {"date", "symbol", "is_st"} <= set(is_st)
    assert len(module.state_calls) == 4
    assert max(len(call["order_book_ids"]) for call in module.state_calls) <= 200
    assert set(factors[0]["field"]) == {"market_cap", "roe"}
    assert len(module.factor_calls) == 4
    assert {"000001.SZ", "600000.SH"} == set(components["symbol"])


def test_rq_acquirer_rejects_paused_and_st_key_mismatch() -> None:
    class MismatchedStateModule(_FakeRQModule):
        def is_st_stock(self, order_book_ids, **kwargs):
            frame = super().is_st_stock(order_book_ids, **kwargs)
            return frame.drop(columns=order_book_ids[-1])

    module = MismatchedStateModule()
    client = RQDataClient(
        RQDataConfig(user="demo", password="secret", host="example:16011"),
        module=module,
    )

    with pytest.raises(DataValidationError, match="paused/ST state.*key mismatch"):
        list(
            RQAcquirer(client, retries=1).market_state_chunks(
                ["000001.SZ", "600000.SH"],
                "2025-01-02",
                "2025-01-03",
            )
        )


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


class _FullUniverseAcquirer(_FakeAcquirer):
    def instruments(self, snapshot_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "snapshot_date": [pd.Timestamp(snapshot_date)] * 3,
                "symbol": ["000001.SZ", "600000.SH", "000002.SZ"],
                "name": ["One", "Two", "Expired"],
                "listed_date": pd.to_datetime(["1991-01-01", "1999-01-01", "1991-01-01"]),
                "de_listed_date": [pd.NaT, pd.NaT, pd.Timestamp("2020-01-01")],
                "status": ["Active", "Active", "Delisted"],
                "is_st": [False, False, False],
                "industry": ["Bank", "Bank", "Property"],
            }
        )

    symbols_for_period = staticmethod(RQAcquirer.symbols_for_period)

    def daily_bar_chunks(self, symbols, *args, **kwargs):
        assert symbols == ["000001.SZ", "600000.SH"]
        yield pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-01-02", "2025-01-02", "2025-01-03", "2025-01-03"]),
                "symbol": ["000001.SZ", "600000.SH"] * 2,
                "open": [10.0, 11.0, 10.1, 11.1],
                "high": [10.5, 11.5, 10.6, 11.6],
                "low": [9.5, 10.5, 9.6, 10.6],
                "close": [10.2, 11.2, 10.3, 11.3],
                "raw_close": [10.0, 11.0, 10.1, 11.1],
                "volume": [1000.0] * 4,
                "amount": [10000.0] * 4,
            }
        )

    def market_state_chunks(self, symbols, *args, **kwargs):
        keys = pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-01-02", "2025-01-02", "2025-01-03", "2025-01-03"]),
                "symbol": ["000001.SZ", "600000.SH"] * 2,
            }
        )
        yield keys.assign(paused=[False, False, False, True]), keys.assign(
            is_st=[False, False, True, False]
        )

    def daily_factor_chunks(self, symbols, fields, *args, **kwargs):
        rows = []
        for field in fields:
            for date in pd.to_datetime(["2025-01-02", "2025-01-03"]):
                for offset, symbol in enumerate(symbols):
                    rows.append(
                        {"date": date, "symbol": symbol, "field": field, "value": 1.0 + offset}
                    )
        yield pd.DataFrame(rows)

    def index_components(self, index_symbols, dates, **kwargs):
        return pd.DataFrame(
            {
                "date": [pd.Timestamp(max(dates))],
                "index_symbol": [index_symbols[0]],
                "symbol": ["000001.SZ"],
            }
        )


def test_full_universe_market_state_sync_is_direct_and_complete(tmp_path) -> None:
    request = SyncRequest(
        datasets=["bars", "market-state"],
        universe="all",
        start="2025-01-02",
        end="2025-01-03",
        daily_factors=["market_cap", "roe"],
        index_symbols=["000300.SH"],
    )
    plan = build_sync_plan(request, root=tmp_path)
    assert plan["symbol_source"] == "rq_instruments_at_run"
    assert {step["dataset"] for step in plan["steps"]} == {
        "rq.instruments",
        "rq.bars",
        "rq.paused",
        "rq.is_st",
        "rq.daily_factors",
        "rq.index_components",
    }

    operations = OperationsStore(tmp_path)
    job_id = operations.create_job(request.model_dump(mode="json"))
    result = RQSyncService(tmp_path, acquirer=_FullUniverseAcquirer()).run(job_id)

    assert result["status"] == "succeeded"
    assert RuntimeStore(tmp_path).read("rq.bars")["symbol"].nunique() == 2
    paused_report = validate_dataset(
        "rq.paused",
        tmp_path,
        as_of_date="2025-01-03",
        fail_on_gap=True,
    )
    assert paused_report["status"] == "passed"
    assert paused_report["metrics"]["bar_key_coverage"] == 1.0
    assert validate_dataset("rq.daily_factors", tmp_path)["status"] == "passed"
    assert validate_dataset("rq.index_components", tmp_path)["status"] == "passed"


def test_completed_bar_chunk_survives_a_later_provider_failure(tmp_path) -> None:
    first_chunk = _FullUniverseAcquirer().daily_bar_chunks(
        ["000001.SZ", "600000.SH"],
        "2025-01-02",
        "2025-01-03",
    )

    class FailingAcquirer(_FakeAcquirer):
        def daily_bar_chunks(self, *args, **kwargs):
            yield next(first_chunk)
            raise DataLoadError("later date chunk failed")

    request = SyncRequest(
        datasets=["bars"],
        symbols=["000001.SZ", "600000.SH"],
        start="2025-01-02",
        end="2026-01-03",
    )
    operations = OperationsStore(tmp_path)
    job_id = operations.create_job(request.model_dump(mode="json"))
    result = RQSyncService(tmp_path, acquirer=FailingAcquirer()).run(job_id)

    assert result["status"] == "failed"
    saved = RuntimeStore(tmp_path).read("rq.bars")
    assert len(saved) == 4
    assert saved["date"].max() == pd.Timestamp("2025-01-03")


def test_fail_on_gap_rejects_incomplete_market_state_keys(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    bars = next(
        _FullUniverseAcquirer().daily_bar_chunks(
            ["000001.SZ", "600000.SH"],
            "2025-01-02",
            "2025-01-03",
        )
    )
    store.write("rq.bars", bars)
    store.write(
        "rq.paused",
        pd.DataFrame(
            {
                "date": [pd.Timestamp("2025-01-02")],
                "symbol": ["000001.SZ"],
                "paused": [False],
            }
        ),
    )

    report = validate_dataset("rq.paused", tmp_path, fail_on_gap=True)
    assert report["status"] == "failed"
    assert {issue["code"] for issue in report["issues"]} == {"bar_key_gap"}


def test_fail_on_gap_rejects_missing_leading_history(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    bars = next(
        _FullUniverseAcquirer().daily_bar_chunks(
            ["000001.SZ", "600000.SH"],
            "2025-01-02",
            "2025-01-03",
        )
    )
    store.write("rq.bars", bars)
    store.write(
        "rq.paused",
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-01-03", "2025-01-03"]),
                "symbol": ["000001.SZ", "600000.SH"],
                "paused": [False, False],
            }
        ),
    )

    report = validate_dataset(
        "rq.paused",
        tmp_path,
        start_date="2025-01-02",
        as_of_date="2025-01-03",
        fail_on_gap=True,
    )

    assert report["status"] == "failed"
    assert report["metrics"]["bar_key_coverage"] == 0.5
    assert {issue["code"] for issue in report["issues"]} == {"bar_key_gap"}


def test_fail_on_gap_checks_bar_start_and_each_factor_date_range(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    bars = next(
        _FullUniverseAcquirer().daily_bar_chunks(
            ["000001.SZ", "600000.SH"],
            "2025-01-02",
            "2025-01-03",
        )
    )
    store.write("rq.bars", bars.loc[bars["date"].eq(pd.Timestamp("2025-01-03"))])
    bar_report = validate_dataset(
        "rq.bars",
        tmp_path,
        start_date="2025-01-02",
        fail_on_gap=True,
    )
    assert {issue["code"] for issue in bar_report["issues"]} == {"stale_start_date"}

    store.write("rq.bars", bars)
    store.write(
        "rq.daily_factors",
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-03"]),
                "symbol": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "field": ["market_cap", "market_cap", "roe"],
                "value": [1.0, 1.1, 0.1],
            }
        ),
    )
    factor_report = validate_dataset(
        "rq.daily_factors",
        tmp_path,
        start_date="2025-01-02",
        as_of_date="2025-01-03",
        fail_on_gap=True,
    )
    assert factor_report["metrics"]["field_date_coverage"] == {
        "market_cap": 1.0,
        "roe": 0.5,
    }
    assert {issue["code"] for issue in factor_report["issues"]} == {"factor_date_gap"}


def test_large_daily_quality_checks_stream_partitions(monkeypatch, tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    bars = next(
        _FullUniverseAcquirer().daily_bar_chunks(
            ["000001.SZ", "600000.SH"],
            "2025-01-02",
            "2025-01-03",
        )
    )
    states = next(
        _FullUniverseAcquirer().market_state_chunks(
            ["000001.SZ", "600000.SH"],
            "2025-01-02",
            "2025-01-03",
        )
    )
    factors = next(
        _FullUniverseAcquirer().daily_factor_chunks(
            ["000001.SZ", "600000.SH"],
            ["market_cap", "roe"],
            "2025-01-02",
            "2025-01-03",
        )
    )
    store.write("rq.bars", bars)
    store.write("rq.paused", states[0])
    store.write("rq.daily_factors", factors)

    def reject_full_read(self, dataset):
        raise AssertionError(f"unexpected full read: {dataset}")

    monkeypatch.setattr(RuntimeStore, "read", reject_full_read)

    for dataset in ("rq.bars", "rq.paused", "rq.daily_factors"):
        report = validate_dataset(
            dataset,
            tmp_path,
            start_date="2025-01-02",
            as_of_date="2025-01-03",
            fail_on_gap=True,
        )
        assert report["status"] == "passed"


def test_validate_all_can_target_only_the_synced_runtime_datasets(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    _, _, bars = _financial_frames()
    store.write("rq.bars", bars)

    reports = validate_all(tmp_path, datasets=["rq.bars"])

    assert [report["dataset"] for report in reports] == ["rq.bars"]
    assert reports[0]["status"] == "passed"


def test_cancellation_after_market_state_chunk_is_not_marked_complete(tmp_path) -> None:
    operations = OperationsStore(tmp_path)

    class CancellingStateAcquirer(_FullUniverseAcquirer):
        def market_state_chunks(self, symbols, *args, **kwargs):
            yield from super().market_state_chunks(symbols, *args, **kwargs)
            operations.request_cancel(job_id)

    request = SyncRequest(
        datasets=["market-state"],
        symbols=["000001.SZ", "600000.SH"],
        start="2025-01-02",
        end="2025-01-03",
    )
    job_id = operations.create_job(request.model_dump(mode="json"))

    result = RQSyncService(tmp_path, acquirer=CancellingStateAcquirer()).run(job_id)

    assert result["status"] == "cancelled"
    assert result["progress"] == 0
    assert RuntimeStore(tmp_path).read("rq.paused").shape[0] == 4


def test_cancellation_after_daily_factor_chunk_is_not_marked_complete(tmp_path) -> None:
    operations = OperationsStore(tmp_path)

    class CancellingFactorAcquirer(_FullUniverseAcquirer):
        def daily_factor_chunks(self, symbols, fields, *args, **kwargs):
            yield from super().daily_factor_chunks(symbols, fields, *args, **kwargs)
            operations.request_cancel(job_id)

    request = SyncRequest(
        datasets=["market-state"],
        symbols=["000001.SZ", "600000.SH"],
        start="2025-01-02",
        end="2025-01-03",
        index_symbols=[],
    )
    job_id = operations.create_job(request.model_dump(mode="json"))

    result = RQSyncService(tmp_path, acquirer=CancellingFactorAcquirer()).run(job_id)

    assert result["status"] == "cancelled"
    assert result["progress"] == 2
    assert RuntimeStore(tmp_path).read("rq.daily_factors").shape[0] == 8
