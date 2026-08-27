"""Shared Monaco editor support backed by fixed local Python tools."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from dashboard.backend.services import python_editor_service

router = APIRouter(prefix="/api/python-editor", tags=["python-editor"])


class MirrorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["strategy", "function", "factor", "data", "validation"]
    document_id: str = Field(min_length=1, max_length=128)
    source: str = Field(max_length=300_000)


class DiagnosticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["strategy", "function", "factor", "data", "validation"]
    document_id: str | None = Field(default=None, max_length=128)
    source: str = Field(max_length=300_000)


@router.get("/capabilities")
def editor_capabilities() -> dict:
    return python_editor_service.capabilities()


@router.post("/documents")
def mirror_document(request: MirrorRequest) -> dict:
    return python_editor_service.mirror_document(request.kind, request.document_id, request.source)


@router.post("/diagnostics")
def diagnostics(request: DiagnosticsRequest) -> dict:
    return python_editor_service.source_diagnostics(
        request.kind,
        request.source,
        document_id=request.document_id,
    )


@router.websocket("/lsp/{server_id}")
async def language_server_socket(websocket: WebSocket, server_id: str) -> None:
    await websocket.accept()
    try:
        process = await python_editor_service.start_language_server(server_id)
    except (KeyError, FileNotFoundError, OSError) as exc:
        await websocket.close(code=1013, reason=str(exc)[:120])
        return
    async def socket_to_process() -> None:
        while True:
            message = await websocket.receive_text()
            await process.write_message(message)

    async def process_to_socket() -> None:
        while True:
            message = await process.read_message()
            await websocket.send_text(message)

    async def drain_stderr() -> None:
        await process.drain_stderr()

    tasks = {
        asyncio.create_task(socket_to_process()),
        asyncio.create_task(process_to_socket()),
        asyncio.create_task(drain_stderr()),
        asyncio.create_task(process.wait()),
    }
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled():
                with contextlib.suppress(WebSocketDisconnect, EOFError):
                    task.result()
    except (WebSocketDisconnect, EOFError, asyncio.IncompleteReadError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await process.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
