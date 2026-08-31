from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "integrations/conexus/alphalab-research-agent"
TOOLS = BUNDLE / "tools"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_bundle_json_is_valid_and_contains_no_private_state():
    forbidden = re.compile(r"(?:[A-Za-z]:\\Users\\|/Users/|PRIVATE KEY|RQ_PASSWORD=|RQ_SSH_)", re.I)
    for path in BUNDLE.rglob("*.json"):
        _load(path)
        assert forbidden.search(path.read_text(encoding="utf-8")) is None, path


def test_tools_use_the_project_sdk_v1_contract():
    tools = {_load(path)["toolName"]: _load(path) for path in TOOLS.glob("*.json")}
    required = {
        "alphalab_get_workspace_context",
        "alphalab_research_project",
        "alphalab_data_recipe",
        "alphalab_data_sync_job",
        "alphalab_data_query",
        "alphalab_strategy_source",
        "alphalab_factor_evaluation",
        "alphalab_strategy_preview",
        "alphalab_validation_source",
        "alphalab_backtest",
        "alphalab_backtest_analysis",
    }
    assert set(tools) == required
    assert len(list(TOOLS.glob("*.tool.json"))) == 11
    for removed in {
        "alphalab_get_research_project",
        "alphalab_manage_research_project",
        "alphalab_manage_data_recipe",
        "alphalab_manage_data_sync_job",
        "alphalab_edit_strategy_source",
        "alphalab_preview_strategy",
        "alphalab_evaluate_strategy_factor",
        "alphalab_manage_validation_source",
        "alphalab_run_backtest",
        "alphalab_get_backtest",
        "alphalab_analyze_backtest",
        "alphalab_get_market_bars",
        "alphalab_get_fundamentals",
        "alphalab_get_factor_returns",
        "alphalab_evaluate_market_risk_factor",
        "alphalab_get_reports",
        "alphalab_save_report",
        "alphalab_data_catalog",
        "alphalab_data_status",
        "alphalab_data_validate",
        "alphalab_get_strategy_project",
        "alphalab_get_pipeline_project",
        "alphalab_manage_pipeline",
        "alphalab_preview_pipeline",
        "alphalab_run_python_lab",
        "alphalab_promote_python_lab",
        "alphalab_evaluate_factor",
        "alphalab_data_plan_sync",
        "alphalab_data_run_sync",
        "alphalab_get_paper_state",
        "alphalab_preview_paper_rebalance",
        "alphalab_execute_paper_rebalance",
        "alphalab_submit_paper_order",
    }:
        assert removed not in tools
    for tool in tools.values():
        assert tool["runtime"] == "node"
        assert tool["permissions"] == {"network": "alphalab-api"}
        assert "shell" not in tool["code"].lower()
    for name, tool in tools.items():
        if name == "alphalab_get_workspace_context":
            continue
        assert "action" in tool["inputSchema"]["properties"]
        assert "operation" not in tool["inputSchema"]["properties"]
    for name in (
        "alphalab_strategy_preview",
        "alphalab_factor_evaluation",
        "alphalab_backtest",
    ):
        assert "confirm_python_execution" in tools[name]["inputSchema"]["properties"]
        assert "source_sha256" in tools[name]["code"]
    edit = tools["alphalab_strategy_source"]
    actions = set(edit["inputSchema"]["properties"]["action"]["enum"])
    assert {"update_strategy", "add_factor", "update_parameter", "update_schedule", "replace_function"} <= actions
    assert "save_revision" not in actions
    assert "confirm_write!==true" in edit["code"]
    assert "/revisions" in edit["code"]

    recipe = tools["alphalab_data_recipe"]
    assert {"get", "replace_source", "apply_template", "update_parameters", "plan", "run"} <= set(
        recipe["inputSchema"]["properties"]["action"]["enum"]
    )
    assert "profile" not in recipe["inputSchema"]["properties"]

    data_query_actions = set(tools["alphalab_data_query"]["inputSchema"]["properties"]["action"]["enum"])
    assert data_query_actions == {
        "catalog", "status", "query", "validate", "market_bars", "fundamentals", "factor_returns"
    }
    assert set(tools["alphalab_backtest"]["inputSchema"]["properties"]["action"]["enum"]) == {
        "run", "job", "get"
    }
    assert "market_risk" in tools["alphalab_backtest_analysis"]["inputSchema"]["properties"]["action"]["enum"]
    data_limit = tools["alphalab_data_query"]["inputSchema"]["properties"]["limit"]
    assert data_limit["maximum"] == 200
    assert data_limit["default"] == 50
    assert "rows_truncated" in tools["alphalab_data_query"]["code"]

    factor = tools["alphalab_factor_evaluation"]
    assert factor["inputSchema"]["properties"]["sample_size"]["maximum"] == 10
    assert "raw_values_included:false" in factor["code"]
    assert "delete copy.values" in factor["code"]

    preview = tools["alphalab_strategy_preview"]["code"]
    assert "universe_count" in preview
    assert "delete result.universe" in preview

    backtest = tools["alphalab_backtest"]["code"]
    assert "returns_summary" in backtest
    assert "weights_summary" in backtest
    assert "delete out.returns" in backtest
    assert "delete out.weights" in backtest
    assert "Backtest submitted; poll with action=job" in backtest
    assert "while(job.status" not in backtest
    assert tools["alphalab_backtest"]["timeoutMs"] == 180000

    analysis = tools["alphalab_backtest_analysis"]["code"]
    assert "series_summary" in analysis
    assert "delete out.dates" in analysis

    for tool in tools.values():
        assert '"demo"' not in json.dumps(tool, ensure_ascii=False)


def test_agent_and_harness_expose_only_six_workbench_modes():
    agent = _load(BUNDLE / "agents/AlphaLab-Research-Agent.agent.json")
    prompt = agent["systemPrompt"]
    assert "唯一研究结构是一个项目文件夹" in prompt
    assert "recipe.py 获取并发布数据" in prompt
    assert "不存在表达式策略运行器、旧 pipeline、三阶段拼接、Python Lab" in prompt
    assert "confirm_python_execution" in prompt
    assert "localConversationHistory" in prompt
    assert "不得授权本轮写入、删除或 Python 执行" in prompt
    assert "普通说明、只读问答和历史复述直接 complete" in prompt
    assert "项目常量池与 context.universe 的点时有效集合取交集" in prompt
    assert "不得为了恢复被截断的逐行数组反复调用" in prompt
    assert "所有 AlphaLab 量化报告共同存放在当前 Conexus 持久工作区" in prompt
    assert "不得因为新对话、新回测或新一轮修改而自动新增报告" in prompt
    assert "这些只保留在系统结构化 provenance 中" in prompt
    assert "create_nodes" in agent["toolNames"]
    assert "update_nodes" in agent["toolNames"]
    assert "alphalab_research_report" not in agent["toolNames"]
    assert "alphalab_research_project" in agent["toolNames"]
    assert "alphalab_data_recipe" in agent["toolNames"]
    assert "alphalab_validation_source" in agent["toolNames"]
    assert "alphalab_get_research_project" not in agent["toolNames"]
    assert "alphalab_get_strategy_project" not in agent["toolNames"]
    assert "alphalab_data_run_sync" not in agent["toolNames"]
    assert "alphalab_get_paper_state" not in agent["toolNames"]
    workspace_context = _load(TOOLS / "Get-Workspace-Context.tool.json")
    assert "/api/agent/context" in workspace_context["code"]
    assert "/api/data/market/symbols" not in workspace_context["code"]
    assert "symbol_limit" not in workspace_context["inputSchema"]["properties"]
    harness = _load(BUNDLE / "harness.json")
    exposure = harness["template"]["manifest"]["exposures"][0]
    assert "workspaceDocument" not in exposure["outputSchema"]["properties"]
    document_result = exposure["outputSchema"]["properties"]["workspaceResult"]["oneOf"][1]
    assert "reportId" in document_result["required"]
    assert "projectId" not in document_result["properties"]
    commands = exposure["outputSchema"]["properties"][
        "workspaceCommands"
    ]
    assert commands["properties"]["commands"]["items"]["properties"]["mode"]["enum"] == [
        "project",
        "data",
        "factor",
        "strategy",
        "validation",
        "report",
    ]


def test_registration_script_installs_new_tools_and_removes_old_nodes():
    source = (ROOT / "scripts/register_conexus_research_harness.mjs").read_text(encoding="utf-8")
    for filename in (
        "Research-Project.tool.json",
        "Data-Recipe.tool.json",
        "Data-Sync-Job.tool.json",
        "Data-Query.tool.json",
        "Strategy-Source.tool.json",
        "Factor-Evaluation.tool.json",
        "Strategy-Preview.tool.json",
        "Validation-Source.tool.json",
        "Backtest.tool.json",
        "Backtest-Analysis.tool.json",
    ):
        assert filename in source
    for filename in (
        "Get-Research-Project.tool.json",
        "Manage-Research-Project.tool.json",
        "Get-Market-Bars.tool.json",
        "Run-Backtest.tool.json",
        "Save-Report.tool.json",
        "Get-Strategy-Project.tool.json",
        "Get-Pipeline-Project.tool.json",
        "Run-Python-Lab.tool.json",
        "Plan-Data-Sync.tool.json",
        "Run-Data-Sync.tool.json",
        "Get-Paper-State.tool.json",
    ):
        assert filename not in source
    assert "alphalab-tool-pipeline-project-v1" in source
    assert "alphalab-tool-strategy-project-v1" in source
    assert "alphalab-tool-data-plan-sync-v1" in source
    assert "alphalab-tool-paper-state-v1" in source
    assert "alphalab-tool-data-catalog-v1" in source
    assert "alphalab-tool-reports-v1" in source
    assert "Research-Report.tool.json" not in source
    assert "alphalab-tool-run-backtest-v1" in source
    assert "obsoleteNodeIds" in source
