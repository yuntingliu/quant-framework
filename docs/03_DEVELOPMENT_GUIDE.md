# AlphaLab Barebone Development Guide

## Local Workflow

Run commands from the repository root:

```powershell
python -m pytest tests/contracts -q
python scripts/check_facade_imports.py
```

Start the workstation:

```powershell
python -m uvicorn dashboard.backend.main:app --reload --port 8000
npm --prefix dashboard/frontend run dev:web
```

## Runtime Data Workflow

The repository ships a checked-in example bundle. Its immutable files and
provenance are declared in `data/manifest.json`. RQ downloads, runtime
partitions, task records, quality reports, and caches remain ignored below
`data/runtime/`.

Use the same service through CLI or FastAPI. Always preview first:

```powershell
alphalab data plan rq
alphalab data sync rq --datasets instruments,bars,fundamentals,factors
alphalab data validate
```

Sync code may write only through `RuntimeStore`; empty responses must never
replace existing partitions. Provider-specific acquisition belongs in
`rq_sync.py`, generic schemas and storage remain provider-neutral, and
`available_date` must be enforced at historical query boundaries.

Rebuild the maintained sample with:

```powershell
python scripts\build_example_data.py `
  --source-root C:\Users\LYT\Documents\GitHub\quant-framework-factors
```

The builder reads the full workspace without modifying it, fixes the universe
before the sample period, adjusts OHLC for corporate actions, uses first-release
financial statements, seeds six backtests and signals, and writes only the
canonical barebone files.

Do not hard-code a vendor in the framework core. Create an adapter that
implements the relevant provider protocol, then register it:

```python
from alphalab import DataEngine

engine = DataEngine()
engine.register_market("my_source", MyMarketProvider(), default=True)
```

The maintained RQ information adapter is optional:

```python
from alphalab import create_rq_engine_from_env

engine = create_rq_engine_from_env()
```

Its credentials stay in the ignored local `.env`; tests must inject a fake RQ
module and must never require a live vendor connection.

For repeatable research, sync first and use the partitioned engine:

```python
from alphalab import create_runtime_engine

engine = create_runtime_engine()
```

The typed registry in `alphalab.tools` is the canonical agent-facing data
surface. The `/api/agent/data-tools` bridge describes and invokes that same
registry; do not duplicate tool behavior in a separate agent adapter. Tools
return bounded rows, counts, statuses, and references rather than large
serialized DataFrames. Mutating tools require an explicit `confirm=true` at the
bridge boundary. The published Conexus RQ-sync adapter supplies that trusted
caller assertion internally under its autonomous synchronization policy; it is
not a user-facing confirmation. The embedded LLM planner remains deliberately
unconfigured; the optional Conexus Harness supplies external planning.

Dashboard vendor pages should stay present as GUI slots, but they must remain
mapped to disabled placeholders until a separate adapter/plugin package owns the
real connection.

## Adding Strategies

Stock-selection templates live in `alphalab/strategies/`; market-timing templates
live in `alphalab/timing_strategies/`; allocation-rotation templates live in
`alphalab/rotation_strategies/`. Built-in reusable behavior should remain
registered framework code plus data-only YAML. A local strategy may instead own
a Python hook. Every newly serialized strategy declares exactly one domain
discriminator:

- `strategy_type: stock_selection`
- `strategy_type: market_timing`
- `strategy_type: allocation_rotation`

Existing selection YAML without `strategy_type` is the sole compatibility
migration and is normalized to `stock_selection`. Do not add heuristic type
detection or additional aliases.

Every config also has one explicit implementation:

- `implementation.kind: configured` uses registered factors, timing signals, or
  lagged style-sleeve ranking.
- `implementation.kind: python` calls the configured public entrypoint, normally
  `generate`, in a timeout-bounded child process.

Omitted `implementation` is the narrow persisted-data migration to `configured`.
Do not infer Python mode from a file or source field. The Python process is for
trusted local code: `-I`, process separation, bounded captured logs, and a 0.1–30
second timeout contain common failures, but do not form an OS security sandbox.

Package templates are immutable through the API. Dashboard edits are stored as
local YAML below `data/runtime/app/strategies` or
`data/runtime/app/timing_strategies`, or
`data/runtime/app/rotation_strategies`, according to type. Python implementations
store an adjacent same-stem `.py` sidecar. All three directories are ignored. Strategy
ids are unique across all repositories and must match the YAML `name`. Saving,
cloning, and deleting a Python strategy must handle YAML and source together.
Validate every local definition before a backtest.

`StrategyConfig.to_dict()`, `TimingStrategyConfig.to_dict()`, and
`RotationStrategyConfig.to_dict()` are the canonical
structured representations used by the Strategy Workbench; `to_yaml()` serializes
those same payloads. Keep visual
form changes and YAML synchronized through `POST /api/strategies/validate`
instead of reproducing YAML serialization in the browser. The endpoint accepts
either `{"config": ...}` or `{"yaml": "..."}` (never both), plus the optional
`python_source`, and returns both normalized representations plus structured
checks and the source hash. `PUT /api/strategies/{strategy_id}` accepts the same
source beside YAML. Add new strategy fields to
the dataclass, both representations, the frontend API type, and the workbench
editor together. The workbench creates a new strategy as an in-memory blank
stock-selection draft by validating `{"config": {"strategy_type":
"stock_selection", "name": strategy_id}}`, a timing draft with
`strategy_type: market_timing`, or a rotation draft with
`strategy_type: allocation_rotation`; the first successful
save uses the existing `PUT /api/strategies/{strategy_id}` path and must still
pass the repository's executable-strategy gates.

Python hooks have exact, type-specific contracts; do not add alternate names or
compatibility fallbacks:

```python
# stock_selection
def generate(context):
    # context: strategy id/date, eligible candidates with PIT OHLCV histories
    # and optional factor scores, current weights, limits, metadata
    return {"weights": {"600519.SH": 0.10}}

# market_timing
def generate(context):
    # context: strategy id/date, PIT monthly MKT returns, limits, metadata
    return {"market_exposure": 0.50}

# allocation_rotation
def generate(context):
    # context: strategy id/date, PIT monthly histories for configured style
    # sleeves, allocation limits, metadata
    return {"weights": {"MKT": 0.50, "MOM": 0.50}}
```

Selection output may contain only eligible symbols, non-negative finite weights,
at most `selection.n_stocks`, no weight above `portfolio.max_weight`, and total
weight at most one. Timing exposure must be finite and within the configured
minimum/maximum. Empty Python selection output means cash; it must not silently
reuse the prior portfolio. Rotation output may contain only configured sleeves,
non-negative finite weights, at most `selection.top_k` positions, no weight above
`portfolio.max_weight`, and total weight at most one. Preserve the one-period
execution lag in all three paths.

`POST /api/strategies/selection-preview` is the canonical non-persisting stock
selection check for both saved templates and unsaved workbench edits. It accepts
the same exclusive `config` or `yaml` representation plus a data profile and
optional as-of date. Keep its candidate ranks, factor contributions, cutoff,
target weights, and exclusions sourced from `SignalEngine`; do not add a second
frontend-only screener or reuse index-timing results as stock-selection output.

`POST /api/strategies/timing-research` is the canonical non-persisting timing
check. It accepts a `market_timing` config or YAML plus profile and date range.
The current implementation times the monthly `MKT` return series with registered
`trend`, `momentum`, and `volatility_control` signals. The combined 0–1 score maps
to the configured exposure range; exposure is shifted one period before it earns
returns, and turnover costs are applied when exposure changes. Keep this
portfolio-level output separate from stock targets and paper-order generation.

`POST /api/strategies/rotation-research` is the canonical non-persisting
allocation-rotation check. It returns equity, benchmark, applied sleeve weights,
cash and point-in-time scores. The built-in MKT/SMB/HML/MOM/RMW sleeves are
factor-mimicking research series, not directly tradable instruments. Configured
and Python target weights are shifted one month before earning returns, and they
never produce paper stock orders.

Every persisted stock-selection backtest can produce a same-universe equal-weight
benchmark; every timing backtest persists MKT as its benchmark; every rotation
backtest persists its configured style benchmark. All three produce a
robustness report. Selection signals use only information available at period end
and execute on the next observed session; timing exposure and rotation weights
are lagged by one month.
The report checks data/weight integrity,
calendar and rolling outcomes, turnover, concentration, 10/20/50 bps cost
assumptions, a final 30% validation segment, bootstrap mean-excess intervals,
Newey-West mean tests, moving-block bootstrap intervals, and a Bonferroni
adjustment using `metadata.research_trials`. Its labels are
research triage labels, not trading authorization.

Keep Backtest Workbench run controls distinct from saved-result inspection: the
selected strategy and dates describe the next run, while charts and metrics must
be labeled from the selected persisted record. New runs persist benchmark and
execution audit data. `/api/backtests/{id}/analysis` returns strategy, benchmark,
excess, drawdown, snapshot, and audit-availability fields without recomputing
missing history. Legacy benchmark reconstruction belongs to the explicit
robustness path because it can be slow; absent execution audits must render as
unknown, never as zero cost or zero constrained periods.

Reported Sharpe uses the annualized arithmetic mean divided by sample standard
deviation. Annual return remains the compounded CAGR; the two are intentionally
not substituted for one another.

Professional defaults and a safe custom factor can be declared entirely in
YAML:

```yaml
universe:
  pool: all
  min_price: 3
  min_history_days: 120
  min_average_amount: 10000000
factors:
  - name: quality_momentum
    source: expression
    expression: zscore(roe) + zscore(momentum_60d) - 0.5 * zscore(volatility_20d)
    weight: 1.0
    winsorize: 0.01
    neutralize: [market_cap]
portfolio:
  max_weight: 0.10
  rebalance_freq: monthly
execution:
  execution_price: next_open
  cost_bps: 10
  slippage_bps: 5
  impact_bps: 10
  portfolio_value: 1000000
  max_participation_rate: 0.10
metadata:
  research_trials: 1
```

Expressions accept only registered factor names, numeric operators and the
whitelisted `abs`, `clip`, `log`, `rank`, `sqrt`, and `zscore` functions. They
never execute Python. Use the Factor Workbench or
`POST /api/factor-research/evaluate` to inspect PIT coverage, Rank IC/ICIR,
quantile returns, long-short returns, decay, top-bucket turnover and deterministic
moving-block bootstrap intervals before adding a factor to a strategy.
The workbench uses the shared Data Workbench profile over the full eligible
universe; selecting one symbol for a K-line does not narrow a factor test.

Missing daily amount blocks the affected trade because participation cannot be
verified. Portfolio caps may deliberately leave cash. Each persisted backtest
stores the execution audit, the exact Python source snapshot when applicable,
and hashes of its YAML, Python source, exact input files and current Git
commit/dirty state. Agent reports are saved through `/api/reports` with the
same data/code provenance and also cached locally for offline startup.

The deterministic research runner persists each step and supports cancel,
retry, and restart interruption states. It may create a signal and paper
rebalance preview, but only `/api/paper/rebalance/execute` with `confirm=true`
can change the local paper account.

## Quality Gate

Use the smallest useful gate first:

```powershell
python -m pytest tests/contracts tests/dataio tests/strategy tests/dashboard -q
python scripts/check_facade_imports.py
npm --prefix dashboard/frontend run build
```

When a backtest produces unusually strong results, inspect alignment and
lookahead risk before expanding the feature.
