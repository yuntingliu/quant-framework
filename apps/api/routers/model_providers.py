"""Local provider setup and a bounded, explicit model connection test."""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.services.configuration_binding_service import (
    configuration_bindings,
    configuration_http_error,
)


class PrivateValidationRoute(APIRoute):
    """Do not echo credential input through default validation responses."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Check the provider name, API URL, model ID, and key length.") from None
            except ConfigurationStoreError as error:
                raise configuration_http_error(error) from None

        return private_handler


router = APIRouter(prefix="/api/model-providers", tags=["model-providers"], route_class=PrivateValidationRoute)


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=8192, repr=False)
    activate: bool = True
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    clear_api_key: bool = False


class ProfileSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern=r"^(profile|environment|none)$")
    id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def require_local() -> None:
    if os.getenv("ALPHALAB_AGENT_MODE") != "local":
        raise HTTPException(409, "Provider settings apply to the bundled local Agent. Start with --agent local.")


@router.get("")
def read_model_providers(binding=Depends(configuration_bindings)) -> dict:
    try:
        return binding.models.public_settings()
    except (ValueError, OSError):
        raise HTTPException(503, "Model settings could not be read.") from None


@router.put("")
def save_model_provider(request: ProviderRequest, binding=Depends(configuration_bindings)) -> dict:
    require_local()
    try:
        return binding.models.save_profile(
            request.model_dump(exclude={"activate", "expected_revision", "clear_api_key"}),
            activate=request.activate,
            expected_revision=request.expected_revision,
            clear_api_key=request.clear_api_key,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "Model settings could not be saved.") from None


@router.post("/test")
async def test_model_provider(request: ProviderRequest, binding=Depends(configuration_bindings)) -> dict:
    require_local()
    try:
        profile = binding.models.resolve_test_profile(
            request.model_dump(exclude={"activate", "expected_revision", "clear_api_key"}),
            clear_api_key=request.clear_api_key,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        loopback = urlsplit(profile["base_url"]).hostname in {"localhost", "127.0.0.1", "::1"}
        proxy = None if loopback else os.getenv("ALPHALAB_MODEL_PROXY") or None
        async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False, proxy=proxy) as client:
            async with client.stream(
                "POST", profile["base_url"] + "/chat/completions",
                headers={"Authorization": f"Bearer {profile['api_key']}"} if profile["api_key"] else {},
                json={"model": profile["model"], "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 16, "stream": False},
            ) as response:
                if response.status_code != 200:
                    return {"ok": False, "message": f"Provider returned HTTP {response.status_code}. Check the endpoint, key, and model access."}
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        return {"ok": False, "message": "Provider response was unexpectedly large."}
                value = json.loads(data)
                choices = value.get("choices") if isinstance(value, dict) else None
                if not isinstance(choices, list) or not choices or not isinstance(choices[0].get("message"), dict):
                    return {"ok": False, "message": "Endpoint did not return a Chat Completions response."}
        return {"ok": True, "message": "Model responded. Run an Agent request to verify tool calling."}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return {"ok": False, "message": "Model request failed or timed out. Check the endpoint and provider configuration."}


@router.put("/selection")
def select_model_profile(request: ProfileSelectionRequest, binding=Depends(configuration_bindings)):
    require_local()
    return binding.models.select(
        request.model_dump(exclude={"expected_revision"}, exclude_none=True),
        expected_revision=request.expected_revision,
    )
