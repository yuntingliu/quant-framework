from __future__ import annotations

from pathlib import Path

import alphalab


ROOT = Path(__file__).resolve().parents[2]
MODES = [
    "data",
    "project",
    "selection",
    "portfolio",
    "execution",
    "backtest",
    "report",
]
STAGES = MODES[2:5]


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


def test_frontend_exposes_seven_parallel_workbench_modes():
    presets = (ROOT / "dashboard/frontend/src/layouts/presets.ts").read_text(encoding="utf-8")
    mode_config = (ROOT / "dashboard/frontend/src/workspace/modes.ts").read_text(encoding="utf-8")
    expected_union = " | ".join(f'"{mode}"' for mode in MODES)
    assert f"export type WorkspaceMode = {expected_union}" in presets
    for mode in ("data", "project", "backtest", "report"):
        assert f'{mode}: createWorkbenchPreset("{mode}"' in presets
    for mode in STAGES:
        assert f'{mode}: createStagePreset("{mode}"' in presets
    for mode in MODES:
        assert f"{mode}: {{ icon:" in mode_config
        assert f'{mode}: ["{mode}.workbench"' in mode_config
    assert (
        'modes: ["selection", "portfolio", "execution"]'
        in mode_config
    )
    assert 'export const DEFAULT_MODE: WorkspaceMode = "data"' in presets


def test_core_widget_catalog_has_one_workbench_per_boundary():
    catalog = (ROOT / "dashboard/frontend/src/widgets/registry/catalog.ts").read_text(
        encoding="utf-8"
    )
    components = (ROOT / "dashboard/frontend/src/widgets/registry/components.tsx").read_text(
        encoding="utf-8"
    )
    for mode in MODES:
        assert f'"{mode}.workbench"' in catalog
        assert f'"{mode}.workbench"' in components
    for old_id in ("factor.workbench", "strategy.workbench", "timing-strategy.workbench"):
        assert old_id not in catalog


def test_stage_workbench_supports_versions_code_and_real_preview():
    source = (ROOT / "dashboard/frontend/src/widgets/pipeline/StageWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    run_hook = (ROOT / "dashboard/frontend/src/hooks/use-pipeline-stage-run.ts").read_text(
        encoding="utf-8"
    )
    for text in (
        "addComponent",
        "saveComponent",
        "搜索组件",
        "组件名称",
        "createInternalId",
        "应用到项目",
        "保存并应用",
    ):
        assert text in source
    assert "/preview" in run_hook
    assert "/analysis" not in run_hook
    assert "Promise.all" not in run_hook
    assert "projectId, selectedStrategyRevision, stage, profile, selectedDate" in run_hook
    assert "isStale" in run_hook
    assert "{ stage, profile," in run_hook
    assert "生成选股结果" in source
    assert "运行至" not in source
    assert "pipeline-stage-tabs" not in source
    assert 'setActiveMode("project")' in source
    assert "cloneProject" not in source
    assert "newProjectOpen" not in source
    assert "projectSettingsOpen" not in source
    assert "setNewProjectOpen" not in source
    assert "项目设置" not in source
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
    assert "<Widget headerless>" in source
    assert "title={`${meta.title}工作台`}" not in source
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


def test_stage_modes_use_distinct_multi_panel_dockview_layouts():
    presets = (ROOT / "dashboard/frontend/src/layouts/presets.ts").read_text(encoding="utf-8")
    components = (ROOT / "dashboard/frontend/src/widgets/registry/components.tsx").read_text(
        encoding="utf-8"
    )
    panels = (ROOT / "dashboard/frontend/src/widgets/pipeline/StageResultPanels.tsx").read_text(
        encoding="utf-8"
    )
    expected = {
        "selection": ("selection.ranking", "selection.chart"),
        "portfolio": ("portfolio.weights", "portfolio.summary"),
        "execution": ("execution.settings", "execution.targets"),
    }
    assert "position?:" in presets
    for widget_ids in expected.values():
        for widget_id in widget_ids:
            assert f'componentId: "{widget_id}"' in presets
            assert f'"{widget_id}"' in components
    assert "CandlestickChart" in panels
    assert "createSeriesMarkers" in (
        ROOT / "dashboard/frontend/src/components/charts/CandlestickChart.tsx"
    ).read_text(encoding="utf-8")


def test_backtest_shows_and_runs_the_frozen_complete_module():
    source = (ROOT / "dashboard/frontend/src/widgets/backtest/BacktestWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    assert "project_id" in source
    assert "/backtests/jobs" in source
    assert "/backtests/run" not in source
    assert "pipeline_manifest.composed_source" in source
    assert "同一份总 Python 源码" in source
    assert "strategy_yaml" not in source
    assert "runResearch" not in source
    assert "回测 + 稳健性验证" not in source
    assert 'tab === "signals"' in source
    assert "/signals" in source
    assert "<Widget headerless>" in source
    assert "title={copy.title}" not in source


def test_sidebar_has_fixed_modes_without_search_or_tab_management():
    sidebar = (ROOT / "dashboard/frontend/src/workspace/ModeSidebar.tsx").read_text(
        encoding="utf-8"
    )
    workspace = (ROOT / "dashboard/frontend/src/Workspace.tsx").read_text(encoding="utf-8")
    for removed in (
        "dispatchCommandPalette",
        "sidebar.manageTabs",
        "sidebar.hideTab",
        "onToggleModeHidden",
    ):
        assert removed not in sidebar
    for removed in ("HIDDEN_MODES_KEY", "loadHiddenModes", "toggleModeHidden"):
        assert removed not in workspace
    toolbar = (ROOT / "dashboard/frontend/src/workspace/Toolbar.tsx").read_text(encoding="utf-8")
    assert "toggleTheme" in sidebar and "toggleLanguage" in sidebar
    assert "toggleTheme" not in toolbar and "toggleLanguage" not in toolbar
    assert "sidebar.statusHint" not in sidebar


def test_alphalab_logo_replaces_the_placeholder_brand_mark():
    sidebar = (ROOT / "dashboard/frontend/src/workspace/ModeSidebar.tsx").read_text(
        encoding="utf-8"
    )
    index = (ROOT / "dashboard/frontend/index.html").read_text(encoding="utf-8")
    logo = ROOT / "dashboard/frontend/public/alphalab-logo.png"
    assert logo.is_file() and logo.stat().st_size > 0
    assert 'src="/alphalab-logo.png"' in sidebar
    assert ">\n          AL\n" not in sidebar
    assert 'type="image/png" href="/alphalab-logo.png"' in index
    assert not (ROOT / "dashboard/frontend/public/vite.svg").exists()


def test_project_workbench_owns_project_profile_and_cutoff_date():
    toolbar = (ROOT / "dashboard/frontend/src/workspace/Toolbar.tsx").read_text(encoding="utf-8")
    stage = (ROOT / "dashboard/frontend/src/widgets/pipeline/StageWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    project = (ROOT / "dashboard/frontend/src/widgets/project/ProjectWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    for label in ("项目与数据", "数据环境", "数据截至日", "三阶段组件"):
        assert label in project
    assert '"/pipeline/projects"' in project
    assert 'aria-label="研究项目"' not in toolbar
    assert 'aria-label="数据环境"' not in toolbar
    assert 'aria-label="决策日期"' not in toolbar
    assert 'setActiveMode("project")' in stage


def test_report_workbench_is_a_persisted_library():
    source = (ROOT / "dashboard/frontend/src/widgets/research/ReportWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    context = (ROOT / "dashboard/frontend/src/contexts/AgentPromptContext.tsx").read_text(
        encoding="utf-8"
    )
    assert "报告库" in source
    assert "researchResults.map" in source
    assert 'api.post<AgentResearchResult>("/reports"' not in context
    assert '"/reports?limit=100"' in context
    assert "refreshResearchResults" in source


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
