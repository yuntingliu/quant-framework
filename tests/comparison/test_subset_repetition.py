from apps.api.services.comparison import ComparisonRunner, Config, TaskLoader


def test_subset_repetition_selection():
    loader = TaskLoader()
    tasks = loader.load_subset(['stub_data_diagnosis_repair'])
    assert len(tasks) == 1
    runner = ComparisonRunner(loader)
    results = runner.run([Config.A], task_subset=['stub_data_diagnosis_repair'], repetitions=2, use_doubles=True)
    assert len(results) == 2
