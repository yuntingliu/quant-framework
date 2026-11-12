"""Loopback-only, explicitly authorized configuration transfer; no probes."""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from alphalab.utils.configuration_store import _raise_redacted
from apps.api.services import configuration_schema as schema
from apps.api.services.configuration_production_service import current_configuration
from apps.api.services.configuration_transfer_service import (
    ConfigurationTransferService,
    TransferError,
    _guard,
    _keys,
)

MAX_REQUEST_BYTES = 1024 * 1024
router = APIRouter(prefix="/api/configuration-transfer", tags=["configuration-transfer"])


def _local(request):
    if any(
        key in request.headers
        for key in ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")
    ):
        return False
    try:
        address = request.client.host
        local = ipaddress.ip_address(address).is_loopback
    except (ValueError, AttributeError):
        local = False
    host = request.url.hostname
    if not local or host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return False
    return request.headers.get("x-alphalab-configuration-consent") == "local-configuration-transfer"


def _error(error):
    code = error.code
    status = (
        409
        if code
        in {
            "STALE_PREVIEW",
            "RECEIPT_USED",
            "WRONG_INSTALLATION",
            "ROLLBACK_CONFLICT",
            "HISTORY_CAPACITY",
            "BUSY",
            "APPLICATION_UNCERTAIN",
            "APPLICATION_REJECTED",
        }
        else 422
    )
    if code == "RECEIPT_EXPIRED":
        status = 410
    elif code in {"UNAVAILABLE", "INVALID_PLAN", "PLAN_LIMIT"}:
        status = 503
    return JSONResponse(
        {"detail": {"code": code, "message": str(error)}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


async def _body(request):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise TransferError("INVALID_REQUEST")
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > MAX_REQUEST_BYTES:
            raise TransferError("INVALID_REQUEST")
    try:
        result = json.loads(
            bytes(raw).decode("utf-8"),
            object_pairs_hook=schema._pairs,
            parse_constant=schema._nonfinite,
        )
        # The private envelope may also carry fresh keys. Portable contents
        # still receive Phase 4A's independent 256-KiB/depth/profile bounds.
        pending, count = [(result, 1)], 0
        while pending:
            value, depth = pending.pop()
            count += 1
            if count > 8192 or depth > 10:
                raise TransferError("INVALID_REQUEST")
            if type(value) in {dict, list}:
                if len(value) > 4096:
                    raise TransferError("INVALID_REQUEST")
                children = (
                    list(value.keys()) + list(value.values()) if type(value) is dict else value
                )
                pending.extend((child, depth + 1) for child in children)
        return result
    except (ValueError, UnicodeError, RecursionError):
        _raise_redacted(TransferError("INVALID_REQUEST"))


async def _execute(request, operation):
    try:
        if not _local(request):
            return JSONResponse(
                {
                    "detail": {
                        "code": "LOCAL_AUTHORIZATION_REQUIRED",
                        "message": "Use the explicitly authorized local configuration interface.",
                    }
                },
                status_code=403,
            )
        if request.query_params:
            raise TransferError("INVALID_REQUEST")
        runtime = current_configuration()
        if runtime is None:
            raise TransferError("UNAVAILABLE")
        service = ConfigurationTransferService(runtime.backend)
        if operation == "export":
            from anyio import to_thread

            value = await to_thread.run_sync(service.export)
            return Response(
                value + "\n",
                media_type="application/json",
                headers={
                    "Content-Disposition": 'attachment; filename="alphalab-configuration.json"',
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        body = await _body(request)

        @_guard
        def run():
            if operation == "preview":
                _keys(
                    body,
                    {"configuration", "services", "mode", "apply_selections", "credential_policy"},
                    {"configuration", "services", "mode", "apply_selections", "credential_policy"},
                )
                return service.preview(**body)
            if operation == "rollback-preview":
                _keys(body, {"operation", "credential_policy"}, {"operation", "credential_policy"})
                return service.rollback_preview(**body)
            if operation == "operation":
                _keys(body, {"operation"}, {"operation"})
                return service.operation(body["operation"])
            _keys(
                body,
                {"receipt", "expected_revision", "confirm", "consents", "credentials"},
                {"receipt", "expected_revision", "confirm", "consents", "credentials"},
            )
            return service.apply(
                **body, kind="rollback" if operation == "rollback-apply" else "import"
            )

        from anyio import to_thread

        value = await to_thread.run_sync(run)
        return JSONResponse(
            value, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        )
    except TransferError as error:
        return _error(error)
    except Exception:
        return _error(TransferError("INVALID_REQUEST"))


@router.get("/export")
async def export(request: Request):
    return await _execute(request, "export")


@router.post("/preview")
async def preview(request: Request):
    return await _execute(request, "preview")


@router.post("/apply")
async def apply(request: Request):
    return await _execute(request, "apply")


@router.post("/rollback/preview")
async def rollback_preview(request: Request):
    return await _execute(request, "rollback-preview")


@router.post("/rollback/apply")
async def rollback_apply(request: Request):
    return await _execute(request, "rollback-apply")


@router.post("/operation")
async def operation(request: Request):
    return await _execute(request, "operation")
