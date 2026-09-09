"""Narrow same-origin proxy for the published AlphaLab Research Agent."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alphalab.utils.paths import RUNTIME_DIR
from apps.api.services.agent_conversation_service import (
    MAX_CONVERSATION_BYTES,
    AgentConversationStore,
)
from apps.api.services.workspace_output_service import (
    prepare_workspace_outputs,
    validate_workspace_delivery,
)

router = APIRouter(prefix="/api/conexus", tags=["conexus"])


class PrepareOutputsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=200)
    outputs: dict[str, Any]


@router.post("/outputs/prepare")
def prepare_outputs(request: PrepareOutputsRequest) -> dict[str, Any]:
    return prepare_workspace_outputs(request.request_id, request.outputs)

_DEFAULT_WEB_ORIGIN = "http://127.0.0.1:3000"
_DEFAULT_PUBLICATION_SLUG = "alphalab-research-agent"
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AgentConversationMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=200)
    role: Literal["user", "assistant"]
    content: str = Field(max_length=40_000)
    created_at: datetime = Field(alias="createdAt")
    run_id: str = Field(alias="runId", min_length=1, max_length=200)
    artifacts: list[dict[str, Any]] | None = Field(default=None, max_length=40)
    error: Literal[True] | None = None


class AgentResearchCheckpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: Literal[1]
    run_id: str = Field(alias="runId", min_length=1, max_length=200)
    updated_at: datetime = Field(alias="updatedAt")
    decision_notebook: dict[str, Any] | None = Field(default=None, alias="decisionNotebook")
    workspace_result: dict[str, Any] | None = Field(default=None, alias="workspaceResult")


class AgentConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=120)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    messages: list[AgentConversationMessageRequest] = Field(max_length=80)
    research_checkpoint: AgentResearchCheckpointRequest | None = Field(
        default=None,
        alias="researchCheckpoint",
    )

    @model_validator(mode="after")
    def validate_encoded_size(self) -> AgentConversationRequest:
        payload = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_CONVERSATION_BYTES:
            raise ValueError("Agent conversation exceeds the shared storage limit")
        return self


def _web_origin() -> str:
    return os.getenv("CONEXUS_WEB_ORIGIN", _DEFAULT_WEB_ORIGIN).rstrip("/")


def _publication_slug() -> str:
    value = os.getenv("CONEXUS_PUBLICATION_SLUG", _DEFAULT_PUBLICATION_SLUG).strip().lower()
    return value if _SLUG.fullmatch(value) else _DEFAULT_PUBLICATION_SLUG


def _workspace_token() -> str:
    return os.getenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "").strip()


def _conversation_store() -> AgentConversationStore:
    configured = os.getenv("ALPHALAB_RUNTIME_DIR", "").strip()
    runtime_dir = Path(configured).expanduser() if configured else RUNTIME_DIR
    return AgentConversationStore(runtime_dir / "app" / "agent-conversations.sqlite3")


def _path_segment(value: str) -> str:
    return quote(value, safe="")


def _upstream_headers(
    request: Request,
    *,
    accept: str = "application/json",
    authorization: Literal["request", "workspace"] = "request",
) -> dict[str, str]:
    headers = {"Accept": accept}
    for name in ("content-type",):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    credential = request.headers.get("authorization") if authorization == "request" else None
    if authorization == "workspace":
        token = _workspace_token()
        credential = f"Bearer {token}" if token else None
    if credential:
        headers["authorization"] = credential
    return headers


def _response(upstream: httpx.Response) -> Response:
    headers: dict[str, str] = {}
    for name in ("content-type", "cache-control"):
        value = upstream.headers.get(name)
        if value:
            headers[name] = value
    return Response(content=upstream.content, status_code=upstream.status_code, headers=headers)


def _unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Optional Conexus Research Agent is not configured or unavailable.",
            "code": "not_configured",
        },
    )


async def _fetch_json(path: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
        response = await client.get(f"{_web_origin()}{path}", headers={"Accept": "application/json"})
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {}


async def _forward(
    request: Request,
    method: str,
    path: str,
    *,
    authorization: Literal["request", "workspace"] = "request",
) -> Response:
    if os.getenv("ALPHALAB_AGENT_MODE") == "off":
        return _unavailable_response()
    if authorization == "workspace" and not _workspace_token():
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Conexus durable workspace access is not configured.",
                "code": "workspace_not_configured",
            },
        )
    body = await request.body() if method not in {"GET", "HEAD"} else None
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            upstream = await client.request(
                method,
                f"{_web_origin()}{path}",
                headers=_upstream_headers(
                    request,
                    authorization=authorization,
                ),
                params=list(request.query_params.multi_items()),
                content=body,
            )
    except httpx.RequestError:
        return _unavailable_response()
    return _response(upstream)


@router.get("/status")
async def conexus_status() -> dict:
    if os.getenv("ALPHALAB_AGENT_MODE") == "off":
        return {"available": False, "mode": "not_configured", "error": "agent_disabled"}
    slug = _publication_slug()
    if not _workspace_token():
        return {
            "available": False,
            "mode": "not_configured",
            "publication": slug,
            "error": "workspace_not_configured",
        }
    try:
        await _fetch_json(f"/api/public/harnesses/{_path_segment(slug)}/descriptor")
        if os.getenv("ALPHALAB_AGENT_MODE") == "local":
            health = await _fetch_json("/health")
            ready = bool(health.get("modelConfigured"))
            return {
                "available": ready, "mode": "local_harness", "publication": slug,
                "model_configured": ready,
                **({} if ready else {"error": "model_not_configured"}),
            }
        return {
            "available": True,
            "mode": "published_harness",
            "publication": slug,
        }
    except (httpx.HTTPError, ValueError) as error:
        return {
            "available": False,
            "mode": "not_configured",
            "publication": slug,
            "error": type(error).__name__,
        }


@router.get("/conversations")
def list_agent_conversations() -> dict[str, list[dict[str, Any]]]:
    return {"conversations": _conversation_store().list_conversations()}


@router.put("/conversations/{conversation_id}")
def upsert_agent_conversation(
    conversation_id: str,
    request: AgentConversationRequest,
) -> dict[str, dict[str, Any]]:
    if conversation_id != request.id:
        raise HTTPException(status_code=409, detail="Conversation path and payload IDs differ")
    payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    try:
        conversation = _conversation_store().upsert(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"conversation": conversation}


@router.get("/manifest")
async def publication_manifest(request: Request) -> Response:
    return await _forward(
        request,
        "GET",
        f"/api/public/harnesses/{_path_segment(_publication_slug())}",
        authorization="workspace",
    )


@router.post("/runs")
async def create_run(request: Request) -> Response:
    return await _forward(
        request,
        "POST",
        f"/api/public/harnesses/{_path_segment(_publication_slug())}/runs",
        authorization="workspace",
    )


@router.get("/workspace")
async def publication_workspace(request: Request) -> Response:
    response = await _forward(
        request,
        "GET",
        f"/api/public/harnesses/{_path_segment(_publication_slug())}/workspace",
        authorization="workspace",
    )
    if response.status_code == 200:
        payload = json.loads(response.body)
        if isinstance(payload.get("workspace"), dict):
            payload["workspace"] = validate_workspace_delivery(payload["workspace"])
            return JSONResponse(payload)
    return response


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> Response:
    return await _forward(request, "GET", f"/api/public/runs/{_path_segment(run_id)}")


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> Response:
    return await _forward(request, "POST", f"/api/public/runs/{_path_segment(run_id)}/cancel")


@router.post("/runs/{run_id}/interactions/{interaction_id}/answer")
async def answer_interaction(run_id: str, interaction_id: str, request: Request) -> Response:
    return await _forward(
        request,
        "POST",
        f"/api/public/runs/{_path_segment(run_id)}/interactions/{_path_segment(interaction_id)}/answer",
    )


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> Response:
    if os.getenv("ALPHALAB_AGENT_MODE") == "off":
        return _unavailable_response()
    client = httpx.AsyncClient(timeout=None, trust_env=False)
    upstream_request = client.build_request(
        "GET",
        f"{_web_origin()}/api/public/runs/{_path_segment(run_id)}/events",
        headers=_upstream_headers(request, accept="text/event-stream"),
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.RequestError:
        await client.aclose()
        return _unavailable_response()
    if upstream.status_code != 200:
        await upstream.aread()
        response = _response(upstream)
        await upstream.aclose()
        await client.aclose()
        return response

    async def stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
