"""Private saved data-source profiles with no credential echoing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from apps.api.routers.model_providers import ProfileSelectionRequest
from apps.api.services import data_source_profile_service as service
from apps.api.services.configuration_binding_service import configuration_bindings


class PrivateDataSourceValidationRoute(APIRoute):
    """Do not echo credential input through validation responses."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(
                    422,
                    "Check the profile name, user, host, and password length.",
                ) from None

        return private_handler

router = APIRouter(
    prefix="/api/data-source-profiles",
    tags=["data-source-profiles"],
    route_class=PrivateDataSourceValidationRoute,
)


class RQDataProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    user: str = Field(min_length=1, max_length=200)
    host: str = Field(min_length=1, max_length=255)
    password: str = Field(default="", max_length=8192, repr=False)
    activate: bool = True
    clear_password: bool = False
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@router.get("")
def read_data_source_profiles(binding=Depends(configuration_bindings)) -> dict:
    try:
        if binding.backend.mode == "sqlite":
            return binding.rqdata.public_settings()
        with binding.backend.json_scope():
            return service.public_settings()
    except (ValueError, OSError):
        raise HTTPException(503, "Data-source settings could not be read.") from None


@router.put("")
def save_data_source_profile(request: RQDataProfileRequest, binding=Depends(configuration_bindings)) -> dict:
    payload = request.model_dump(exclude={"activate", "clear_password", "expected_revision"})
    try:
        return binding.rqdata.save_profile(
            payload, activate=request.activate, clear_password=request.clear_password,
            expected_revision=request.expected_revision,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "Data-source settings could not be saved.") from None


@router.put("/selection")
def select_data_source_profile(request: ProfileSelectionRequest, binding=Depends(configuration_bindings)):
    return binding.rqdata.select(
        request.model_dump(exclude={"expected_revision"}, exclude_none=True),
        expected_revision=request.expected_revision,
    )
