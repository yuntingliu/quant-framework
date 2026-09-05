from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from dashboard.backend.routers import conexus
from dashboard.backend.services.workspace_output_service import (
    prepare_workspace_outputs,
    validate_workspace_delivery,
)


def _outputs():
    return {
        "summary": "Updated the current report.",
        "decisionNotebook": {
            "classification": "reviewed", "baseCase": "Current strategy evidence.",
            "riskCase": "Trading constraints.", "nextAction": "Review the report.",
            "candidateExpressions": [],
        },
        "workspaceResult": {
            "version": 1, "requestId": "request-1", "kind": "document",
            "reportId": "report-1", "title": "Current report", "sources": [],
        },
        "workspaceCommands": {
            "version": 1, "requestId": "request-1", "commands": [
                {"type": "set_focus", "projectId": "new-project"},
                {"type": "switch_mode", "mode": "strategy"},
                {"type": "open_widget", "widgetId": "strategy.workbench"},
                {"type": "open_result", "resultId": "report-1"},
            ],
        },
    }


def _workspace():
    prepared = prepare_workspace_outputs("request-1", _outputs())
    nodes = [
        {"id": operation["node_id"], "updatedByRunId": "run-1", "values": {**operation["set"], "content": "stale old report"}}
        for operation in prepared["operations"]
    ]
    nodes.append({"id": "report-1", "type": "note", "description": "AlphaLab quantitative report", "values": {"content": "# Current report"}})
    return {"nodes": nodes}


def test_prepare_returns_the_complete_atomic_batch_with_derived_content():
    app = FastAPI()
    app.include_router(conexus.router)
    outputs = _outputs()
    response = TestClient(app).post("/api/conexus/outputs/prepare", json={"request_id": "request-1", "outputs": outputs})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "succeeded"
    assert len(result["operations"]) == 3
    assert all(operation["kind"] == "patch" and set(operation["set"]) == {"content", "data"} for operation in result["operations"])
    assert result["operations"][0]["set"]["content"] == outputs["decisionNotebook"]["baseCase"]
    assert result["operations"][1]["set"]["content"] == "研究报告：Current report"


@pytest.mark.parametrize("failure", ["command_key", "missing_widget", "stale_request", "wrong_report", "wrong_order", "unknown_widget", "missing_title"])
def test_prepare_rejects_wrong_fields_and_cross_output_links(failure):
    outputs = _outputs()
    commands = outputs["workspaceCommands"]["commands"]
    if failure == "command_key":
        commands[0]["command"] = commands[0].pop("type")
    elif failure == "missing_widget":
        commands[2].pop("widgetId")
    elif failure == "stale_request":
        outputs["workspaceResult"]["requestId"] = "older-request"
    elif failure == "wrong_report":
        commands[3]["resultId"] = "older-report"
    elif failure == "wrong_order":
        commands.append(commands.pop(0))
    elif failure == "unknown_widget":
        commands[2]["widgetId"] = "unknown.workbench"
    else:
        outputs["workspaceResult"].pop("title")
    result = prepare_workspace_outputs("request-1", outputs)
    assert result["status"] == "failed"
    assert result["error_code"] == "INVALID_WORKSPACE_OUTPUTS"
    assert result["errors"]
    assert "operations" not in result


def test_workspace_proxy_blocks_invalid_delivery_and_preserves_report_history(monkeypatch):
    workspace = _workspace()
    workspace["nodes"][2]["values"]["data"]["commands"][3]["resultId"] = "missing-report"
    original = deepcopy(workspace)

    async def forward(*args, **kwargs):
        return JSONResponse({"workspace": workspace})

    monkeypatch.setattr(conexus, "_forward", forward)
    app = FastAPI()
    app.include_router(conexus.router)
    delivered = TestClient(app).get("/api/conexus/workspace").json()["workspace"]
    assert delivered["outputValidation"]["status"] == "failed"
    assert "commands" not in delivered["nodes"][2]["values"]["data"]
    assert delivered["nodes"][-1] == workspace["nodes"][-1]
    assert workspace == original  # No mutation of persisted Conexus nodes.


def test_workspace_delivery_derives_content_and_rejects_partial_commits():
    workspace = _workspace()
    delivered = validate_workspace_delivery(deepcopy(workspace))
    assert delivered["outputValidation"]["status"] == "succeeded"
    assert delivered["nodes"][1]["values"]["content"] == "研究报告：Current report"
    workspace["nodes"][0]["updatedByRunId"] = "older-run"
    assert validate_workspace_delivery(workspace)["outputValidation"]["status"] == "failed"


def test_outputs_reject_nonfinite_json_and_missing_report_documents():
    outputs = _outputs()
    outputs["workspaceResult"]["rows"] = [{"value": float("nan")}]
    assert prepare_workspace_outputs("request-1", outputs)["error_code"] == "INVALID_WORKSPACE_OUTPUTS"
    workspace = _workspace()
    workspace["nodes"][-1]["type"] = "tool"
    assert validate_workspace_delivery(workspace)["outputValidation"]["status"] == "failed"
