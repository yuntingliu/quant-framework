# AlphaLab Barebone Architecture

AlphaLab is a provider-first research core plus a React/Electron workstation.
The current strategy interface is Python-native and has one execution path.

## Research and Workbench Model

The top-level workbenches are parallel navigation destinations:

```text
Research Project | Data | Factor Research | Signal Model | Backtest | Report
```

The Factor Research Workbench owns the factor library, project factor basket,
safe-expression authoring, latest validated cross-section, and point-in-time
single-factor evidence before those factors are consumed by the signal model. Its
default surface is one data-first workbench: public market and fundamental API
data remain visible in the shared market terminal while the researcher builds
or edits a factor. K lines, native technical indicators, the security list, and
crosshair quotes use the same component as the Data Workbench; Factor Research
adds the expression-field inspector on the right. Its field catalog is generated
from the selected profile's physical Parquet schemas; it is not a hand-maintained
list of vendor columns.
Raw market/fundamental fields and executable base factors
can be inserted at the expression cursor. Single-factor evidence is a final
validation view rather than the primary construction surface. It
can attach only the currently validated factor definition to the selected
project, but it is not embedded in the project-management layout.

The Research Project Workbench owns project lifecycle, data profile, and
navigation to the three pinned strategy components. The Data Workbench owns the
user-facing research scope and automatic eligibility filters. Those values remain
structured project settings so factor research, signals, and backtests consume the
same point-in-time candidate definition. Factor definitions are persisted with project
revisions but are edited only in the Factor Research Workbench. Stage
workbenches show the current project read-only and never duplicate project
settings. A revision or profile change makes cached stage output stale
until the stage is run again.
It is the first navigation destination. Data, Factor Research, Signal Model,
Backtest, and Report remain disabled until an editable research project has been
created or selected; the built-in default project is a creation template rather
than an active research context.

The strategy and research dependency is:

```text
DataSnapshot
  -> data-workbench research scope + factor definitions and single-factor research
  -> core eligibility gates
  -> signal model: normalize + weight + rank + holding buffer
  -> select_assets(context)
  -> construct_portfolio(context)
  -> configure_execution(context)
  -> BacktestRun
  -> Performance / Risk / Alpha-Beta Attribution
  -> ResearchReport
```

The research scope is structured project input authored in the Data Workbench,
not a separate strategy stage or a weighted factor. It defines the initial symbols
and hard data/tradability eligibility filters; factor scores own preferences and
ranking inside the eligible cross-section. The signal model owns the
daily/weekly/monthly decision calendar,
the current cross-section as-of date,
cross-sectional normalization, project-specific effective factor weights,
minimum coverage, target count, and entry/exit rank buffer. It is recomputed at
every decision date from eligible data and factors available at that date. The
internal stage ID remains `selection` for persisted API compatibility.
The Signal Model workbench also owns the user-facing allocation method, target
gross exposure, and single-name limit. The internal `portfolio` Python stage
remains version-pinned and emits final target weights so runtime boundaries and
historical provenance stay intact, but it is not a separate navigation
destination. Backtest owns the user-facing next-session fill, liquidity,
capital, and cost assumptions. The internal `execution` Python stage remains
version-pinned for runtime provenance; order creation remains in the guarded
engine.

There is no timing stage and no independent risk component. A drawdown-driven
exposure change would be a stateful timing rule and is outside the current
contract. Maximum drawdown, volatility, turnover, and similar limits are saved
research metrics and thresholds. Core portfolio and execution gates remain
non-bypassable.

## Canonical Strategy Objects

`PipelineRepository` stores two public object families in SQLite:

- a component has an immutable identity, one of three stages, and immutable versions;
- a project pins exactly one `component_id@version` for every stage and has
  revisioned structured settings.

Built-in components and the default project are immutable. Users clone them to
customize. A component version stores Python source, entrypoint, default JSON
parameters, notes, and SHA-256. A project revision stores all three refs,
settings, composed source, and SHA-256.

`compose_strategy()` concatenates the three reviewed component sources and adds
`run_stage(context)` plus `run_strategy(context)`. A preview calls `run_stage`
and stops at the requested stage after running its upstream dependencies. A
backtest calls `run_strategy` at every fixed-calendar decision date. Both use
the exact composed module in the isolated child process.

Historical BacktestRuns freeze their source and manifest. Six-stage and
four-stage runs remain readable as historical snapshots, but their removed
universe/timing/risk components cannot be selected or executed by current
authoring APIs.

Python is trusted local code with timeout and crash containment, not an OS
security sandbox. The framework owns non-bypassable controls:

- point-in-time instruments and fundamental availability;
- project research-scope, selection, and final-weight membership;
- finite scores and weights, concentration, gross exposure, and cash;
- next-session alignment, valid price, and positive volume;
- amount participation, costs, slippage, and market impact.

## Backtest and Attribution

A BacktestRun is the durability boundary for historical strategy behavior. It
persists returns, benchmark, weights, execution audit, stage outputs, factor
score correlations, source/manifest provenance, and a frozen attribution
snapshot.

Attribution consumes saved returns rather than rerunning the strategy. Daily or
weekly strategy returns are compounded to monthly before alignment with the
canonical monthly `MKT/SMB/HML/MOM/RMW/rf` data. The current regression is:

```text
Rp - rf = alpha + beta_MKT * (MKT - rf)
                  + beta_SMB * SMB + beta_HML * HML
                  + beta_MOM * MOM + beta_RMW * RMW
```

Saved outputs include CAPM and multi-factor alpha/beta, HAC standard errors,
confidence intervals, R-squared, factor-return correlation, cross-sectional
selection-factor correlation, and configured research-threshold checks.

## Module Ownership

| Path | Responsibility |
| --- | --- |
| `alphalab/dataio/` | Provider protocols, `DataEngine`, runtime partitions, sync, and quality. |
| `alphalab/factors/` | Cross-sectional technical/fundamental inputs, safe expressions, and reusable custom-factor definitions. |
| `alphalab/pipeline/` | Three-stage models, built-ins, composition, repository, runtime adapter, and narrow migrations. |
| `alphalab/strategy/python_runtime.py` | Source validation and timeout-bounded child execution. |
| `alphalab/engine.py` | Point-in-time features, fixed schedules, core gates, execution simulation, and parity. |
| `alphalab/analytics/` | Signal evidence, performance, robustness, regression, and factor correlations. |
| `alphalab/store.py` | Backtests, frozen source/manifest/attribution, paper state, reports, and artifacts. |
| `dashboard/backend/routers/pipeline.py` | Current component/project API. |
| `dashboard/` | FastAPI plus Dockview workstations. |
| `integrations/conexus/` | Optional published Agent using current typed tools only. |

The public strategy facade stays small:

```python
from alphalab import (
    DataEngine,
    PipelineProject,
    PipelineRepository,
    preview_pipeline_project,
    run_pipeline_project_backtest,
)
```

Internal configuration dataclasses adapt structured project settings to the
guarded engine; they are not an authoring or persistence format.

## Persistence and Migration

Current authoring and runtime never read or write YAML. Component source is
Python; small metadata is JSON in SQLite. `legacy_migration.py` is the sole YAML
exception for one-time import of ignored pre-pipeline user definitions.

`four-stage-pipeline-v1` is a narrow persisted-data migration. It removes active
timing refs, combines old portfolio/risk source into a current portfolio
component with timing fixed at full exposure, records a review note, and
preserves old component rows for historical snapshots. It does not expose a
runtime compatibility branch.

`three-stage-pipeline-v1` removes the authorable universe reference. For custom
projects it folds frozen universe and selection source into one generated
selection component, preserving prior stock-pool behavior without exposing the
removed stage. The default project is reseeded as `three-stage-default`; its
base pool remains structured project settings.

Backtests persist:

- project id and revision provenance;
- complete composed Python source and SHA-256;
- component ids, versions, parameters, and hashes;
- structured project settings and research thresholds;
- returns, benchmark, holdings, and period execution audit;
- frozen factor-attribution inputs/results;
- data-file, environment, and Git fingerprints.

## Data Contract

Adapters implement protocols under `alphalab.dataio.providers.protocol`. Demo
and runtime profiles are explicit and never silently combined. Historical
fundamentals require `available_date <= decision_date`; dated instruments are
selected at or before the signal date. Missing amount blocks a trade rather
than implying unlimited liquidity.

An RQ sync request without explicit symbols resolves all A-shares from a live
dated instrument snapshot at job start. It never substitutes the bundled demo
manifest. Read-only plans may use the latest local RQ instrument snapshot to
show an exact symbol and batch count; otherwise those values remain pending.

Canonical local data remains:

```text
data/market/bars.parquet
data/instruments/instruments.parquet
data/fundamentals/fundamentals.parquet
data/factors/factor_returns.parquet
data/app/alphalab.db
data/manifest.json
```

## Current HTTP Surface

Strategy workstations use the component/project endpoints under `/api/pipeline`,
background jobs under `/api/backtests/jobs`, and saved-run endpoints including:

- `GET /api/backtests/{id}/analysis`
- `GET /api/backtests/{id}/signals`
- `GET /api/backtests/{id}/attribution`
- `GET /api/backtests/{id}/robustness`
- `POST /api/backtests/compare`

There are no universe/timing/risk stage aliases and no independent stage-history API.
