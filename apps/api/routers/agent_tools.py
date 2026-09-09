"""Typed AlphaLab tools exposed to the optional Conexus Research Agent."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from alphalab.dataio.errors import DataLoadError, MissingDataError
from alphalab.tools import create_data_tool_registry

router = APIRouter(prefix="/api/agent/data-tools", tags=["agent-tools"])


class DataToolInvocation(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False


@router.get("")
def describe_data_tools() -> dict:
    tools = create_data_tool_registry().describe()
    return {"count": len(tools), "tools": tools}


@router.post("/{tool_name}/invoke")
def invoke_data_tool(tool_name: str, request: DataToolInvocation) -> dict:
    registry = create_data_tool_registry()
    try:
        spec = registry.spec(tool_name)
        if spec.mutating and not request.confirm:
            raise HTTPException(
                status_code=409,
                detail=f"Mutating data tool requires confirm=true: {tool_name}",
            )
        result = registry.invoke(tool_name, request.input)
        return {
            "tool": tool_name,
            "mutating": spec.mutating,
            "result": result,
        }
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except MissingDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DataLoadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
