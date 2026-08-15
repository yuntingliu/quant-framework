from __future__ import annotations

from pathlib import Path

import alphalab


ROOT = Path(__file__).resolve().parents[2]
MODES = [
    "data",
    "universe",
    "selection",
    "timing",
    "portfolio",
    "risk",
    "execution",
    "backtest",
    "report",
]
STAGES = MODES[1:7]


def test_public_facade_is_small_and_pipeline_native():
    expected = {
        "ComponentRef",
        "DataEngine",
        "PipelineProject",
        "PipelineRepository",
        "PythonStrategyError",
        "RQDataConfig",
        "RQDataProvider",
        "ResultStore",
        "STAGE_ENTRYPOINTS",
        "STAGE_NAMES",
        "compute_factor",
        "create_default_engine",
        "create_rq_engine_from_env",
        "create_runtime_engine",
        "evaluate_factor",
        "get_factor",
        "list_factors",
        "preview_pipeline_project",
        "run_pipeline_project_backtest",
        "validate_python_source",
    }
    assert set(alphalab.__all__) == expected
    assert alphalab.STAGE_NAMES == tuple(STAGES)
    assert not hasattr(alphalab, "StrategyRepository")
    assert not hasattr(alphalab, "TimingStrategyRepository")


def test_frontend_exposes_nine_parallel_workbench_modes():
    presets = (ROOT / "dashboard/frontend/src/layouts/presets.ts").read_text(encoding="utf-8")
    mode_config = (ROOT / "dashboard/frontend/src/workspace/modes.ts").read_text(encoding="utf-8")
    expected_union = " | ".join(f'\"{mode}\"' for mode in MODES)
    assert f"export type WorkspaceMode = {expected_union}" in presets
    for mode in MODES:
        assert f'{mode}: createWorkbenchPreset("{mode}"' in presets
        assert f"{mode}: {{ icon:" in mode_config
    assert 'export const DEFAULT_MODE: WorkspaceMode = "data"' in presets


def test_core_widget_catalog_has_one_workbench_per_boundary():
    catalog = (ROOT / "dashboard/frontend/src/widgets/registry/catalog.ts").read_text(encoding="utf-8")
    components = (ROOT / "dashboard/frontend/src/widgets/registry/components.tsx").read_text(encoding="utf-8")
    for mode in MODES:
        assert f'"{mode}.workbench"' in catalog
        assert f'"{mode}.workbench"' in components
    for old_id in ("factor.workbench", "strategy.workbench", "timing-strategy.workbench"):
        assert old_id not in catalog


def test_stage_workbench_supports_versions_code_and_real_preview():
    source = (ROOT / "dashboard/frontend/src/widgets/pipeline/StageWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    for text in (
        "addComponent",
        "saveComponent",
        "cloneProject",
        "/preview",
        "搜索组件",
        "组件名称",
        "项目名称",
        "createInternalId",
        "应用到项目",
        "保存并应用",
    ):
        assert text in source
    assert "pipeline-stage-tabs" not in source
    assert "setActiveMode" not in source
    assert "版本详情" not in source
    assert "保存新版本" not in source
    assert "@v" not in source
    assert "当前项目尚未改变" not in source
    assert "应用到当前项目" not in source
    assert "research-message" not in source
    assert "meta.contract" not in source
    assert "阶段 {stageNumber}" not in source
    assert "Python 源码" not in source
    assert "唯一逻辑来源" not in source
    assert "入口必须是" not in source
    assert "组件参数 JSON" not in source
    assert "参数与组件源码一同保存" not in source
    assert "阶段输入与输出" not in source
    assert "完整执行当前项目的冻结源码" not in source
    assert "刷新阶段结果" not in source
    assert '<Widget headerless>' in source
    assert 'title={`${meta.title}工作台`}' not in source
    for implementation_term in (
        "系统预置",
        "用户添加",
        "正在查看",
        "新组件 ID",
        "新项目 ID",
        "搜索名称或 ID",
    ):
        assert implementation_term not in source
    styles = (ROOT / "dashboard/frontend/src/styles.css").read_text(encoding="utf-8")
    assert ".python-stage-workbench" in styles
    assert ".pipeline-preview-grid" in styles
    assert "overflow: auto" in styles


def test_backtest_shows_and_runs_the_frozen_complete_module():
    source = (ROOT / "dashboard/frontend/src/widgets/backtest/BacktestWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    assert "project_id" in source
    assert "/backtests/run" in source
    assert "pipeline_manifest.composed_source" in source
    assert "同一份总 Python 源码" in source
    assert "strategy_yaml" not in source


def test_current_authoring_assets_do_not_bundle_yaml_strategies():
    assert list((ROOT / "alphalab/strategies").glob("*.yaml")) == []
    assert list((ROOT / "alphalab/timing_strategies").glob("*.yaml")) == []
    for path in (
        ROOT / "dashboard/frontend/src/widgets/pipeline/StageWorkbench.tsx",
        ROOT / "dashboard/frontend/src/widgets/backtest/BacktestWorkbench.tsx",
        ROOT / "dashboard/backend/routers/pipeline.py",
    ):
        assert "yaml" not in path.read_text(encoding="utf-8").lower()


def test_workspace_theme_switch_covers_dockview_headers():
    workspace = (ROOT / "dashboard/frontend/src/Workspace.tsx").read_text(encoding="utf-8")
    dockview_css = (ROOT / "dashboard/frontend/src/index.css").read_text(encoding="utf-8")
    assert 'className="dockview-theme-light alphalab-dockview"' in workspace
    assert ".alphalab-dockview .dv-tabs-and-actions-container" in dockview_css
