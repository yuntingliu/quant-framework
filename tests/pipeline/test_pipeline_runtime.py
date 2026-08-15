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
            "six-stage-default",
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
    assert preview["diagnostics"]["complete_pipeline"]["timing"]["exposure"] == 1.0
    assert preview["timing_reference"]
    assert preview["executed_stages"] == list(STAGE_NAMES)


def test_stage_preview_stops_before_downstream_components(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        preview = preview_pipeline_project(
            repository,
            "six-stage-default",
            create_default_engine(),
            "2024-12-31",
            stage="selection",
        )
    finally:
        repository.close()

    assert preview["requested_stage"] == "selection"
    assert preview["executed_stages"] == ["universe", "selection"]
    assert tuple(preview["stage_outputs"]) == ("universe", "selection")
    assert "timing" not in preview["diagnostics"]["complete_pipeline"]


def test_short_backtest_uses_the_same_frozen_source(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        project = repository.get_project("six-stage-default")
        assert project is not None
        result = run_pipeline_project_backtest(
            repository,
            "six-stage-default",
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


def test_pure_selection_and_pure_timing_are_explicit_components(tmp_path):
    repository = PipelineRepository(tmp_path / "pipeline.db")
    try:
        always_on = repository.get_component("timing-always-on")
        pass_through = repository.get_component("selection-pass-through")
    finally:
        repository.close()
    assert always_on is not None and always_on["stage"] == "timing"
    assert "exposure\": 1.0" in always_on["source"]
    assert pass_through is not None and pass_through["stage"] == "selection"
    assert "universe_symbols" in pass_through["source"]


def test_core_rejects_a_component_that_escapes_the_universe(tmp_path):
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
        project = repository.clone_project("six-stage-default", "invalid-project")
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
        with pytest.raises(ValueError, match="outside its universe"):
            preview_pipeline_project(
                repository,
                "invalid-project",
                create_default_engine(),
                "2024-12-31",
                stage="selection",
            )
    finally:
        repository.close()
