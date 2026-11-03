import pytest

from apps.api.services.comparison import Adapter, BudgetGuard, ComparisonRunner, Config


class SpendingAdapter(Adapter):
    """Adapter that reports cost eagerly through the budget guard, like a real client would."""

    def __init__(self, cost_usd):
        self.cost_usd = cost_usd

    def setup(self, task, limits, initial_state, use_doubles):
        pass

    def run(self, task, limits, budget_guard):
        yield {"type": "start"}
        budget_guard.add_cost(self.cost_usd)
        yield {"type": "done"}

    def stop(self):
        pass

    def get_capabilities(self):
        return {}

    def get_financial_entry_points(self):
        return []


def run_one(adapter=None, **kwargs):
    runner = ComparisonRunner()
    records = runner.run(
        [Config.A],
        task_subset=["stub_data_diagnosis_repair"],
        repetitions=1,
        use_doubles=kwargs.pop("use_doubles", True),
        adapters=None if adapter is None else {Config.A: adapter},
        **kwargs,
    )
    assert len(records) == 1
    return records[0]


def test_budget_guard_blocks_real_runs_without_opt_in():
    rec = run_one(use_doubles=False, budget_opt_in=False, total_budget_usd=10.0)

    assert rec.execution["status"] == "failed"
    assert rec.results["errors"][0]["code"] == "BUDGET_OR_OPTIN"
    assert rec.results["errors"][0]["summary"] == "Real runs require explicit opt-in"
    assert rec.budget["enabled"] is True


def test_budget_guard_blocks_real_runs_without_a_stated_budget():
    rec = run_one(use_doubles=False, budget_opt_in=True, total_budget_usd=None)

    assert rec.execution["status"] == "failed"
    assert rec.results["errors"][0]["summary"] == "Real runs require a stated budget"


def test_opted_in_run_with_budget_completes(make_adapter):
    rec = run_one(
        make_adapter(events=[{"type": "start"}]),
        use_doubles=False,
        budget_opt_in=True,
        total_budget_usd=1.0,
    )

    assert rec.execution["status"] == "completed"
    assert rec.budget == {
        "enabled": True,
        "opt_in": True,
        "total_budget_usd": 1.0,
        "spent_usd": 0.0,
        "within_budget": True,
    }


def test_doubles_run_is_free_and_needs_no_opt_in(make_adapter):
    rec = run_one(make_adapter(events=[{"type": "start"}]))

    assert rec.execution["status"] == "completed"
    assert rec.budget["enabled"] is False
    assert rec.budget["total_budget_usd"] is None


def test_spending_over_the_budget_fails_the_trial():
    rec = run_one(
        SpendingAdapter(cost_usd=2.0),
        use_doubles=False,
        budget_opt_in=True,
        total_budget_usd=1.0,
    )

    assert rec.execution["status"] == "failed"
    assert rec.results["errors"][0] == {"code": "BUDGET_EXCEEDED", "summary": "Budget exceeded"}
    assert rec.budget["spent_usd"] == pytest.approx(2.0)
    assert rec.budget["within_budget"] is False


def test_event_cost_is_tracked_against_the_budget(make_adapter):
    adapter = make_adapter(events=[{"type": "usage", "cost_usd": 0.25}, {"type": "usage", "cost_usd": 0.25}])

    rec = run_one(adapter, use_doubles=False, budget_opt_in=True, total_budget_usd=1.0)

    assert rec.execution["status"] == "completed"
    assert rec.budget["spent_usd"] == pytest.approx(0.5)
    assert rec.budget["within_budget"] is True


def test_budget_guard_rejects_negative_progress():
    guard = BudgetGuard(total_budget_usd=1.0, opt_in=True, use_doubles=False)
    guard.add_cost(0.4)
    guard.add_cost(0.4)
    with pytest.raises(RuntimeError, match="Budget exceeded"):
        guard.add_cost(0.4)
