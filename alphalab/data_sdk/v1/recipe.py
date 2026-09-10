"""Python-authored data recipes for trusted local acquisition.

The SDK deliberately does not mirror the RQData API.  ``rq`` lazily exposes
the installed ``rqdatac`` module, while this module owns only AlphaLab's
recipe, execution-result, and materialization boundaries.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from alphalab.dataio.catalog import DataCatalog
from alphalab.dataio.quality import validate_dataset
from alphalab.dataio.runtime import RuntimeStore
from alphalab.dataio.symbols import to_framework_symbol
from alphalab.utils.paths import RUNTIME_DIR

_RECIPE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
_OUTPUT_ID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class RQSyncRequest:
    """Request AlphaLab's incremental RQ synchronizer from Python code."""

    template_id: str
    datasets: tuple[str, ...]
    start: str | None = None
    end: str | None = None
    symbols: tuple[str, ...] | None = None
    force: bool = False
    bar_chunk_days: int = 366
    market_state_chunk_days: int = 366
    daily_factors: tuple[str, ...] = ("market_cap", "roe")
    index_symbols: tuple[str, ...] = ("000300.SH", "000905.SH", "000852.SH")
    component_frequency: str = "ME"

    def __init__(
        self,
        *,
        template_id: str,
        datasets: Iterable[str],
        start: str | None = None,
        end: str | None = None,
        symbols: Iterable[str] | None = None,
        required_fields: Iterable[str] | None = None,
        force: bool = False,
        bar_chunk_days: int = 366,
        market_state_chunk_days: int = 366,
        daily_factors: Iterable[str] = ("market_cap", "roe"),
        index_symbols: Iterable[str] = ("000300.SH", "000905.SH", "000852.SH"),
        component_frequency: str = "ME",
    ) -> None:
        object.__setattr__(self, "template_id", str(template_id).strip())
        object.__setattr__(self, "datasets", tuple(str(value).strip() for value in datasets))
        object.__setattr__(self, "start", str(start) if start else None)
        object.__setattr__(self, "end", str(end) if end else None)
        object.__setattr__(
            self,
            "symbols",
            tuple(str(value).strip() for value in symbols) if symbols is not None else None,
        )
        object.__setattr__(self, "force", bool(force))
        object.__setattr__(self, "bar_chunk_days", int(bar_chunk_days))
        object.__setattr__(self, "market_state_chunk_days", int(market_state_chunk_days))
        object.__setattr__(
            self, "daily_factors", tuple(str(value).strip() for value in daily_factors)
        )
        object.__setattr__(
            self, "index_symbols", tuple(str(value).strip() for value in index_symbols)
        )
        object.__setattr__(self, "component_frequency", str(component_frequency).strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": "rq",
            "template_id": self.template_id,
            "datasets": list(self.datasets),
            "start": self.start,
            "end": self.end,
            "symbols": list(self.symbols) if self.symbols is not None else None,
            "force": self.force,
            "bar_chunk_days": self.bar_chunk_days,
            "market_state_chunk_days": self.market_state_chunk_days,
            "daily_factors": list(self.daily_factors),
            "index_symbols": list(self.index_symbols),
            "component_frequency": self.component_frequency,
        }


@dataclass(frozen=True)
class DateSymbolBatch:
    """One deterministic, resumable date/symbol acquisition batch."""

    start: str
    end: str
    symbols: tuple[str, ...]


class RQDataAPI:
    """Lazy transparent access to every public operation in ``rqdatac``."""

    def __init__(self) -> None:
        self._connected_module: Any | None = None
        self._connection_lock = threading.Lock()

    def _module(self) -> Any:
        from alphalab.dataio.providers.rq import RQDataClient

        if self._connected_module is not None:
            return self._connected_module
        with self._connection_lock:
            if self._connected_module is None:
                self._connected_module = RQDataClient.from_env().connect()
        return self._connected_module

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._module(), name)

    def __dir__(self) -> list[str]:
        try:
            return sorted(name for name in dir(self._module()) if not name.startswith("_"))
        except Exception:
            return []


rq = RQDataAPI()


class DataRecipeContext:
    """Core-owned output boundary passed to one trusted data recipe."""

    def __init__(self, *, mode: str, root: str | None = None) -> None:
        if mode not in {"plan", "run"}:
            raise ValueError("data recipe mode must be plan or run")
        self.mode = mode
        self._root = Path(root) if root is not None else RUNTIME_DIR
        self._catalog = DataCatalog(self._root)
        self._store = RuntimeStore(self._root) if mode == "run" else None
        self._published: list[dict[str, Any]] = []
        self._outputs: list[dict[str, Any]] = []
        self._planned: list[dict[str, Any]] = []
        self._coverage_requirements: list[dict[str, Any]] = []

    @property
    def rq(self) -> RQDataAPI:
        """Return the same lazy full RQData API exposed as the module-level ``rq``."""

        return rq

    def publish(self, dataset: str, frame: pd.DataFrame) -> dict[str, Any]:
        """Validate and materialize one canonical runtime dataset batch."""

        dataset_id = str(dataset).strip()
        spec = self._catalog.spec(dataset_id)
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("context.publish() requires a pandas.DataFrame")
        if frame.empty:
            raise ValueError("context.publish() refuses an empty DataFrame")
        missing = sorted(set(spec.key_columns) - set(frame.columns))
        if missing:
            raise ValueError(f"{dataset_id} is missing contract columns: {missing}")
        summary: dict[str, Any] = {
            "dataset": dataset_id,
            "rows": int(len(frame)),
            "columns": [str(value) for value in frame.columns],
            "mode": self.mode,
        }
        if self.mode == "run":
            assert self._store is not None
            written = self._store.write(dataset_id, frame)
            summary["files_written"] = len(written["written"])
            summary["status"] = "written"
        self._published.append(summary)
        return summary

    def expect(self, dataset: str, operation: str, **details: Any) -> dict[str, Any]:
        """Declare a preview step next to the actual query code that will run."""

        dataset_id = str(dataset).strip()
        self._catalog.spec(dataset_id)
        summary = {
            "dataset": dataset_id,
            "operation": str(operation).strip(),
            **{str(key): value for key, value in details.items()},
        }
        self._planned.append(summary)
        return summary

    def read(self, dataset: str) -> pd.DataFrame:
        """Read a dataset already published by the current or an earlier recipe."""

        if self.mode != "run" or self._store is None:
            raise RuntimeError("context.read() is available only while running a recipe")
        return self._store.read(str(dataset).strip())

    def sync_batches(
        self,
        dataset: str,
        symbols: Iterable[str],
        *,
        start: str,
        end: str,
        batch_size: int = 200,
        chunk_days: int = 366,
        overlap_days: int = 1,
        force: bool = False,
        available_from: dict[str, Any] | None = None,
        dimension: tuple[str, str] | None = None,
        companions: Iterable[str] = (),
        required_columns: Iterable[str] = (),
    ) -> tuple[DateSymbolBatch, ...]:
        """Plan missing date/symbol batches from persisted per-symbol watermarks.

        Recipes still own and visibly execute each provider call. This helper
        only provides the deterministic batching/resume boundary used by built-in
        and custom recipes.
        """

        dataset_id = str(dataset).strip()
        spec = self._catalog.spec(dataset_id)
        if spec.date_column is None or "symbol" not in spec.key_columns:
            raise ValueError(f"{dataset_id} does not support date/symbol batching")
        if int(batch_size) < 1 or int(chunk_days) < 1 or int(overlap_days) < 0:
            raise ValueError("batch_size/chunk_days must be positive and overlap_days non-negative")
        requested_start = pd.Timestamp(start).normalize()
        requested_end = pd.Timestamp(end).normalize()
        if requested_start > requested_end:
            raise ValueError("start must be on or before end")
        normalized = tuple(
            dict.fromkeys(
                to_framework_symbol(str(symbol)) for symbol in symbols if str(symbol).strip()
            )
        )
        if not normalized:
            return ()
        listed = {
            to_framework_symbol(str(symbol)): pd.Timestamp(value).normalize()
            for symbol, value in dict(available_from or {}).items()
            if value is not None and not pd.isna(value)
        }
        companion_ids = tuple(str(value).strip() for value in companions)
        required = tuple(
            dict.fromkeys(str(value).strip() for value in required_columns if str(value).strip())
        )
        for companion in companion_ids:
            companion_spec = self._catalog.spec(companion)
            if (
                companion_spec.date_column != spec.date_column
                or "symbol" not in companion_spec.key_columns
            ):
                raise ValueError(f"{companion} is not compatible with {dataset_id} batching")
        watermark_sets = (
            [
                self._store.watermarks(
                    candidate,
                    dimension=dimension,
                    required_columns=required if candidate == dataset_id else (),
                    start_date=requested_start,
                    end_date=requested_end,
                    available_from=listed,
                )
                for candidate in (dataset_id, *companion_ids)
            ]
            if self.mode == "run" and self._store is not None and not force
            else []
        )
        grouped: dict[pd.Timestamp, list[str]] = {}
        for symbol in normalized:
            effective = max(requested_start, listed.get(symbol, requested_start))
            values = [watermarks.get(symbol) for watermarks in watermark_sets]
            if values and all(value is not None for value in values):
                effective = max(
                    effective,
                    min(pd.Timestamp(value) for value in values if value is not None)
                    - pd.Timedelta(days=int(overlap_days)),
                )
            if effective > requested_end:
                continue
            grouped.setdefault(effective, []).append(symbol)

        planned: list[DateSymbolBatch] = []
        for effective_start, group_symbols in sorted(grouped.items()):
            cursor = effective_start
            while cursor <= requested_end:
                chunk_end = min(
                    requested_end,
                    cursor + pd.Timedelta(days=int(chunk_days) - 1),
                )
                for offset in range(0, len(group_symbols), int(batch_size)):
                    planned.append(
                        DateSymbolBatch(
                            start=cursor.strftime("%Y-%m-%d"),
                            end=chunk_end.strftime("%Y-%m-%d"),
                            symbols=tuple(group_symbols[offset : offset + int(batch_size)]),
                        )
                    )
                cursor = chunk_end + pd.Timedelta(days=1)
        return tuple(planned)

    def watermark(
        self,
        dataset: str,
        *,
        dimension: tuple[str, str] | None = None,
    ) -> str | None:
        """Return the latest persisted date for a dataset or one dimension."""

        dataset_id = str(dataset).strip()
        self._catalog.spec(dataset_id)
        if self.mode != "run" or self._store is None:
            return None
        if dimension is not None:
            latest = self._store.dimension_watermarks(dataset_id, dimension[0]).get(
                str(dimension[1])
            )
        else:
            status = self._catalog.status(dataset_id)
            latest = status.get("date_end")
        if latest is None:
            return None
        return pd.Timestamp(latest).strftime("%Y-%m-%d")

    def require_coverage(
        self,
        dataset: str,
        *,
        start: str | None = None,
        end: str | None = None,
        fail_on_gap: bool = True,
        symbols: Iterable[str] | None = None,
        required_fields: Iterable[str] | None = None,
    ) -> None:
        """Declare the bounded quality gate that must pass after publication."""

        dataset_id = str(dataset).strip()
        self._catalog.spec(dataset_id)
        self._coverage_requirements.append(
            {
                "dataset": dataset_id,
                "start": start,
                "end": end,
                "fail_on_gap": bool(fail_on_gap),
                "required_fields": list(dict.fromkeys(required_fields)) if required_fields is not None else None,
                "symbols": (
                    list(dict.fromkeys(to_framework_symbol(value) for value in symbols))
                    if symbols is not None
                    else None
                ),
            }
        )

    def finalize(self) -> None:
        """Run each published dataset's quality contract once after all batches."""

        if self.mode != "run":
            return
        requirements = {item["dataset"]: item for item in self._coverage_requirements}
        selected = dict.fromkeys(
            [
                *(item["dataset"] for item in self._published),
                *(item["dataset"] for item in self._coverage_requirements),
            ]
        )
        for dataset_id in selected:
            requirement = requirements.get(dataset_id, {})
            report = validate_dataset(
                dataset_id,
                self._root,
                start_date=requirement.get("start"),
                as_of_date=requirement.get("end"),
                fail_on_gap=bool(requirement.get("fail_on_gap", False)),
                symbols=requirement.get("symbols"),
                required_fields=requirement.get("required_fields"),
            )
            if report["status"] != "passed":
                raise ValueError(f"quality validation failed for {dataset_id}")
            for item in self._published:
                if item["dataset"] == dataset_id:
                    item["status"] = "passed"

    def output(self, name: str, value: Any) -> dict[str, Any]:
        """Expose a bounded result summary without materializing research data."""

        output_id = str(name).strip()
        if not _OUTPUT_ID.fullmatch(output_id):
            raise ValueError(
                "output name must be 1-64 letters, numbers, dots, underscores, or hyphens"
            )
        if isinstance(value, pd.DataFrame):
            summary = {
                "name": output_id,
                "type": "dataframe",
                "rows": int(len(value)),
                "columns": [str(column) for column in value.columns],
            }
        elif isinstance(value, pd.Series):
            summary = {"name": output_id, "type": "series", "rows": int(len(value))}
        else:
            rendered = re.sub(
                r"(?i)\b(password|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+",
                r"\1=***",
                repr(value),
            )
            summary = {"name": output_id, "type": type(value).__name__, "value": rendered[:500]}
        self._outputs.append(summary)
        return summary

    def result(self) -> dict[str, Any]:
        return {
            "planned": list(self._planned),
            "published": list(self._published),
            "outputs": list(self._outputs),
        }


def data_recipe(
    *,
    id: str,
    label: str | None = None,
    template: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register exactly one callable data recipe in a Python module."""

    recipe_id = str(id).strip().lower()
    if not _RECIPE_ID.fullmatch(recipe_id):
        raise ValueError(
            "data recipe id must be 2-64 lowercase letters, numbers, dots, underscores, or hyphens"
        )

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        if hasattr(function, "__alphalab_data_recipe__"):
            raise ValueError("function is already registered as a data recipe")
        setattr(
            function,
            "__alphalab_data_recipe__",
            {"id": recipe_id, "label": label, "template": template},
        )
        return function

    return decorate


__all__ = [
    "DataRecipeContext",
    "DateSymbolBatch",
    "RQDataAPI",
    "RQSyncRequest",
    "data_recipe",
    "rq",
]
