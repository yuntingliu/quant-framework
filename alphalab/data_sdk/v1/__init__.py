"""AlphaLab Data SDK v1.

User code implements the small provider protocols and combines them into one
PythonDataSource.  The resulting DataEngine is the same engine consumed by
factor evaluation and strategy backtests; no provider-specific strategy API is
introduced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from alphalab.dataio.engine import DataCache, DataEngine
from alphalab.dataio.factor_returns import build_factor_returns
from alphalab.dataio.fundamentals import (
    BALANCE_FIELDS,
    INCOME_FIELDS,
    build_canonical_fundamentals,
)
from alphalab.dataio.providers.protocol import (
    FactorProvider,
    FundamentalProvider,
    InstrumentProvider,
    MarketDataProvider,
)
from alphalab.dataio.rq_frames import (
    framework_symbols,
    normalize_rq_bars,
    normalize_rq_financials,
    normalize_rq_instruments,
    normalize_rq_yield_curve,
    rq_order_book_ids,
)
from alphalab.data_sdk.v1.recipe import (
    DataRecipeContext,
    RQDataAPI,
    RQSyncRequest,
    data_recipe,
    rq,
)

SDK_VERSION = 1
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")


@dataclass(frozen=True)
class PythonDataSource:
    """One explicit bundle of providers implemented by trusted local Python."""

    id: str
    market: MarketDataProvider
    instruments: InstrumentProvider
    fundamentals: FundamentalProvider | None = None
    factors: FactorProvider | None = None

    def __post_init__(self) -> None:
        normalized = str(self.id).strip().lower()
        if not _SOURCE_ID.fullmatch(normalized):
            raise ValueError(
                "data source id must be 2-64 lowercase letters, numbers, dots, "
                "underscores, or hyphens"
            )
        if not isinstance(self.market, MarketDataProvider):
            raise TypeError("market must implement MarketDataProvider")
        if not isinstance(self.instruments, InstrumentProvider):
            raise TypeError("instruments must implement InstrumentProvider")
        if self.fundamentals is not None and not isinstance(self.fundamentals, FundamentalProvider):
            raise TypeError("fundamentals must implement FundamentalProvider")
        if self.factors is not None and not isinstance(self.factors, FactorProvider):
            raise TypeError("factors must implement FactorProvider")
        object.__setattr__(self, "id", normalized)

    @property
    def capabilities(self) -> tuple[str, ...]:
        values = ["market", "instruments"]
        if self.fundamentals is not None:
            values.append("fundamentals")
        if self.factors is not None:
            values.append("factors")
        return tuple(values)

    def create_engine(self, *, cache: DataCache | None = None) -> DataEngine:
        engine = DataEngine(cache=cache)
        engine.register_market(self.id, self.market, default=True)
        engine.register_instrument(self.id, self.instruments, default=True)
        if self.fundamentals is not None:
            engine.register_fundamental(self.id, self.fundamentals, default=True)
        if self.factors is not None:
            engine.register_factor(self.id, self.factors, default=True)
        return engine


def data_source(
    source_id: str,
    *,
    market: MarketDataProvider,
    instruments: InstrumentProvider,
    fundamentals: FundamentalProvider | None = None,
    factors: FactorProvider | None = None,
) -> PythonDataSource:
    """Build a versioned custom data source from ordinary Python providers."""

    return PythonDataSource(
        id=source_id,
        market=market,
        instruments=instruments,
        fundamentals=fundamentals,
        factors=factors,
    )


__all__ = [
    "SDK_VERSION",
    "BALANCE_FIELDS",
    "DataCache",
    "DataEngine",
    "FactorProvider",
    "FundamentalProvider",
    "InstrumentProvider",
    "INCOME_FIELDS",
    "MarketDataProvider",
    "PythonDataSource",
    "DataRecipeContext",
    "RQDataAPI",
    "RQSyncRequest",
    "build_canonical_fundamentals",
    "build_factor_returns",
    "data_source",
    "data_recipe",
    "framework_symbols",
    "normalize_rq_bars",
    "normalize_rq_financials",
    "normalize_rq_instruments",
    "normalize_rq_yield_curve",
    "rq",
    "rq_order_book_ids",
]
