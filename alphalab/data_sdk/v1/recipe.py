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

    def __init__(
        self,
        *,
        template_id: str,
        datasets: Iterable[str],
        start: str | None = None,
        end: str | None = None,
        symbols: Iterable[str] | None = None,
        force: bool = False,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": "rq",
            "template_id": self.template_id,
            "datasets": list(self.datasets),
            "start": self.start,
            "end": self.end,
            "symbols": list(self.symbols) if self.symbols is not None else None,
            "force": self.force,
        }


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

    def finalize(self) -> None:
        """Run each published dataset's quality contract once after all batches."""

        if self.mode != "run":
            return
        for dataset_id in dict.fromkeys(item["dataset"] for item in self._published):
            report = validate_dataset(dataset_id, self._root)
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
    "RQDataAPI",
    "RQSyncRequest",
    "data_recipe",
    "rq",
]
