# Data SDK v1 Contract

`alphalab.data_sdk.v1` is the stable Python boundary for data acquisition and
custom sources. The primary Data Workbench contract is a code-backed recipe:

```python
from alphalab.data_sdk.v1 import (
    data_recipe, normalize_rq_bars, normalize_rq_instruments, rq,
    rq_order_book_ids,
)

@data_recipe(id="research_data", label="ETF 日线", template="rq.etf_daily")
def research_data(
    context,
    *,
    start: str = "2021-08-25",
    end: str = "2026-08-25",
    symbols: tuple[str, ...] | None = None,
):
    raw_instruments = rq.all_instruments(type="ETF", market="cn")
    instruments = normalize_rq_instruments(
        raw_instruments, snapshot_date=end, asset_type="ETF",
    )
    context.publish("rq.instruments", instruments)

    order_book_ids = rq_order_book_ids(symbols or tuple(instruments["symbol"]))
    adjusted = rq.get_price(
        order_book_ids, start_date=start, end_date=end,
        frequency="1d", fields=None, adjust_type="pre", expect_df=True,
    )
    unadjusted = rq.get_price(
        order_book_ids, start_date=start, end_date=end,
        frequency="1d", fields=["open", "high", "low", "close"],
        adjust_type="none", expect_df=True,
    )
    context.publish("rq.bars", normalize_rq_bars(adjusted, unadjusted))
```

The normalizer preserves the adjusted provider schema and adds unadjusted
`raw_open`, `raw_high`, `raw_low`, and `raw_close`. Price-limit checks use the
matching unadjusted execution price; factor research continues to use adjusted
OHLC.

Production recipes place those visible RQ calls inside
`context.sync_batches(...)`. The helper uses persisted per-symbol watermarks,
listing dates, an explicit overlap, and optional field companions to return
deterministic `DateSymbolBatch(start, end, symbols)` values. It never calls the
vendor. A recipe may also use:

```python
context.watermark("rq.daily_factors", dimension=("field", "roe"))
context.require_coverage(
    "rq.bars", start=start, end=end, fail_on_gap=True, symbols=active_symbols
)
```

Recipes may pass `required_columns` to `sync_batches`. In run mode, a watermark
is ignored when a persisted partition still uses an older schema that lacks a
required column. The built-in `rq.bars` recipe uses this mechanism for
`raw_open/raw_high/raw_low/raw_close`, so its first normal run after the schema
upgrade replays the configured history without an operator-only force flag.

Passing the recipe's active symbols keeps a template-specific validation from
being polluted by bars acquired through another template in the shared runtime
store. Omitting `symbols` intentionally validates the whole stored dataset.

`context.publish()` commits every completed batch atomically. If a later RQ
request fails or the job is cancelled, rerunning the same recipe resumes from
the stored symbol/field watermarks and retains the earlier batches. `force=True`
rewrites the requested range by primary key; it does not delete rows outside
that range.

`rq` connects lazily through the configured `RQDataClient` and transparently
exposes the installed `rqdatac` module. AlphaLab deliberately does not mirror
all vendor functions; new RQData operations become available when the local
package is upgraded. Refer to the
[official RQData Python API](https://www.ricequant.com/doc/rqdata/python/index-rqdatac)
for operation signatures.

A built-in recipe executes these visible RQ calls and returns `None` after
calling `context.publish(dataset, frame)`. The decorator's `template` value is
UI metadata, not an execution switch. `RQSyncRequest` remains an optional
explicit handoff for custom/CLI integrations, but built-ins do not use it.
`publish` accepts only catalogued runtime
datasets, validates their key columns, and materializes only in run mode.
Keyword-only literal defaults are the no-code projection boundary; form edits
rewrite those exact Python nodes, while arbitrary function logic remains
untouched and is shown as custom.

The recipe runs in a spawned process using the same local Python environment.
It is trusted local execution, not a security sandbox. Preview and run require
explicit execution confirmation, impose a timeout and bounded logs, and retain
the exact source hash in the synchronization job.

## Custom provider facade

For a long-lived source adapter, a custom source supplies ordinary provider
objects; the SDK registers them into the same `DataEngine` used by built-in
sources.

```python
from alphalab.data_sdk.v1 import data_source

source = data_source(
    "internal.vendor",
    market=my_market_provider,
    instruments=my_instrument_provider,
    fundamentals=my_fundamental_provider,  # optional
    factors=my_factor_provider,            # optional
    research=my_research_provider,          # optional state/factors/membership
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
prices and a point-in-time investable universe. Fundamentals, factor returns,
and historical research data are optional capabilities.

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

Optional `ResearchDataProvider` implements `get_market_state()`,
`get_daily_factors()`, and `get_index_components()`. Its canonical outputs are
respectively state rows keyed by `date,symbol`, long factor rows keyed by
`date,symbol,field`, and membership rows keyed by
`date,index_symbol,symbol`. Strategy code consumes these only through Context;
it never receives the provider object.

Instrument publication records both `snapshot_date` (the requested effective
research date) and `retrieved_at` (when the current vendor reference snapshot
was fetched), so historical filtering and provenance are not conflated.

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
