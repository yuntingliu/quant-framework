from apps.api.services.comparison import ComparisonRunner, Config


def test_budget_guard_blocks_real_runs_without_opt_in():
    runner = ComparisonRunner()
    results = runner.run([Config.A], task_subset=['stub_data_diagnosis_repair'], repetitions=1, use_doubles=False, budget_opt_in=False, total_budget_usd=10.0)
    assert len(results) == 1
    assert results[0].execution['status'] == 'failed'
    assert any(e.get('code') == 'BUDGET_OR_OPTIN' for e in results[0].results['errors'])
