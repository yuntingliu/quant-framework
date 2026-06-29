"""Runtime factor registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FactorRecord:
    name: str
    kind: str
    description: str = ""


_REGISTRY: dict[str, FactorRecord] = {}


def register_factor(name: str, kind: str, description: str = "") -> FactorRecord:
    record = FactorRecord(name=name, kind=kind, description=description)
    _REGISTRY[name] = record
    return record


def _populate() -> None:
    if _REGISTRY:
        return
    from alphalab.factors.fundamental import FundamentalFactors
    from alphalab.factors.technical import TechnicalFactors

    for name in TechnicalFactors().available_factors:
        register_factor(name, "technical", f"Technical factor {name}")
    for name in FundamentalFactors.available_factors:
        register_factor(name, "fundamental", f"Fundamental factor {name}")


def list_factors(kind: str | None = None) -> list[FactorRecord]:
    _populate()
    records = list(_REGISTRY.values())
    if kind is not None:
        records = [record for record in records if record.kind == kind]
    return sorted(records, key=lambda record: (record.kind, record.name))


def get_factor(name: str) -> FactorRecord:
    _populate()
    if name not in _REGISTRY:
        raise KeyError(f"Unknown factor {name!r}")
    return _REGISTRY[name]


def compute_factor(name: str, data: Any, **kwargs: Any) -> pd.Series | pd.DataFrame:
    record = get_factor(name)
    if record.kind == "technical":
        from alphalab.factors.technical import TechnicalFactors

        return TechnicalFactors().compute(name, data)
    if record.kind == "fundamental":
        from alphalab.factors.fundamental import FundamentalFactors

        return FundamentalFactors().compute(name, data)
    raise NotImplementedError(f"Cannot compute factor kind {record.kind!r}")


__all__ = ["FactorRecord", "compute_factor", "get_factor", "list_factors", "register_factor"]

