"""Offline preflight for the opt-in paid extension without provider access."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.evaluate_agent_benchmark import main

_ROOT = Path(__file__).resolve().parents[2]
_PAID_PLAN = _ROOT / "benchmark" / "evaluation" / "paid_extension_plan.json"


def _plan(tmp_path, **overrides):
    plan = {
        "experiment_id": "paid-preflight",
        "split": "dev",
        "source_class": "live",
        "task_ids": ["task-a"],
        "repetitions": 1,
        "configurations": {"B": {"base_config": "B", "limits": {"cost_usd": 0.5}}},
    }
    plan.update(overrides)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_preflight_is_disabled_by_default_and_does_not_read_the_plan(tmp_path, capsys):
    missing = tmp_path / "absent.json"
    assert main(["preflight-paid", "--plan", str(missing)]) == 0
    assert "disabled" in capsys.readouterr().out
    assert not missing.exists()


def test_preflight_refuses_stub_adapters(tmp_path, capsys):
    plan = _plan(tmp_path)
    assert main([
        "preflight-paid", "--enable", "--plan", str(plan), "--total-budget-usd", "5",
    ]) == 2
    assert "verified real adapters" in capsys.readouterr().err


def test_preflight_rejects_budget_shortfall_and_non_live_plan(tmp_path, capsys):
    plan = _plan(tmp_path)
    assert main([
        "preflight-paid", "--enable", "--plan", str(plan),
        "--total-budget-usd", "0.1", "--runner-supports-paid",
    ]) == 2
    assert "exceeds" in capsys.readouterr().err
    synthetic = _plan(tmp_path, source_class="synthetic")
    assert main([
        "preflight-paid", "--enable", "--plan", str(synthetic),
        "--total-budget-usd", "5", "--runner-supports-paid",
    ]) == 2
    assert "source_class 'live'" in capsys.readouterr().err


def test_preflight_approves_a_fully_covered_plan(tmp_path, capsys):
    plan = _plan(tmp_path, task_ids=["a", "b", "c"], repetitions=2)
    assert main([
        "preflight-paid", "--enable", "--plan", str(plan),
        "--total-budget-usd", "5", "--runner-supports-paid",
    ]) == 0
    assert "approved: 6 trials" in capsys.readouterr().out


def test_preflight_covers_the_checked_in_paid_plan(capsys):
    assert main([
        "preflight-paid", "--enable", "--plan", str(_PAID_PLAN),
        "--total-budget-usd", "100", "--runner-supports-paid",
    ]) == 0
    assert "approved: 18 trials" in capsys.readouterr().out
