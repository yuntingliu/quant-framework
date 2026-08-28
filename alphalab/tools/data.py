"""Structured, dataframe-safe tools for the runtime data control plane."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from pydantic import BaseModel, Field

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.quality import validate_dataset
from alphalab.dataio.rq_templates import list_rq_sync_templates
from alphalab.dataio.runtime import RuntimeStore
from alphalab.dataio.sync import SyncJobManager, SyncRequest, build_sync_plan


class EmptyInput(BaseModel):
    pass


class DatasetInput(BaseModel):
    dataset: str
    start: str | None = None
    as_of: str | None = None
    fail_on_gap: bool = False


class QueryInput(BaseModel):
    dataset: str
    symbols: list[str] | None = None
    start: str | None = None
    end: str | None = None
    columns: list[str] | None = None
    limit: int = Field(default=100, ge=1, le=1000)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel], dict | list]
    mutating: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def describe(self) -> list[dict]:
        return [
            {
                "name": item.name,
                "description": item.description,
                "mutating": item.mutating,
                "input_schema": item.input_model.model_json_schema(),
            }
            for item in self._tools.values()
        ]

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown data tool: {name}") from exc

    def invoke(self, name: str, payload: dict[str, Any] | None = None) -> dict | list:
        tool = self.spec(name)
        validated = tool.input_model.model_validate(payload or {})
        return tool.handler(validated)


def create_data_tool_registry(root: str | Path | None = None) -> ToolRegistry:
    catalog = DataCatalog(root)
    runtime = RuntimeStore(root)
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "data.templates",
            "List built-in RQ synchronization templates and their data contracts.",
            EmptyInput,
            lambda _value: [item.to_dict() for item in list_rq_sync_templates()],
        )
    )
    registry.register(
        ToolSpec(
            "data.catalog",
            "List runtime datasets and their contracts.",
            EmptyInput,
            lambda _value: catalog.list(),
        )
    )
    registry.register(
        ToolSpec(
            "data.status",
            "Return runtime coverage and readiness.",
            EmptyInput,
            lambda _value: catalog.summary(),
        )
    )
    registry.register(
        ToolSpec(
            "data.plan_sync",
            "Build a read-only RQ synchronization plan.",
            SyncRequest,
            lambda value: build_sync_plan(value, root=root),
        )
    )
    registry.register(
        ToolSpec(
            "data.run_sync",
            "Run an RQ synchronization job synchronously.",
            SyncRequest,
            lambda value: SyncJobManager(root).run_now(value),
            mutating=True,
        )
    )
    registry.register(
        ToolSpec(
            "data.validate",
            "Validate one runtime dataset.",
            DatasetInput,
            lambda value: validate_dataset(
                value.dataset,
                root,
                start_date=value.start,
                as_of_date=value.as_of,
                fail_on_gap=value.fail_on_gap,
            ),
        )
    )
    registry.register(
        ToolSpec(
            "data.query",
            "Return a bounded preview of one runtime dataset.",
            QueryInput,
            lambda value: _query(runtime, value),
        )
    )
    return registry


def _query(runtime: RuntimeStore, request: QueryInput) -> dict:
    frame = runtime.read(request.dataset)
    if request.symbols and "symbol" in frame:
        requested = {str(value).strip().upper() for value in request.symbols}
        frame = frame.loc[frame["symbol"].astype(str).str.upper().isin(requested)]
    date_column = next(
        (
            name
            for name in ("date", "available_date", "info_date", "snapshot_date")
            if name in frame
        ),
        None,
    )
    if date_column:
        dates = pd.to_datetime(frame[date_column], errors="coerce")
        if request.start:
            frame = frame.loc[dates.ge(pd.Timestamp(request.start))]
            dates = pd.to_datetime(frame[date_column], errors="coerce")
        if request.end:
            frame = frame.loc[dates.le(pd.Timestamp(request.end))]
    if request.columns:
        unknown = sorted(set(request.columns) - set(frame.columns))
        if unknown:
            raise KeyError(f"Unknown columns: {unknown}")
        frame = frame[request.columns]
    preview = frame.head(request.limit).copy()
    for name in preview.select_dtypes(include=["datetime", "datetimetz"]).columns:
        preview[name] = preview[name].dt.strftime("%Y-%m-%d")
    return {
        "dataset": request.dataset,
        "matched_rows": len(frame),
        "returned_rows": len(preview),
        "truncated": len(frame) > len(preview),
        "rows": preview.where(pd.notna(preview), None).to_dict("records"),
    }


__all__ = ["ToolRegistry", "ToolSpec", "create_data_tool_registry"]
