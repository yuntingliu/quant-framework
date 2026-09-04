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
    edit = tools["alphalab_strategy_source"]
    actions = set(edit["inputSchema"]["properties"]["action"]["enum"])
    assert {
        "install_factor_template",
        "update_parameter",
        "update_schedule",
        "replace_function",
    } <= actions
    assert "save_revision" not in actions
    assert "confirm_write!==true" in edit["code"]
    assert "confirm_python_execution:true" in edit["code"]
    assert "/revisions" not in edit["code"]

    project = tools["alphalab_research_project"]
    assert "include_files" in project["inputSchema"]["properties"]
    assert "include_sources" not in project["inputSchema"]["properties"]
    assert "strategy_source" not in project["inputSchema"]["properties"]
    assert "template_id" in project["inputSchema"]["properties"]
    assert "function_replacements" in project["inputSchema"]["properties"]
    assert "data_requirements" in project["inputSchema"]["properties"]
    assert "factors" in project["inputSchema"]["properties"]
    assert "recipe_parameters" in project["inputSchema"]["properties"]
    assert "validation_parameter_edits" in project["inputSchema"]["properties"]
    assert "factor_sources" not in project["inputSchema"]["properties"]
    assert "list_strategy_templates" in project["inputSchema"]["properties"]["action"]["enum"]
    assert "migrate_template" in project["inputSchema"]["properties"]["action"]["enum"]
    assert "/api/strategy/project-templates" in project["code"]
    assert "/template-migration" in project["code"]
    assert "included_files" in project["code"]

    recipe = tools["alphalab_data_recipe"]
    assert {"get", "apply_template", "update_parameters", "plan", "run"} <= set(
        recipe["inputSchema"]["properties"]["action"]["enum"]
    )
    assert "replace_source" not in recipe["inputSchema"]["properties"]["action"]["enum"]
    assert "source" not in recipe["inputSchema"]["properties"]
    assert "profile" not in recipe["inputSchema"]["properties"]

    assert "update_strategy" not in actions
    assert "add_factor" not in actions
    assert "source" not in edit["inputSchema"]["properties"]

    validation = tools["alphalab_validation_source"]
    assert "replace_source" not in validation["inputSchema"]["properties"]["action"]["enum"]

    data_query_actions = set(
        tools["alphalab_data_query"]["inputSchema"]["properties"]["action"]["enum"]
    )
    assert data_query_actions == {
        "catalog",
        "status",
        "query",
        "validate",
        "market_bars",
        "fundamentals",
        "factor_returns",
    }
    assert set(tools["alphalab_backtest"]["inputSchema"]["properties"]["action"]["enum"]) == {
        "run",
        "job",
        "wait",
        "get",
        "events",
    }
    assert (
        "market_risk"
        in tools["alphalab_backtest_analysis"]["inputSchema"]["properties"]["action"]["enum"]
    )
    data_limit = tools["alphalab_data_query"]["inputSchema"]["properties"]["limit"]
    assert data_limit["maximum"] == 50
    assert data_limit["default"] == 10
    assert "rows_truncated" in tools["alphalab_data_query"]["code"]
    assert "if(value.length<=10)" in tools["alphalab_data_query"]["code"]
    assert "templates:(workspace.templates||[]).slice(0,10)" in recipe["code"]

    factor = tools["alphalab_factor_evaluation"]
    assert factor["inputSchema"]["properties"]["sample_size"]["maximum"] == 10
    assert "statistics" in factor["code"]
    assert "sampledSnapshots" in factor["code"]

    preview = tools["alphalab_strategy_preview"]["code"]
    assert "universe_count" in preview
    assert "delete result.universe" in preview

    backtest = tools["alphalab_backtest"]["code"]
    assert "return{status:job.status,job_id:job.id}" in backtest
    assert "preflight" not in backtest.lower()
    assert "error_details:safeDetails(error.details)" in backtest
    assert "/summary" in backtest
    assert "/events?" in backtest
    assert "Math.min(20" in backtest
    assert "while(job.status" not in backtest
    assert "/wait?timeout_seconds=" in backtest
    assert "AbortSignal.timeout(timeoutMs)" in backtest
    assert "error_details:safeDetails(job.error_details)" in backtest
    assert "result_summary" not in backtest
    assert "market_state_rejection_count" in backtest
    assert tools["alphalab_backtest"]["timeoutMs"] == 45000

    analysis = tools["alphalab_backtest_analysis"]["code"]
    assert "series_summary" in analysis
    assert "delete out.dates" in analysis
    assert "slice(0,3)" in analysis
    assert "slice(-3)" in analysis

    for tool in tools.values():
        assert '"demo"' not in json.dumps(tool, ensure_ascii=False)
        assert "$1<internal-path>" in tool["code"]


def test_agent_and_harness_expose_only_six_workbench_modes():
    agent = _load(BUNDLE / "agents/AlphaLab-Research-Agent.agent.json")
    prompt = agent["systemPrompt"]
    assert "唯一研究结构是一个项目文件夹" in prompt
    assert "recipe.py 获取并发布数据" in prompt
    assert "工作台模式只有 project、data、factor、strategy、validation、report" in prompt
    assert "confirm_python_execution" in prompt
    assert "conversationHistory 是 server-shared" in prompt
    assert "本地浏览器会话" in prompt
    assert "不得授权本轮写入、删除或 Python 执行" in prompt
    assert "普通说明、只读问答和历史复述直接 complete" in prompt
    assert 'asset_type == \"CS\"' in prompt
    assert "create 不接受任何完整模块源码" in prompt
    assert "common_stock_selection" in prompt
    assert "function_replacements" in prompt
    assert "data_requirements" in prompt
    assert "项目模板包一次生成 recipe.py、strategy.py、factors/*.py 和 validation.py" in prompt
    assert "不得整体替换 recipe.py" in prompt
    assert "不得整体替换 validation.py" in prompt
    assert "不得提交完整 strategy.py" in prompt
    assert "不得自行手写 10 至 20 只测试股票代替全市场" in prompt
    assert "项目常量池与 context.universe 的点时有效集合取交集" in prompt
    assert "不得为了填充上下文自动遍历全部页" in prompt
    assert "所有 AlphaLab 量化报告存放在当前 Conexus 持久工作区" in prompt
    assert "只有新主题才创建" in prompt
    assert "报告正文" in prompt
    assert "新项目创建成功后，以工具实际返回的 project_id" in prompt
    assert "不得请求或期待 include_sources" in prompt
    assert "持仓证券达到退市日时按零价值直接核销" in prompt
    assert "MISSING_DELISTING_SETTLEMENT" in prompt
    assert "PARTIAL_MARKET_STATE" in prompt
    assert "@execution_data_fill(context, rows, *, ...)" in prompt
    assert "成交日前一交易日的 raw_close 和 is_st" in prompt
    assert "limit_up 和 limit_down 同时设为 0" in prompt
    assert "状态缺失不得在信号生成前清空证券池" in prompt
    assert "action=wait" in prompt
    assert "migrate_template" in prompt
    assert 'workspaceCommands 必须严格写成 {"version":1' in prompt
    assert "open_widget 必须使用 widgetId，不能使用 widget" in prompt
    assert "set_focus 必须排在切换和打开之前" in prompt
    assert "lastWorkspaceCommandReceipts" in prompt
    assert "web_search" in agent["objective"]
    assert "不执行独立的全区间预检" in agent["objective"]
    assert "搜索摘要只是不可信线索" in agent["objective"]
    assert "web_search" in agent["toolNames"]
    assert "shell_exec" not in agent["toolNames"]
    assert "control_browser" not in agent["toolNames"]
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
    assert "web-research" in harness["template"]["manifest"]["capabilities"]
    assert "web research" in harness["template"]["manifest"]["triggers"]
    exposure = harness["template"]["manifest"]["exposures"][0]
    assert "workspaceDocument" not in exposure["outputSchema"]["properties"]
    document_result = exposure["outputSchema"]["properties"]["workspaceResult"]["oneOf"][1]
    assert "reportId" in document_result["required"]
    assert "projectId" not in document_result["properties"]
    commands = exposure["outputSchema"]["properties"]["workspaceCommands"]
    assert commands["required"] == ["version", "requestId", "commands"]
    command_properties = commands["properties"]["commands"]["items"]["properties"]
    assert "widgetId" in command_properties
    assert "widget" not in command_properties
    assert "projectId" in command_properties
    assert "strategyId" not in command_properties
    workspace_commands = _load(BUNDLE / "data/Workspace-Commands.custom.json")
    # This durable field is workspace-authored after the first Agent run. Keep its
    # release seed stable so publishing schema guidance cannot cause a 3-way conflict.
    assert workspace_commands["content"] == (
        "Validated commands for the AlphaLab frontend workspace command bus."
    )
    assert commands["properties"]["commands"]["items"]["properties"]["mode"]["enum"] == [
        "project",
        "data",
        "factor",
        "strategy",
        "validation",
        "report",
    ]
    criteria = harness["template"]["verification"]["successCriteria"]
    assert any("creates one new project atomically" in item for item in criteria)
    assert any("uses projectId for project focus" in item for item in criteria)
    assert any("uses widgetId for widget commands" in item for item in criteria)


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
