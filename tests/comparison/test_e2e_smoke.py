import json

from apps.api.services.comparison import ComparisonRunner, Config, create_adapter


def test_abc_end_to_end_one_task_per_category_smoke(tmp_path):
    runner = ComparisonRunner()
    adapters = {config: create_adapter(config) for config in (Config.A, Config.B, Config.C)}

    results = runner.run(
        [Config.A, Config.B, Config.C],
        repetitions=1,
        seed=0,
        use_doubles=True,
        adapters=adapters,
        artifact_dir=str(tmp_path),
    )

    assert len(results) == 18  # 3 configs * 6 categories
    for rec in results:
        assert rec.is_complete is True
        assert rec.execution["status"] == "completed"
        assert rec.reset["verified"] is True

    assert all(rec.execution["financial_entry_points"] == [] for rec in results if rec.config is Config.A)
    b_points = {rec.task_id: rec.execution["financial_entry_points"] for rec in results if rec.config is Config.B}
    c_points = {rec.task_id: rec.execution["financial_entry_points"] for rec in results if rec.config is Config.C}
    assert b_points and b_points == c_points
    assert len(b_points["stub_data_diagnosis_repair"]) > 20

    files = sorted(tmp_path.glob("*.json"))
    assert len(files) == 18
    persisted = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    assert {item["config"] for item in persisted} == {"A", "B", "C"}
    assert all(item["is_complete"] is True for item in persisted)


def test_each_persisted_record_is_named_after_its_trial(tmp_path):
    runner = ComparisonRunner()

    records = runner.run(
        [Config.B],
        task_subset=["stub_result_auditing"],
        use_doubles=True,
        adapters={Config.B: create_adapter(Config.B)},
        artifact_dir=str(tmp_path),
    )

    assert {path.name for path in tmp_path.glob("*.json")} == {f"{records[0].trial_id}.json"}
