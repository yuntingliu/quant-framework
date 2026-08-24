# AlphaLab Barebone Architecture

AlphaLab is a provider-first research core plus a React/Electron workstation.
The current strategy interface is Python-native and has one execution path.

## Research and Workbench Model

The top-level workbenches are parallel navigation destinations:

```text
Data | Research Project | Universe | Selection | Timing | Portfolio | Risk | Execution | Backtest | Report
```

The Research Project Workbench owns the shared research context: project
selection and lifecycle, data profile, optional data cutoff date, project-level
settings, and navigation to the six pinned components. The toolbar is navigation
and layout chrome only. Stage workbenches show the current project read-only and
never create projects or edit project-level settings. A project revision,
profile, or cutoff change makes cached stage output stale until that stage is
run again.

The six strategy-stage destinations are top-level Dockview layouts rather than
single editor screens. Every layout contains one shared component/Python editor
and stage-specific result panels. Universe and selection link security lists to
price charts; timing links exposure changes to price markers and an exposure
trace; portfolio, risk, and execution show weights, constraint deltas, turnover,
and current execution targets. Panels observe one run cache keyed by project,
target stage, data profile, and data cutoff date, so running selection cannot
populate or execute timing, portfolio, risk, or execution workspaces.

Their data dependency is sequential:

```text
DataSnapshot
  -> build_universe(context)
  -> select_assets(context)
  -> compute_exposure(context)
  -> construct_portfolio(context)
  -> apply_risk(context)
  -> configure_execution(context)
  -> BacktestRun
  -> ResearchReport
```

There is no separate “factor strategy” object and no monolithic “strategy and
portfolio” workbench. Cross-sectional factors are possible inputs to the
selection stage. MKT/SMB/HML/MOM/RMW/rf remain data, benchmark, attribution,
and risk-exposure inputs.

Pure selection, pure timing, and selection plus timing use the same six stages:

- pure selection pins the `timing-always-on` identity component;
- pure timing pins `selection-pass-through` and an explicit base instrument;
- a combined strategy pins non-identity selection and timing components.

## Canonical Strategy Objects

`PipelineRepository` stores two public object families in SQLite:

- a component has an immutable identity, one stage, and immutable versions;
- a project pins exactly one `component_id@version` for every stage and has
  revisioned structured settings.

Built-in components and the default project are immutable. Users clone them to
customize. A component version stores its Python source, entrypoint, default
parameter JSON, notes, and SHA-256. A project revision stores all six refs,
settings, the composed source, and its SHA-256.

`compose_strategy()` concatenates the six reviewed component sources and adds
`run_stage(context)` plus `run_strategy(context)`. A current-date stage run calls
`run_stage` and stops after the requested stage; required upstream stages run to
provide its inputs, so timing and every later stage consume the selection output
from that same frozen run. A backtest calls `run_strategy` and executes all six stages.
Both execute the exact composed module in the isolated child process. A backtest
freezes the composed source and component manifest; inspecting a historical run
never substitutes a newer project revision.

There is no separate stage-history or “independent research” execution path.
Stage workbenches answer only what the selected component emits at the current
data cutoff. Historical repetition, turnover, costs, robustness, attribution,
and comparison belong to a persisted `BacktestRun` in the Backtest Workbench.

Python is trusted local code with timeout and crash containment, not an OS
security sandbox. The framework still owns non-bypassable controls:

- point-in-time instruments and fundamental availability;
- membership of universe, selection, and final weights;
- finite scores, 0–1 timing exposure, concentration, and gross exposure;
- next-session alignment, valid price and positive volume;
- amount participation, cash, costs, slippage, and impact.

## Module Ownership

| Path | Responsibility |
| --- | --- |
| `alphalab/dataio/` | Provider protocols, `DataEngine`, runtime partitions, sync, and quality. |
| `alphalab/factors/` | Cross-sectional technical/fundamental inputs and safe expressions. |
| `alphalab/pipeline/` | Six-stage models, built-ins, composition, SQLite repository, runtime adapter, and one-time legacy import. |
| `alphalab/strategy/python_runtime.py` | Source validation and timeout-bounded child process execution. |
| `alphalab/engine.py` | Point-in-time features, core gates, execution simulation, and backtest parity. |
| `alphalab/analytics/` | Signal evidence, benchmarks, robustness, and inference. |
| `alphalab/store.py` | Backtests, frozen source/manifest, paper state, reports, and research artifacts. |
| `dashboard/backend/routers/pipeline.py` | Current component/project API. |
| `dashboard/` | FastAPI plus Dockview workstations. |
| `integrations/conexus/` | Optional published Agent using current typed tools only. |

The public strategy facade is intentionally small:

```python
from alphalab import (
    DataEngine,
    PipelineProject,
    PipelineRepository,
    preview_pipeline_project,
    run_pipeline_project_backtest,
)
```

Internal configuration dataclasses may adapt structured project settings to the
guarded legacy backtest core; they are not an authoring or persistence format.

## Persistence and Migration

Current authoring and runtime never read or write YAML. Component source is
Python; small metadata is JSON in SQLite. `legacy_migration.py` is the sole
exception: on first open it can import ignored user-authored definitions from a
pre-pipeline release, records their path/hash, and never imports the same file
again. Package YAML templates have been removed. Historical backtest rows retain
their old columns only for in-place database compatibility.

Backtests persist:

- project id and revision provenance;
- complete composed Python source and SHA-256;
- six component ids, versions, parameters, and source hashes;
- structured project settings;
- returns, benchmark, holdings, and period execution audit;
- data-file, environment, and Git fingerprints.

The Backtest Workbench derives signal diagnostics from that saved run: Rank IC,
score coverage, top-minus-bottom returns, selection turnover, and timing
exposure. It does not launch a separate factor-validation run. Reports are
durable research artifacts linked to a BacktestRun when `backtestId` is present.
The Agent persists a completed report through the report API before publishing
its workspace nodes. Browser state is only a display cache and is never the
durability boundary for a `ResearchReport`.

## Data Contract

Adapters implement protocols under `alphalab.dataio.providers.protocol`.
Demo and runtime profiles are explicit and never silently combined. Historical
fundamentals require `available_date <= decision_date`; dated instrument
snapshots are selected at or before the signal date. Missing amount blocks a
trade rather than implying unlimited liquidity.

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

Strategy workstations use:

- `GET/POST /api/pipeline/components`
- `GET /api/pipeline/components/{id}`
- `POST /api/pipeline/components/{id}/clone`
- `POST /api/pipeline/components/{id}/versions`
- `DELETE /api/pipeline/components/{id}`
- `GET/POST /api/pipeline/projects`
- `GET/PUT/DELETE /api/pipeline/projects/{id}`
- `POST /api/pipeline/projects/{id}/clone`
- `POST /api/pipeline/projects/{id}/preview` with required `stage`
- `POST /api/backtests/jobs` with `project_id` returns `202` immediately
- `GET /api/backtests/jobs/{job_id}` reports the persistent background job and its final `BacktestRun`
- backtest detail, analysis, robustness, comparison, data, report, and paper APIs.

The removed `/api/strategies` API is not an alias. This keeps frontend, Agent,
documentation, and runtime on one current contract.
