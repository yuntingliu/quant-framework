"""Option recipe integration with the shared store; all source data is synthetic."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from alphalab.data_sdk.v1 import DataRecipeContext, rq
from alphalab.dataio.option_snapshots import option_sync_dates
from alphalab.dataio.quality import validate_dataset
from alphalab.dataio.recipes import (
    builtin_recipe_catalog,
    inspect_data_recipe_source,
    render_builtin_recipe,
)
from alphalab.dataio.runtime import RuntimeStore
from alphalab.options import QualityError

UNDERLYING = "510050.XSHG"


class FakeRQ:
    def __init__(self):
        self.options = self
        self.calls = []
        self.bad = set()
        self.ids = ["10000001", "10000002"]

    def get_trading_dates(self, start, end, market):
        return pd.bdate_range(start, end)

    def get_contracts(self, underlying, trading_date):
        self.calls.append(trading_date)
        return self.ids

    def instruments(self, ids):
        return [
            SimpleNamespace(
                order_book_id=code,
                underlying_order_book_id=UNDERLYING,
                option_type="C" if code.endswith("1") else "P",
                exercise_type="E",
                listed_date="2024-12-01",
                maturity_date="2025-02-26",
            )
            for code in ids
        ]

    def get_price(self, ids, **kwargs):
        dates = pd.bdate_range(kwargs["start_date"], kwargs["end_date"])
        assert kwargs["adjust_type"] in ("none", "pre")
        if ids == UNDERLYING or ids == [UNDERLYING]:
            return pd.DataFrame(
                {
                    "date": dates,
                    "order_book_id": [UNDERLYING] * len(dates),
                    "close": [3.0] * len(dates),
                    "volume": [100.0] * len(dates),
                }
            )
        return pd.DataFrame(
            [
                {
                    "date": pd.Timestamp(day),
                    "order_book_id": code,
                    "open": 0.1,
                    "high": 0.12,
                    "low": 0.08,
                    "close": 0.1,
                    "settlement": 0.1,
                    "volume": 100.0,
                    "open_interest": 200.0,
                }
                for day in dates
                for code in (ids[:1] if str(day.date()) in self.bad else ids)
            ]
        ).set_index(["order_book_id", "date"])

    def get_contract_property(self, ids, **kwargs):
        return pd.DataFrame(
            [
                {
                    "trading_date": day,
                    "order_book_id": code,
                    "strike_price": 2.94,
                    "contract_multiplier": 10220.0,
                }
                for day in pd.bdate_range(kwargs["start_date"], kwargs["end_date"])
                for code in ids
            ]
        ).set_index(["order_book_id", "trading_date"])

    def get_yield_curve(self, start_date, end_date, market):
        dates = pd.bdate_range(start_date, end_date)
        return pd.DataFrame({"1M": [0.01] * len(dates), "1Y": [0.02] * len(dates)}, index=dates)


def run_recipe(root, fake, monkeypatch, overlap=0, start="2025-01-06", end="2025-01-08"):
    monkeypatch.setattr(rq, "_connected_module", fake)
    source = render_builtin_recipe("rq.etf_options", start=start, end=end)
    source = source.replace("OVERLAP_SESSIONS = 3", f"OVERLAP_SESSIONS = {overlap}")
    source = source.replace("INCLUDE_RESEARCH = True", "INCLUDE_RESEARCH = False")
    source = source.replace("DAILY_MASTER_DISCOVERY = True", "DAILY_MASTER_DISCOVERY = False")
    scope = {}
    exec(source, scope)
    context = DataRecipeContext(mode="run", root=str(root))
    scope["etf_options"](context)
    context.finalize()
    return context.result()


def test_recipe_plan_is_offline_and_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "_connected_module", object())
    source = render_builtin_recipe("rq.etf_options", start="2025-01-06", end="2025-01-08")
    assert inspect_data_recipe_source(source).matched_template_id == "rq.etf_options"
    for operation in (
        "rq.options.get_contracts",
        "rq.options.get_contract_property",
        "rq.get_price",
        "rq.instruments",
    ):
        assert operation in source
    scope = {}
    exec(source, scope)
    context = DataRecipeContext(mode="plan", root=str(tmp_path))
    scope["etf_options"](context)
    assert context.result()["planned"][0]["dataset"] == "rq.option_snapshots"
    assert any(
        row["id"] == "rq.etf_options"
        for row in builtin_recipe_catalog(start="2025-01-06", end="2025-01-08")
    )


def test_short_sync_rebases_retained_market_and_revalidates_prior_features(monkeypatch):
    from alphalab.dataio import option_features

    monkeypatch.setattr(rq, "_connected_module", FakeRQ())
    source = render_builtin_recipe("rq.etf_options", start="2025-01-08", end="2025-01-08")
    for flag in ("INCLUDE_OPTIONS", "INCLUDE_RATES", "INCLUDE_UNDERLYINGS"):
        source = source.replace(f"{flag} = True", f"{flag} = False")
    scope, requests, checks = {}, {}, []
    exec(source, scope)
    scope["_sync_etf_market"] = lambda context, **kwargs: requests.update(market=kwargs)
    scope["_sync_etf_intraday"] = lambda context, **kwargs: requests.update(intraday=kwargs)

    def features(context, **kwargs):
        requests["features"] = kwargs
        return iter(())

    monkeypatch.setattr(option_features, "iter_feature_batches", features)

    class Context:
        mode = "run"

        def iter_partitions(self, dataset, **kwargs):
            assert dataset == "rq.bars" and kwargs["columns"] == ("date", "symbol")
            # An unrelated older stock must not expand this ETF refresh.
            yield pd.DataFrame(
                {
                    "date": pd.to_datetime(["2022-01-03", "2025-01-10", "2000-01-03"]),
                    "symbol": ["510050.SH", "510050.SH", "430047.BJ"],
                }
            )

        def read(self, dataset):
            assert dataset == "research.option_features"
            return pd.DataFrame(
                {"date": pd.to_datetime(["2023-01-03", "2025-01-10"]), "symbol": UNDERLYING}
            )

        def output(self, *args):
            pass

        def require_coverage(self, dataset, **kwargs):
            checks.append(dataset)

    scope["etf_options"](Context())
    assert (requests["market"]["start"], requests["market"]["end"]) == ("2022-01-03", "2025-01-10")
    assert (requests["features"]["start"], requests["features"]["end"]) == (
        "2023-01-03",
        "2025-01-10",
    )
    assert set(checks) == {
        "rq.option_rates",
        "rq.option_underlyings",
        "rq.option_etf_5m",
        "research.option_intraday_variance",
        "research.option_features",
    }


def test_underlying_history_does_not_require_quotes_before_etf_listing(tmp_path, monkeypatch):
    later = "510300.XSHG"

    class Listed(FakeRQ):
        def instruments(self, ids):
            values = super().instruments(ids)
            for value in values:
                value.listed_date = "2025-01-07" if value.order_book_id == later else "2025-01-06"
            return values

        def get_price(self, ids, **kwargs):
            return pd.DataFrame(
                [
                    {"date": day, "order_book_id": symbol, "close": 3.0, "volume": 100.0}
                    for symbol in ids
                    for day in pd.bdate_range(kwargs["start_date"], kwargs["end_date"])
                    if day >= pd.Timestamp("2025-01-07" if symbol == later else "2025-01-06")
                ]
            )

    monkeypatch.setattr(rq, "_connected_module", Listed())
    source = render_builtin_recipe(
        "rq.etf_options", start="2025-01-06", end="2025-01-08", symbols=[UNDERLYING, later]
    )
    for flag in ("INCLUDE_OPTIONS", "INCLUDE_RATES", "INCLUDE_RESEARCH"):
        source = source.replace(f"{flag} = True", f"{flag} = False")
    scope = {}
    exec(source, scope)
    context = DataRecipeContext(mode="run", root=str(tmp_path))
    scope["etf_options"](context)
    context.finalize()
    frame = RuntimeStore(tmp_path).read("rq.option_underlyings")
    assert len(frame) == 5
    assert frame.loc[frame.symbol.eq(later)].date.min() == pd.Timestamp("2025-01-07")


def test_incremental_adjusted_history_rebases_retained_dates(tmp_path, monkeypatch):
    fake = FakeRQ()
    run_recipe(tmp_path, fake, monkeypatch)
    original = fake.get_price
    requests = []

    def after_distribution(ids, **kwargs):
        frame = original(ids, **kwargs)
        if ids == [UNDERLYING] and kwargs["adjust_type"] == "pre":
            requests.append((kwargs["start_date"], kwargs["end_date"]))
            frame["close"] *= 0.9
        return frame

    fake.get_price = after_distribution
    run_recipe(tmp_path, fake, monkeypatch, start="2025-01-08", end="2025-01-09")
    assert requests == [("2025-01-06", "2025-01-09")]
    history = RuntimeStore(tmp_path).read("rq.option_underlyings")
    assert len(history) == 4
    assert history.adjusted_close.eq(2.7).all()
    assert history.adjusted_close.pct_change().dropna().eq(0).all()


def test_missing_etf_history_cannot_be_published(tmp_path, monkeypatch):
    fake = FakeRQ()
    original = fake.get_price

    def missing_session(ids, **kwargs):
        frame = original(ids, **kwargs)
        return frame.iloc[1:] if ids == [UNDERLYING] else frame

    fake.get_price = missing_session
    with pytest.raises(ValueError, match="missing requested"):
        run_recipe(tmp_path, fake, monkeypatch)
    assert not RuntimeStore(tmp_path).catalog.files("rq.option_underlyings")


def test_partial_failure_resume_overlap_and_daily_adjustment(tmp_path, monkeypatch):
    fake = FakeRQ()
    fake.bad.add("2025-01-08")
    with pytest.raises(QualityError, match="coverage"):
        run_recipe(tmp_path, fake, monkeypatch)
    store = RuntimeStore(tmp_path)
    assert len(store.read("rq.option_snapshots")) == 4
    report = validate_dataset("rq.option_snapshots", tmp_path, fail_on_gap=True)
    assert report["status"] == "failed"
    assert report["metrics"]["missing_sessions"][UNDERLYING] == ["2025-01-08"]
    fake.bad.clear()
    fake.calls.clear()
    run_recipe(tmp_path, fake, monkeypatch)
    assert fake.calls == ["2025-01-08"]
    frame = store.read("rq.option_snapshots")
    assert len(frame) == 6 and frame.contract_multiplier.eq(10220).all()
    assert frame.strike_price.eq(2.94).all()
    fake.calls.clear()
    run_recipe(tmp_path, fake, monkeypatch)
    assert not fake.calls
    run_recipe(tmp_path, fake, monkeypatch, overlap=1)
    assert fake.calls == ["2025-01-08"]
    assert len(store.read("rq.option_snapshots")) == 6


def test_invalid_refresh_cannot_replace_valid_partition(tmp_path, monkeypatch):
    fake = FakeRQ()
    run_recipe(tmp_path, fake, monkeypatch)
    store = RuntimeStore(tmp_path)
    before = store.read("rq.option_snapshots")
    invalid = before[before.date.eq(pd.Timestamp("2025-01-08"))].iloc[:1]
    with pytest.raises(QualityError):
        store.write("rq.option_snapshots", invalid)
    pd.testing.assert_frame_equal(store.read("rq.option_snapshots"), before)
    assert validate_dataset("rq.option_snapshots", tmp_path, fail_on_gap=True)["status"] == "passed"


def test_revision_replaces_full_day_and_removes_old_contracts(tmp_path, monkeypatch):
    fake = FakeRQ()
    run_recipe(tmp_path, fake, monkeypatch)
    fake.ids = ["10000001"]
    run_recipe(tmp_path, fake, monkeypatch, overlap=1)
    frame = RuntimeStore(tmp_path).read("rq.option_snapshots")
    assert len(frame) == 5
    assert len(frame[frame.date.eq(pd.Timestamp("2025-01-08"))]) == 1


def test_gap_planner_does_not_skip_a_missing_middle_day(tmp_path, monkeypatch):
    fake = FakeRQ()
    run_recipe(tmp_path, fake, monkeypatch)
    frame = RuntimeStore(tmp_path).read("rq.option_snapshots")
    frame = frame[frame.date.ne(pd.Timestamp("2025-01-07"))]
    assert option_sync_dates(
        frame, pd.bdate_range("2025-01-06", "2025-01-08"), UNDERLYING, overlap=0
    ) == ["2025-01-07"]


def test_manifest_tampering_and_symbol_path_injection_rejected(tmp_path, monkeypatch):
    fake = FakeRQ()
    run_recipe(tmp_path, fake, monkeypatch)
    store = RuntimeStore(tmp_path)
    frame = store.read("rq.option_snapshots")
    frame["expected_contracts"] = json.dumps(["missing"])
    with pytest.raises(QualityError):
        store.write("rq.option_snapshots", frame)
    frame["underlying"] = "../../elsewhere"
    with pytest.raises(QualityError):
        store.write("rq.option_snapshots", frame)


def test_zero_volume_reference_condition_reaches_quality_report(tmp_path, monkeypatch):
    fake = FakeRQ()
    run_recipe(tmp_path, fake, monkeypatch)
    store = RuntimeStore(tmp_path)
    frame = store.read("rq.option_snapshots")
    day = frame.loc[frame.date.eq(pd.Timestamp("2025-01-08"))].copy()
    day.loc[day.index[0], "volume"] = 0
    day.loc[day.index[0], "close"] = 0.07
    day.loc[day.index[0], "open"] = 0.06
    store.write("rq.option_snapshots", day)
    report = validate_dataset("rq.option_snapshots", tmp_path, fail_on_gap=True)
    assert report["status"] == "passed"
    assert report["metrics"]["zero_volume_close_outside_range"] == 1
    assert report["metrics"]["zero_volume_open_outside_range"] == 1
    assert any(
        issue["code"] == "non_trading_reference_open" and issue["severity"] == "warning"
        for issue in report["issues"]
    )
    assert any(
        issue["code"] == "non_trading_reference_close" and issue["severity"] == "warning"
        for issue in report["issues"]
    )


def test_option_publications_do_not_rescan_the_entire_history_per_day(tmp_path, monkeypatch):
    fake = FakeRQ()
    run_recipe(tmp_path, fake, monkeypatch)
    store = RuntimeStore(tmp_path)
    original = store.catalog.files
    calls = []

    def inventory(dataset):
        calls.append(dataset)
        return original(dataset)

    monkeypatch.setattr(store.catalog, "files", inventory)
    frame = store.read("rq.option_snapshots")
    calls.clear()
    for _, day in frame.groupby("date"):
        store.write("rq.option_snapshots", day)
    assert calls.count("rq.option_snapshots") == 1
    status = store.operations.dataset_status("rq.option_snapshots")
    assert status["rows"] == 6 and status["files"] == 3


def test_anomalous_close_requires_complete_reconciled_trade_evidence(tmp_path, monkeypatch):
    from alphalab.dataio.option_rq import recover_daily_closes

    fake = FakeRQ()
    run_recipe(tmp_path, fake, monkeypatch)
    frame = RuntimeStore(tmp_path).read("rq.option_snapshots")
    day = frame.loc[frame.date.eq(pd.Timestamp("2025-01-08"))].copy()
    day.loc[day.index[0], "close"] = 0.04
    times = pd.date_range("2025-01-08 09:35", periods=24, freq="5min").union(
        pd.date_range("2025-01-08 13:05", periods=24, freq="5min")
    )
    minute = pd.DataFrame(
        {
            "datetime": times,
            "order_book_id": day.order_book_id.iloc[0],
            "open": 0.1,
            "high": 0.12,
            "low": 0.08,
            "close": 0.1,
            "volume": 0.0,
        }
    )
    minute.loc[0, "volume"] = 100.0
    following = pd.DataFrame(
        {
            "order_book_id": [day.order_book_id.iloc[0]],
            "date": [pd.Timestamp("2025-01-09")],
            "contract_multiplier": [25550.0],
            "strike_price": [1.176],
        }
    )
    fixed = recover_daily_closes(day, minute, following)
    assert fixed.close.iloc[0] == 0.1 and fixed.source_close.iloc[0] == 0.04
    store = RuntimeStore(tmp_path)
    store.write("rq.option_snapshots", fixed)
    report = validate_dataset("rq.option_snapshots", tmp_path, fail_on_gap=True)
    assert report["status"] == "passed" and report["metrics"]["reconstructed_closes"] == 1
    assert any(i["code"] == "audited_intraday_close_recovery" for i in report["issues"])
    with pytest.raises(ValueError, match="48-bar"):
        recover_daily_closes(day, minute.iloc[:-1], following)
    bad = minute.copy()
    bad.loc[0, "volume"] = 99.0
    with pytest.raises(ValueError, match="reconcile"):
        recover_daily_closes(day, bad, following)
    fixed.loc[fixed.index[0], "close"] = 0.11
    with pytest.raises(QualityError, match="evidence"):
        store.write("rq.option_snapshots", fixed)


def test_parallel_underlying_reads_keep_shared_publication_exclusive(tmp_path, monkeypatch):
    import time

    class Multi(FakeRQ):
        def all_instruments(self, type, date=None):
            self.calls.append(date)
            return pd.DataFrame(
                [
                    {
                        "order_book_id": symbol[:6] + kind,
                        "underlying_order_book_id": symbol,
                        "listed_date": "2025-01-07" if symbol == "510300.XSHG" else "2024-12-01",
                        "de_listed_date": "2025-02-26",
                    }
                    for symbol in (UNDERLYING, "510300.XSHG")
                    for kind in ("0001", "0002")
                ]
            )

        def get_contracts(self, underlying, trading_date):
            return [underlying[:6] + "0001", underlying[:6] + "0002"]

        def get_price(self, ids, **kwargs):
            if isinstance(ids, str) or all("." in code for code in ids):
                symbols = [ids] if isinstance(ids, str) else ids
                return pd.DataFrame(
                    [
                        {"date": day, "order_book_id": symbol, "close": 3.0, "volume": 100.0}
                        for day in pd.bdate_range(kwargs["start_date"], kwargs["end_date"])
                        for symbol in symbols
                    ]
                )
            return super().get_price(ids, **kwargs)

        def instruments(self, ids):
            values = super().instruments(ids)
            for item in values:
                item.underlying_order_book_id = item.order_book_id[:6] + ".XSHG"
            return values

    fake = Multi()
    monkeypatch.setattr(rq, "_connected_module", fake)
    original = RuntimeStore._partitions

    def slower(self, dataset, frame):
        if dataset == "rq.option_snapshots":
            time.sleep(0.02)
        return original(self, dataset, frame)

    monkeypatch.setattr(RuntimeStore, "_partitions", slower)
    source = render_builtin_recipe(
        "rq.etf_options", start="2025-01-06", end="2025-01-08", symbols=[UNDERLYING, "510300.XSHG"]
    )
    source = source.replace("INCLUDE_RESEARCH = True", "INCLUDE_RESEARCH = False")
    scope = {}
    exec(source, scope)
    context = DataRecipeContext(mode="run", root=str(tmp_path))
    scope["etf_options"](context)
    frame = RuntimeStore(tmp_path).read("rq.option_snapshots")
    assert len(frame) == 10 and frame.underlying.nunique() == 2
    later = frame.loc[frame.underlying.eq("510300.XSHG")]
    assert later.date.min() == pd.Timestamp("2025-01-07")
    assert all(
        json.loads(value) == ["2025-01-07", "2025-01-08"] for value in later.expected_sessions
    )
    assert fake.calls == [None]
    assert validate_dataset("rq.option_snapshots", tmp_path, fail_on_gap=True)["status"] == "passed"


def test_reference_open_audit_certifies_hlcv_without_inventing_an_open(tmp_path, monkeypatch):
    from alphalab.dataio.option_rq import audit_daily_opens

    fake = FakeRQ()
    run_recipe(tmp_path, fake, monkeypatch)
    store = RuntimeStore(tmp_path)
    frame = store.read("rq.option_snapshots")
    day = frame.loc[frame.date.eq(pd.Timestamp("2025-01-08"))].copy()
    day.loc[day.index[0], "open"] = 0.13
    times = pd.date_range("2025-01-08 09:35", periods=24, freq="5min").union(
        pd.date_range("2025-01-08 13:05", periods=24, freq="5min")
    )
    minute = pd.DataFrame(
        {
            "datetime": times,
            "order_book_id": day.order_book_id.iloc[0],
            "open": 0.1,
            "high": 0.12,
            "low": 0.08,
            "close": 0.1,
            "volume": 0.0,
        }
    )
    minute.loc[0, "volume"] = 100.0
    audited = audit_daily_opens(day, minute)
    assert audited.open.iloc[0] == 0.13
    store.write("rq.option_snapshots", audited)
    report = validate_dataset("rq.option_snapshots", tmp_path, fail_on_gap=True)
    assert report["status"] == "passed"
    assert report["metrics"]["audited_reference_opens"] == 1
    assert any(i["code"] == "audited_reference_open" for i in report["issues"])
    # Reused DataFrame labels must not let one contract's audit certify another.
    reused_index = audited.copy()
    reused_index.loc[reused_index.index[1], "open"] = 0.14
    reused_index.index = [0] * len(reused_index)
    with pytest.raises(QualityError, match="OHLC"):
        store.write("rq.option_snapshots", reused_index)
    with pytest.raises(ValueError, match="48-bar"):
        audit_daily_opens(day, minute.iloc[:-1])
    wrong_volume = minute.copy()
    wrong_volume.loc[0, "volume"] = 99
    with pytest.raises(ValueError, match="HLCV"):
        audit_daily_opens(day, wrong_volume)
    wrong_high = minute.copy()
    wrong_high.loc[0, "high"] = 0.14
    with pytest.raises(ValueError, match="HLCV"):
        audit_daily_opens(day, wrong_high)
    quarantined = audit_daily_opens(day, wrong_high, quarantine_unverified=True)
    assert quarantined.open.iloc[0] == day.open.iloc[0]
    assert pd.isna(quarantined.open_audit.iloc[0])
    store.write("rq.option_snapshots", quarantined)
    q_report = validate_dataset("rq.option_snapshots", tmp_path, fail_on_gap=True)
    assert q_report["status"] == "passed" and q_report["metrics"]["quarantined_quotes"] == 1
    assert any(i["code"] == "quarantined_source_quotes" for i in q_report["issues"])
    audited.loc[audited.index[0], "open"] = 0.14
    with pytest.raises(QualityError, match="evidence"):
        store.write("rq.option_snapshots", audited)
