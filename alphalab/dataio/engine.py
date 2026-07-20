"""Provider registry and cached access to framework data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import pandas as pd

from alphalab.dataio.errors import MissingDataError
from alphalab.dataio.io_utils import atomic_write_parquet
from alphalab.dataio.providers.local import (
    LocalParquetFactorProvider,
    LocalParquetFundamentalProvider,
    LocalParquetMarketDataProvider,
    PartitionedParquetFundamentalProvider,
    PartitionedParquetMarketDataProvider,
)
from alphalab.dataio.providers.protocol import (
    FactorProvider,
    FundamentalProvider,
    MarketDataProvider,
    to_wide,
)
from alphalab.utils.paths import CACHE_DIR, DATA_DIR, RUNTIME_DIR


class DataCache:
    """Small file-backed cache keyed by request parameters."""

    def __init__(self, cache_dir: str | Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR / "engine"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, pd.DataFrame] = {}

    def get(self, key: str) -> pd.DataFrame | None:
        if key in self._memory:
            return self._memory[key].copy()
        path = self.cache_dir / f"{key}.parquet"
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        self._memory[key] = df
        return df.copy()

    def put(self, key: str, df: pd.DataFrame) -> None:
        self._memory[key] = df.copy()
        atomic_write_parquet(df, self.cache_dir / f"{key}.parquet")

    def clear(self) -> None:
        self._memory.clear()
        if self.cache_dir.exists():
            for path in self.cache_dir.glob("*.parquet"):
                path.unlink(missing_ok=True)

    @staticmethod
    def key(*parts: object) -> str:
        raw = json.dumps(parts, sort_keys=True, default=str)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


class DataEngine:
    """Central access point for market, fundamental and factor providers."""

    def __init__(self, cache: DataCache | None = None):
        self._market: dict[str, MarketDataProvider] = {}
        self._fundamental: dict[str, FundamentalProvider] = {}
        self._factor: dict[str, FactorProvider] = {}
        self._default_market: str | None = None
        self._default_fundamental: str | None = None
        self._default_factor: str | None = None
        self._cache = cache or DataCache()

    def register_market(self, name: str, provider: MarketDataProvider, default: bool = False) -> "DataEngine":
        self._market[name] = provider
        if default or self._default_market is None:
            self._default_market = name
        return self

    def register_fundamental(
        self,
        name: str,
        provider: FundamentalProvider,
        default: bool = False,
    ) -> "DataEngine":
        self._fundamental[name] = provider
        if default or self._default_fundamental is None:
            self._default_fundamental = name
        return self

    def register_factor(self, name: str, provider: FactorProvider, default: bool = False) -> "DataEngine":
        self._factor[name] = provider
        if default or self._default_factor is None:
            self._default_factor = name
        return self

    def get_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        freq: str = "1d",
        source: Optional[str] = None,
        use_cache: bool = True,
        strict: bool = True,
        fields: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        source = source or self._default_market
        if source is None or source not in self._market:
            raise MissingDataError(f"No market provider registered for source {source!r}")
        key = DataCache.key("bars", source, sorted(symbols), start, end, freq, sorted(fields or []))
        if use_cache:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        out = self._market[source].get_bars(symbols, start, end, freq=freq, fields=fields)
        if strict and out.empty:
            raise MissingDataError(f"No market bars returned for {symbols} from {source!r}")
        if use_cache and not out.empty:
            self._cache.put(key, out)
        return out

    def get_bars_wide(
        self,
        symbols: list[str],
        start: str,
        end: str,
        field: str = "close",
        freq: str = "1d",
        source: Optional[str] = None,
        strict: bool = True,
    ) -> pd.DataFrame:
        bars = self.get_bars(
            symbols,
            start,
            end,
            freq=freq,
            source=source,
            strict=strict,
            fields=[field],
        )
        return to_wide(bars, field)

    def get_symbols(self, universe: str = "all", source: Optional[str] = None) -> list[str]:
        source = source or self._default_market
        if source is None or source not in self._market:
            return []
        return self._market[source].get_symbols(universe)

    def get_latest_date(self, source: Optional[str] = None) -> str | None:
        source = source or self._default_market
        if source is None or source not in self._market:
            return None
        provider = self._market[source]
        if hasattr(provider, "get_latest_date"):
            return provider.get_latest_date()
        return None

    def get_fundamentals(
        self,
        symbols: list[str],
        fields: list[str],
        start_quarter: str,
        end_quarter: str,
        source: Optional[str] = None,
        use_cache: bool = True,
        asof_date: Optional[str] = None,
        strict: bool = True,
    ) -> pd.DataFrame:
        source = source or self._default_fundamental
        if source is None or source not in self._fundamental:
            raise MissingDataError(f"No fundamental provider registered for source {source!r}")
        key = DataCache.key("fundamentals", source, sorted(symbols), sorted(fields), start_quarter, end_quarter, asof_date)
        if use_cache:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        out = self._fundamental[source].get_fundamentals(
            symbols,
            fields,
            start_quarter,
            end_quarter,
            asof_date=asof_date,
            strict=strict,
        )
        if strict and out.empty:
            raise MissingDataError(f"No fundamentals returned for {symbols} from {source!r}")
        if use_cache and not out.empty:
            self._cache.put(key, out)
        return out

    def get_factors(
        self,
        names: list[str],
        start: str,
        end: str,
        freq: str = "1M",
        source: Optional[str] = None,
        use_cache: bool = True,
        strict: bool = True,
    ) -> pd.DataFrame:
        source = source or self._default_factor
        if source is None or source not in self._factor:
            raise MissingDataError(f"No factor provider registered for source {source!r}")
        key = DataCache.key("factors", source, sorted(names), start, end, freq)
        if use_cache:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        out = self._factor[source].get_factors(names, start, end, freq=freq, strict=strict)
        if strict and out.empty:
            raise MissingDataError(f"No factor returns returned for {names} from {source!r}")
        if use_cache and not out.empty:
            self._cache.put(key, out)
        return out

    def get_risk_free_rate(self, start: str, end: str, freq: str = "1M", source: Optional[str] = None) -> pd.Series:
        source = source or self._default_factor
        if source is None or source not in self._factor:
            return pd.Series(dtype=float, name="rf")
        return self._factor[source].get_risk_free_rate(start, end, freq)

    def providers(self) -> dict[str, list[str]]:
        return {
            "market": sorted(self._market),
            "fundamental": sorted(self._fundamental),
            "factor": sorted(self._factor),
        }

    def clear_cache(self) -> None:
        self._cache.clear()


def create_default_engine(data_dir: str | Path | None = None) -> DataEngine:
    """Create an engine wired to the generic local parquet layout."""

    root = Path(data_dir) if data_dir is not None else DATA_DIR
    engine = DataEngine()
    engine.register_market("local", LocalParquetMarketDataProvider(root / "market"), default=True)
    engine.register_fundamental("local", LocalParquetFundamentalProvider(root / "fundamentals"), default=True)
    engine.register_factor("local", LocalParquetFactorProvider(root / "factors"), default=True)
    return engine


def create_rq_engine_from_env(cache: DataCache | None = None) -> DataEngine:
    """Create an engine whose market and fundamental source is RQData."""

    from alphalab.dataio.providers.rq import RQDataProvider

    provider = RQDataProvider.from_env()
    engine = DataEngine(cache=cache)
    engine.register_market("rq", provider, default=True)
    engine.register_fundamental("rq", provider, default=True)
    return engine


def create_runtime_engine(runtime_dir: str | Path | None = None) -> DataEngine:
    """Create an engine over ignored, partitioned runtime data."""

    root = Path(runtime_dir) if runtime_dir is not None else RUNTIME_DIR
    engine = DataEngine(cache=DataCache(root / "cache" / "engine"))
    engine.register_market(
        "runtime",
        PartitionedParquetMarketDataProvider(root),
        default=True,
    )
    engine.register_fundamental(
        "runtime",
        PartitionedParquetFundamentalProvider(root),
        default=True,
    )
    return engine
