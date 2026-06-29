# AlphaLab Barebone Architecture

AlphaLab Barebone is a framework core plus workstation shell. Concrete data
vendors and live broker adapters are extension points, not bundled product code.

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
```

## Dashboard Contract

The barebone backend exposes:

- `/api/data/providers`
- `/api/strategies`
- `/api/strategies/{strategy_id}`
- `/api/backtests`
- `/api/backtests/run`
- `/api/system/stats`
- `/api/system/logs`

The frontend deliberately keeps the original AlphaLab workstation shell:
multi-mode sidebar, toolbar, command palette, right rail, saved layouts, and the
large widget catalog. Widgets that require a concrete vendor, data product, or
live broker adapter are registered to `AdapterDisabledWidget` in
`dashboard/frontend/src/widgets/registry/components.tsx`. Core framework panels
remain active against the barebone API.
