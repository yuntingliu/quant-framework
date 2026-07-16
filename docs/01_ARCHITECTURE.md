# AlphaLab Barebone Architecture

AlphaLab Barebone is a framework core plus workstation shell and a compact real
historical sample. RQData is available as an optional, environment-configured
information provider. Live broker adapters remain extension points.

## Core Loop

```text
local/vendor adapter -> DataEngine -> StrategyConfig -> SignalEngine -> run_backtest
                                      \-> ResultStore / dashboard preview
```

The public Python facade is:

```python
from alphalab import (
    DataEngine,
    create_default_engine,
    create_rq_engine_from_env,
    RQDataConfig,
    RQDataProvider,
    StrategyConfig,
    SignalEngine,
    run_backtest,
    ResultStore,
    list_factors,
    compute_factor,
    get_factor,
)
```

## Module Ownership

| Path | Owns |
| --- | --- |
| `alphalab/dataio/` | Provider protocols, `DataEngine`, generic local parquet providers. |
| `alphalab/factors/` | Generic technical/fundamental factor registry and formulas. |
| `alphalab/strategy/` | YAML schema and validation. |
| `alphalab/strategies/` | Generic built-in templates only. |
| `alphalab/engine.py` | Target generation and backtest parity point. |
| `alphalab/store.py` | SQLite state for strategies, backtests, signals, orders and journal. |
| `alphalab/execution/` | Broker-neutral contracts and paper execution helpers. |
| `dashboard/` | FastAPI backend and original-style React/Electron Dockview workstation GUI. |

## Data Contract

Adapters should implement one or more protocols from
`alphalab.dataio.providers.protocol`:

- `MarketDataProvider`
- `FundamentalProvider`
- `FactorProvider`
- `RealtimeProvider`

The built-in local provider expects:

```text
data/market/bars.parquet
data/fundamentals/fundamentals.parquet
data/factors/factor_returns.parquet
data/app/alphalab.db
data/manifest.json
```

`fundamentals.parquet` includes `available_date`. The local provider applies
`available_date <= asof_date` before returning rows, so historical signals only
see statements available at the decision date. `manifest.json` owns sample
coverage, provenance, adjustment policy, hashes, and research caveats.

`create_rq_engine_from_env()` registers the optional `RQDataProvider` as both
the market and fundamental source. It reads only `RQ_USER`, `RQ_PASSWORD`, and
`RQ_HOST`, initializes lazily, and does not own realtime or execution behavior.

## Dashboard Contract

The barebone backend exposes:

- `/api/data/providers`
- `/api/data/manifest`
- `/api/data/market/symbols`
- `/api/data/market/bars`
- `/api/data/factors/returns`
- `/api/strategies`
- `/api/strategies/{strategy_id}`
- `/api/backtests`
- `/api/backtests/run`
- `/api/backtests/{backtest_id}`
- `/api/signals/generate`
- `/api/signals/latest`
- `/api/paper/orders`
- `/api/system/stats`
- `/api/system/logs`

The frontend deliberately keeps the original AlphaLab workstation shell:
multi-mode sidebar, toolbar, command palette, right rail, saved layouts, and the
large widget catalog. Default layouts contain only real-data panels backed by
the routes above. Widgets requiring a concrete vendor or live broker remain
registered to `AdapterDisabledWidget` as optional extension slots.
