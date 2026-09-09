"""Deployment-only ASGI entrypoint for the authenticated AlphaLab web app."""

from __future__ import annotations

import base64
import binascii
import hmac
import os
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles

from apps.api.main import app as api_app


_DIST = Path("/home/dev/apps/dev版/build/web")
_BACKEND_PATHS = ("/api", "/docs", "/redoc", "/openapi.json", "/health")
_HEALTH_PATHS = {"/health", "/api/health"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


class SpaStaticFiles(StaticFiles):
    @staticmethod
    def _prevent_stale_shell(response: Response) -> Response:
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            response = None
        if response is None or response.status_code == 404:
            response = await super().get_response("index.html", scope)
        return self._prevent_stale_shell(response)


static_app = SpaStaticFiles(directory=str(_DIST), html=True)


def _auth_enabled() -> bool:
    return os.getenv("ALPHALAB_DEV_WEB_AUTH_ENABLED", "true").strip().lower() in _TRUE_VALUES


def _read_basic_credentials(scope) -> tuple[str, str] | None:
    authorization = Request(scope).headers.get("authorization", "")
    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


def _expected_password() -> str:
    encoded = os.getenv("ALPHALAB_DEV_WEB_PASSWORD_B64", "")
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return ""


def _is_conexus_run_token_request(scope) -> bool:
    if scope.get("type") != "http":
        return False
    if not scope.get("path", "").startswith("/api/conexus/runs/"):
        return False
    authorization = Request(scope).headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and bool(token.strip())


async def _serve(scope, receive, send):
    path = scope.get("path", "")
    if scope.get("type") == "http" and path in _HEALTH_PATHS:
        response = JSONResponse(
            {
                "status": "ok",
                "name": "AlphaLab dev workstation",
                "profile": "dev",
            }
        )
        await response(scope, receive, send)
        return
    if scope.get("type") != "http" or path.startswith(_BACKEND_PATHS):
        await api_app(scope, receive, send)
        return
    await static_app(scope, receive, send)


async def app(scope, receive, send):
    if _is_conexus_run_token_request(scope):
        await _serve(scope, receive, send)
        return

    if scope.get("type") != "http" or not _auth_enabled():
        await _serve(scope, receive, send)
        return

    expected_username = os.getenv("ALPHALAB_DEV_WEB_USERNAME", "").strip()
    expected_password = _expected_password()
    if not expected_username or not expected_password:
        response = JSONResponse(
            {"status": "unavailable", "detail": "Web access password is not configured."},
            status_code=503,
        )
        await response(scope, receive, send)
        return

    supplied = _read_basic_credentials(scope)
    authenticated = bool(
        supplied
        and hmac.compare_digest(supplied[0], expected_username)
        and hmac.compare_digest(supplied[1], expected_password)
    )
    if not authenticated:
        response = Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="AlphaLab Dev", charset="UTF-8"'},
        )
        await response(scope, receive, send)
        return

    await _serve(scope, receive, send)
