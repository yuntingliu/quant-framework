from apps.api.services.comparison import ComparisonRunner, Config


def test_reset_restores_initial_state():
    runner = ComparisonRunner()
    results = runner.run([Config.A], task_subset=['stub_data_diagnosis_repair'], repetitions=1, use_doubles=True)
    assert len(results) == 1
    rec = results[0]
    assert rec.reset['performed'] is True
    assert rec.reset['verified'] is True
    assert rec.reset['method'] == 'temp_runtime_dir_fresh_project_from_sdk-v1-default'
    assert rec.initial_state['data_profile'] == 'runtime'
    assert 'conexus_local_root' in rec.initial_state
    assert 'model_providers_file' in rec.initial_state
