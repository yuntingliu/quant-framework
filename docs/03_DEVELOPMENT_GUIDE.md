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
| `selection` (Signal Model) | `select_assets(context)` | `{"selected": [...], "scores": {...}}` |
| `portfolio` | `construct_portfolio(context)` | `{"weights": {...}}` |
| `execution` | `configure_execution(context)` | `{"execution": {...}}` |

Do not add optional stages, alternate entrypoints, a universe/timing stage, an
independent risk stage, or a second backtest engine. The signal model owns the
decision calendar, cross-sectional normalization, effective factor weights,
coverage, target count, rank buffer, and user-facing allocation controls. The
internal portfolio component applies the chosen allocation method and static
single-name/gross constraints, then emits final target weights. Core validation
checks those weights again. Execution emits fill, liquidity, and cost assumptions
only; order creation remains inside guarded backtest or confirmed paper flow.

Built-ins belong in `alphalab/pipeline/builtins.py` and must be deterministic,
JSON-compatible, and free of hidden external state. Seeded built-ins are
immutable. Users clone then save immutable versions. Projects pin all three
versions; changing a ref or settings creates a new project revision and composed
source snapshot.

`signal_frequency` has one truth source: selection/signal-model parameters.
Supported values are `daily`, `weekly`, and `monthly`. Historical
`execution.rebalance_freq` values are migrated on read/persistence and ignored
if an old execution component still emits them.

## Python Runtime Rules

Validate each component with its exact entrypoint, then validate the composed
module with `run_strategy`. The child process supplies `-I`, timeout, bounded
logs, and crash containment. It is not a security sandbox. The core revalidates
every reached boundary and retains point-in-time, eligibility, concentration,
gross, liquidity, cash, cost, and next-session gates.

Stage preview executes the composed module through `run_stage`:

- selection executes the signal model over eligible candidates from the project stock pool;
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

The six user-facing modes are:

```text
project, data, factor, selection, backtest, report
```

Factor is an independent Dockview workspace. Its default preset opens one
Factor Research workbench with a persistent public-data surface above the
builder. It reads the same `/api/data/market/bars` and `/api/data/fundamentals`
contracts used elsewhere, visualizes K lines or fundamental field history, and
shows every field discovered from the selected profile's Parquet metadata.
Changing a profile must change the factor-library query key; never duplicate a
vendor field list in the router or frontend. Raw data fields and base
factors can be inserted at the expression cursor. Historical IC, grouping,
decay, and snapshot evidence belongs in the final-validation view, not in the
construction toolbar. The
catalog and the project's adopted-factor basket are separate tabs, not one
mixed list. Factor evaluation uses `/api/factor-research` and does not run or
mutate a strategy. Saving is disabled for an unevaluated or stale definition;
an accepted factor updates the selected project's structured
`settings.factors` and therefore creates a project revision.

Research Project owns project selection/lifecycle, data profile, cutoff, stock
pool, and research thresholds. Its UI must not expose factor authoring or raw
factor settings; those belong to the Factor workspace. The toolbar remains
navigation/layout chrome.
The sidebar is one ungrouped list with Research Project first. All later modes
are disabled until the selected project has been validated and is editable;
built-in projects may be inspected or cloned but do not unlock the workflow.

Built-in technical/fundamental factors and safe vector expressions are the
current executable factor contracts. Expressions may reference PIT public-data
fields (`open/high/low/close/volume/amount` and canonical fundamental fields)
as cross-sectional inputs. External feature packs such as Qlib
Alpha158/Alpha360 must remain explicitly marked adapter-required until their
data fields and point-in-time behavior are implemented. Do not expose an
arbitrary Python factor editor without a versioned source, dependency, timeout,
and output contract.

Signal Model and Backtest are user-facing workbenches. Portfolio remains an
internal version-pinned Python stage whose allocation controls and results are
embedded in Signal Model. Execution also remains an internal version-pinned
Python stage, while its fill, liquidity, capital, and cost controls are embedded
in Backtest run setup. Stage previews share one query keyed by project revision,
target stage, data profile, and cutoff; a portfolio-prefix preview is also cached
for its executed selection output.

Stage result panels visualize real prefix output:

- selection/signal model: universe/eligibility/scoring funnel, scores and ranks,
  score distribution, current factor-structure diagnostics, selected names,
  constrained target weights, cash/gross exposure, and linked history;
- backtest: read-only Signal Model frequency, editable fill/cost assumptions,
  saved execution audit, and a clear distinction between target weights and
  historical fills.

The default stage presets should foreground these research panels. Component
source and parameters remain available in a Dockview tab in the same workspace,
rather than occupying the largest panel by default. New panels must consume the
shared stage-preview cache; they must not trigger an independent strategy run.

Backtest has one run action. Each decision date uses the frequency and allocation
policy saved by Signal Model, then applies Backtest's next-session execution
assumptions. Its tabs derive from the saved run: performance, selection evidence,
alpha/beta and factor correlations, robustness, execution audit, and holdings.
Do not add stage history or an independent execution workspace.

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
