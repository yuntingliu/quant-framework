from __future__ import annotations

import pytest

from dashboard.backend.services import backtest_analytics_service as service


def test_signal_diagnostics_use_saved_stage_outputs(monkeypatch):
    scores = {f"S{index}": float(index) for index in range(10)}
    forward = {f"S{index}": float(index) / 100.0 for index in range(10)}
    monkeypatch.setattr(
        service,
        "get_backtest",
        lambda _backtest_id: {
            "id": "run-1",
            "executions": [
                {
                    "signal_date": "2025-01-31",
                    "stage_outputs": {
                        "universe": {"symbols": list(scores)},
                        "selection": {"scores": scores, "selected": ["S8", "S9"]},
                        "timing": {"exposure": 0.8},
                    },
                    "selection_forward_returns": forward,
                }
            ],
        },
    )

    result = service.analyze_signal_diagnostics("run-1")

    assert result["periods"] == 1
    assert result["evidence_periods"] == 1
    assert result["summary"]["mean_ic"] == pytest.approx(1.0)
    assert result["summary"]["average_coverage"] == 1.0
    assert result["rows"][0]["quantile_spread"] > 0
    assert result["rows"][0]["timing_exposure"] == 0.8
