# AlphaLab Barebone Development Guide

Start with `docs/01_ARCHITECTURE.md` when deciding where code belongs. Keep the
public facade small and use it from tests and scripts. Local data, generated
artifacts, caches, scratch folders, and deployment credentials stay out of git.

## Local Gates

```powershell
python -m pytest tests/contracts tests/dataio tests/strategy tests/dashboard -q
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
Demo and runtime profiles must remain explicit.

Use the same service through CLI or FastAPI and plan before syncing:

```powershell
alphalab data plan rq
alphalab data sync rq --datasets instruments,bars,fundamentals,factors
alphalab data validate
```

## Adding a Pipeline Component

Every current strategy uses exactly these names and entrypoints:

| Stage | Entrypoint | Required result |
| --- | --- | --- |
| `universe` | `build_universe(context)` | `{"symbols": [...]}` |
| `selection` | `select_assets(context)` | `{"selected": [...], "scores": {...}}` |
| `timing` | `compute_exposure(context)` | `{"exposure": number}` |
| `portfolio` | `construct_portfolio(context)` | `{"weights": {...}}` |
| `risk` | `apply_risk(context)` | `{"weights": {...}}` |
| `execution` | `create_orders(context)` | `{"execution": {...}}` |

Do not add alternate names, optional stages, configured/Python branches, or a
second timing engine. Identity presets express pure-selection and pure-timing
projects without changing the execution graph.

Built-ins belong in `alphalab/pipeline/builtins.py` and must be deterministic,
JSON-compatible, and free of hidden external state. Seeded built-ins are
immutable. A user modification is a clone followed by a new immutable version.
Never update an old component version in place.

Projects pin all six versions. Changing a ref or project settings creates a new
project revision and stores a newly composed source snapshot. Component default
parameters are merged with project `stage_parameters`; all small configuration
is JSON-compatible.

## Python Runtime Rules

Validate each component with its exact entrypoint, then validate the composed
module with `run_strategy`. The child process supplies `-I`, a timeout, bounded
logs, and crash containment. It is not a security sandbox. The core must validate
all stage outputs again and must retain point-in-time, eligibility, exposure,
liquidity, cash, cost, and next-period gates.

Preview must execute the complete composed module and then expose the requested
stage output. It must not call a separate mock implementation. Backtest must use
the same source hash returned by project inspection and persist it with the six
component versions.

The candidate-history context is a performance-sensitive boundary. Convert
frames to records with vectorized operations; never construct a pandas Series
for every field of every row.

## Persistence Rules

Python source is the only strategy logic format. Store source, hashes, refs,
parameters, and project settings in SQLite/JSON. Do not add YAML authoring,
serialization, API fields, sidecars, or runtime fallbacks.

`alphalab/pipeline/legacy_migration.py` is a narrow persisted-user-data import.
It may read a pre-pipeline local file once, must record path and hash, and must
emit current project objects. Do not broaden it into a dual runtime.

Historical SQLite columns may remain for in-place compatibility. New writes
must populate `strategy_source`, `component_manifest_json`, `settings_json`, and
`pipeline_project_id`.

## Frontend Rules

The nine modes are:

```text
data, universe, selection, timing, portfolio, risk, execution, backtest, report
```

Each stage mode opens one `StageWorkbench` specialization and shares the selected
project through `WorkspaceContext`. Each workbench must show:

- project and its currently applied component;
- one searchable component library;
- full Python source and JSON parameters;
- add, save, save-and-apply, and explicit apply actions respecting immutability;
- read-only library browsing that never changes the project;
- project creation and project settings outside the component editor tabs;
- actual input and output from a complete-pipeline preview;
- independent vertical/horizontal scrolling inside Dockview.

Immutable component revisions remain an internal persistence and audit detail.
The authoring UI must not expose version counters, selectors, hashes, or a
version-details tab; historical provenance belongs in backtest inspection.
Component/project IDs and origin flags are also internal. Lists display names
and descriptions, while create dialogs ask for a name and generate IDs without
user involvement.

Backtest selects a project, shows the complete composed source, and labels
historical results from their saved snapshot. Do not resurrect a separate
strategy overview, signal workbench, YAML editor, or monolithic strategy editor.

## Agent Rules

Conexus tools use current names only:

- `alphalab_get_pipeline_project`
- `alphalab_manage_pipeline`
- `alphalab_preview_pipeline`
- `alphalab_run_backtest`

Do not keep old tool aliases. Update the Agent prompt, harness mode schema,
frontend capabilities, docs, and contract tests together. Project/component
mutation and Python execution require explicit current-user confirmation.

## Review Checklist

- Does the change fit one current stage or the core gate layer?
- Are all six component versions pinned and inspectable?
- Does preview/backtest execute the displayed composed source?
- Are Python and JSON the only new persistence formats?
- Are historical data boundaries and core risk gates still enforced?
- Do API, frontend, Agent, docs, and tests use the same names?
- Did frontend build and focused Python tests pass?

Do not push unless the user explicitly asks.
