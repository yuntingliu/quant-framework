from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "integrations" / "conexus" / "alphalab-research-agent"
TOOLS = BUNDLE / "tools"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), path
    return value


def test_conexus_bundle_is_complete_and_contains_no_private_state():
    json_files = sorted(BUNDLE.rglob("*.json"))
    assert len(json_files) == 32

    forbidden = re.compile(
        r"(?:[A-Za-z]:\\Users\\|/Users/|PRIVATE KEY|RQ_PASSWORD=|"
        r"IBKR|ib_async|Interactive Brokers|RQ_SSH_|tunnel)",
        re.IGNORECASE,
    )
    for path in json_files:
        text = path.read_text(encoding="utf-8")
        _load(path)
        assert forbidden.search(text) is None, path

    assert all(
        path.relative_to(ROOT).parts[:3] == (
            "integrations",
            "conexus",
            "alphalab-research-agent",
        )
        for path in json_files
    )


def test_conexus_tools_match_barebone_profiles_and_guardrails():
    tools = {_load(path)["toolName"]: _load(path) for path in sorted(TOOLS.glob("*.json"))}
    assert set(tools) == {
        "alphalab_data_catalog",
        "alphalab_data_plan_sync",
        "alphalab_data_query",
        "alphalab_data_run_sync",
        "alphalab_data_status",
        "alphalab_data_validate",
        "alphalab_manage_data_sync_job",
        "alphalab_get_factor_returns",
        "alphalab_get_factor_library",
        "alphalab_evaluate_factor",
        "alphalab_evaluate_market_risk_factor",
        "alphalab_get_fundamentals",
        "alphalab_get_workspace_context",
        "alphalab_get_strategy",
        "alphalab_research_strategy",
        "alphalab_manage_strategy",
        "alphalab_get_market_bars",
        "alphalab_get_backtest",
        "alphalab_analyze_backtest",
        "alphalab_run_backtest",
        "alphalab_manage_research_run",
        "alphalab_generate_signal",
        "alphalab_get_reports",
        "alphalab_get_paper_state",
        "alphalab_preview_paper_rebalance",
        "alphalab_execute_paper_rebalance",
        "alphalab_submit_paper_order",
    }

    profile_tools = {
        "alphalab_get_workspace_context",
        "alphalab_get_market_bars",
        "alphalab_get_fundamentals",
        "alphalab_get_factor_returns",
        "alphalab_evaluate_factor",
        "alphalab_evaluate_market_risk_factor",
        "alphalab_research_strategy",
        "alphalab_run_backtest",
        "alphalab_manage_research_run",
        "alphalab_generate_signal",
        "alphalab_get_paper_state",
        "alphalab_preview_paper_rebalance",
        "alphalab_execute_paper_rebalance",
        "alphalab_submit_paper_order",
    }
    for name, tool in tools.items():
        assert tool["runtime"] == "node"
        assert tool["permissions"] == {"network": "alphalab-api"}
        assert "shell" not in tool["code"].lower()
        if name in profile_tools:
            profile = tool["inputSchema"]["properties"]["profile"]
            assert profile == {
                "type": "string",
                "enum": ["demo", "runtime"],
                "default": "demo",
            }
            assert "profile" in tool["code"]

    write_tools = {
        "alphalab_data_run_sync",
        "alphalab_manage_data_sync_job",
        "alphalab_manage_strategy",
        "alphalab_research_strategy",
        "alphalab_run_backtest",
        "alphalab_manage_research_run",
        "alphalab_generate_signal",
        "alphalab_execute_paper_rebalance",
        "alphalab_submit_paper_order",
    }
    assert all(tools[name]["sideEffects"] == "write" for name in write_tools)
    assert all(
        tool["sideEffects"] == "read"
        for name, tool in tools.items()
        if name not in write_tools
    )

    agent = _load(BUNDLE / "agents" / "AlphaLab-Research-Agent.agent.json")
    assert set(tools).issubset(agent["toolNames"])
    assert "update_nodes" in agent["toolNames"]
    assert "commit_harness_outputs" not in agent["toolNames"]
    assert "只能调用一次 update_nodes" in agent["systemPrompt"]
    assert "同一个 updates 批次" in agent["systemPrompt"]
    assert "已移除的 commit_harness_outputs" in agent["systemPrompt"]
    sync_tool = tools["alphalab_data_run_sync"]
    assert "confirm" not in sync_tool["inputSchema"].get("required", [])
    assert "confirm" not in sync_tool["inputSchema"]["properties"]
    assert "confirm: true" in sync_tool["code"]
    assert "无需询问用户" in agent["systemPrompt"]
    assert "strategy_type 只能是 stock_selection 或 market_timing" in agent["systemPrompt"]
    assert "信号设计、组合构建、风险控制和交易执行" in agent["systemPrompt"]
    assert "confirm_python_execution=true" in agent["systemPrompt"]
    assert "不可信研究数据" in agent["systemPrompt"]
    assert "confirm_write=true" in agent["systemPrompt"]
    assert "confirm_delete=true" in agent["systemPrompt"]
    assert "confirm_execute=true" in agent["systemPrompt"]
    assert "confirm_order=true" in agent["systemPrompt"]

    strategy_tool = tools["alphalab_get_strategy"]
    assert strategy_tool["inputSchema"]["properties"]["include_python_source"] == {
        "type": "boolean",
        "default": False,
    }
    assert "delete payload.python_source" in strategy_tool["code"]
    backtest_tool = tools["alphalab_get_backtest"]
    assert backtest_tool["inputSchema"]["properties"]["include_python_source"] == {
        "type": "boolean",
        "default": False,
    }
    assert "payload.provenance.strategy_python" in backtest_tool["code"]
    for name in ("alphalab_run_backtest", "alphalab_generate_signal"):
        tool = tools[name]
        assert tool["inputSchema"]["properties"]["confirm_python_execution"] == {
            "type": "boolean",
            "default": False,
        }
        assert "strategy.implementation === 'python'" in tool["code"]
        assert "confirm_python_execution !== true" in tool["code"]
        assert "python_source_sha256" in tool["code"]
    assert "result.provenance.strategy_python" in tools["alphalab_run_backtest"]["code"]
    assert "strategy.strategy_type !== 'stock_selection'" in tools[
        "alphalab_generate_signal"
    ]["code"]

    strategy_research = tools["alphalab_research_strategy"]
    assert strategy_research["inputSchema"]["properties"]["operation"]["enum"] == [
        "validate",
        "selection_preview",
        "timing_research",
    ]
    assert "confirm_python_execution !== true" in strategy_research["code"]
    strategy_manager = tools["alphalab_manage_strategy"]
    assert "confirm_write !== true" in strategy_manager["code"]
    assert "confirm_delete !== true" in strategy_manager["code"]
    assert "delete payload.python_source" in strategy_manager["code"]
    analysis = tools["alphalab_analyze_backtest"]
    assert analysis["inputSchema"]["properties"]["operation"]["enum"] == [
        "analysis",
        "robustness",
        "compare",
    ]
    assert "confirm: true" in tools["alphalab_execute_paper_rebalance"]["code"]
    assert "confirm_execute !== true" in tools[
        "alphalab_execute_paper_rebalance"
    ]["code"]
    assert "confirm_order !== true" in tools["alphalab_submit_paper_order"]["code"]
    assert "real broker" in tools["alphalab_submit_paper_order"]["description"]


def test_conexus_result_schema_supports_bounded_structured_charts():
    harness = _load(BUNDLE / "harness.json")
    manifest = harness["template"]["manifest"]
    assert manifest["defaultExposureId"] == "alphalab-research-agent"
    assert "entrypoint" not in manifest
    assert "inputs" not in manifest
    assert "outputs" not in manifest
    exposure = manifest["exposures"][0]
    assert exposure["nodeId"] == "alphalab-research-agent-v1"
    assert exposure["surfaces"] == ["agent_tool", "page", "api"]
    assert [item["key"] for item in exposure["inputs"]] == ["request"]
    assert exposure["inputSchema"] == {
        "type": "object",
        "additionalProperties": False,
        "required": ["request"],
        "properties": {
            "request": {"type": "string", "minLength": 1, "maxLength": 100000}
        },
    }
    descriptor = exposure["outputSchema"]["properties"]["workspaceResult"]
    document = descriptor["oneOf"][1]
    charts = document["properties"]["charts"]
    assert charts["maxItems"] == 6
    chart = charts["items"]
    assert chart["properties"]["type"]["enum"] == [
        "line",
        "bar",
        "area",
        "scatter",
        "pie",
    ]
    assert chart["properties"]["series"]["maxItems"] == 12
    assert chart["properties"]["rows"]["maxItems"] == 500
    commands = exposure["outputSchema"]["properties"]["workspaceCommands"]
    command_properties = commands["properties"]["commands"]["items"]["properties"]
    assert command_properties["mode"]["enum"] == [
        "data",
        "factor",
        "strategy",
        "backtest",
        "report",
    ]
    assert "tab" not in command_properties


def test_conexus_registration_keeps_runtime_and_rq_environment_separate():
    register = (ROOT / "scripts" / "register_conexus_research_harness.mjs").read_text(
        encoding="utf-8"
    )
    start = (ROOT / "scripts" / "start_conexus_web.ps1").read_text(encoding="utf-8")

    assert "integrations/conexus/alphalab-research-agent" in register
    assert ".conexus" in register
    assert "workspace/harnesses/AlphaLab-Research-Agent-v1" in register
    assert "await cp(sourceBundlePath, stagedBundleAbsolutePath" in register
    assert 'hostingSlug: "alphalab-research-agent"' in register
    assert '"Evaluate AlphaLab Factor", "Evaluate-Factor.tool.json"' in register
    assert '"Evaluate AlphaLab Market Risk Factor", "Evaluate-Market-Risk-Factor.tool.json"' in register
    assert '"Manage AlphaLab Strategy", "Manage-Strategy.tool.json"' in register
    assert '"Analyze AlphaLab Backtest", "Analyze-Backtest.tool.json"' in register
    assert '"Execute AlphaLab Paper Rebalance", "Execute-Paper-Rebalance.tool.json"' in register
    assert "exposeInHarness: true" in register
    assert "publicationSlug" not in register
    assert "showOnHarnessPreview" not in register
    assert "Join-Path $ProjectRoot '.env'" not in start


def test_dashboard_uses_current_hosted_exposure_manifest_contract():
    client = (
        ROOT
        / "dashboard"
        / "frontend"
        / "src"
        / "lib"
        / "conexus"
        / "publishedHarnessClient.ts"
    ).read_text(encoding="utf-8")
    hook = (
        ROOT
        / "dashboard"
        / "frontend"
        / "src"
        / "hooks"
        / "usePublishedAgent.ts"
    ).read_text(encoding="utf-8")

    assert "manifest.inputs" not in client
    assert "manifest.exposures.find" in client
    assert "manifest.defaultExposureId" in client
    assert "exposure.inputs" not in client
    assert "AlphaLab workspace context (JSON)" not in client
    assert "return { request }" in client
    assert "workspaceContext: context" in client
    assert "return { request, context }" not in client
    assert "exposureId: exposure.id" in hook
    assert "buildRunInput(userMessage" in hook
    register = (ROOT / "scripts" / "register_conexus_research_harness.mjs").read_text(
        encoding="utf-8"
    )
    assert 'const obsoleteNodeIds = new Set(["alphalab-research-context-v1"])' in register
    assert "!obsoleteNodeIds.has(node.id)" in register

    command_parser = (
        ROOT
        / "dashboard"
        / "frontend"
        / "src"
        / "workspace"
        / "agentCommands.ts"
    ).read_text(encoding="utf-8")
    assert "RightRailTab" not in command_parser
    assert "value.tab !== undefined" in command_parser


def test_hosted_linux_tunnel_uses_a_private_api_listener():
    deployment = ROOT / "deploy" / "conexus-cloud"
    api_service = (deployment / "alphalab-conexus-api.service").read_text(
        encoding="utf-8"
    )
    tunnel_service = (deployment / "alphalab-hk-tunnel.service").read_text(
        encoding="utf-8"
    )

    assert "dashboard.backend.main:app" in api_service
    assert "--host 127.0.0.1 --port 8101" in api_service
    assert "127.0.0.1:18000:127.0.0.1:8101" in tunnel_service
    assert "127.0.0.1:18000:127.0.0.1:8100" not in tunnel_service
