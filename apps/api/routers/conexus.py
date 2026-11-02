"""Narrow same-origin proxy for the published AlphaLab Research Agent."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from apps.api.services import agent_run_service
from apps.api.services.agent_conversation_service import (
    MAX_CONVERSATION_BYTES,
)
from apps.api.services.agent_run_service import conversation_store as _conversation_store
from apps.api.services.agent_run_service import publication_slug as _publication_slug
from apps.api.services.agent_run_service import web_origin as _web_origin
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

class RunConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=120)
    createdAt: datetime
    messageId: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=40_000)


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


def _workspace_token() -> str:
    return os.getenv("CONEXUS_PUBLICATION_WORKSPACE_TOKEN", "").strip()


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
    # Workstation Basic authentication belongs to this proxy. Managed Runs
    # authenticate upstream with their saved, scoped Conexus credential.
    if credential and not credential.lower().startswith("bearer "):
        credential = None
    if authorization == "workspace":
        token = _workspace_token()
        credential = f"Bearer {token}" if token else None
    if credential:
        headers["authorization"] = credential
    elif authorization == "request" and request.path_params.get("run_id"):
        headers.update(agent_run_service.run_headers(request.path_params["run_id"]))
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


async def _fetch_manifest(slug: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
        response = await client.get(
            f"{_web_origin()}/api/public/harnesses/{_path_segment(slug)}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {_workspace_token()}",
            },
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("Expected a hosted Harness manifest")
        return value


def _has_supported_agent(manifest: dict) -> bool:
    """Match the manifest boundary currently consumed by the workstation."""
    exposures = manifest.get("exposures")
    default = manifest.get("defaultExposureId")
    return (
        (manifest.get("identityPolicy") == "enterprise"
         or (os.getenv("ALPHALAB_AGENT_MODE") == "local" and manifest.get("schema") == "conexus.local-harness.v1"))
        and isinstance(default, str)
        and bool(default.strip())
        and isinstance(exposures, list)
        and any(
            isinstance(exposure, dict)
            and exposure.get("id") == default
            and exposure.get("nodeType") == "agent"
            and isinstance(exposure.get("surfaces"), list)
            and "api" in exposure["surfaces"]
            for exposure in exposures
        )
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
    body_override: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
    raise_request_errors: bool = False,
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
    body = body_override if body_override is not None else (
        await request.body() if method not in {"GET", "HEAD"} else None
    )
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            upstream = await client.request(
                method,
                f"{_web_origin()}{path}",
                headers={
                    **_upstream_headers(request, authorization=authorization),
                    **(extra_headers or {}),
                },
                params=list(request.query_params.multi_items()),
                content=body,
            )
    except httpx.RequestError:
        if raise_request_errors:
            raise
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
        manifest = await _fetch_manifest(slug)
        if not _has_supported_agent(manifest):
            raise ValueError("Unsupported hosted Agent contract")
        if os.getenv("ALPHALAB_AGENT_MODE") == "local":
            health = await _fetch_json("/health")
            ready = bool(health.get("modelConfigured"))
            return {"available": ready, "mode": "local_harness", "publication": slug,
                    "model_configured": ready,
                    **({} if ready else {"error": "model_not_configured", "message": "Open Model providers to configure and test your model."})}
        return {
            "available": True,
            "mode": "published_harness",
            "publication": slug,
        }
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        code, message = {
            401: ("service_authentication_failed", "Conexus rejected the publication service credential."),
            403: ("service_authentication_failed", "The service credential cannot access this publication."),
            404: ("publication_unavailable", "Conexus cannot load this publication. Check its slug and stored release compatibility."),
            409: ("publication_incompatible", "The hosted release is incompatible with this Conexus runtime."),
        }.get(status, ("upstream_error", "Conexus returned an unexpected HTTP error."))
        detail = {"error": code, "message": message, "upstream_status": status}
    except httpx.RequestError:
        detail = {
            "error": "connection_failed",
            "message": "The AlphaLab backend could not reach Conexus. Check the configured origin and server process.",
        }
    except ValueError:
        detail = {
            "error": "publication_incompatible",
            "message": "Conexus did not return a supported AlphaLab Agent manifest. Check the server and integration versions.",
        }
    return {"available": False, "mode": "not_configured", "publication": slug, **detail}


@router.get("/conversations")
def list_agent_conversations() -> dict[str, list[dict[str, Any]]]:
    store = _conversation_store()
    scope = agent_run_service.run_scope()
    return {
        "conversations": store.list_conversations(),
        "runs": [
            {**item, "run": agent_run_service.decorate_run(item["run"])}
            for item in store.conversation_runs(scope)
        ],
        "submissions": store.open_submissions(scope),
    }


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


def _submission_response(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": code})


_IN_PROGRESS = "This message is already being submitted. It was not sent again."
_UNCONFIRMED = (
    "The Agent Host may have accepted this message, but that could not be confirmed. "
    "It was not sent again, to avoid a second billed run."
)


def _accepted_submission(run: dict[str, Any], turn: dict[str, Any]) -> JSONResponse:
    store = _conversation_store()
    saved = next((item for item in store.list_conversations() if item["id"] == turn["id"]), turn)
    return JSONResponse({"run": agent_run_service.decorate_run(run), "conversation": saved, "replayed": True})


@router.post("/runs")
async def create_run(request: Request) -> Response:
    try:
        body = await request.json()
        metadata = body.pop("conversation", None) if isinstance(body, dict) else None
        conversation = RunConversationRequest.model_validate(metadata) if metadata is not None else None
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Invalid Agent conversation submission") from exc
    path = f"/api/public/harnesses/{_path_segment(_publication_slug())}/runs"
    payload = json.dumps(body).encode("utf-8")
    if conversation is None:
        return await _forward(request, "POST", path, authorization="workspace", body_override=payload)

    store = _conversation_store()
    scope = agent_run_service.run_scope()
    key = f"{conversation.id}:{conversation.messageId}"
    now = datetime.now(timezone.utc).isoformat()
    turn = {
        "id": conversation.id, "title": conversation.title,
        "createdAt": conversation.createdAt.isoformat(), "updatedAt": now,
        "messages": [{
            "id": conversation.messageId, "role": "user", "content": conversation.message,
            "createdAt": now, "runId": "pending",
        }],
    }
    claim = store.claim_submission(scope, key, turn, message_id=conversation.messageId)
    if not claim["claimed"]:
        state = claim["state"]
        if state == "unconfirmed":
            state = await agent_run_service.resolve_submission(key)
        if state == "accepted":
            # Whichever attempt won, every duplicate sees the one Run it created.
            accepted = store.get_submission(scope, key) or {}
            run = agent_run_service.stored_run(accepted.get("run_id") or claim.get("run_id") or "")
            if run is not None:
                turn["messages"][0]["runId"] = run["id"]
                return _accepted_submission(run, turn)
            state = "unconfirmed"
        if state == "submitting":
            return _submission_response(409, "submission_in_progress", _IN_PROGRESS)
        if state != "not_started":
            return _submission_response(409, "submission_unconfirmed", _UNCONFIRMED)
        store.claim_submission(scope, key, turn, message_id=conversation.messageId)

    try:
        response = await _forward(
            request, "POST", path, authorization="workspace", body_override=payload,
            extra_headers={"Idempotency-Key": agent_run_service.host_submission_key(key)}, raise_request_errors=True,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        # The request never reached the Host, so a later attempt cannot duplicate anything.
        store.release_submission(scope, key)
        return _unavailable_response()
    except httpx.RequestError:
        store.mark_submission(scope, key, "unconfirmed")
        return _submission_response(503, "submission_unconfirmed", _UNCONFIRMED)
    if not 200 <= response.status_code < 300:
        store.release_submission(scope, key)
        return response
    result = json.loads(response.body)
    run = result["run"]
    turn["messages"][0]["runId"] = run["id"]
    saved = store.attach_run(scope, run, result["accessToken"], turn, submission_key=key)
    await agent_run_service.record_snapshot(run)
    # Managed browser sessions authenticate through the server's saved Run credential.
    return JSONResponse(
        {"run": agent_run_service.decorate_run(run), "conversation": saved}, status_code=response.status_code,
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


def _run_payload(run: dict[str, Any], **extra: Any) -> JSONResponse:
    return JSONResponse({"run": agent_run_service.decorate_run(run), **extra})


@router.get("/runs/{run_id}/trace")
def run_trace(run_id: str) -> dict[str, Any]:
    trace = agent_run_service.run_trace(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="No trace is recorded for this Run.")
    return trace


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> Response:
    response = await _forward(request, "GET", f"/api/public/runs/{_path_segment(run_id)}")
    if response.status_code in agent_run_service.RUN_UNKNOWN_STATUSES:
        # The server record stays authoritative while recovery decides whether the Run is gone.
        stored = agent_run_service.stored_run(run_id)
        if stored is not None:
            return _run_payload(stored)
    return await _record_run_response(response)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> Response:
    response = await _forward(request, "POST", f"/api/public/runs/{_path_segment(run_id)}/cancel")
    stored = agent_run_service.stored_run(run_id)
    if response.status_code in agent_run_service.RUN_UNKNOWN_STATUSES and stored is not None:
        # Nothing is left to cancel at the Host, and saying "cancelled" would be a guess.
        run = agent_run_service.mark_run_lost(run_id) or stored
        return _run_payload(run, jobs=agent_run_service.cancel_run_jobs(run_id))
    if response.status_code != 200 or stored is None:
        return response
    payload = json.loads(response.body)
    run = payload.get("run")
    if not isinstance(run, dict):
        return response
    await agent_run_service.record_snapshot(run)
    return _run_payload(run, jobs=agent_run_service.cancel_run_jobs(run_id))


@router.post("/runs/{run_id}/interactions/{interaction_id}/answer")
async def answer_interaction(run_id: str, interaction_id: str, request: Request) -> Response:
    response = await _forward(
        request,
        "POST",
        f"/api/public/runs/{_path_segment(run_id)}/interactions/{_path_segment(interaction_id)}/answer",
    )
    return await _record_run_response(response)


async def _record_run_response(response: Response) -> Response:
    if response.status_code == 200:
        payload = json.loads(response.body)
        if isinstance(payload.get("run"), dict):
            await agent_run_service.record_snapshot(payload["run"])
            return JSONResponse({**payload, "run": agent_run_service.decorate_run(payload["run"])})
    return response


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> Response:
    if os.getenv("ALPHALAB_AGENT_MODE") == "off":
        return _unavailable_response()
    client = httpx.AsyncClient(timeout=None, trust_env=False)
    headers = _upstream_headers(request, accept="text/event-stream")
    last_event_id = request.headers.get("last-event-id", "")
    if last_event_id.isascii() and last_event_id.isdigit() and len(last_event_id) <= 16:
        headers["Last-Event-ID"] = last_event_id
    upstream_request = client.build_request(
        "GET",
        f"{_web_origin()}/api/public/runs/{_path_segment(run_id)}/events",
        headers=headers,
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
