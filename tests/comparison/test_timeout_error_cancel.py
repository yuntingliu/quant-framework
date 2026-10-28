from apps.api.services.comparison import ComparisonRunner, Config


def test_timeout_error_cancel_produce_complete_records():
    runner = ComparisonRunner()
    results = runner.run([Config.A], repetitions=1, use_doubles=True)
    for rec in results:
        assert rec.is_complete is True
        assert rec.execution['status'] in ['completed', 'failed', 'timeout', 'cancelled', 'interrupted']
