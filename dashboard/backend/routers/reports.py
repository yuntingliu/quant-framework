"""Durable, provenance-bound Agent research artifacts."""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from alphalab import ResultStore
from alphalab.provenance import build_research_provenance

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ResearchArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["demo", "runtime"] = "demo"
    result: dict[str, Any]
    strategy_source: str | None = Field(default=None, max_length=100_000)


@router.get("")
def reports(limit: int = 20) -> dict:
    bounded = min(100, max(1, limit))
    store = ResultStore()
    try:
        rows = store.list_research_artifacts(bounded)
    finally:
        store.close()
    return {"items": [_artifact_payload(row) for row in rows]}


@router.get("/{artifact_id}")
def report(artifact_id: str) -> dict:
    store = ResultStore()
    try:
        row = store.get_research_artifact(artifact_id)
    finally:
        store.close()
    if row is None:
        raise HTTPException(status_code=404, detail="research artifact not found")
    return _artifact_payload(row)


@router.post("", status_code=201)
def save_report(request: ResearchArtifactRequest) -> dict:
    result = _validate_result(request.result)
    provenance = build_research_provenance(request.profile, request.strategy_source)
    artifact_id = str(result["requestId"])
    store = ResultStore()
    try:
        store.save_research_artifact(
            artifact_id,
            artifact_id,
            request.profile,
            str(result["title"]),
            result,
            provenance,
        )
        row = store.get_research_artifact(artifact_id)
    finally:
        store.close()
    if row is None:
        raise HTTPException(status_code=500, detail="research artifact was not persisted")
    return _artifact_payload(row)


def _validate_result(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) > 1_000_000:
        raise HTTPException(status_code=413, detail="research artifact exceeds 1 MB")
    if value.get("version") != 1 or value.get("kind") != "document":
        raise HTTPException(status_code=422, detail="research artifact must be a version 1 document")
    request_id = value.get("requestId")
    title = value.get("title")
    markdown = value.get("markdown")
    sources = value.get("sources", [])
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
        raise HTTPException(status_code=422, detail="research artifact requestId is invalid")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise HTTPException(status_code=422, detail="research artifact title is invalid")
    if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > 500_000:
        raise HTTPException(status_code=422, detail="research artifact markdown is invalid")
    if not isinstance(sources, list) or len(sources) > 20 or not all(
        isinstance(item, str) and 0 < len(item) <= 500 for item in sources
    ):
        raise HTTPException(status_code=422, detail="research artifact sources are invalid")
    return value


def _artifact_payload(row: dict) -> dict:
    try:
        payload = json.loads(row["payload_json"])
        provenance = json.loads(row["provenance_json"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="stored research artifact is invalid") from exc
    return {
        **payload,
        "profile": row["profile"],
        "provenance": provenance,
        "persistedAt": row["updated_at"],
    }


__all__ = ["router"]
