"""Paired rotations pin one cohort and differ only in the forecast source."""

from apps.api.services import options_research_service as research
from apps.api.services import options_rotation_service as rotation
from apps.api.services import options_strategy_service as strategies


def test_control_project_uses_baseline_field_and_frozen_study_dates(monkeypatch):
    report = {
        "id": "a" * 20,
        "prediction_field": "option_prediction_" + "a" * 20,
        "scope": {"underlyings": ["510050.XSHG"], "end": "2026-10-23"},
        "folds": [{"test_start": "2024-01-05"}],
        "config": {"fields": ["option_smirk"]},
    }
    monkeypatch.setattr(research, "read_research", lambda *_: report)
    request = {
        "project_id": "paired-controls",
        "name": "Controls",
        "research_id": report["id"],
        "prediction_source": "controls",
        "controlled_suite_id": "b" * 20,
        "top_n": 3,
        "frequency": "weekly",
    }
    spec = research.project_spec(request)
    field = "option_prediction_controls_" + report["id"]
    assert spec["data_requirements"]["daily_factors"] == [field]
    assert spec["recipe_parameters"]["start"] == "2024-01-05"
    assert spec["settings"]["option_prediction_source"] == "controls"
    source = spec["function_replacements"][1]["function_source"]
    assert f"weights={{{field!r}: 1.0}}" in source
    assert strategies._family({field: 1})[0] == "controls_forecast"


def test_pair_saves_both_projects_before_starting_jobs_and_reuses_manifest(tmp_path, monkeypatch):
    from apps.api.services import backtest_job_service, strategy_service

    actions = []
    monkeypatch.setattr(rotation, "_path", lambda *_: tmp_path / "pair.json")
    monkeypatch.setattr(
        rotation.evidence,
        "read_suite",
        lambda *_: {
            "rows": [{"horizon": 5, "variant": "joint", "run_id": "a" * 20}],
            "config": {"holdout_start": None},
        },
    )
    monkeypatch.setattr(
        rotation.research,
        "read_research",
        lambda *_: {
            "id": "a" * 20,
            "scope": {"end": "2026-10-23", "underlyings": ["510050.XSHG"]},
            "folds": [{"test_start": "2024-01-05"}],
        },
    )
    monkeypatch.setattr(strategy_service, "get_project", lambda *_: None)

    def create(spec):
        actions.append(("create", spec["prediction_source"]))
        assert spec["top_n"] == 3 and spec["frequency"] == "weekly"
        return {"project": {"current_revision": 1}}

    def submit(request):
        assert len([a for a in actions if a[0] == "create"]) == 2
        assert request["start_date"] == "2024-01-05" and request["revision"] == 1
        actions.append(("submit", request["project_id"]))
        return {"id": str(len(actions))}

    monkeypatch.setattr(rotation.research, "create_research_project", create)
    monkeypatch.setattr(backtest_job_service, "submit_backtest_job", submit)
    monkeypatch.setattr(backtest_job_service, "get_backtest_job", lambda _: {"status": "queued"})
    result = rotation.run_rotation_pair("b" * 20, 5)
    assert [j["role"] for j in result["jobs"]] == ["controls", "model"]
    assert rotation.run_rotation_pair("b" * 20, 5) == result
    assert len(actions) == 4
    monkeypatch.setattr(
        backtest_job_service,
        "get_backtest_job",
        lambda key: {"status": "failed" if key == "3" else "queued"},
    )
    resumed = rotation.run_rotation_pair("b" * 20, 5)
    assert len(actions) == 5
    assert len(resumed["pair"]["previous_attempts"]) == 1
    assert resumed["pair"]["jobs"][1]["job_id"] == result["pair"]["jobs"][1]["job_id"]
