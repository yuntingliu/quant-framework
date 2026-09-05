from __future__ import annotations

from pathlib import Path

import alphalab

ROOT = Path(__file__).resolve().parents[2]
MODES = ["project", "data", "factor", "strategy", "validation", "report"]


def test_public_facade_is_small_and_sdk_native():
    assert set(alphalab.__all__) == {
        "DataEngine",
        "RQDataConfig",
        "RQDataProvider",
        "ResultStore",
        "StrategyBacktestResult",
        "StrategyRepository",
        "StrategySourceError",
        "create_default_engine",
        "create_rq_engine_from_env",
        "create_runtime_engine",
        "evaluate_factor_history",
        "evaluate_factor_research",
        "evaluate_factor_snapshot",
        "inspect_strategy_source",
        "preview_strategy",
        "run_strategy_backtest",
    }
    assert not hasattr(alphalab, "PipelineRepository")


def test_frontend_has_six_views_over_one_sdk_source():
    presets = (ROOT / "dashboard/frontend/src/layouts/presets.ts").read_text(encoding="utf-8")
    modes = (ROOT / "dashboard/frontend/src/workspace/modes.ts").read_text(encoding="utf-8")
    components = (ROOT / "dashboard/frontend/src/widgets/registry/components.tsx").read_text(
        encoding="utf-8"
    )
    assert (
        'export type WorkspaceMode = "project" | "data" | "factor" | "strategy" | "validation" | "report"'
        in presets
    )
    for mode in MODES:
        assert f'{mode}: createWorkbenchPreset("{mode}"' in presets
        assert f'{mode}: ["{mode}.workbench"]' in modes
        assert f'"{mode}.workbench"' in components
    for removed in ('"selection.workbench":', '"backtest.workbench":', '"python.workbench":'):
        assert removed not in components


def test_workbenches_share_the_strategy_sdk_context():
    workspace = (ROOT / "dashboard/frontend/src/Workspace.tsx").read_text(encoding="utf-8")
    context = (ROOT / "dashboard/frontend/src/contexts/StrategySdkContext.tsx").read_text(
        encoding="utf-8"
    )
    factor = (ROOT / "dashboard/frontend/src/widgets/factors/FactorWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    strategy = (ROOT / "dashboard/frontend/src/widgets/strategy/StrategyWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    validation = (ROOT / "dashboard/frontend/src/widgets/backtest/BacktestWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    assert "<StrategySdkProvider>" in workspace
    assert '"/strategy/projects"' in context
    assert (
        "useStrategySdk" in factor
        and "useStrategySdk" in strategy
        and "useStrategySdk" in validation
    )
    assert '"/pipeline/projects"' not in (
        ROOT / "dashboard/frontend/src/workspace/Toolbar.tsx"
    ).read_text(encoding="utf-8")
    assert "context.history" in factor and "context.factor" in factor
    assert "structuredEdit" in strategy
    assert 'kind="strategy"' in strategy
    assert 'kind="function"' not in strategy
    assert 'operation: "replace_function"' not in strategy
    assert "/entrypoints/" not in strategy
    assert "StrategyVisualEditor" in strategy
    assert "应用全部设置" in strategy
    assert "strategy-split-authoring" in strategy
    assert "完整策略 Python" in strategy
    assert "project.strategy_source" in strategy
    assert "project.draft_source" not in strategy
    assert "addFactorSource" in factor
    assert "strategy-code-mode-switch" not in strategy
    assert "编辑 Python" not in strategy
    assert "`/strategy/projects/${projectId}/edits/preview`" in strategy
    assert "onDraftChange" in strategy
    assert "strategy-business-flow" not in strategy
    assert "strategy-stage-sidebar" not in strategy
    assert "/backtests/jobs" in validation and "revision" in validation


def test_strategy_save_hides_internal_revision_workflow():
    context = (ROOT / "dashboard/frontend/src/contexts/StrategySdkContext.tsx").read_text(
        encoding="utf-8"
    )
    factor = (ROOT / "dashboard/frontend/src/widgets/factors/FactorWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    strategy = (ROOT / "dashboard/frontend/src/widgets/strategy/StrategyWorkbench.tsx").read_text(
        encoding="utf-8"
    )
    backtest = (ROOT / "dashboard/frontend/src/widgets/backtest/BacktestWorkbench.tsx").read_text(
        encoding="utf-8"
    )

    assert "commitSavedProject" in context
    assert "installFactorTemplate" in context
    visible_workbenches = factor + strategy + backtest
    for removed_label in (
        "冻结新版本",
        "草稿未冻结",
        "冻结修订",
        "运行冻结源码",
        "当前冻结 revision",
    ):
        assert removed_label not in visible_workbenches


def test_active_backend_mounts_only_sdk_strategy_authoring():
    main = (ROOT / "dashboard/backend/main.py").read_text(encoding="utf-8")
    assert "app.include_router(strategy.router)" in main
    assert "app.include_router(pipeline.router)" not in main
    assert "app.include_router(factor_research.router)" not in main
    assert "app.include_router(python_lab.router)" not in main


def test_no_separate_python_lab_runtime_remains():
    assert not (ROOT / "alphalab/python_lab/runtime.py").exists()
    assert not (ROOT / "alphalab/python_lab/__init__.py").exists()
    assert not (ROOT / "dashboard/backend/routers/python_lab.py").exists()
    assert not (ROOT / "dashboard/frontend/src/widgets/python/PythonLabWorkbench.tsx").exists()
    assert "ALPHALAB_PYTHON_LAB" not in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_no_legacy_executable_strategy_runtime_remains():
    assert not (ROOT / "alphalab/engine.py").exists()
    assert not (ROOT / "alphalab/strategy/python_runtime.py").exists()
    assert not (ROOT / "alphalab/pipeline/runtime.py").exists()
    assert not (ROOT / "alphalab/pipeline/repository.py").exists()
    schema = (ROOT / "alphalab/schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS pipeline_" not in schema
    assert "CREATE TABLE IF NOT EXISTS python_lab_" not in schema


def test_strategy_authoring_units_assemble_into_one_runtime_package():
    schema = (ROOT / "alphalab/schema.sql").read_text(encoding="utf-8")
    source = (ROOT / "alphalab/strategy/source.py").read_text(encoding="utf-8")
    edit_tool = (
        ROOT / "integrations/conexus/alphalab-research-agent/tools/Strategy-Source.tool.json"
    ).read_text(encoding="utf-8")

    assert "strategy_source_units" in schema
    assert "strategy_source_package_units" in schema
    assert "def split_strategy_source(" in source
    assert "def assemble_strategy_source(" in source
    assert '"install_factor_template"' in edit_tool
    assert '"add_factor"' not in edit_tool
    assert "/factor-templates/" in edit_tool

    project_tool = (
        ROOT / "integrations/conexus/alphalab-research-agent/tools/Research-Project.tool.json"
    ).read_text(encoding="utf-8")
    assert '"migrate_default"' in project_tool
    assert "/default-migration" in project_tool


def test_strategy_contract_is_declared_implemented():
    contract = (ROOT / "docs/02_STRATEGY_SDK_V1_CONTRACT.md").read_text(encoding="utf-8")
    assert "Implementation status: implemented" in contract
    assert "alphalab.sdk.v1" in contract


def test_frontend_uses_runtime_and_server_shared_agent_state_only():
    profile = (ROOT / "dashboard/frontend/src/lib/data-profile.ts").read_text(encoding="utf-8")
    agent = (ROOT / "dashboard/frontend/src/hooks/usePublishedAgent.ts").read_text(encoding="utf-8")
    prompt = (ROOT / "dashboard/frontend/src/widgets/research/ResearchAgent.tsx").read_text(
        encoding="utf-8"
    )
    commands = (ROOT / "dashboard/frontend/src/workspace/agentCommands.ts").read_text(
        encoding="utf-8"
    )
    backend = (ROOT / "dashboard/backend/main.py").read_text(encoding="utf-8")

    assert 'export type DataProfile = "runtime"' in profile
    assert 'return "runtime"' in profile
    assert "localStorage" not in agent
    assert "localConversationHistory" not in agent
    assert "conversationHistory" in agent
    assert "activeDataProfile" not in prompt
    assert "selectedProjectId" in prompt
    assert "projectId" in commands
    assert "strategyId" not in commands
    workspace = (ROOT / "dashboard/frontend/src/Workspace.tsx").read_text(encoding="utf-8")
    strategy_context = (
        ROOT / "dashboard/frontend/src/contexts/StrategySdkContext.tsx"
    ).read_text(encoding="utf-8")
    assert "await openStrategyProject(projectId)" in workspace
    assert "projectReadyRef.current" in workspace
    assert "Agent 返回的工作台命令格式无效" in prompt
    assert "openProject: (projectId: string) => Promise<StrategyProject>" in strategy_context
    assert 'response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"' in backend
