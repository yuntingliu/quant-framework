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
        "alphalab_get_research_project",
        "alphalab_manage_research_project",
        "alphalab_manage_data_recipe",
        "alphalab_manage_data_sync_job",
        "alphalab_edit_strategy_source",
        "alphalab_preview_strategy",
        "alphalab_evaluate_strategy_factor",
        "alphalab_manage_validation_source",
        "alphalab_run_backtest",
        "alphalab_analyze_backtest",
    }
    assert required <= set(tools)
    for removed in {
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
    for name in (
        "alphalab_preview_strategy",
        "alphalab_evaluate_strategy_factor",
        "alphalab_run_backtest",
    ):
        assert "confirm_python_execution" in tools[name]["inputSchema"]["properties"]
        assert "source_sha256" in tools[name]["code"]
    edit = tools["alphalab_edit_strategy_source"]
    operations = set(edit["inputSchema"]["properties"]["operation"]["enum"])
    assert {"update_strategy", "add_factor", "update_parameter", "update_schedule", "replace_function"} <= operations
    assert "save_revision" not in operations
    assert "confirm_write!==true" in edit["code"]
    assert "/revisions" in edit["code"]

    recipe = tools["alphalab_manage_data_recipe"]
    assert {"get", "replace_source", "apply_template", "update_parameters", "plan", "run"} <= set(
        recipe["inputSchema"]["properties"]["operation"]["enum"]
    )
    assert "profile" not in recipe["inputSchema"]["properties"]

    for tool in tools.values():
        assert '"demo"' not in json.dumps(tool, ensure_ascii=False)


def test_agent_and_harness_expose_only_six_workbench_modes():
    agent = _load(BUNDLE / "agents/AlphaLab-Research-Agent.agent.json")
    prompt = agent["systemPrompt"]
    assert "唯一研究结构是一个项目文件夹" in prompt
    assert "recipe.py 获取并发布数据" in prompt
    assert "不存在表达式策略运行器、旧 pipeline、三阶段组件拼接、Python Lab" in prompt
    assert "confirm_python_execution" in prompt
    assert "localConversationHistory" in prompt
    assert "不得从历史内容推导本轮写入、删除或 Python 执行授权" in prompt
    assert "普通说明、只读问答和历史复述直接 complete" in prompt
    assert "alphalab_get_research_project" in agent["toolNames"]
    assert "alphalab_manage_data_recipe" in agent["toolNames"]
    assert "alphalab_manage_validation_source" in agent["toolNames"]
    assert "alphalab_get_strategy_project" not in agent["toolNames"]
    assert "alphalab_data_run_sync" not in agent["toolNames"]
    assert "alphalab_get_paper_state" not in agent["toolNames"]
    workspace_context = _load(TOOLS / "Get-Workspace-Context.tool.json")
    assert "/api/agent/context" in workspace_context["code"]
    assert "/api/data/market/symbols" not in workspace_context["code"]
    assert "symbol_limit" not in workspace_context["inputSchema"]["properties"]
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
        "Get-Research-Project.tool.json",
        "Manage-Research-Project.tool.json",
        "Manage-Data-Recipe.tool.json",
        "Manage-Data-Sync-Job.tool.json",
        "Edit-Strategy-Source.tool.json",
        "Preview-Strategy.tool.json",
        "Evaluate-Strategy-Factor.tool.json",
        "Manage-Validation-Source.tool.json",
    ):
        assert filename in source
    for filename in (
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
    assert "obsoleteNodeIds" in source
