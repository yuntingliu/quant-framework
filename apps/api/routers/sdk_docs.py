"""Read-only endpoints for the canonical AlphaLab SDK guide."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from apps.api.services.sdk_docs_service import (
    sdk_document,
    sdk_document_catalog,
)

router = APIRouter(prefix="/api/sdk-docs", tags=["sdk-docs"])


@router.get("")
def catalog() -> dict:
    return sdk_document_catalog()


@router.get("/{topic_id}")
def document(topic_id: str) -> dict:
    result = sdk_document(topic_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown SDK document topic {topic_id!r}")
    return result
