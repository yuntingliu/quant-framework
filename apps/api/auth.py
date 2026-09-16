"""Optional HTTP Basic authentication for the deployed workstation."""
from __future__ import annotations

import base64
import binascii
import hmac
import os

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_TRUE_VALUES = {"1", "true", "yes", "on"}


class HttpBasicAuthMiddleware:
    """Protect HTTP and editor WebSocket routes when password auth is enabled."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not _auth_enabled():
            await self.app(scope, receive, send)
            return

        header = Headers(scope=scope).get("authorization", "")
        # Run-scoped Bearer credentials are verified by the upstream Conexus
        # run endpoints. Other API paths still require workstation credentials.
        if (scope["type"] == "http" and scope.get("path", "").startswith("/api/conexus/runs/")
                and header.lower().startswith("bearer ") and header[7:].strip()):
            await self.app(scope, receive, send)
            return

        username = os.getenv("ALPHALAB_WEB_USERNAME", "").strip()
        password = os.getenv("ALPHALAB_WEB_PASSWORD", "")
        if not username or not password:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1011})
                return
            response = JSONResponse(
                status_code=503,
                content={
                    "status": "invalid",
                    "detail": "AlphaLab web authentication is enabled but not configured.",
                },
            )
            await response(scope, receive, send)
            return

        supplied = _decode_basic_credentials(header)
        if supplied is None or not (
            hmac.compare_digest(supplied[0].encode(), username.encode())
            and hmac.compare_digest(supplied[1].encode(), password.encode())
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            response = JSONResponse(
                status_code=401,
                content={"status": "unauthorized", "detail": "Authentication required."},
                headers={"WWW-Authenticate": 'Basic realm="AlphaLab", charset="UTF-8"'},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _auth_enabled() -> bool:
    return os.getenv("ALPHALAB_WEB_AUTH_ENABLED", "").strip().lower() in _TRUE_VALUES


def _decode_basic_credentials(header: str | None) -> tuple[str, str] | None:
    if not header:
        return None
    scheme, separator, token = header.partition(" ")
    if separator != " " or scheme.lower() != "basic" or not token:
        return None
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    username, separator, password = decoded.partition(":")
    if separator != ":":
        return None
    return username, password


__all__ = ["HttpBasicAuthMiddleware"]
