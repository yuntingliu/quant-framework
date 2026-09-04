from __future__ import annotations

import os

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
from alphalab.dataio import runtime as runtime_module
from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.fundamentals import (
    INCOME_FIELDS,
    build_canonical_fundamentals,
    first_disclosures,
)
from alphalab.dataio.quality import validate_all, validate_dataset
from alphalab.dataio.rq_sync import RQAcquirer
from alphalab.dataio.runtime import OperationsStore, RuntimeStore
from alphalab.dataio.sync import RQSyncService, SyncJobManager, SyncRequest, build_sync_plan
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
            "raw_open": [10.0] * 8,
            "raw_high": [10.5] * 8,
            "raw_low": [9.5] * 8,
            "raw_close": [10.0] * 8,
            "volume": [1000.0] * 8,
            "amount": [10000.0] * 8,
        }
    )
    return income, balance, bars


def test_runtime_store_deduplicates_and_rejects_empty_overwrite(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    bars_contract = store.catalog.status("rq.bars")
    assert bars_contract["provider_api"] == ("get_price",)
    assert bars_contract["research_role"] == "factor_input"
    assert bars_contract["field_policy"] == "provider_daily_schema_plus_raw_ohlc"
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


def test_runtime_store_reuses_lock_file_left_by_dead_worker(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    lock_path = tmp_path / ".locks" / "rq_is_st.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("999999", encoding="ascii")

    with store.dataset_lock("rq.is_st"):
        assert lock_path.exists()

    assert lock_path.read_text(encoding="ascii") == str(os.getpid())


def test_runtime_store_rejects_an_os_locked_dataset(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    lock_path = tmp_path / ".locks" / "rq_is_st.lock"
    lock_path.parent.mkdir(parents=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    runtime_module._acquire_file_lock(descriptor)
    try:
        with pytest.raises(DataLoadError, match="locked by another process"):
            with store.dataset_lock("rq.is_st"):
                pass
    finally:
        runtime_module._release_file_lock(descriptor)
        os.close(descriptor)


def test_runtime_store_allows_concurrent_readers_and_excludes_a_writer(tmp_path) -> None:
    first = RuntimeStore(tmp_path)
    second = RuntimeStore(tmp_path)

    with first.dataset_read_lock("rq.bars"):
        if os.name == "nt":
            with pytest.raises(DataLoadError, match="already being written"):
                with second.dataset_read_lock("rq.bars", timeout_seconds=0):
                    pass
        else:
            with second.dataset_read_lock("rq.bars"):
                pass
        with pytest.raises(DataLoadError, match="already being written"):
            with second.dataset_lock("rq.bars"):
                pass

    with second.dataset_lock("rq.bars"):
        pass


def test_operations_store_requeues_unfinished_jobs_and_honors_cancellation(tmp_path) -> None:
    operations = OperationsStore(tmp_path)
    resumable_id = operations.create_job({"source": "rq"})
    cancelled_id = operations.create_job({"source": "rq"})
    operations.update_job(resumable_id, status="running", progress=2, total=5)
    operations.update_job(cancelled_id, status="running", progress=1, total=5)
    assert operations.request_cancel(cancelled_id)

    recovered = operations.recover_jobs()

    assert [job["id"] for job in recovered] == [resumable_id]
    assert operations.get_job(resumable_id)["status"] == "queued"
    assert operations.get_job(resumable_id)["progress"] == 2
    assert operations.get_job(cancelled_id)["status"] == "cancelled"


def test_sync_manager_resumes_a_persisted_job_on_startup(monkeypatch, tmp_path) -> None:
    operations = OperationsStore(tmp_path)
    job_id = operations.create_job({"source": "rq"})
    operations.update_job(job_id, status="running", progress=2, total=5)

    def finish_recovered_job(service, recovered_job_id):
        service.operations.update_job(recovered_job_id, status="succeeded", progress=5, total=5)
        return service.operations.get_job(recovered_job_id)

    monkeypatch.setattr(RQSyncService, "run", finish_recovered_job)
    manager = SyncJobManager(tmp_path)
    try:
        manager.shutdown()
    finally:
        recovered = operations.get_job(job_id)

    assert recovered is not None
    assert recovered["status"] == "succeeded"
    assert recovered["progress"] == 5


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
        self.bar_symbols: list[str] = []

    def instruments(self, snapshot_date: str, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "snapshot_date": [pd.Timestamp(snapshot_date)],
                "symbol": ["000001.SZ"],
                "asset_type": ["CS"],
                "name": ["Example"],
                "listed_date": [pd.Timestamp("1991-01-01")],
                "de_listed_date": [pd.NaT],
                "status": ["Active"],
                "is_st": [False],
                "industry": ["Bank"],
            }
        )

    def daily_bars(self, symbols, *args, **kwargs) -> pd.DataFrame:
        self.bar_symbols = list(symbols)
        return self.bars.copy()

    def financials(self, symbols, fields, *args, **kwargs) -> pd.DataFrame:
        source = self.income if set(fields) == set(INCOME_FIELDS) else self.balance
        return source.copy()

    def market_state_chunks(self, symbols, start, end, **kwargs):
        dates = pd.date_range(start, end, freq="B")
        rows = [(date, symbol) for date in dates for symbol in symbols]
        paused = pd.DataFrame(rows, columns=["date", "symbol"])
        paused["paused"] = False
        is_st = pd.DataFrame(rows, columns=["date", "symbol"])
        is_st["is_st"] = False
        yield paused, is_st

    def daily_factor_chunks(self, symbols, fields, start, end, **kwargs):
        rows = [
            (date, symbol, field, 1.0)
            for date in pd.date_range(start, end, freq="B")
            for symbol in symbols
            for field in fields
        ]
        yield pd.DataFrame(rows, columns=["date", "symbol", "field", "value"])

    def index_components(self, indexes, dates, **kwargs):
        return pd.DataFrame(
            [(date, index_symbol, "000001.SZ") for date in dates for index_symbol in indexes],
            columns=["date", "index_symbol", "symbol"],
        )


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


def test_default_sync_scope_resolves_all_a_shares_from_rq_instruments(tmp_path) -> None:
    request = SyncRequest(
        datasets=["instruments", "bars"],
        start="2025-01-01",
        end="2025-03-31",
        force=True,
    )
    preview = build_sync_plan(request, root=tmp_path)
    assert preview["scope"] == "a_share_research"
    assert preview["symbol_source"] == "rq_template:rq.a_share_research"
    assert preview["symbols_resolved"] is False
    assert preview["symbol_count"] is None
    assert preview["estimated_batches"] is None

    class AllAShareAcquirer(_FakeAcquirer):
        def instruments(self, snapshot_date: str, **kwargs) -> pd.DataFrame:
            first = super().instruments(snapshot_date, **kwargs)
            second = first.copy()
            second["symbol"] = "600000.SH"
            second["name"] = "Second"
            return pd.concat([first, second], ignore_index=True)

    acquirer = AllAShareAcquirer()
    operations = OperationsStore(tmp_path)
    job_id = operations.create_job(request.model_dump(mode="json"))
    result = RQSyncService(tmp_path, acquirer=acquirer).run(job_id)

    assert result["status"] == "succeeded"
    assert acquirer.bar_symbols == ["000001.SZ", "600000.SH"]
    assert set(RuntimeStore(tmp_path).read("rq.instruments")["symbol"]) == {
        "000001.SZ",
        "600000.SH",
    }


def test_etf_template_resolves_only_etfs_and_uses_daily_defaults(tmp_path) -> None:
    request = SyncRequest(
        template_id="rq.etf_daily",
        start="2025-01-01",
        end="2025-03-31",
        force=True,
    )
    assert request.datasets == ["instruments", "bars", "market-state"]
    preview = build_sync_plan(request, root=tmp_path)
    assert preview["scope"] == "etfs"
    assert preview["template"]["instrument_types"] == ("ETF",)

    class ETFDataAcquirer(_FakeAcquirer):
        def instruments(self, snapshot_date: str, **kwargs) -> pd.DataFrame:
            assert kwargs == {"instrument_types": ("ETF",), "market": "cn"}
            frame = super().instruments(snapshot_date, **kwargs)
            frame["symbol"] = "510300.SH"
            frame["asset_type"] = "ETF"
            return frame

        def daily_bars(self, symbols, *args, **kwargs) -> pd.DataFrame:
            frame = super().daily_bars(symbols, *args, **kwargs)
            frame["symbol"] = "510300.SH"
            return frame

    acquirer = ETFDataAcquirer()
    operations = OperationsStore(tmp_path)
    job_id = operations.create_job(request.model_dump(mode="json"))
    result = RQSyncService(tmp_path, acquirer=acquirer).run(job_id)

    assert result["status"] == "succeeded"
    assert acquirer.bar_symbols == ["510300.SH"]
    instruments = RuntimeStore(tmp_path).read("rq.instruments")
    assert instruments[["symbol", "asset_type"]].to_dict("records") == [
        {"symbol": "510300.SH", "asset_type": "ETF"}
    ]


def test_template_rejects_unsupported_dataset_combination() -> None:
    with pytest.raises(ValueError, match="does not support datasets"):
        SyncRequest(template_id="rq.etf_daily", datasets=["fundamentals"])


def test_sync_plan_reuses_cached_rq_instrument_scope(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    frame = _FakeAcquirer().instruments("2025-03-31")
    store.write("rq.instruments", frame)

    plan = build_sync_plan(
        SyncRequest(datasets=["bars"], start="2025-01-01", end="2025-03-31"),
        root=tmp_path,
    )

    assert plan["symbol_source"] == "cached_rq_instruments"
    assert plan["symbols_resolved"] is True
    assert plan["symbol_count"] == 1
    assert plan["symbols"] == ["000001.SZ"]
    assert plan["estimated_batches"] == 2


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
    income_step = next(item for item in plan["steps"] if item["dataset"] == "rq.financials.income")
    assert bar_step["mode"] == "incremental"
    assert bar_step["start"] == "2025-03-23"
    assert income_step["mode"] == "revision_lookback"
    assert income_step["start_quarter"] == "2023q2"


def test_incremental_bars_backfill_new_symbols_from_requested_start(tmp_path) -> None:
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
                    "raw_open": [10.0] * len(symbols),
                    "raw_high": [10.5] * len(symbols),
                    "raw_low": [9.5] * len(symbols),
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


def test_new_factor_fields_and_indexes_backfill_independently(tmp_path) -> None:
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
            datasets=["daily-factors", "index-components"],
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
            "prev_close": [10.0] * len(index),
            "num_trades": [12] * len(index),
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


def test_rq_acquirer_filters_b_shares_without_filtering_etfs() -> None:
    class InstrumentRQModule(_FakeRQModule):
        def all_instruments(self, *, type, market):
            assert market == "cn"
            symbols = (
                ["600000.XSHG", "900901.XSHG", "200002.XSHE"] if type == "CS" else ["510300.XSHG"]
            )
            return pd.DataFrame(
                {
                    "order_book_id": symbols,
                    "symbol": symbols,
                    "listed_date": pd.Timestamp("2020-01-01"),
                }
            )

    module = InstrumentRQModule()
    client = RQDataClient(
        RQDataConfig(user="demo", password="secret", host="example:16011"),
        module=module,
    )
    frame = RQAcquirer(client, retries=1).instruments(
        "2025-01-02",
        instrument_types=("CS", "ETF"),
    )

    assert frame[["symbol", "asset_type"]].to_dict("records") == [
        {"symbol": "600000.SH", "asset_type": "CS"},
        {"symbol": "510300.SH", "asset_type": "ETF"},
    ]


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
    assert {"raw_open", "raw_high", "raw_low", "raw_close"}.issubset(bars)
    assert "prev_close" in bars
    assert "num_trades" in bars
    assert not financials.empty


def test_rq_acquirer_rejects_adjusted_and_raw_bar_key_mismatch() -> None:
    class MismatchedBarsModule(_FakeRQModule):
        def get_price(self, order_book_ids, **kwargs):
            frame = super().get_price(order_book_ids, **kwargs)
            return frame.iloc[:-1] if kwargs["adjust_type"] == "none" else frame

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


def test_rq_acquirer_batches_market_state_and_daily_factors() -> None:
    class ResearchRQModule(_FakeRQModule):
        def __init__(self) -> None:
            super().__init__()
            self.state_calls: list[tuple[str, list[str]]] = []
            self.factor_calls: list[list[str]] = []

        def is_suspended(self, order_book_ids, **kwargs):
            self.state_calls.append(("paused", list(order_book_ids)))
            index = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="B")
            return pd.DataFrame(False, index=index, columns=order_book_ids)

        def is_st_stock(self, order_book_ids, **kwargs):
            self.state_calls.append(("is_st", list(order_book_ids)))
            index = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="B")
            return pd.DataFrame(False, index=index, columns=order_book_ids)

        def get_factor(self, order_book_ids, field, **kwargs):
            self.factor_calls.append(list(order_book_ids))
            index = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="B")
            return pd.DataFrame(1.0, index=index, columns=order_book_ids)

        def index_components(self, index_symbol, date=None):
            return ["000001.XSHE", "600000.XSHG"]

    module = ResearchRQModule()
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
    assert max(len(symbols) for _kind, symbols in module.state_calls) <= 200
    assert set(factors[0]["field"]) == {"market_cap", "roe"}
    assert len(module.factor_calls) == 4
    assert set(components["symbol"]) == {"000001.SZ", "600000.SH"}


def test_rq_acquirer_rejects_paused_and_st_key_mismatch() -> None:
    class MismatchedStateModule(_FakeRQModule):
        def is_suspended(self, order_book_ids, **kwargs):
            index = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="B")
            return pd.DataFrame(False, index=index, columns=order_book_ids)

        def is_st_stock(self, order_book_ids, **kwargs):
            index = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="B")
            return pd.DataFrame(False, index=index, columns=order_book_ids[:-1])

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
        "data.templates",
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


def test_extended_rq_datasets_are_materialized_by_the_same_sync_service(tmp_path) -> None:
    request = SyncRequest(
        datasets=[
            "instruments",
            "bars",
            "market-state",
            "daily-factors",
            "index-components",
        ],
        symbols=["000001.SZ"],
        start="2025-01-01",
        end="2025-03-31",
        force=True,
    )
    operations = OperationsStore(tmp_path)
    job_id = operations.create_job(request.model_dump(mode="json"))

    result = RQSyncService(tmp_path, acquirer=_FakeAcquirer()).run(job_id)

    assert result["status"] == "succeeded", result["error"]
    catalog = DataCatalog(tmp_path)
    for dataset in (
        "rq.paused",
        "rq.is_st",
        "rq.daily_factors",
        "rq.index_components",
    ):
        assert catalog.status(dataset)["status"] == "ready"


def test_completed_date_chunk_survives_a_later_provider_failure(tmp_path) -> None:
    class PartialAcquirer(_FakeAcquirer):
        def daily_bar_chunks(self, symbols, start, end, **kwargs):
            yield self.bars.iloc[:1].copy()
            raise DataLoadError("later date chunk failed")

    request = SyncRequest(
        datasets=["bars"],
        symbols=["000001.SZ"],
        start="2023-01-01",
        end="2025-03-31",
        force=True,
    )
    operations = OperationsStore(tmp_path)
    job_id = operations.create_job(request.model_dump(mode="json"))

    result = RQSyncService(tmp_path, acquirer=PartialAcquirer()).run(job_id)

    assert result["status"] == "failed"
    assert len(RuntimeStore(tmp_path).read("rq.bars")) == 1


def test_gap_validation_detects_incomplete_market_state(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    _, _, bars = _financial_frames()
    store.write("rq.bars", bars)
    store.write(
        "rq.paused",
        pd.DataFrame(
            {
                "date": [bars.iloc[0]["date"]],
                "symbol": ["000001.SZ"],
                "paused": [False],
            }
        ),
    )

    report = validate_dataset(
        "rq.paused",
        tmp_path,
        start_date="2023-04-30",
        as_of_date="2025-03-30",
        fail_on_gap=True,
    )

    assert report["status"] == "failed"
    assert any(issue["code"] == "bar_key_gap" for issue in report["issues"])


def _coverage_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02", "2025-01-02", "2025-01-03", "2025-01-03"]),
            "symbol": ["000001.SZ", "600000.SH"] * 2,
            "open": [10.0, 11.0, 10.1, 11.1],
            "high": [10.5, 11.5, 10.6, 11.6],
            "low": [9.5, 10.5, 9.6, 10.6],
            "close": [10.2, 11.2, 10.3, 11.3],
            "raw_open": [10.0, 11.0, 10.1, 11.1],
            "raw_high": [10.5, 11.5, 10.6, 11.6],
            "raw_low": [9.5, 10.5, 9.6, 10.6],
            "raw_close": [10.0, 11.0, 10.1, 11.1],
            "volume": [1000.0] * 4,
            "amount": [10000.0] * 4,
        }
    )


def test_gap_validation_checks_each_daily_factor_date_range(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    store.write("rq.bars", _coverage_bars())
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

    report = validate_dataset(
        "rq.daily_factors",
        tmp_path,
        start_date="2025-01-02",
        as_of_date="2025-01-03",
        fail_on_gap=True,
    )

    assert report["metrics"]["field_key_coverage"] == {
        "market_cap": 0.5,
        "roe": 0.25,
    }
    assert {issue["code"] for issue in report["issues"]} == {"factor_key_gap"}


def test_instrument_snapshot_is_not_required_at_historical_start_bound(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    store.write(
        "rq.instruments",
        pd.DataFrame(
            {
                "snapshot_date": pd.to_datetime(["2025-01-03"]),
                "symbol": ["000001.SZ"],
                "asset_type": ["CS"],
                "listed_date": pd.to_datetime(["1991-04-03"]),
                "de_listed_date": [pd.NaT],
            }
        ),
    )

    report = validate_dataset(
        "rq.instruments",
        tmp_path,
        start_date="2020-01-01",
        as_of_date="2025-01-03",
        fail_on_gap=True,
    )

    assert report["status"] == "passed"
    assert not any(issue["code"] == "stale_start_date" for issue in report["issues"])


def test_recipe_quality_scope_does_not_mix_other_template_symbols(tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    bars = _coverage_bars()
    store.write("rq.bars", bars)
    store.write(
        "rq.paused",
        bars.loc[bars["symbol"].eq("000001.SZ"), ["date", "symbol"]].assign(paused=False),
    )

    scoped = validate_dataset(
        "rq.paused",
        tmp_path,
        fail_on_gap=True,
        symbols=["000001.SZ"],
    )
    global_report = validate_dataset("rq.paused", tmp_path, fail_on_gap=True)

    assert scoped["status"] == "passed"
    assert scoped["metrics"]["bar_key_coverage"] == 1.0
    assert global_report["status"] == "failed"


def test_large_daily_quality_checks_stream_partitions(monkeypatch, tmp_path) -> None:
    store = RuntimeStore(tmp_path)
    bars = _coverage_bars()
    state = bars[["date", "symbol"]].assign(paused=False)
    factors = pd.concat(
        [
            bars[["date", "symbol"]].assign(field=field, value=1.0)
            for field in ("market_cap", "roe")
        ],
        ignore_index=True,
    )
    store.write("rq.bars", bars)
    store.write("rq.paused", state)
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


def test_validate_all_can_target_only_selected_datasets(tmp_path) -> None:
    RuntimeStore(tmp_path).write("rq.bars", _coverage_bars())

    reports = validate_all(tmp_path, datasets=["rq.bars"])

    assert [report["dataset"] for report in reports] == ["rq.bars"]
    assert reports[0]["status"] == "passed"


def test_cancellation_after_state_chunk_keeps_checkpoint_without_completing_step(
    tmp_path,
) -> None:
    operations = OperationsStore(tmp_path)

    class CancellingStateAcquirer(_FakeAcquirer):
        def market_state_chunks(self, symbols, start, end, **kwargs):
            yield from super().market_state_chunks(symbols, start, end, **kwargs)
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


def test_cancellation_after_factor_chunk_keeps_checkpoint_without_completing_step(
    tmp_path,
) -> None:
    operations = OperationsStore(tmp_path)

    class CancellingFactorAcquirer(_FakeAcquirer):
        def daily_factor_chunks(self, symbols, fields, start, end, **kwargs):
            yield from super().daily_factor_chunks(symbols, fields, start, end, **kwargs)
            operations.request_cancel(job_id)

    request = SyncRequest(
        datasets=["daily-factors"],
        symbols=["000001.SZ", "600000.SH"],
        start="2025-01-02",
        end="2025-01-03",
    )
    job_id = operations.create_job(request.model_dump(mode="json"))

    result = RQSyncService(tmp_path, acquirer=CancellingFactorAcquirer()).run(job_id)

    assert result["status"] == "cancelled"
    assert result["progress"] == 0
    assert RuntimeStore(tmp_path).read("rq.daily_factors").shape[0] == 4
