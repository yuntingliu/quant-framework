from __future__ import annotations

import pytest

from alphalab import (
    PipelineRepository,
    STAGE_NAMES,
    create_default_engine,
    preview_pipeline_project,
    run_pipeline_project_backtest,
)


def test_default_project_preview_runs_the_complete_module(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        preview = preview_pipeline_project(
            repository,
            "three-stage-default",
            create_default_engine(),
            "2024-12-31",
            stage="execution",
        )
    finally:
        repository.close()

    assert preview["source_sha256"]
    assert tuple(preview["stage_outputs"]) == STAGE_NAMES
    assert preview["targets"]
    assert sum(preview["targets"].values()) <= 1.0 + 1e-9
    assert max(preview["targets"].values()) <= 0.1 + 1e-9
    assert preview["executed_stages"] == list(STAGE_NAMES)


def test_stage_preview_stops_before_downstream_components(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        preview = preview_pipeline_project(
            repository,
            "three-stage-default",
            create_default_engine(),
            "2024-12-31",
            stage="selection",
        )
    finally:
        repository.close()

    assert preview["requested_stage"] == "selection"
    assert preview["executed_stages"] == ["selection"]
    assert tuple(preview["stage_outputs"]) == ("selection",)
    assert "portfolio" not in preview["diagnostics"]["complete_pipeline"]


def test_signal_allocation_methods_emit_distinct_constrained_target_weights(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        previews = {}
        for method in ("equal_weight", "score_weight", "rank_decay"):
            project_id = f"allocation-{method.replace('_', '-')}"
            project = repository.clone_project("three-stage-default", project_id)
            settings = dict(project["settings"])
            stage_parameters = dict(settings.get("stage_parameters") or {})
            stage_parameters["selection"] = {
                **dict(stage_parameters.get("selection") or {}),
                "count": 10,
            }
            stage_parameters["portfolio"] = {
                **dict(stage_parameters.get("portfolio") or {}),
                "optimizer": method,
                "rank_decay": 1.0,
                "max_weight": 1.0,
                "max_gross_exposure": 0.9,
            }
            settings["stage_parameters"] = stage_parameters
            repository.update_project(
                project_id,
                name=project["name"],
                description=project["description"],
                components=project["components"],
                settings=settings,
            )
            previews[method] = preview_pipeline_project(
                repository,
                project_id,
                create_default_engine(),
                "2024-12-31",
                stage="portfolio",
            )
    finally:
        repository.close()

    equal_weights = list(previews["equal_weight"]["targets"].values())
    score_weights = list(previews["score_weight"]["targets"].values())
    rank_weights = list(previews["rank_decay"]["targets"].values())
    assert len(equal_weights) >= 2
    assert sum(equal_weights) == pytest.approx(0.9)
    assert sum(score_weights) == pytest.approx(0.9)
    assert sum(rank_weights) == pytest.approx(0.9)
    assert max(equal_weights) == pytest.approx(min(equal_weights))
    assert max(score_weights) > min(score_weights)
    assert rank_weights[0] > rank_weights[-1]
    for preview in previews.values():
        assert preview["executed_stages"] == ["selection", "portfolio"]
        assert max(preview["targets"].values()) <= 1.0 + 1e-9


def test_short_backtest_uses_the_same_frozen_source(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        project = repository.get_project("three-stage-default")
        assert project is not None
        result = run_pipeline_project_backtest(
            repository,
            "three-stage-default",
            "2024-08-01",
            "2024-12-31",
            create_default_engine(),
        )
    finally:
        repository.close()

    assert result.composed.source_sha256 == project["source_sha256"]
    assert result.composed.source == project["composed_source"]
    assert [item["stage"] for item in result.composed.manifest] == list(STAGE_NAMES)
    assert len(result.result.returns) >= 2
    assert not result.result.weights.empty
    assert result.result.executions
    assert tuple(result.result.executions[0]["stage_outputs"]) == STAGE_NAMES
    assert result.result.diagnostics["strategy_source_sha256"] == project["source_sha256"]
    assert "selection_forward_returns" in result.result.executions[0]
    assert "factor_score_correlation" in result.result.executions[0]


def test_daily_signal_model_drives_the_core_schedule(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        project = repository.clone_project(
            "three-stage-default", "daily-project", "Daily Project"
        )
        project["settings"]["stage_parameters"] = {
            "selection": {"signal_frequency": "daily"}
        }
        repository.update_project(
            "daily-project",
            name=project["name"],
            description=project["description"],
            components=project["components"],
            settings=project["settings"],
        )
        result = run_pipeline_project_backtest(
            repository,
            "daily-project",
            "2024-08-01",
            "2024-08-12",
            create_default_engine(),
        )
    finally:
        repository.close()

    assert result.config.selection.signal_frequency == "daily"
    assert result.result.diagnostics["frequency"] == "daily"
    assert len(result.result.returns) >= 5
    assert all(
        "rebalance_freq" not in item["execution_settings"]
        for item in result.result.executions
    )


def test_backtest_treats_pre_history_rebalances_as_cash(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        result = run_pipeline_project_backtest(
            repository,
            "three-stage-default",
            "2021-07-11",
            "2021-12-31",
            create_default_engine(),
        )
    finally:
        repository.close()

    assert len(result.result.returns) >= 4
    skipped = [
        item["pipeline_execution"]
        for item in result.result.executions
        if item["pipeline_execution"].get("skipped")
    ]
    assert skipped
    assert {item["reason"] for item in skipped} == {"no_eligible_assets"}
    assert result.result.executions[0]["cash_weight"] == 1.0


def test_removed_stages_are_not_exposed_and_execution_has_no_signal_calendar(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        always_on = repository.get_component("timing-always-on")
        universe = repository.get_component("universe-all")
        pass_through = repository.get_component("selection-pass-through")
        daily = repository.get_component("execution-daily")
    finally:
        repository.close()
    assert always_on is None
    assert universe is None
    assert pass_through is not None and pass_through["stage"] == "selection"
    assert 'context["candidates"]' in pass_through["source"]
    assert daily is not None and "rebalance_freq" not in daily["parameters"]
    assert "rebalance_freq" not in daily["source"]


def test_core_rejects_a_component_that_escapes_the_project_stock_pool(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        selection = repository.clone_component(
            "selection-factor-top", "invalid-selection", "Invalid Selection"
        )
        repository.save_component_version(
            "invalid-selection",
            source=(
                "def select_assets(context):\n"
                "    return {'selected': ['NOT_ELIGIBLE'], 'scores': {'NOT_ELIGIBLE': 1.0}}\n"
            ),
        )
        project = repository.clone_project("three-stage-default", "invalid-project")
        project["components"]["selection"] = {
            "component_id": "invalid-selection",
            "version": 2,
        }
        repository.update_project(
            "invalid-project",
            name=project["name"],
            description=project["description"],
            components=project["components"],
            settings=project["settings"],
        )
        with pytest.raises(ValueError, match="outside the eligible project stock pool"):
            preview_pipeline_project(
                repository,
                "invalid-project",
                create_default_engine(),
                "2024-12-31",
                stage="selection",
            )
    finally:
        repository.close()
