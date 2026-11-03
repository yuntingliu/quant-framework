import json
from pathlib import Path

from apps.api.services.comparison import ComparisonRunner, Config


def test_reset_restores_initial_state():
    runner = ComparisonRunner()
    results = runner.run([Config.A], task_subset=["stub_data_diagnosis_repair"], repetitions=1, use_doubles=True)
    assert len(results) == 1
    rec = results[0]
    assert rec.reset["performed"] is True
    assert rec.reset["verified"] is True
    assert rec.reset["method"] == "temp_runtime_dir_fresh_project_from_sdk-v1-default"
    assert rec.initial_state["data_profile"] == "runtime"
    assert "conexus_local_root" in rec.initial_state
    assert "model_providers_file" in rec.initial_state


def test_each_trial_gets_fresh_isolated_dirs_and_providers_file():
    runner = ComparisonRunner()
    records = runner.run(
        [Config.A],
        task_subset=["stub_data_diagnosis_repair"],
        repetitions=2,
        seed=3,
        use_doubles=True,
        cleanup=False,
    )

    runtime_dirs = [rec.initial_state["runtime_dir"] for rec in records]
    assert len(records) == 2
    assert len(set(runtime_dirs)) == 2
    for rec in records:
        assert Path(rec.initial_state["runtime_dir"]).is_dir()
        assert Path(rec.initial_state["conexus_local_root"]).is_dir()
        providers = Path(rec.initial_state["model_providers_file"])
        assert providers.is_file()
        assert json.loads(providers.read_text(encoding="utf-8")) == {"profiles": [], "active_id": None}
        assert rec.initial_state["project_id"].startswith("cmp_")

    runner.reset_mgr.cleanup()
    for rec in records:
        assert not Path(rec.initial_state["runtime_dir"]).exists()
        assert not Path(rec.initial_state["conexus_local_root"]).exists()


def test_cleanup_happens_by_default():
    runner = ComparisonRunner()
    records = runner.run([Config.A], task_subset=["stub_data_diagnosis_repair"], use_doubles=True)

    assert not Path(records[0].initial_state["runtime_dir"]).exists()
    assert runner.reset_mgr._tmpdirs == []


def test_reset_does_not_touch_existing_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", str(tmp_path / "user-runtime"))
    (tmp_path / "user-runtime").mkdir()

    runner = ComparisonRunner()
    records = runner.run([Config.A], task_subset=["stub_data_diagnosis_repair"], use_doubles=True)

    assert Path(records[0].initial_state["runtime_dir"]) != tmp_path / "user-runtime"
    assert (tmp_path / "user-runtime").is_dir()
