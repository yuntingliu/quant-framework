# AlphaLab Barebone Development Guide

Start with `docs/01_ARCHITECTURE.md` when deciding where code belongs. Keep the
public facade small and use it from tests and scripts. Local data, generated
artifacts, caches, scratch folders, and deployment credentials stay out of git.

## Local Gates

```powershell
python -m pytest tests/contracts tests/dataio tests/pipeline tests/strategy tests/dashboard -q
python scripts/check_facade_imports.py
npm --prefix dashboard/frontend run build
```

Start the workstation with:

```powershell
python -m uvicorn dashboard.backend.main:app --reload --port 8000
npm --prefix dashboard/frontend run dev:web
```

## Data Changes

Provider-specific acquisition belongs in the adapter. Generic schema, atomic
partition writes, catalog status, and point-in-time filtering belong in
`alphalab/dataio`. Empty responses must never replace existing runtime data.
Demo and runtime profiles remain explicit.

```powershell
alphalab data plan rq
alphalab data sync rq --datasets instruments,bars,fundamentals,factors
alphalab data validate
```

## Adding a Pipeline Component

Every current project pins exactly these stages and entrypoints. The stock pool
is project configuration consumed by the core before selection:

| Stage | Entrypoint | Required result |
| --- | --- | --- |
| `selection` | `select_assets(context)` | `{"selected": [...], "scores": {...}}` |
| `portfolio` | `construct_portfolio(context)` | `{"weights": {...}}` |
| `execution` | `configure_execution(context)` | `{"execution": {...}}` |

Do not add optional stages, alternate entrypoints, a universe/timing stage, an
independent risk stage, or a second backtest engine. Portfolio owns static
single-name/gross constraints and emits final target weights. Core validation
checks those weights again. Execution emits fixed-calendar and fill assumptions
only; order creation remains inside guarded backtest or confirmed paper flow.

Built-ins belong in `alphalab/pipeline/builtins.py` and must be deterministic,
JSON-compatible, and free of hidden external state. Seeded built-ins are
immutable. Users clone then save immutable versions. Projects pin all three
versions; changing a ref or settings creates a new project revision and composed
source snapshot.

`rebalance_freq` has one truth source: execution parameters. Supported values
are `daily`, `weekly`, and `monthly`. The engine rejects an execution component
whose emitted frequency disagrees with the pinned schedule.

## Python Runtime Rules

Validate each component with its exact entrypoint, then validate the composed
module with `run_strategy`. The child process supplies `-I`, timeout, bounded
logs, and crash containment. It is not a security sandbox. The core revalidates
every reached boundary and retains point-in-time, eligibility, concentration,
gross, liquidity, cash, cost, and next-session gates.

Stage preview executes the composed module through `run_stage`:

- selection executes over eligible candidates from the project stock pool;
- portfolio executes selection then portfolio;
- execution preview and backtest execute all three stages.

Preview must not call a mock implementation. Backtest must use the project
source hash returned by inspection and persist it with all component versions.

Candidate history is a performance-sensitive boundary. Convert frames to
records with vectorized operations; never construct one pandas Series per field
per row. Daily schedules require a focused performance check.

## Portfolio, Execution, and Research Risk

Keep these concepts separate:

- portfolio constraints: selected membership, finite/non-negative weights,
  `max_weight`, `max_gross_exposure`, cash;
- execution controls: next-session price, positive volume, amount participation,
  cash, commission, slippage, and market impact;
- research risk: volatility, drawdown, turnover, robustness, and factor
  exposure derived from a saved BacktestRun.

Maximum drawdown is not an execution setting. A drawdown-triggered exposure
overlay would be timing and requires a future explicit contract change.

## Attribution Rules

`alphalab/analytics/attribution.py` consumes saved strategy returns and canonical
factor returns. It must never rerun current project source for a historical run.
Daily/weekly returns are compounded to month periods before alignment.

CAPM and multi-factor regression use portfolio excess return on `MKT-rf`, SMB,
HML, MOM, and RMW. Persist the factor input snapshot, alpha/beta, HAC uncertainty,
R-squared, correlations, threshold checks, and warnings with the BacktestRun.
Missing historical attribution returns an explicit unavailable result; it does
not silently read newer factor data.

## Persistence Rules

Python source is the only strategy logic format. Store source, hashes, refs,
parameters, settings, attribution, and thresholds in SQLite/JSON. Do not add
YAML authoring, sidecars, or runtime fallbacks.

`legacy_migration.py` is a narrow one-time persisted-user-data import.
`contract_migration.py` may preserve old project data by creating one current
component revision, but removed stage names must never re-enter authoring/API
enums. Historical BacktestRuns may retain old manifest values as read-only data.

New backtest writes populate `strategy_source`, `component_manifest_json`,
`settings_json`, `execution_json`, `attribution_json`, and
`pipeline_project_id`.

## Frontend Rules

The seven modes are:

```text
data, project, selection, portfolio, execution, backtest, report
```

Research Project owns project selection/lifecycle, data profile, cutoff, factor
inputs, and research thresholds. The toolbar remains navigation/layout chrome.
Each stage mode opens a distinct Dockview preset and shares one preview query
keyed by project revision, target stage, data profile, and cutoff.

Stage result panels visualize real prefix output:

- selection: scores/ranks, selected names, and linked history;
- portfolio: constrained final weights, cash, gross, and concentration;
- execution: fixed schedule, fill assumptions, and final targets.

Backtest has one run action. Its tabs derive from the saved run: performance,
selection evidence, alpha/beta and factor correlations, robustness, execution,
and holdings. Do not add stage history or independent strategy execution.

Immutable versions, ids, and hashes remain internal authoring details. Historical
provenance belongs in backtest inspection.

## Agent Rules

Conexus uses current names only:

- `alphalab_get_pipeline_project`
- `alphalab_manage_pipeline`
- `alphalab_preview_pipeline`
- `alphalab_run_backtest`
- `alphalab_analyze_backtest`
- `alphalab_save_report`

Update Agent prompt, harness mode schema, frontend capabilities, docs, and
contract tests together. Project/component mutation and Python execution require
explicit current-user confirmation. Attribution uses
`alphalab_analyze_backtest` with `operation=attribution`.

## Review Checklist

- Does the change fit one of three stages, project stock-pool settings, the core gate layer, or saved-run analytics?
- Are all three component versions pinned and inspectable?
- Does preview/backtest execute the displayed composed source?
- Are Python and JSON the only new persistence formats?
- Are PIT data, fixed schedule, and execution gates enforced?
- Is attribution frozen and independent of later project/data changes?
- Do API, frontend, Agent, docs, and tests use the same names?
- Did focused Python tests and the frontend build pass?

Do not push unless the user explicitly asks.
