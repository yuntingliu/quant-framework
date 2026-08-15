# AlphaLab Barebone Architecture

AlphaLab Barebone is a framework core plus workstation shell and a compact real
historical sample. RQData is available as an optional, environment-configured
information provider. Live broker adapters remain extension points.

## Core Loop

```text
                                      /-> StrategyConfig -> SignalEngine -> run_backtest_detailed
local/vendor adapter -> DataEngine --|
                                      \-> TimingStrategyConfig -> run_timing_backtest
                                      \-> factor diagnostics / ResultStore / dashboard
```

Both strategy signal types dispatch on `implementation.kind`. `configured` uses
the registered factor/signal engines; `python` calls the same selection or timing
boundary through a timeout-bounded child process. Python is an
implementation choice, not a separate strategy domain or backtest path.

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
    TimingStrategyConfig,
    StrategyImplementationSpec,
    PythonStrategyError,
    validate_python_source,
    ExecutionSpec,
    SignalEngine,
    BacktestResult,
    run_backtest,
    run_backtest_detailed,
    run_timing_backtest,
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
| `alphalab/strategy/` | Stock-selection and market-timing schemas, validation, immutable templates, ignored local copies, and trusted-local Python execution. |
| `alphalab/strategies/` | Generic built-in stock-selection templates. |
| `alphalab/timing_strategies/` | Generic built-in market-timing templates. |
| `alphalab/engine.py` | Target generation and backtest parity point. |
| `alphalab/timing.py` | Monthly timing-signal evaluation, lagged market exposure and timing backtests. |
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
- `/api/market/custom-risk-factor/evaluate`
- `/api/agent/data-tools`
- `/api/agent/data-tools/{tool_name}/invoke`
- `/api/data-sync/health`
- `/api/data-sync/catalog`
- `/api/data-sync/plan`
- `/api/data-sync/jobs`
- `/api/data-sync/validate`
- `/api/strategies`
- `/api/strategies/validate`
- `/api/strategies/selection-preview`
- `/api/strategies/timing-research`
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
history-range selection, candlesticks, and volume. The symbol picker searches
both instrument code and instrument name. The bundled demo manifest includes a
display-only name snapshot; runtime names come from synchronized instruments.
Its compact watchlist is browser-local UI preference state and does not become research data
or alter the selected data profile. Catalog, bounded query,
local synchronization, and quality controls remain in a collapsed advanced
data-management section of the same workstation rather than competing with the
primary market view or becoming separate navigation destinations.

The Factor Workbench shares the same `demo` or `runtime` data profile selected
in the Data Workbench, but a factor evaluation uses the profile's full eligible
cross-sectional universe rather than the Data Workbench's currently displayed
symbol or selected raw dataset. Its UI separates three different research
objects: stock cross-sectional factors and their PIT diagnostics, MKT/SMB/HML
market-risk-factor return analytics, and a timing-signal catalog. Timing signals
produce aggregate market exposure and link to the Strategy Workbench for full
signal, portfolio, risk, execution, and backtest research; they are not treated
as stock-ranking factors. The cross-sectional area gives safe custom expressions
their own visible research path. Expressions compose registered PIT technical and
fundamental inputs through a whitelist; they do not execute arbitrary Python.
Validated expressions can be added directly to a configured stock-selection
strategy and are persisted as part of that strategy definition.

Market-risk customization has a different contract. The Factor Workbench may
derive a descriptive return series as a safe linear combination of registered
MKT/SMB/HML/MOM/RMW/rf returns. It permits only addition, subtraction, scalar
multiplication, and division by a non-zero scalar; it does not silently promote
the result to a tradable portfolio or timing signal. Timing customization remains
in the Strategy Workbench, where registered signals are parameterized or a
trusted-local Python hook returns bounded aggregate market exposure.

The Strategy Workbench is one composition boundary with two signal types and a
shared first-principles workflow: signal design, portfolio construction, risk
control, and trade execution. A `stock_selection` strategy owns PIT universe and
factor signals, top-N/equal-weight construction, investability and concentration
limits, and stock execution assumptions. A `market_timing` strategy owns MKT
time-series signals, score-to-exposure construction, exposure limits, and turnover
costs. Additional strategy templates must compose these primitives instead of
introducing another top-level type without a distinct execution contract.

YAML remains the advanced view of the corresponding canonical config. Strategy
detail responses include both representations, and `/api/strategies/validate`
dispatches on the required `strategy_type`, returning normalized YAML, normalized
config, and machine-readable checks. The only migration exception is an existing
selection YAML with no discriminator, which is read as `stock_selection`; newly
serialized configs always include the type. Built-in templates remain immutable
and local copies must be saved before Backtest Workbench handoff.

Within both signal types, `implementation.kind` is exactly `configured` or `python`.
Python source is a sidecar beside the ignored local YAML, is validated with the
same request, and is executed only by the core selection/timing engine. Selection
code receives point-in-time eligible candidates and returns exact target weights;
timing code receives point-in-time monthly MKT history and returns one aggregate
exposure. The core still enforces universe membership, stock count, weight and
exposure limits. The child process provides
timeout and crash containment, not a
security sandbox: custom source is trusted local code and may access the user's
machine with the Python process's permissions.

Stock selection can send an unsaved config to
`/api/strategies/selection-preview` and inspect cross-sectional rank, factor
contribution, cutoff, target weight, and exclusions. The same `SignalEngine`
supplies periodic holdings and paper signals. Timing can send an unsaved config
to `/api/strategies/timing-research` and inspect the lagged monthly exposure,
signal components, MKT benchmark, and performance without persisting a backtest.
Timing signals formed at one month-end apply to the following return period and
never produce individual stock orders.

The Backtest Workbench keeps next-run settings separate from the identity of the
persisted result being inspected. Strategy handoff from the Strategy Workbench is
preserved, while each result is labeled by its saved strategy snapshot, profile,
date range, run time, and provenance. The analysis response exposes persisted
strategy, its type-appropriate benchmark, and excess series together with execution-audit
availability. Legacy results never infer zero costs or zero constraints when the
audit is absent. A missing legacy benchmark is reported explicitly; rebuilding it
is an opt-in robustness operation because it may require a full historical query.

Research orchestration in the framework core is deterministic and uses the
existing services. Stock-selection research stops after paper risk preview;
market-timing research stops after the latest aggregate exposure because it has
no individual stock orders. The embedded LLM planner remains `not_configured`,
and paper execution requires a separate explicit
confirmation request. The optional Conexus Harness is the external planner: it
can discover and invoke the canonical typed data registry, combine market,
point-in-time fundamental, factor, strategy, and backtest data, and return
bounded reports, tables, charts, and workspace commands. Missing Conexus state
never changes the deterministic workflow or data profile.

The Harness treats saved strategy content as untrusted data. It distinguishes
stock selection from market timing and configured implementations from Python
implementations before analysis. Full Python source is fetched only for review,
an explicitly requested local-strategy write, or an explicitly requested
execution. Backtest, research, and signal tools preflight the saved strategy and
require a separate Python-execution assertion. The Harness may validate and
manage local strategies through the same bounded API as the Strategy Workbench;
every save, clone, or delete requires a dedicated current-user confirmation.
It has no arbitrary shell, application-source editing, real-order, or broker tool.

Backtests generate signals from period-end information and execute them on the
next observed session at the configured open or close. Cash, one-way costs,
slippage, square-root participation impact, positive-volume checks and amount
participation limits are explicit. Missing amount blocks a trade instead of
assuming infinite liquidity. Saved runs include per-period execution audits plus
exact strategy YAML and Python-source snapshots, data-file and Git fingerprints.
This remains a daily-bar,
weekly/monthly-rebalance, long-only research engine; it is not a live or
intraday execution simulator.
