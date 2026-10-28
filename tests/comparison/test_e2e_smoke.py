from apps.api.services.comparison import ComparisonRunner, Config


def test_abc_end_to_end_one_task_per_category_smoke():
    runner = ComparisonRunner()
    # one stand-in task per category via default stubs
    results = runner.run([Config.A, Config.B, Config.C], repetitions=1, use_doubles=True)
    assert len(results) == 18  # 3 configs * 6 categories
    for rec in results:
        assert rec.is_complete is True
