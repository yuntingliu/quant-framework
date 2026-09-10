from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "integrations/conexus/alphalab-research-agent"
TOOLS = BUNDLE / "tools"
SKILL = BUNDLE / "skills/alphalab-sdk-v1/SKILL.md"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_bundle_json_is_valid_and_contains_no_private_state():
    forbidden = re.compile(r"(?:[A-Za-z]:\\Users\\|/Users/|PRIVATE KEY|RQ_PASSWORD=|RQ_SSH_)", re.I)
    for path in BUNDLE.rglob("*.json"):
        _load(path)
        assert forbidden.search(path.read_text(encoding="utf-8")) is None, path


def test_agent_exposes_two_compact_alphalab_tools():
    tools = {_load(path)["toolName"]: _load(path) for path in TOOLS.glob("*.tool.json")}
    assert set(tools) == {"alphalab_project_files", "alphalab_project_run"}
    assert len(list(TOOLS.glob("*.tool.json"))) == 2

    for tool in tools.values():
        assert tool["runtime"] == "node"
        assert tool["permissions"] == {"network": "alphalab-api"}
        assert "shell" not in tool["code"].lower()
        schema = tool["inputSchema"]
        assert schema["required"] == ["command"]
        assert "args" in schema["properties"]
        assert "action" not in schema["properties"]
        assert "async function execute(input)" in tool["code"]
        assert "function publicFailure(reason)" in tool["code"]
        assert "return publicFailure(reason)" in tool["code"]
        assert "$1<internal-path>" in tool["code"]

    files = tools["alphalab_project_files"]
    assert set(files["inputSchema"]["properties"]) == {
        "command",
        "args",
        "confirm_write",
        "confirm_delete",
        "confirm_python_execution",
    }
    assert set(files["inputSchema"]["properties"]["command"]["enum"]) == {
        "projects.list",
        "projects.get",
        "projects.create",
        "projects.update",
        "projects.migrate_default",
        "projects.delete",
        "files.list",
        "files.read",
        "files.write",
        "files.install_factor_template",
        "templates.factors",
        "strategies.create",
        "strategies.rename",
    }
    assert "sdk-v1-default is read-only" in files["code"]
    assert "Unsupported project file path" in files["code"]
    assert "/api/strategy/projects" in files["code"]
    assert "/api/data-sync/recipes/" in files["code"]
    assert "/api/validation/projects/" in files["code"]
    assert "confirm_write!==true" in files["code"]
    assert "confirm_python_execution!==true" in files["code"]
    assert "confirm_delete!==true" in files["code"]

    run = tools["alphalab_project_run"]
    assert set(run["inputSchema"]["properties"]) == {
        "command",
        "args",
        "confirm_python_execution",
        "confirm_cancel",
    }
    commands = set(run["inputSchema"]["properties"]["command"]["enum"])
    assert {
        "workspace.context",
        "recipe.plan",
        "recipe.sync",
        "data.query",
        "factor.history",
        "factor.research",
        "strategy.preview",
        "backtest.run",
        "backtest.status",
        "backtest.wait",
        "backtest.summary",
        "backtest.events",
        "backtest.analysis",
        "backtest.validation",
        "workspace.outputs.prepare",
    } <= commands
    assert "/wait?timeout_seconds=" in run["code"]
    assert "AbortSignal.timeout(timeoutMs)" in run["code"]
    assert "return{status:job.status,job_id:job.id,strategy_id:" in run["code"]
    assert "while(job.status" not in run["code"]
    assert "Math.min(20" in run["code"]
    assert "rows_truncated" in run["code"]
    assert "seriesSummary" in run["code"]
    for field in (
        "research_valid",
        "execution_reliable",
        "execution_invalid_reasons",
        "research_assessment",
        "research_invalid_reasons",
        "execution_fidelity",
        "attempted_trade_count",
        "successful_trade_count",
        "synthetic_state_count",
        "market_state_rejection_count",
        "suspension_rejection_count",
        "limit_up_rejection_count",
        "limit_down_rejection_count",
        "capacity_rejection_count",
        "cash_rejection_count",
    ):
        assert field in run["code"]


def test_deployment_research_commands_use_canonical_api_and_bound_results():
    script = r"""
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const tool = JSON.parse(readFileSync(process.argv[1], 'utf8'));
const calls = [];
globalThis.fetch = async (url, options) => {
  calls.push({url, body: options.body ? JSON.parse(options.body) : null});
  const value = url.endsWith('/research') ? {evidence: Array.from({length: 30}, (_,i) => ({ic: i}))}
    : url.endsWith('/validation') ? {outputs: {research_evidence: {status: 'pass'}}}
    : {dirty: false, current_revision: 7};
  return {ok: true, json: async () => value};
};
const execute = new Function('input', tool.code + '\nreturn run(input);');
const args = {project_id: 'test', factor_id: 'momentum', start_date: '2025-01-01', end_date: '2025-06-30', horizons: [1,3], quantiles: 4};
assert.equal((await execute({command:'factor.research',args})).status, 'failed');
assert.equal(calls.length, 0);
const result = await execute({command:'factor.research',args,confirm_python_execution:true});
assert.equal(result.status, 'succeeded');
assert.equal(result.result.evidence.count, 30);
assert.equal(result.result.evidence.head.length, 3);
assert.ok(calls[1].url.endsWith('/projects/test/factors/momentum/research'));
assert.equal(calls[1].body.revision, 7);
assert.equal(calls[1].body.confirm_python_execution, true);
assert.deepEqual(calls[1].body.horizons, [1,3]);
assert.equal(calls[1].body.quantiles, 4);
const frozen = await execute({command:'backtest.validation',args:{backtest_id:'frozen-run'}});
assert.equal(frozen.result.outputs.research_evidence.status, 'pass');
assert.ok(calls.at(-1).url.endsWith('/backtests/frozen-run/validation'));
assert.equal(calls.at(-1).body, null);
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(TOOLS / "Project-Run.tool.json")],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert completed.returncode == 0, completed.stderr


def test_sdk_skill_is_a_single_injected_command_guide():
    skill = SKILL.read_text(encoding="utf-8")
    assert skill.startswith("---\nname: alphalab-sdk-v1\n")
    assert "alphalab_project_files" in skill
    assert "alphalab_project_run" in skill
    assert "`command` and one `args` object" in skill
    assert "sdk-v1-default" in skill
    assert "context.universe" in skill
    assert 'asset_type == "CS"' in skill
    assert "backtest.wait" in skill
    assert "at most once" in skill
    assert "prior-session raw, unadjusted prices" in skill
    assert "not a security\nsandbox" in skill

    agent = _load(BUNDLE / "agents/AlphaLab-Research-Agent.agent.json")
    prompt = agent["systemPrompt"]
    assert prompt.count("{{ALPHALAB_SDK_SKILL}}") == 1
    assert prompt.count("{{ALPHALAB_WORKSPACE_COMMAND_SCHEMA}}") == 1
    assert "每条导航命令必须使用 type 作为动作字段，不得使用 command" in prompt
    assert "两个逻辑工具 alphalab_project_files 与 alphalab_project_run" in prompt
    assert "conversationHistory 是 server-shared" in prompt
    assert "不得授权本轮写入、删除、取消或 Python 执行" in prompt
    assert "operations 中同时包含" in prompt
    assert "set.content" in prompt
    assert "workspace.outputs.prepare" in prompt
    assert "禁止只更新 data 而留下旧 content" in prompt
    assert "lastWorkspaceCommandReceipts" in prompt
    assert agent["toolNames"] == ["find", "observe", "create", "edit", "use", "request_user_input", "complete"]


def test_agent_file_and_batch_tools_preserve_explicit_strategy_selection():
    script = r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const files=JSON.parse(readFileSync(process.argv[1],'utf8'));
const runs=JSON.parse(readFileSync(process.argv[2],'utf8'));
const calls=[];
globalThis.fetch=async (url,options)=>{
  const parsed=new URL(url), id=parsed.searchParams.get('strategy_id')||'main';
  const body=options.body?JSON.parse(options.body):null;
  calls.push({path:parsed.pathname,id,body,method:options.method||'GET'});
  const value=parsed.pathname.endsWith('/batches')?{batch_id:'batch',jobs:body.strategy_ids.map(strategy_id=>({id:strategy_id,status:'queued',request:{strategy_id}}))}
    :{id:'research',strategy_id:id,strategy_source:'source for '+id,current_revision:2,dirty:false,source_units:[],strategies:[{id:'main',path:'strategies/main.py'},{id:'alternate',path:'strategies/alternate.py'}],inspection:{entrypoints:[]}};
  return{ok:true,json:async()=>value};
};
const invokeFiles=new Function('input',files.code+'\nreturn run(input);');
const invokeRuns=new Function('input',runs.code+'\nreturn run(input);');
const read=await invokeFiles({command:'files.read',args:{project_id:'research',paths:['strategies/main.py','strategies/alternate.py']}});
assert.equal(read.included_files['strategies/main.py'],'source for main');
assert.equal(read.included_files['strategies/alternate.py'],'source for alternate');
const saved=await invokeFiles({command:'files.write',args:{project_id:'research',path:'strategies/alternate.py',content:'edited source'},confirm_write:true,confirm_python_execution:true});
assert.equal(saved.status,'succeeded');
assert.equal(calls.at(-1).id,'alternate');
assert.equal(calls.at(-1).body.source,'edited source');
assert.equal(calls.at(-1).body.confirm_python_execution,true);
const invalid=await invokeFiles({command:'files.write',args:{project_id:'research',path:'../escape.py',content:'invalid'},confirm_write:true,confirm_python_execution:true});
assert.equal(invalid.status,'failed');
assert.notEqual(calls.at(-1).method,'PUT');
const args={project_id:'research',strategy_ids:['alternate','main'],start_date:'2025-01-01',end_date:'2025-03-31'};
const before=calls.length;
assert.equal((await invokeRuns({command:'backtest.batch',args})).status,'failed');
assert.equal(calls.length,before);
const batch=await invokeRuns({command:'backtest.batch',args,confirm_python_execution:true});
assert.equal(batch.batch_id,'batch');
assert.deepEqual(batch.jobs.map(j=>j.strategy_id),['alternate','main']);
assert.deepEqual(calls.at(-1).body.strategy_ids,args.strategy_ids);
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(TOOLS / "Project-Files.tool.json"), str(TOOLS / "Project-Run.tool.json")],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert completed.returncode == 0, completed.stderr


def test_harness_outputs_allow_explicit_strategy_focus():
    harness = _load(BUNDLE / "harness.json")
    assert "web-research" in harness["template"]["manifest"]["capabilities"]
    exposure = harness["template"]["manifest"]["exposures"][0]
    assert not {"inputs", "outputs", "inputSchema", "outputSchema"}.intersection(exposure)
    outputs = _load(BUNDLE / "workspace-output.schema.json")["properties"]
    assert "workspaceDocument" not in outputs
    document_result = outputs["workspaceResult"]["oneOf"][1]
    assert "reportId" in document_result["required"]
    assert "projectId" not in document_result["properties"]
    commands = outputs["workspaceCommands"]
    assert commands["required"] == ["version", "requestId", "commands"]
    properties = commands["properties"]["commands"]["items"]["properties"]
    assert commands["properties"]["commands"]["items"]["required"] == ["type"]
    assert "command" not in properties
    assert "widgetId" in properties
    assert "widget" not in properties
    assert "projectId" in properties
    assert properties["strategyId"]["pattern"] == "^[a-z][a-z0-9_]{0,63}$"
    assert properties["mode"]["enum"] == [
        "project",
        "data",
        "factor",
        "strategy",
        "validation",
        "report",
    ]

    workspace_commands = _load(BUNDLE / "data/Workspace-Commands.custom.json")
    assert workspace_commands["content"] == (
        "Validated commands for the AlphaLab frontend workspace command bus."
    )


def test_registration_injects_skill_and_replaces_old_tool_nodes():
    source = (ROOT / "scripts/register_conexus_research_harness.mjs").read_text(
        encoding="utf-8"
    )
    assert "Project-Files.tool.json" in source
    assert "Project-Run.tool.json" in source
    assert "skills/alphalab-sdk-v1/SKILL.md" in source
    assert "sdkSkillPlaceholder" in source
    assert ".replace(sdkSkillPlaceholder, sdkSkill.trim())" in source
    assert ".replace(workspaceCommandSchemaPlaceholder, JSON.stringify(workspaceCommandSchema))" in source

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
        assert filename not in source

    for node_id in (
        "alphalab-tool-workspace-context-v1",
        "alphalab-tool-research-project-v1",
        "alphalab-tool-data-recipe-v1",
        "alphalab-tool-data-sync-job-v1",
        "alphalab-tool-data-query-v1",
        "alphalab-tool-edit-strategy-source-v1",
        "alphalab-tool-evaluate-strategy-factor-v1",
        "alphalab-tool-preview-strategy-v1",
        "alphalab-tool-validation-source-v1",
        "alphalab-tool-backtest-v1",
        "alphalab-tool-analyze-backtest-v1",
        "alphalab-tool-pipeline-project-v1",
        "alphalab-tool-paper-state-v1",
    ):
        assert node_id in source
    assert "obsoleteNodeIds" in source
