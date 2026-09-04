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
                        "selection": {"scores": scores, "selected": ["S8", "S9"]},
                    },
                    "eligibility": {"eligible_count": len(scores)},
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
    assert "timing_exposure" not in result["rows"][0]


def test_signal_diagnostics_prefer_compact_sdk_evidence(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_backtest",
        lambda _backtest_id: {
            "id": "sdk-run",
            "run_diagnostics": {
                "signal_evidence": {
                    "rows": [
                        {
                            "signal_date": "2025-01-31",
                            "horizon_end_date": "2025-02-28",
                            "universe_count": 5000,
                            "scored_count": 4800,
                            "selected_count": 20,
                            "coverage": 0.96,
                            "ic": 0.12,
                            "ic_observations": 4700,
                            "selection_turnover": None,
                        }
                    ]
                }
            },
            "executions": [{"stage_outputs": {"selection": {"scores": {"BAD": 1.0}}}}],
        },
    )

    result = service.analyze_signal_diagnostics("sdk-run")

    assert result["periods"] == 1
    assert result["evidence_periods"] == 1
    assert result["summary"]["mean_ic"] == pytest.approx(0.12)
    assert result["rows"][0]["scored_count"] == 4800
    assert "scores" not in result["rows"][0]


def test_empty_sdk_signal_evidence_does_not_use_legacy_stage_outputs(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_backtest",
        lambda _backtest_id: {
            "id": "sdk-empty",
            "run_diagnostics": {"signal_evidence": {"rows": []}},
            "executions": [
                {
                    "stage_outputs": {
                        "selection": {
                            "scores": {"A": 1.0, "B": 2.0, "C": 3.0},
                            "selected": ["C"],
                        }
                    },
                    "selection_forward_returns": {"A": 0.01, "B": 0.02, "C": 0.03},
                }
            ],
        },
    )

    result = service.analyze_signal_diagnostics("sdk-empty")

    assert result["periods"] == 0
    assert result["rows"] == []
    assert result["warning_code"] == "NO_SIGNAL_EVIDENCE"
    assert result["warning"] == "该回测没有产生可用信号截面。"


def test_two_asset_signal_evidence_reports_insufficient_cross_section(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_backtest",
        lambda _backtest_id: {
            "id": "pair-run",
            "run_diagnostics": {
                "signal_evidence": {
                    "rows": [
                        {
                            "signal_date": "2025-01-31",
                            "universe_count": 2,
                            "scored_count": 2,
                            "selected_count": 1,
                            "coverage": 1.0,
                            "ic": None,
                            "ic_observations": 2,
                        },
                        {
                            "signal_date": "2025-02-28",
                            "universe_count": 2,
                            "scored_count": 2,
                            "selected_count": 1,
                            "coverage": 1.0,
                            "ic": None,
                            "ic_observations": 2,
                        },
                    ]
                }
            },
        },
    )

    result = service.analyze_signal_diagnostics("pair-run")

    assert result["periods"] == 2
    assert result["evidence_periods"] == 0
    assert result["warning_code"] == "INSUFFICIENT_CROSS_SECTION"
    assert "不足 3 只" in result["warning"]
