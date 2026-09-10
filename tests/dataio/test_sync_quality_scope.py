"""Regression cases for the audited RQ sync acceptance gaps."""

import pandas as pd
import pytest

from alphalab.data_sdk.v1 import DataRecipeContext
from alphalab.dataio import DataLoadError
from alphalab.dataio.quality import validate_dataset
from alphalab.dataio.runtime import RuntimeStore
from alphalab.dataio.sync import RQSyncService, SyncRequest, _symbol_sync_groups


def _market(root, *, hole=False):
    dates = pd.bdate_range("2025-01-02", periods=64)
    rows = [(date, symbol) for date in dates for symbol in ("000001.SZ", "600000.SH")]
    bars = pd.DataFrame(rows, columns=["date", "symbol"])
    for column in ("open", "high", "low", "close", "raw_open", "raw_high", "raw_low", "raw_close"):
        bars[column] = 10.0
    bars["volume"] = 1000.0
    bars["amount"] = 10000.0
    paused = bars[["date", "symbol"]].assign(paused=False)
    if hole:
        bars = bars.loc[bars.symbol.eq("000001.SZ") | bars.date.isin(dates[[0, -1]])]
    store = RuntimeStore(root)
    store.write("rq.bars", bars)
    store.write("rq.paused", paused)
    return store, dates, paused


def test_strict_bars_reject_internal_missing_symbol_dates(tmp_path):
    _market(tmp_path, hole=True)
    report = validate_dataset("rq.bars", tmp_path, fail_on_gap=True)
    assert report["status"] == "failed"
    assert report["metrics"]["missing_keys"] == 62


def test_only_explicit_suspension_excuses_missing_bar(tmp_path):
    store, dates, paused = _market(tmp_path, hole=True)
    mask = paused.symbol.eq("600000.SH") & ~paused.date.isin(dates[[0, -1]])
    paused.loc[mask, "paused"] = True
    store.write("rq.paused", paused)
    assert validate_dataset("rq.bars", tmp_path, fail_on_gap=True)["status"] == "passed"
    paused["paused"] = paused["paused"].astype("boolean")
    paused.loc[mask, "paused"] = pd.NA
    store.write("rq.paused", paused)
    assert validate_dataset("rq.bars", tmp_path, fail_on_gap=True)["status"] == "failed"


def test_absent_requested_field_is_not_silently_ignored(tmp_path):
    store, _, paused = _market(tmp_path)
    store.write("rq.daily_factors", paused[["date", "symbol"]].assign(field="market_cap", value=1.0))
    report = validate_dataset("rq.daily_factors", tmp_path, required_fields=["market_cap", "roe"], fail_on_gap=True)
    assert report["status"] == "failed"
    assert report["metrics"]["field_key_coverage"] == {"market_cap": 1.0, "roe": 0.0}


def test_sync_cannot_complete_partial_requested_factor_scope(tmp_path):
    store, dates, paused = _market(tmp_path)
    factors = paused.loc[paused.symbol.eq("000001.SZ"), ["date", "symbol"]].assign(field="market_cap", value=1.0)
    store.write("rq.daily_factors", factors)
    request = SyncRequest(datasets=["daily-factors"], symbols=["000001.SZ", "600000.SH"], start=str(dates[0].date()), end=str(dates[-1].date()), daily_factors=["market_cap"])
    job_id = store.operations.create_job(request.model_dump(mode="json"))
    with pytest.raises(DataLoadError, match="Quality validation failed"):
        RQSyncService(tmp_path)._complete_step(job_id, "rq.daily_factors", 0, 1)
    assert store.operations.get_job(job_id)["progress"] == 0


def test_symbol_backfill_ignores_other_symbols_global_latest(tmp_path):
    store, _, _ = _market(tmp_path)
    groups = _symbol_sync_groups(store, ["rq.bars"], ["000001.SZ"], default_start="2026-01-01", requested_start="2025-01-01", instruments=pd.DataFrame(), force=False, overlap_days=7)
    assert max(groups) < "2026-01-01"
    # Moving the requested history back a year invalidates a late-start watermark.
    groups = _symbol_sync_groups(store, ["rq.bars"], ["000001.SZ"], default_start="2026-01-01", requested_start="2024-01-01", instruments=pd.DataFrame(), force=False, overlap_days=7)
    assert groups == {"2024-01-01": ["000001.SZ"]}


def test_recipe_enforces_requested_fields_after_publication(tmp_path):
    store, _, paused = _market(tmp_path)
    store.write("rq.daily_factors", paused[["date", "symbol"]].assign(field="market_cap", value=1.0))
    context = DataRecipeContext(mode="run", root=tmp_path)
    context.require_coverage("rq.daily_factors", required_fields=["market_cap", "roe"])
    with pytest.raises(ValueError, match="quality validation failed"):
        context.finalize()


def test_listing_interval_excludes_pre_ipo_and_delisted_dates(tmp_path):
    store, dates, paused = _market(tmp_path, hole=True)
    master = pd.DataFrame([{"snapshot_date": dates[-1], "symbol": "600000.SH", "listed_date": dates[-1], "de_listed_date": pd.NaT}])
    store.write("rq.instruments", master)
    assert validate_dataset("rq.bars", tmp_path, fail_on_gap=True)["status"] == "passed"


def test_absent_requested_symbol_fails_even_when_other_bars_are_complete(tmp_path):
    _market(tmp_path)
    report = validate_dataset("rq.bars", tmp_path, symbols=["000002.SZ"], fail_on_gap=True)
    assert report["status"] == "failed"
    assert report["metrics"]["missing_keys"] == 64


@pytest.mark.parametrize("suspended,expected", [(True, "succeeded"), (False, "failed")])
def test_sync_validates_prices_after_final_market_state(tmp_path, suspended, expected):
    store, dates, paused = _market(tmp_path, hole=True)
    missing = paused.symbol.eq("600000.SH") & ~paused.date.isin(dates[[0, -1]])
    paused.loc[missing, "paused"] = suspended
    bars = store.read("rq.bars")

    class Acquirer:
        def daily_bars(self, symbols, start, end, **kwargs):
            return bars

        def market_state_chunks(self, symbols, start, end, **kwargs):
            yield paused, paused[["date", "symbol"]].assign(is_st=False)

    request = SyncRequest(datasets=["bars", "market-state"], symbols=["000001.SZ", "600000.SH"], start=str(dates[0].date()), end=str(dates[-1].date()), force=True)
    job_id = store.operations.create_job(request.model_dump(mode="json"))
    result = RQSyncService(tmp_path, acquirer=Acquirer()).run(job_id)
    assert result["status"] == expected, result.get("error")
