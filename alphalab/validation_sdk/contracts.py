"""Public contracts available inside a project's validation.py."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, TypeVar

import pandas as pd

F = TypeVar("F", bound=Callable[..., Any])


def analysis(*, id: str, label: str | None = None) -> Callable[[F], F]:
    """Register one named validation output."""

    if not id or id.startswith("_"):
        raise ValueError("analysis id must be public")

    def decorate(function: F) -> F:
        setattr(function, "__alphalab_analysis__", {"id": id, "label": label})
        return function

    return decorate


@dataclass(frozen=True)
class ValidationContext:
    """Frozen run inputs exposed to validation.py after the event engine finishes."""

    returns: pd.Series
    benchmark_returns: pd.Series
    weights: pd.DataFrame
    factor_returns: pd.DataFrame
    executions: tuple[dict[str, Any], ...]
    settings: Mapping[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ValidationContext":
        return cls(
            returns=pd.Series(payload["returns"]).copy(),
            benchmark_returns=pd.Series(payload["benchmark_returns"]).copy(),
            weights=pd.DataFrame(payload["weights"]).copy(),
            factor_returns=pd.DataFrame(payload["factor_returns"]).copy(),
            executions=tuple(dict(item) for item in payload.get("executions", ())),
            settings=MappingProxyType(dict(payload.get("settings", {}))),
        )
