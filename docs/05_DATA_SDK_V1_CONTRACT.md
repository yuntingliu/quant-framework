# Data SDK v1 Contract

`alphalab.data_sdk.v1` is the stable Python boundary for data sources that are
not built into AlphaLab. A custom source supplies ordinary provider objects; the
SDK registers them into the same `DataEngine` used by built-in sources.

```python
from alphalab.data_sdk.v1 import data_source

source = data_source(
    "internal.vendor",
    market=my_market_provider,
    instruments=my_instrument_provider,
    fundamentals=my_fundamental_provider,  # optional
    factors=my_factor_provider,            # optional
)
engine = source.create_engine()
bars = engine.get_bars(["510300.SH"], "2025-01-01", "2025-12-31")
```

That engine can be passed directly to the public `preview_strategy`,
`evaluate_factor_snapshot`, `evaluate_factor_history`, and
`run_strategy_backtest` functions. The dashboard's named `runtime` profile is
still the materialized local runtime store; registering a Python provider does
not silently replace that profile.

The source ID is 2–64 lowercase letters, numbers, dots, underscores, or
hyphens. `market` and `instruments` are required because a backtest needs both
prices and a point-in-time investable universe. Fundamentals and factor returns
are optional capabilities.

## Provider methods

`MarketDataProvider` implements:

```python
def get_bars(symbols, start, end, freq="1d", fields=None) -> pandas.DataFrame: ...
def get_symbols(universe="all") -> list[str]: ...
```

Bars use a long table with `date`, `symbol`, and requested market fields. Daily
strategy execution normally requires `open`, `high`, `low`, `close`, `volume`,
and `amount`. Dates and symbols must be unique at the provider's natural key.

`InstrumentProvider` implements:

```python
def get_instruments(asof_date=None) -> pandas.DataFrame: ...
```

The result requires `symbol`. Production research should also provide
`snapshot_date`, `listed_date`, `de_listed_date`, `asset_type`, and tradability
metadata available from the source. This snapshot is what prevents current
membership from leaking into historical research.

Optional `FundamentalProvider` and `FactorProvider` implement the runtime-
checkable protocols exported by the facade. Their exact signatures are visible
through normal Python typing and match `DataEngine.get_fundamentals()` and
`DataEngine.get_factors()`.

## Ownership and safety boundary

- Provider code owns authentication, transport, retries, and vendor field
  mapping.
- `DataEngine` owns registration, source selection, bounded reads, caching, and
  missing-data semantics.
- Strategy SDK `Context` owns point-in-time slicing and never exposes a provider.
- Custom provider code is trusted local Python, not sandboxed Python. Review it
  before execution and keep secrets in local environment configuration.
- A provider-specific method must not be called from strategy source. Extend or
  adapt the provider at this boundary so factor tests and backtests see the same
  data contract.

The runnable [CSV adapter example](../examples/custom_data_source.py) shows the
full market/instrument implementation. Replace its file reads with any vendor
SDK, database, or internal service while keeping the same provider methods.

The v1 import path is versioned deliberately. New optional capabilities can be
added without changing existing providers; incompatible method or schema
changes require a new SDK major version.
