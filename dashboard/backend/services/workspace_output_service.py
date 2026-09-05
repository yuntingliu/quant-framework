"""Validate the canonical Harness outputs and derive their display content."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_HARNESS = Path(__file__).resolve().parents[3] / "integrations/conexus/alphalab-research-agent/harness.json"
_OUTPUT_NODES = {
    "decisionNotebook": "alphalab-decision-notebook-v1",
    "workspaceResult": "alphalab-workspace-result-v1",
    "workspaceCommands": "alphalab-workspace-commands-v1",
}
_WIDGETS = {f"{mode}.workbench" for mode in ("project", "data", "factor", "strategy", "validation", "report")}


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    harness = json.loads(_HARNESS.read_text(encoding="utf-8"))
    exposure = next(
        item for item in harness["template"]["manifest"]["exposures"]
        if item["id"] == "alphalab-research-agent"
    )
    schema = exposure["outputSchema"]
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def prepare_workspace_outputs(request_id: str, outputs: dict[str, Any]) -> dict[str, Any]:
    """Return a complete atomic edit batch, or a bounded error the Agent can correct."""

    try:
        encoded = json.dumps(outputs, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return _invalid(["outputs must contain only finite JSON values"])
    if len(encoded) > 600_000:
        return _invalid(["outputs exceeds the 600000-character limit"])
    errors = sorted(_validator().iter_errors(outputs), key=lambda error: str(error.json_path))
    if errors:
        # Do not echo rejected content: it may contain source, secrets, or unrelated old reports.
        return _invalid([
            f"{'.'.join(map(str, error.absolute_path)) or 'outputs'}: invalid {error.validator}; follow the canonical output schema"
            for error in errors[:10]
        ])
    result = outputs["workspaceResult"]
    batch = outputs["workspaceCommands"]
    if result["requestId"] != request_id or batch["requestId"] != request_id:
        return _invalid(["workspaceResult and workspaceCommands must use the current request_id"])
    semantic_errors = []
    commands = batch["commands"]
    focus_indexes = [index for index, command in enumerate(commands) if command["type"] == "set_focus"]
    for index, command in enumerate(commands):
        kind = command["type"]
        if kind == "open_result" and (
            result["kind"] != "document" or command["resultId"] != result["reportId"]
        ):
            semantic_errors.append("open_result must reference the current workspaceResult.reportId")
        if kind in {"open_widget", "close_widget"} and command["widgetId"] not in _WIDGETS:
            semantic_errors.append("widgetId must be a declared AlphaLab workbench")
        if kind in {"switch_mode", "open_widget", "open_result"} and any(position > index for position in focus_indexes):
            semantic_errors.append("set_focus must precede mode and open commands")
        if kind == "set_focus" and not any(key in command for key in ("projectId", "backtestId", "symbol", "date")):
            semantic_errors.append("set_focus requires a project, backtest, symbol, or date")
    if semantic_errors:
        return _invalid(list(dict.fromkeys(semantic_errors)))
    content = {
        "decisionNotebook": outputs["decisionNotebook"]["baseCase"][:600],
        "workspaceResult": f"研究报告：{result['title']}" if result["kind"] == "document" else "本轮未交付研究报告。",
        "workspaceCommands": "工作台请求：" + " → ".join(command["type"] for command in commands) if commands else "本轮无工作台导航请求。",
    }
    return {
        "status": "succeeded",
        "request_id": request_id,
        "summary": outputs["summary"],
        "operations": [
            {"kind": "patch", "node_id": node_id, "set": {"content": content[key], "data": outputs[key]}}
            for key, node_id in _OUTPUT_NODES.items()
        ],
    }


def _invalid(errors: list[str]) -> dict[str, Any]:
    return {
        "status": "failed",
        "error_code": "INVALID_WORKSPACE_OUTPUTS",
        "error_summary": "Workspace outputs were rejected. Correct the fields and prepare again before editing output nodes.",
        "errors": errors[:10],
    }


def validate_workspace_delivery(workspace: dict[str, Any]) -> dict[str, Any]:
    """Gate browser delivery even when an Agent bypasses output preparation."""

    nodes = {node["id"]: node for node in workspace.get("nodes", [])}
    if not all(node_id in nodes for node_id in _OUTPUT_NODES.values()):
        return workspace
    outputs = {key: nodes[node_id].get("values", {}).get("data") for key, node_id in _OUTPUT_NODES.items()}
    batch = outputs["workspaceCommands"]
    if isinstance(batch, dict) and batch.get("requestId") == "" and batch.get("commands") == []:
        return workspace  # Unused template outputs have no current request to deliver.
    outputs["summary"] = "Workspace delivery"
    request_id = batch.get("requestId", "") if isinstance(batch, dict) else ""
    prepared = prepare_workspace_outputs(request_id, outputs)
    output_runs = {nodes[node_id].get("updatedByRunId") for node_id in _OUTPUT_NODES.values()}
    if len(output_runs) != 1 or not all(output_runs):
        prepared = _invalid(["The three Harness outputs must be committed by the same run"])
    result = outputs["workspaceResult"]
    if prepared["status"] == "succeeded" and result["kind"] == "document":
        report = nodes.get(result["reportId"])
        if not report or report.get("type") not in {"note", "document"} or report.get("description") != "AlphaLab quantitative report" or not report.get("values", {}).get("content"):
            prepared = _invalid(["workspaceResult must reference an existing AlphaLab report Document"])
    workspace["outputValidation"] = {key: value for key, value in prepared.items() if key in {"status", "error_code", "error_summary", "errors"}}
    if prepared["status"] == "failed":
        nodes[_OUTPUT_NODES["workspaceCommands"]]["values"]["data"] = {
            "version": 1, "requestId": request_id, "deliveryError": prepared["error_summary"],
        }
    else:
        for operation in prepared["operations"]:
            nodes[operation["node_id"]]["values"]["content"] = operation["set"]["content"]
    return workspace
