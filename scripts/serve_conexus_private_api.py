"""Loopback SSH ingress to the existing authenticated workstation process.

One backend continues to own jobs and database state. Browser credentials stay
on the workstation; they are never embedded in a published Agent tool.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response

_PREFIXES = ("agent", "strategy", "data", "data-sync", "validation", "backtests")


def create_proxy(username: str, password: str, port: int, *, transport=None) -> FastAPI:
    if not username or not password:
        raise ValueError("Authenticated workstation credentials are required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def forward(path: str, request: Request) -> Response:
        if path.split("/", 1)[0] not in _PREFIXES and path != "conexus/outputs/prepare":
            return Response(status_code=404)
        headers = {key: request.headers[key] for key in ("accept", "content-type") if key in request.headers}
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", auth=(username, password),
            timeout=180, trust_env=False, follow_redirects=False, transport=transport,
        ) as client:
            try:
                upstream = await client.request(
                    request.method, f"/api/{path}", params=list(request.query_params.multi_items()),
                    headers=headers, content=await request.body(),
                )
            except httpx.RequestError:
                return Response(content='{"detail":"Workstation backend unavailable"}', status_code=503,
                                media_type="application/json")
        return Response(content=upstream.content, status_code=upstream.status_code,
                        headers={key: upstream.headers[key] for key in ("content-type", "cache-control")
                                 if key in upstream.headers})

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8301)
    parser.add_argument("--backend-port", type=int, default=8300)
    args = parser.parse_args()
    values = {}
    for line in args.env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = json.loads(value)
    uvicorn.run(create_proxy(values["ALPHALAB_WEB_USERNAME"], values["ALPHALAB_WEB_PASSWORD"],
                            args.backend_port), host="127.0.0.1", port=args.port)
