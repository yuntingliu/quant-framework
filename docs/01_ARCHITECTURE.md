# AlphaLab Barebone Architecture

AlphaLab Barebone is a framework core plus workstation shell and a compact real
historical sample. RQData is available as an optional, environment-configured
information provider. Live broker adapters remain extension points.

## Core Loop

```text
local/vendor adapter -> DataEngine -> StrategyConfig -> SignalEngine -> run_backtest_detailed
                                      \-> factor diagnostics / ResultStore / dashboard
```

The public Python facade is:

```python
from alphalab import (
    DataEngine,
    create_default_engine,
    create_runtime_engine,
    create_rq_engine_from_env,
    RQDataConfig,
    RQDataProvider,
    StrategyConfig,
    ExecutionSpec,
    SignalEngine,
    BacktestResult,
    run_backtest,
    run_backtest_detailed,
    evaluate_factor,
    ResultStore,
    list_factors,
    compute_factor,
    get_factor,
)
```

## Module Ownership

| Path | Owns |
| --- | --- |
| `alphalab/dataio/` | Provider protocols, `DataEngine`, runtime catalog/storage, RQ acquisition, sync jobs and quality checks. |
| `alphalab/tools/` | Canonical typed data tools shared by CLI/API and optional external agent orchestration; no embedded LLM planner. |
| `alphalab/factors/` | Generic technical/fundamental factor registry and formulas. |
| `alphalab/analytics/` | Factor diagnostics, benchmark construction, validation splits, bootstrap inference and robustness gates. |
| `alphalab/strategy/` | YAML schema, validation, immutable templates and ignored local copies. |
| `alphalab/strategies/` | Generic built-in templates only. |
| `alphalab/engine.py` | Target generation and backtest parity point. |
| `alphalab/store.py` | SQLite state for strategies, backtests, research runs, provenance-bound reports, signals, paper accounts and journal. |
| `alphalab/execution/` | Broker-neutral contracts and paper execution helpers. |
| `dashboard/` | FastAPI backend and original-style React/Electron Dockview workstation GUI. |
| `dashboard/backend/routers/conexus.py` | Optional same-origin proxy for a separately hosted published Research Agent. |
| `integrations/conexus/` | Reviewable optional Harness source; generated Canvas and publication state stay ignored. |

## Data Contract

Adapters should implement one or more protocols from
`alphalab.dataio.providers.protocol`:

- `MarketDataProvider`
- `InstrumentProvider`
- `FundamentalProvider`
- `FactorProvider`
- `RealtimeProvider`

The built-in local provider expects:

```text
data/market/bars.parquet
data/instruments/instruments.parquet  # optional dated listing snapshots
data/fundamentals/fundamentals.parquet
data/factors/factor_returns.parquet
data/app/alphalab.db
data/manifest.json
```

`fundamentals.parquet` includes `available_date`. The local provider applies
`available_date <= asof_date` before returning rows, so historical signals only
see statements available at the decision date. `manifest.json` owns sample
coverage, provenance, adjustment policy, hashes, and research caveats.

Instrument snapshots are selected at or before each signal date and filtered by
`listed_date`/`de_listed_date`. If no earlier snapshot exists, the earliest
later snapshot is used only with an explicit audit warning. If no snapshot
exists, the engine falls back to bar-history membership and records that
limitation. The bundled demo uses a fixed pre-sample universe and remains
development data, not a survivor-bias-free investable universe.

`create_rq_engine_from_env()` registers the optional `RQDataProvider` as the
market, instrument and fundamental source. It reads only `RQ_USER`, `RQ_PASSWORD`, and
`RQ_HOST`, initializes lazily, and does not own realtime or execution behavior.

`create_runtime_engine()` reads only the ignored partitioned datasets below
`data/runtime/`. Demo and runtime are explicit profiles and are never silently
combined. Runtime factor returns are derived explicitly after RQ bars and PIT
fundamentals are ready; a missing factor dataset remains an explicit error.

## Dashboard Contract

The barebone backend exposes:

- `/api/data/providers`
- `/api/data/manifest`
- `/api/data/market/symbols`
- `/api/data/market/bars`
- `/api/data/fundamentals`
- `/api/data/factors/returns`
- `/api/factor-research/library`
- `/api/factor-research/evaluate`
- `/api/agent/data-tools`
- `/api/agent/data-tools/{tool_name}/invoke`
- `/api/data-sync/health`
- `/api/data-sync/catalog`
- `/api/data-sync/plan`
- `/api/data-sync/jobs`
- `/api/data-sync/validate`
- `/api/strategies`
- `/api/strategies/{strategy_id}`
- `/api/strategies/{strategy_id}/clone`
- `/api/backtests`
- `/api/backtests/run`
- `/api/backtests/{backtest_id}`
- `/api/backtests/{backtest_id}/analysis`
- `/api/backtests/{backtest_id}/robustness`
- `/api/backtests/compare`
- `/api/research/runs`
- `/api/reports`
- `/api/reports/{artifact_id}`
- `/api/signals/generate`
- `/api/signals/latest`
- `/api/paper/account`
- `/api/paper/orders`
- `/api/paper/fills`
- `/api/paper/rebalance/preview`
- `/api/paper/rebalance/execute`
- `/api/system/stats`
- `/api/system/logs`
- `/api/conexus/status` and `/api/conexus/*` when the optional Web Host is available

The frontend keeps the AlphaLab workstation shell while exposing five primary
research widgets: `data.workbench`, `factor.workbench`, `strategy.workbench`,
`backtest.workbench`, and `report.workbench`. Each widget owns one durable
artifact boundary in the Agent workflow. Default layouts compose only these
workstations. The left navigation exposes the matching Data, Factor, Strategy,
Backtest, and Report modes. Each mode starts with its primary workbench and
keeps an independent user-customizable Dockview layout. Vendor and live-broker
pages remain registered as disabled extension slots until an adapter package
supplies them.

The Data Workbench opens on a profile-aware daily OHLCV view with symbol and
history-range selection, candlesticks, and volume. Catalog, bounded query,
local synchronization, and quality controls remain additional views in the
same workstation rather than separate navigation destinations.

Research orchestration in the framework core is deterministic and uses the
existing services. It stops after paper risk preview. The embedded LLM planner
remains `not_configured`, and paper execution requires a separate explicit
confirmation request. The optional Conexus Harness is the external planner: it
can discover and invoke the canonical typed data registry, combine market,
point-in-time fundamental, factor, strategy, and backtest data, and return
bounded reports, tables, charts, and workspace commands. Missing Conexus state
never changes the deterministic workflow or data profile.

Backtests generate signals from period-end information and execute them on the
next observed session at the configured open or close. Cash, one-way costs,
slippage, square-root participation impact, positive-volume checks and amount
participation limits are explicit. Missing amount blocks a trade instead of
assuming infinite liquidity. Saved runs include per-period execution audits plus
exact strategy, data-file and Git fingerprints. This remains a daily-bar,
weekly/monthly-rebalance, long-only research engine; it is not a live or
intraday execution simulator.
