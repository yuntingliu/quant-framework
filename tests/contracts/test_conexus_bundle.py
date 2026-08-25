from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "integrations" / "conexus" / "alphalab-research-agent"
TOOLS = BUNDLE / "tools"
STAGES = ["selection", "portfolio", "execution"]


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), path
    return value


def test_conexus_bundle_is_complete_and_contains_no_private_state():
    json_files = sorted(BUNDLE.rglob("*.json"))
    assert len(json_files) == 31
    forbidden = re.compile(
        r"(?:[A-Za-z]:\\Users\\|/Users/|PRIVATE KEY|RQ_PASSWORD=|"
        r"IBKR|ib_async|Interactive Brokers|RQ_SSH_|tunnel)",
        re.IGNORECASE,
    )
    for path in json_files:
        text = path.read_text(encoding="utf-8")
        _load(path)
        assert forbidden.search(text) is None, path


def test_conexus_tools_match_the_python_pipeline_contract():
    tools = {_load(path)["toolName"]: _load(path) for path in TOOLS.glob("*.json")}
    assert set(tools) == {
        "alphalab_analyze_backtest",
        "alphalab_data_catalog",
        "alphalab_data_plan_sync",
        "alphalab_data_query",
        "alphalab_data_run_sync",
        "alphalab_data_status",
        "alphalab_data_validate",
        "alphalab_evaluate_factor",
        "alphalab_evaluate_market_risk_factor",
        "alphalab_execute_paper_rebalance",
        "alphalab_get_backtest",
        "alphalab_get_factor_library",
        "alphalab_get_factor_returns",
        "alphalab_get_fundamentals",
        "alphalab_get_market_bars",
        "alphalab_get_paper_state",
        "alphalab_get_pipeline_project",
        "alphalab_get_reports",
        "alphalab_save_report",
        "alphalab_get_workspace_context",
        "alphalab_manage_data_sync_job",
        "alphalab_manage_pipeline",
        "alphalab_preview_paper_rebalance",
        "alphalab_preview_pipeline",
        "alphalab_run_backtest",
        "alphalab_submit_paper_order",
    }
    write_tools = {
        "alphalab_data_run_sync",
        "alphalab_manage_data_sync_job",
        "alphalab_manage_pipeline",
        "alphalab_preview_pipeline",
        "alphalab_run_backtest",
        "alphalab_save_report",
        "alphalab_execute_paper_rebalance",
        "alphalab_submit_paper_order",
    }
    for name, tool in tools.items():
        assert tool["runtime"] == "node"
        assert tool["permissions"] == {"network": "alphalab-api"}
        assert "shell" not in tool["code"].lower()
        assert tool["sideEffects"] == ("write" if name in write_tools else "read")

    get_project = tools["alphalab_get_pipeline_project"]
    assert get_project["inputSchema"]["properties"]["include_composed_source"] == {
        "type": "boolean",
        "default": False,
    }
    assert "delete value.composed_source" in get_project["code"]

    manager = tools["alphalab_manage_pipeline"]
    assert manager["inputSchema"]["properties"]["stage"]["enum"] == STAGES
    assert "create_project" in manager["inputSchema"]["properties"]["operation"]["enum"]
    assert "'/api/pipeline/projects'" in manager["code"]
    assert "confirm_write!==true" in manager["code"]
    assert "confirm_delete!==true" in manager["code"]

    for name in ("alphalab_preview_pipeline", "alphalab_run_backtest"):
        tool = tools[name]
        assert "confirm_python_execution" in tool["inputSchema"]["properties"]
        assert "confirm_python_execution!==true" in tool["code"]
        assert "source_sha256" in tool["code"]
    preview = tools["alphalab_preview_pipeline"]
    assert "stage" in preview["inputSchema"]["required"]
    assert preview["inputSchema"]["properties"]["stage"]["enum"] == STAGES
    assert "JSON.stringify({stage," in preview["code"]
    assert "component_manifest.length!==3" in tools["alphalab_run_backtest"]["code"]
    assert "'/api/backtests/jobs'" in tools["alphalab_run_backtest"]["code"]
    assert "job.status==='queued'||job.status==='running'" in tools["alphalab_run_backtest"]["code"]
    save_report = tools["alphalab_save_report"]
    assert save_report["inputSchema"]["required"] == ["profile", "workspace_result", "markdown"]
    assert "'/api/reports'" in save_report["code"]
    assert "markdown:input.markdown" in save_report["code"]


def test_agent_and_harness_use_only_the_six_user_facing_workbenches():
    agent = _load(BUNDLE / "agents" / "AlphaLab-Research-Agent.agent.json")
    assert agent["model"] == "openai/gpt-5.6-sol"
    tools = {_load(path)["toolName"] for path in TOOLS.glob("*.json")}
    assert tools.issubset(agent["toolNames"])
    for removed in (
        "alphalab_get_strategy",
        "alphalab_manage_strategy",
        "alphalab_research_strategy",
        "alphalab_generate_signal",
        "alphalab_manage_research_run",
    ):
        assert removed not in agent["toolNames"]
    prompt = agent["systemPrompt"]
    for text in (
        "DataSnapshot/项目股票池与核心资格闸门 → select_assets → construct_portfolio → configure_execution",
        "不得使用或生成 YAML",
        "唯一策略模型是版本固定的三阶段 Python 管线",
        "不存在 universe、timing 或独立 risk 策略阶段",
        "不得声称 Python 能绕过核心闸门",
        "单独的 selection API 预览仍不得继续运行 portfolio 或 execution",
        "等权、按得分或按排名衰减的仓位分配控制",
        "attribution",
        "factor.workbench",
        "执行假设请求切换 backtest",
        "只能调用一次 update_nodes",
        "必须在 update_nodes 前调用且只调用一次 alphalab_save_report",
    ):
        assert text in prompt

    harness = _load(BUNDLE / "harness.json")
    exposure = harness["template"]["manifest"]["exposures"][0]
    assert [item["key"] for item in exposure["inputs"]] == ["request"]
    assert exposure["inputSchema"]["required"] == ["request"]
    commands = exposure["outputSchema"]["properties"]["workspaceCommands"]
    mode_enum = commands["properties"]["commands"]["items"]["properties"]["mode"]["enum"]
    assert mode_enum == [
        "data",
        "factor",
        "project",
        "selection",
        "backtest",
        "report",
    ]


def test_registration_script_installs_current_tools_and_removes_old_nodes():
    register = (ROOT / "scripts" / "register_conexus_research_harness.mjs").read_text(
        encoding="utf-8"
    )
    for filename in (
        "Get-Pipeline-Project.tool.json",
        "Manage-Pipeline.tool.json",
        "Preview-Pipeline.tool.json",
        "Save-Report.tool.json",
    ):
        assert filename in register
    for filename in (
        "Get-Strategy.tool.json",
        "Manage-Strategy.tool.json",
        "Research-Strategy.tool.json",
        "Generate-Signal.tool.json",
    ):
        assert filename not in register
    assert "alphalab-tool-strategy-v1" in register
    assert "obsoleteNodeIds" in register


def test_dashboard_invocation_input_is_exactly_request():
    client = (
        ROOT / "dashboard" / "frontend" / "src" / "lib" / "conexus" / "publishedHarnessClient.ts"
    ).read_text(encoding="utf-8")
    assert "return { request }" in client
    assert "return { request, context }" not in client
