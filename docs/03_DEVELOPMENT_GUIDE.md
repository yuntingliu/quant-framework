# Development Guide

Read [01_ARCHITECTURE.md](01_ARCHITECTURE.md) before deciding where code belongs.
The public contract is [02_STRATEGY_SDK_V1_CONTRACT.md](02_STRATEGY_SDK_V1_CONTRACT.md).

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dashboard,dev]"
cd dashboard\frontend
npm install
```

Run the backend and frontend in separate terminals:

```powershell
python -m uvicorn dashboard.backend.main:app --reload --port 8000
cd dashboard\frontend
npm run dev
```

Before investigating a local startup failure, run the read-only environment
doctor. It reports Python/Node/RQData/editor-tool availability, runtime paths,
and the usual development ports without printing credential values:

```powershell
alphalab dev doctor
```

The strategy runtime invokes the same local Python environment in a spawned
child process. Do not describe it as sandboxed and do not add a Docker-only or
second Lab execution path.

## Placement rules

- Public user strategy types belong in `alphalab/sdk/v1/`.
- AST/CST source behavior belongs in `alphalab/strategy/source.py`.
- Built-in reusable factor source belongs in
  `alphalab/strategy/factor_templates.py`; templates must be complete SDK
  `@factor` functions, never callbacks into the legacy factor evaluator.
- Draft/package persistence belongs in `alphalab/strategy/repository.py`.
- User-code invocation and boundary coercion belong in
  `alphalab/strategy/sdk_runtime.py`.
- Validation authoring contracts, persistence, and post-run invocation belong
  in `alphalab/validation_sdk/` and `alphalab/validation/`. Validation code may
  consume frozen engine outputs but may not mutate fills or accounting.
- Session ordering, tradability, fills, costs, and accounting belong in
  `alphalab/strategy/engine.py`.
- Provider logic stays in `alphalab/dataio/`; strategy Python never receives a
  provider or database handle.
- Public data-recipe types and the lazy RQ proxy belong in
  `alphalab/data_sdk/v1/recipe.py`; source rendering, AST/CST projection, and
  execution belong in `alphalab/dataio/recipes.py`.
- API and workbench changes must preserve one deterministically assembled
  revision/hash across frontend, backend, Agent tools, Runs, and reports.
- User-facing SDK documentation belongs only in
  `docs/06_ALPHALAB_SDK_GUIDE.md`. The read-only `/api/sdk-docs` endpoint and
  every workbench documentation drawer must read that same file.

Do not add an expression evaluator, generated stage source, hidden fallback to
demo/default logic, or a parallel backtest path. Pre-SDK database tables are
read only: migration is implemented directly by `StrategyRepository`, while
historical report reconstruction stays in the analytics service.

## SDK source rules

Tests and examples import the small `alphalab` facade. Project authoring is a
`strategy.py` unit plus one `factors/<factor_id>.py` unit per factor. Imports,
constants, helpers, and non-factor registrations belong in `strategy.py`;
factor units contain exactly one registered function. Factor units are not
executed alone: the repository assembles them into the runtime module first.
The runtime supplies the stable `alphalab.sdk.v1` public prelude to every unit,
so a factor never depends on a decorator imported incidentally by
`strategy.py`. Non-SDK imports needed by a factor belong inside its function.

Every built-in source template must be instructional as well as executable.
Each registered function needs a useful docstring describing its return
contract, and comments must explain time alignment, missing-data behavior, or
another non-obvious choice. Keep comments semantic rather than narrating
ordinary Python syntax. Template tests enforce this coverage.
When the shipped default source changes, repositories advance only the
immutable built-in strategy and validation projects and retain the prior
package revision. Never rewrite editable clones. Data-recipe comment upgrades
likewise apply only when the executable AST is unchanged.

```python
from alphalab.sdk.v1 import (
    ExecutionPolicy, Monthly, PortfolioDecision, SignalResult, UniverseResult,
    execution, factor, portfolio, signal, universe,
)

SDK_VERSION = 1

@universe(id="etfs")
def etfs(context):
    return UniverseResult(symbols=context.universe)

@signal(id="monthly", schedule=Monthly.last_trading_day(at="close"))
def monthly(context, state, *, top_n: int = 1):
    scores = context.combine_factors(
        weights={"momentum": 1.0},
        normalization="rank",
        parameters={"momentum": {"window": 20}},
    ).dropna()
    selected = list(scores.nlargest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)

@portfolio(id="weights")
def weights(context, signal, state):
    return PortfolioDecision(
        target_weights={symbol: 1 / len(signal.selected) for symbol in signal.selected},
        state=state,
    )

@execution(id="next_open")
def next_open(context, decision):
    return ExecutionPolicy(activation="next_session_open")
```

`factors/momentum.py`:

```python
@factor(id="momentum")
def momentum(context, *, window: int = 20):
    close = context.history("close", window=window + 1)
    return close.iloc[-1] / close.iloc[0] - 1
```

Keyword-only literal defaults are form-editable. Anything else displays as
custom. Structured edits must use LibCST and target the registered entrypoint
ID; never replace an arbitrary number or text match.

The Strategy Workbench presents all four strategy stages in a scrollable,
collapsible visual panel on the left and the shared Python editor on the right.
The divider is resizable; the Python pane edits the complete `strategy.py`
authoring unit so selection, scheduling, portfolio, event risk, and execution
logic remain visible together. It must not display `@factor` definitions.
Visual stage sections do not contain separate Python edit buttons or derived
function editors.
Projected schedules, factor blends, and literal parameters share one draft and
one global apply action. While visual changes are pending, the backend applies
the ordered edits in memory and returns a read-only Python preview without
persisting it. Applying submits the same ordered `batch` edit, validates the
final assembled module, and persists the source units plus one immutable
revision in the same transaction; partial visual-form saves and dirty
intermediate revisions are not allowed.
Unsaved code locks the visual controls, and pending visual changes make the
`strategy.py` preview read-only.

Literal `context.combine_factors(weights=..., normalization=...)` calls are the
form-editable multi-factor boundary. Signed weights encode factor direction.
Keep conditional or non-literal formulas in Python and project them as custom;
do not add an expression evaluator.

Adding a built-in factor merges the template's data requirements and creates a
new `factors/<factor_id>.py` unit. Deterministic runtime assembly inserts its
function before the registered signal. Templates are copyable:
repeat installation allocates a deterministic unique public ID and Python
function name (`factor_id`, `factor_id_2`, `factor_id_3`, ...). The project owns
each Python copy independently; later catalog edits do not rewrite saved
project source.

## Validation and errors

Saving `strategy.py` or a factor source unit assembles the complete runtime
module, automatically records an internal package, and performs parse, SDK
version, registry uniqueness, signature,
literal metadata, factor dependency/cycle, compile/import, runtime requirement,
and synthetic output probes. Add new validation at the narrowest boundary and
return a phase: `parse`, `register`, `input`, `execute`, `output`, or `state`.

Output validation must remain core-owned: finite series/scores/weights,
in-universe symbols, concentration and gross limits, JSON state size, execution
policy, valid prices, liquidity, and cash.

## API conventions

Current authoring routes are under `/api/strategy`. Atomic strategy/factor
source writes require both `confirm_write` and `confirm_python_execution`
because saving runs trusted-local cross-file probes. Project creation and
explicit revision saves require `confirm_save` plus
`confirm_python_execution`; deletion requires `confirm_delete`.

`PUT /api/strategy/projects/{id}/draft` writes `strategy.py`, not the assembled
module. `POST /api/strategy/projects/{id}/factors` creates one factor unit;
registered-function edits update the matching factor unit through the assembled
CST and then split it back atomically. Responses expose `strategy_source` for
authoring and retain `draft_source` only as the derived runtime bundle/audit
artifact.

Backtests use `/api/backtests/jobs`. The frontend supplies the current internal
strategy package ID; users do not choose or freeze revisions. The backend also
pins the current validation package before queueing, and historical result
endpoints never read later source. Submission performs only bounded input and
revision checks, persists the job, and returns immediately. Runtime data
preparation happens inside the background backtest itself; there is no separate
full-range preflight action in the workbench or submission path. Execution-state
gaps do not alter the signal universe. For actual order rows the event engine
runs the frozen project's optional `@execution_data_fill` Python, validates its
returned frame, and rejects only orders whose required state remains missing.
The hook derives ordinary limits from prior-session unadjusted `raw_close` and
may use a paired zero for a known IPO no-limit session; provider non-positive
values are normalized to missing and a single zero is invalid.
Backtest workers acquire shared runtime-data
locks and a per-job exclusive process lock. Dataset writers remain exclusive,
but multiple API services and multiple backtests must not duplicate a task or
block one another merely because they are reading the same partitions.

The Agent-facing `run` operation returns only the job ID. Polling returns task
status plus a stable error code and bounded summary. Default result reads omit
daily events; `/api/backtests/{id}/events` provides explicit bounded pages for
events or executions. Public errors contain a safe summary and log reference,
never server paths, environment hashes, or tracebacks. Terminal task state is
self-consistent: success exposes no error fields, and failure exposes no stale
result identifier or result summary. Temporary write contention uses the
stable `DATASET_BUSY` code rather than a market-coverage error.

Runtime data-sync job endpoints follow the same boundary: task responses expose
status, progress, a bounded request summary, stable error fields, and a log
reference. Recipe source, captured output, and stored traceback details remain
internal. The old bundled-data manifest endpoint is not part of the runtime API.

Reports are Conexus Document nodes, read through `/api/conexus/workspace` and
mutated by the published Agent through `create_nodes` and `update_nodes`.
AlphaLab does not keep a second Markdown copy in SQLite. Report IDs are stable
Document node IDs; the Agent updates an existing matching report and creates a
new one only for a new research subject. The current report history is global
to the AlphaLab publication and is not partitioned by project. Report Markdown
must remain domain-facing and omit hashes and execution identifiers.

Validation source routes are under `/api/validation`. `validation.py` must
declare `VALIDATION_SDK_VERSION = 1` and provide `performance` and `alpha_beta`
`@analysis` functions. Each receives one `ValidationContext`; keyword-only
literal defaults are form-editable. Saving requires `confirm_write`. Execution
uses the already confirmed backtest job's trusted-local Python boundary and
must retain timeout and JSON-output limits.

Data recipe routes are under `/api/data-sync/recipes`. Source and no-code
parameter writes require `confirm_write`; invoking recipe source requires
`confirm_python_execution`. A sync job stores the exact recipe source and hash.
Do not implement a second arbitrary-Python runner for data acquisition.

## Frontend conventions

All six workbenches use the selected project from `StrategySdkContext`. A strategy-source edit saves
`strategy.py`; parameter and schedule forms call the CST edit endpoint. Every source
save or structured edit automatically validates and records the internal source
package through the shared context. Do not expose revision numbers, hashes,
draft/freeze states, or a second freeze action in normal workbench UI. Factor
data-field controls select point-in-time series for the shared market chart and
must not mutate Python; factor dependency buttons may insert a dependency into
the active factor function. Factor tests and backtests are disabled only while editor changes are
unsaved. The factor template catalog may add only to a clean editable project
and must then refresh the shared project context so Factor and Strategy views
see the same registered factors immediately.

Factor, Strategy, Data, Validation, and Report workbenches open the matching
chapter of `docs/06_ALPHALAB_SDK_GUIDE.md` through the shared
`SdkDocumentation` drawer. The chapter selector must retain access to the
complete guide. Do not copy SDK prose into frontend constants or maintain
separate per-workbench help documents.

The default factor catalog contains complete, financially meaningful factor
implementations rather than one template per raw dataset column. Identifiers
and execution-state fields such as suspension, ST, and price-limit status remain
data or core trading constraints; they are not promoted to selection factors.

The Data Workbench is the acquisition exception: it edits the project's one
data-recipe module, not the strategy module. Built-in and user-saved template
cards replace that exact source; date/symbol controls edit literal function
defaults; preview and sync execute the current editor source after saving it.
Keep the official RQData Python documentation link next to the editor.

All Python workbench inputs use
`dashboard/frontend/src/components/python/PythonEditor`. Do not instantiate a
second Monaco runtime. The Strategy Workbench uses the project's `strategy.py`
model; the Validation Workbench uses its independent `validation.py` model.
The Factor Workbench uses one persistent factor source
unit per selected factor. Each factor document must contain exactly one complete
registered function and save only through the factor-create or
`replace_function` boundary. The assembled module is the only executable
artifact. Data recipes use a separate `recipe.py` URI through the same
component. The validation editor uses `kind="validation"` so completion and
contract diagnostics expose `ValidationContext` and `@analysis`, not trading
Context methods.

Monaco and the language clients are lazy-loaded. `vite.config.ts` must retain ES
worker output. Pyrefly and Ruff come from the active backend Python environment
installed by the `dev` extra. Their process commands are resolved server-side
from a fixed allowlist; never accept a client-supplied executable or argument
list. LSP mirrors belong only under ignored `data/runtime/editor/`, must stay
path-contained and size-bounded, and must never be read by a strategy or data
execution endpoint.

AlphaLab-specific completion should be derived from public SDK contracts and
live project/runtime metadata. A completion provider may assist authoring, but
the backend source inspector remains authoritative for SDK errors. Ctrl+S saves
through the existing canonical draft API; formatting and code actions come from
Ruff and may modify only the active Monaco model until the user saves.

Data-template migration is intentionally narrow: only source that exactly
matches a former request-only or visible built-in renderer after normalizing its
three form parameters is replaced with the current visible RQ command recipe.
Any manually changed or custom source is preserved.

Inactive legacy widgets may not be registered in `widgetComponents`, a layout
preset, Agent workspace commands, or navigation.

## Verification

```powershell
python -m pytest tests -q --basetemp=data\pytest
python scripts\check_facade_imports.py
python scripts\check_repository_hygiene.py
python -m ruff check alphalab dashboard\backend tests scripts
python -m compileall -q alphalab dashboard\backend
cd dashboard\frontend
npm run lint
npm run build
npm audit --omit=dev
```

Also run `git diff --check` and validate every Conexus JSON document. Generated
databases, caches, frontend builds, pytest scratch, and local environment files
must not be committed. Do not push unless explicitly requested.
