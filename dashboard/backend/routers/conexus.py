"""Narrow same-origin proxy for the published AlphaLab Research Agent."""
from __future__ import annotations

import os
import re
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter(prefix="/api/conexus", tags=["conexus"])

_DEFAULT_WEB_ORIGIN = "http://127.0.0.1:3000"
_DEFAULT_PUBLICATION_SLUG = "alphalab-research-agent"
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _web_origin() -> str:
    return os.getenv("CONEXUS_WEB_ORIGIN", _DEFAULT_WEB_ORIGIN).rstrip("/")


def _publication_slug() -> str:
    value = os.getenv("CONEXUS_PUBLICATION_SLUG", _DEFAULT_PUBLICATION_SLUG).strip().lower()
    return value if _SLUG.fullmatch(value) else _DEFAULT_PUBLICATION_SLUG


def _path_segment(value: str) -> str:
    return quote(value, safe="")


def _upstream_headers(request: Request, *, accept: str = "application/json") -> dict[str, str]:
    headers = {"Accept": accept}
    for name in ("authorization", "content-type"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
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


async def _forward(request: Request, method: str, path: str) -> Response:
    body = await request.body() if method not in {"GET", "HEAD"} else None
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            upstream = await client.request(
                method,
                f"{_web_origin()}{path}",
                headers=_upstream_headers(request),
                params=list(request.query_params.multi_items()),
                content=body,
            )
    except httpx.RequestError:
        return _unavailable_response()
    return _response(upstream)


@router.get("/status")
async def conexus_status() -> dict:
    slug = _publication_slug()
    try:
        await _fetch_json(f"/api/public/harnesses/{_path_segment(slug)}/descriptor")
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


@router.get("/manifest")
async def publication_manifest(request: Request) -> Response:
    return await _forward(
        request,
        "GET",
        f"/api/public/harnesses/{_path_segment(_publication_slug())}",
    )


@router.post("/runs")
async def create_run(request: Request) -> Response:
    return await _forward(
        request,
        "POST",
        f"/api/public/harnesses/{_path_segment(_publication_slug())}/runs",
    )


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
