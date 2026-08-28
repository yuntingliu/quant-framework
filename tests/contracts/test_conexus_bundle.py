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


def test_tools_use_the_strategy_sdk_v1_contract():
    tools = {_load(path)["toolName"]: _load(path) for path in TOOLS.glob("*.json")}
    required = {
        "alphalab_get_strategy_project",
        "alphalab_edit_strategy_source",
        "alphalab_preview_strategy",
        "alphalab_evaluate_strategy_factor",
        "alphalab_run_backtest",
    }
    assert required <= set(tools)
    for removed in {
        "alphalab_get_pipeline_project",
        "alphalab_manage_pipeline",
        "alphalab_preview_pipeline",
        "alphalab_run_python_lab",
        "alphalab_promote_python_lab",
        "alphalab_evaluate_factor",
    }:
        assert removed not in tools
    for tool in tools.values():
        assert tool["runtime"] == "node"
        assert tool["permissions"] == {"network": "alphalab-api"}
        assert "shell" not in tool["code"].lower()
    for name in (
        "alphalab_preview_strategy",
        "alphalab_evaluate_strategy_factor",
        "alphalab_run_backtest",
    ):
        assert "confirm_python_execution" in tools[name]["inputSchema"]["properties"]
        assert "source_sha256" in tools[name]["code"]
    edit = tools["alphalab_edit_strategy_source"]
    assert {"update_parameter", "update_schedule", "replace_function", "save_revision"} <= set(
        edit["inputSchema"]["properties"]["operation"]["enum"]
    )
    assert "confirm_write!==true" in edit["code"]


def test_agent_and_harness_expose_only_six_workbench_modes():
    agent = _load(BUNDLE / "agents/AlphaLab-Research-Agent.agent.json")
    prompt = agent["systemPrompt"]
    assert "当前且唯一的可执行策略合同是 alphalab.sdk.v1" in prompt
    assert "不存在表达式运行器、三阶段组件拼接、Python Lab" in prompt
    assert "confirm_python_execution" in prompt
    assert "alphalab_get_strategy_project" in agent["toolNames"]
    harness = _load(BUNDLE / "harness.json")
    commands = harness["template"]["manifest"]["exposures"][0]["outputSchema"]["properties"][
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
        "Get-Strategy-Project.tool.json",
        "Edit-Strategy-Source.tool.json",
        "Preview-Strategy.tool.json",
        "Evaluate-Strategy-Factor.tool.json",
    ):
        assert filename in source
    for filename in ("Get-Pipeline-Project.tool.json", "Run-Python-Lab.tool.json"):
        assert filename not in source
    assert "alphalab-tool-pipeline-project-v1" in source
    assert "obsoleteNodeIds" in source
