import json
from pathlib import Path

from apps.api.services.comparison import ComparisonRunner, Config, Limits

TOKEN = "sk-live-abcdef0123456789abcdef"
WINDOWS_PATH = "C:\\Users\\someone\\private\\report.csv"


def test_trial_record_is_persisted_with_redaction(tmp_path, make_adapter):
    adapter = make_adapter(events=[{
        "type": "log",
        "token": TOKEN,
        "api_key": "plain-secret",
        "path": str(tmp_path / "private" / "report.csv"),
        "message": f"Bearer {TOKEN} written to {WINDOWS_PATH}",
    }])
    artifacts = tmp_path / "artifacts"
    runner = ComparisonRunner()

    records = runner.run(
        [Config.A],
        task_subset=["stub_strategy_backtesting"],
        use_doubles=True,
        adapters={Config.A: adapter},
        artifact_dir=str(artifacts),
    )

    rec = records[0]
    files = list(artifacts.glob("*.json"))
    assert len(files) == 1
    assert files[0].name == f"{rec.trial_id}.json"

    text = files[0].read_text(encoding="utf-8")
    assert TOKEN not in text
    assert "sk-" not in text
    assert "plain-secret" not in text
    assert str(tmp_path) not in text
    assert json.dumps(str(tmp_path))[1:-1] not in text
    assert "C:\\Users" not in text

    payload = json.loads(text)
    event = payload["execution"]["events"][0]
    assert event["token"] == "<redacted>"
    assert event["api_key"] == "<redacted>"
    assert event["path"] == "<abs-path>"
    assert "Bearer <redacted>" in event["message"]
    assert "<abs-path>" in event["message"]
    assert payload["is_complete"] is True
    assert payload["execution"]["status"] == "completed"
    assert payload["initial_state"]["runtime_dir"] == "<abs-path>"


def test_default_persistence_lives_in_the_trial_runtime_dir_and_is_cleaned_up(make_adapter):
    runner = ComparisonRunner()

    records = runner.run(
        [Config.A],
        task_subset=["stub_strategy_backtesting"],
        use_doubles=True,
        adapters={Config.A: make_adapter(events=[{"type": "start"}])},
        cleanup=False,
    )

    rec = records[0]
    record_path = Path(rec.initial_state["runtime_dir"]) / "trials" / f"{rec.trial_id}.json"
    assert record_path.is_file()
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["reset"]["performed"] is True
    assert payload["reproducibility"]["interleave_seed"] is not None

    runner.reset_mgr.cleanup()
    assert not record_path.exists()


def test_failed_trial_is_still_persisted(tmp_path, hanging_adapter):
    runner = ComparisonRunner()

    records = runner.run(
        [Config.A],
        task_subset=["stub_strategy_backtesting"],
        use_doubles=True,
        adapters={Config.A: hanging_adapter},
        limits=Limits(time_s=0.2),
        artifact_dir=str(tmp_path),
    )

    payload = json.loads((tmp_path / f"{records[0].trial_id}.json").read_text(encoding="utf-8"))
    assert payload["execution"]["status"] == "timeout"
    assert payload["is_complete"] is True
    assert payload["results"]["errors"][0]["code"] == "TIMEOUT"


def test_persisted_artifact_path_is_recorded_in_memory(tmp_path, make_adapter):
    runner = ComparisonRunner()

    records = runner.run(
        [Config.A],
        task_subset=["stub_strategy_backtesting"],
        use_doubles=True,
        adapters={Config.A: make_adapter()},
        artifact_dir=str(tmp_path),
    )

    artifacts = records[0].results["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "trial_record"
    assert Path(artifacts[0]["path"]).is_file()
