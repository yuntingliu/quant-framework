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
    assert len(json_files) == 20

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
        "alphalab_get_factor_returns",
        "alphalab_get_fundamentals",
        "alphalab_get_workspace_context",
        "alphalab_get_strategy",
        "alphalab_get_market_bars",
        "alphalab_get_backtest",
        "alphalab_run_backtest",
        "alphalab_generate_signal",
    }

    profile_tools = {
        "alphalab_get_workspace_context",
        "alphalab_get_market_bars",
        "alphalab_get_fundamentals",
        "alphalab_get_factor_returns",
        "alphalab_run_backtest",
        "alphalab_generate_signal",
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
        "alphalab_run_backtest",
        "alphalab_generate_signal",
    }
    assert all(tools[name]["sideEffects"] == "write" for name in write_tools)
    assert all(
        tool["sideEffects"] == "read"
        for name, tool in tools.items()
        if name not in write_tools
    )

    agent = _load(BUNDLE / "agents" / "AlphaLab-Research-Agent.agent.json")
    assert set(tools).issubset(agent["toolNames"])
    sync_tool = tools["alphalab_data_run_sync"]
    assert "confirm" not in sync_tool["inputSchema"].get("required", [])
    assert "confirm" not in sync_tool["inputSchema"]["properties"]
    assert "confirm: true" in sync_tool["code"]
    assert "无需询问用户或要求确认" in agent["systemPrompt"]
    assert "不得只打开数据中心让用户点击" in agent["systemPrompt"]


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
    assert "return { request, context }" in client
    assert "exposureId: exposure.id" in hook
    assert "buildRunInput(userMessage" in hook

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
