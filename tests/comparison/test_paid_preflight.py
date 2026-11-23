"""Offline preflight for the opt-in paid extension without provider access."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_agent_benchmark import main

_ROOT = Path(__file__).resolve().parents[2]
_PAID_PLAN = _ROOT / "benchmark" / "evaluation" / "paid_extension_plan.json"
_SUITE = _ROOT / "tests" / "fixtures" / "agent_benchmark"
_SUITE_ARGS = ["--suite-root", str(_SUITE)]


def _plan(tmp_path, **overrides):
    plan = json.loads(_PAID_PLAN.read_text(encoding="utf-8"))
    plan.update(overrides)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def _suite(tmp_path, task_ids):
    manifest = {
        "schema_version": 1,
        "tasks": [{"task_id": task_id, "split": "dev"} for task_id in task_ids],
    }
    path = tmp_path / "benchmark" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return str(tmp_path)


def test_preflight_is_disabled_by_default_and_does_not_read_the_plan(tmp_path, capsys):
    missing = tmp_path / "absent.json"
    assert main(["preflight-paid", "--plan", str(missing)]) == 0
    assert "disabled" in capsys.readouterr().out
    assert not missing.exists()


def test_preflight_refuses_stub_adapters(tmp_path, capsys):
    plan = _plan(tmp_path, task_ids=["data-pit-leak-crosscheck"])
    assert main([
        *_SUITE_ARGS, "preflight-paid", "--enable", "--plan", str(plan),
        "--total-budget-usd", "100",
    ]) == 2
    assert "verified real adapters" in capsys.readouterr().err


def test_preflight_rejects_budget_shortfall_and_non_live_plan(tmp_path, capsys):
    plan = _plan(tmp_path, task_ids=["data-pit-leak-crosscheck"])
    assert main([
        *_SUITE_ARGS, "preflight-paid", "--enable", "--plan", str(plan),
        "--total-budget-usd", "0.1", "--runner-supports-paid",
    ]) == 2
    assert "exceeds" in capsys.readouterr().err
    synthetic = _plan(tmp_path, task_ids=["data-pit-leak-crosscheck"], source_class="synthetic")
    assert main([
        *_SUITE_ARGS, "preflight-paid", "--enable", "--plan", str(synthetic),
        "--total-budget-usd", "5", "--runner-supports-paid",
    ]) == 2
    assert "source_class 'live'" in capsys.readouterr().err


def test_preflight_approves_a_fully_covered_plan(tmp_path, capsys):
    task_ids = [
        "data-pit-leak-crosscheck",
        "strategy-unauthorized-execution",
        "strategy-backtest-pinned-metrics",
    ]
    plan = _plan(tmp_path, task_ids=task_ids, repetitions=2)
    suite = _suite(tmp_path, task_ids)
    assert main([
        "--suite-root", suite, "preflight-paid", "--enable", "--plan", str(plan),
        "--total-budget-usd", "100", "--runner-supports-paid",
    ]) == 0
    assert "approved: 18 trials" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("task_ids", "message"),
    [
        ([""], "task_id must be a short identifier"),
        ([123], "task_id must be a short identifier"),
        (["a", "a"], "Duplicate task IDs in plan"),
    ],
)
def test_preflight_rejects_invalid_task_ids(tmp_path, capsys, task_ids, message):
    plan = _plan(tmp_path, task_ids=task_ids)
    assert main([
        *_SUITE_ARGS, "preflight-paid", "--enable", "--plan", str(plan),
        "--total-budget-usd", "100", "--runner-supports-paid",
    ]) == 2
    assert message in capsys.readouterr().err


def test_preflight_rejects_tasks_absent_from_the_suite(tmp_path, capsys):
    missing = _plan(tmp_path, task_ids=["not-in-suite"])
    assert main([
        *_SUITE_ARGS, "preflight-paid", "--enable", "--plan", str(missing),
        "--total-budget-usd", "100", "--runner-supports-paid",
    ]) == 2
    assert "absent from the dev split" in capsys.readouterr().err
    holdout = _plan(tmp_path, task_ids=["audit-underspecified-request"])
    assert main([
        *_SUITE_ARGS, "preflight-paid", "--enable", "--plan", str(holdout),
        "--total-budget-usd", "100", "--runner-supports-paid",
    ]) == 2
    assert "absent from the dev split" in capsys.readouterr().err


def test_preflight_requires_the_task_set_manifest(tmp_path, capsys):
    plan = _plan(tmp_path)
    assert main([
        "--suite-root", str(tmp_path), "preflight-paid", "--enable", "--plan", str(plan),
        "--total-budget-usd", "100", "--runner-supports-paid",
    ]) == 2
    assert "Missing benchmark/manifest.json" in capsys.readouterr().err


def test_preflight_requires_plan_and_budget_when_enabled(tmp_path, capsys):
    assert main(["preflight-paid", "--enable"]) == 2
    assert "--plan" in capsys.readouterr().err
    plan = _plan(tmp_path)
    assert main(["preflight-paid", "--enable", "--plan", str(plan)]) == 2
    assert "--total-budget-usd" in capsys.readouterr().err


def test_preflight_covers_the_checked_in_paid_plan(tmp_path, capsys):
    task_ids = json.loads(_PAID_PLAN.read_text(encoding="utf-8"))["task_ids"]
    suite = _suite(tmp_path, task_ids)
    assert main([
        "--suite-root", suite, "preflight-paid", "--enable", "--plan", str(_PAID_PLAN),
        "--total-budget-usd", "100", "--runner-supports-paid",
    ]) == 0
    assert "approved: 18 trials" in capsys.readouterr().out
