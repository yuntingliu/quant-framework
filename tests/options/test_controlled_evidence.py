"""A controlled family must compare identical observations and untouched labels."""

import numpy as np
import pandas as pd
import pytest

from apps.api.services import options_evidence_service as service
from apps.api.services import options_research_service as research


def test_loss_improvement_has_correct_sign_and_date_clustering():
    rows = [
        {
            "date": str(day),
            "target_return": 0.01 * (day + 1),
            "prediction": 0.0,
            "baseline_prediction": 0.01 * (day + 1),
        }
        for day in range(20)
        for _ in range(5)
    ]
    loss = service.loss_test(rows, 5)
    assert loss["mean"] < 0 and loss["hac_t"] < 0
    assert loss["observations"] == 20
    swapped = [
        {**r, "prediction": r["baseline_prediction"], "baseline_prediction": r["prediction"]}
        for r in rows
    ]
    assert service.loss_test(swapped, 5)["mean"] == pytest.approx(-loss["mean"])


def test_controlled_suite_uses_common_missingness_and_persists_frozen_reports(
    tmp_path, monkeypatch
):
    rng = np.random.default_rng(18)
    days = pd.bdate_range("2024-01-02", periods=210)
    symbols = ["510050.XSHG", "510300.XSHG", "159919.XSHE", "510500.XSHG", "159922.XSHE"]
    features, bars = [], []
    for symbol in symbols:
        price = 3.0
        for index, day in enumerate(days):
            price *= np.exp(rng.normal(0, 0.01))
            bars.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "open": price,
                    "close": price * np.exp(rng.normal(0, 0.002)),
                }
            )
            features.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "option_smirk": rng.normal(),
                    "option_rr25_30": np.nan if index % 19 == 0 else rng.normal(),
                    "option_rv20": rng.uniform(0.1, 0.3),
                    "option_momentum20": rng.normal(),
                }
            )
    f, b = pd.DataFrame(features), pd.DataFrame(bars)
    request = {
        "start": str(days[0].date()),
        "end": str(days[-1].date()),
        "underlyings": symbols,
        "fields": ["option_smirk", "option_rr25_30"],
        "min_train": 60,
        "refit_every": 20,
        "ridge": 1.0,
        "embargo": 1,
        "controls": True,
        "holdout_start": str(days[150].date()),
    }
    monkeypatch.setattr(
        research, "_research_inputs", lambda *_: (f.copy(), b.copy(), symbols, days[0], days[-1])
    )
    result = service.run_suite(request, tmp_path)
    assert result["tests"] == 9 and result["common_rows"] < len(f)
    assert service.read_suite(result["id"], tmp_path) == result
    assert service.run_suite(request, tmp_path) == result
    for horizon in (1, 5, 20):
        rows = [r for r in result["rows"] if r["horizon"] == horizon]
        assert len({r["coverage"]["scored_rows"] for r in rows}) == 1
        reports = [research.read_research(r["run_id"], tmp_path) for r in rows]
        keys = [
            [(p["date"], p["symbol"], p["baseline_prediction"]) for p in r["predictions"]]
            for r in reports
        ]
        assert keys[0] == keys[1] == keys[2]
        assert all(p["training_label_end"] < p["date"] for r in reports for p in r["predictions"])
        assert all(
            0 <= r["loss_test"]["p_bonferroni"] <= 1
            for r in rows
            if r["loss_test"]["p_bonferroni"] is not None
        )
    assert service.suites(tmp_path)["suites"][0]["id"] == result["id"]
    with pytest.raises(ValueError):
        service.run_suite({**request, "controls": False}, tmp_path)
    with pytest.raises(ValueError):
        service.read_suite("../../secret", tmp_path)
