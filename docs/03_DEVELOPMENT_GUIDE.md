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
- Session ordering, tradability, fills, costs, and accounting belong in
  `alphalab/strategy/engine.py`.
- Provider logic stays in `alphalab/dataio/`; strategy Python never receives a
  provider or database handle.
- Public data-recipe types and the lazy RQ proxy belong in
  `alphalab/data_sdk/v1/recipe.py`; source rendering, AST/CST projection, and
  execution belong in `alphalab/dataio/recipes.py`.
- API and workbench changes must preserve one revision/hash across frontend,
  backend, Agent tools, Runs, and reports.

Do not add an expression evaluator, generated stage source, hidden fallback to
demo/default logic, or a parallel backtest path. Pre-SDK database tables are
read only: migration is implemented directly by `StrategyRepository`, while
historical report reconstruction stays in the analytics service.

## SDK source rules

Tests and examples import the small `alphalab` facade. Strategy modules import
only their public authoring API from `alphalab.sdk.v1`.

```python
from alphalab.sdk.v1 import (
    ExecutionPolicy, Monthly, PortfolioDecision, SignalResult, UniverseResult,
    execution, factor, portfolio, signal, universe,
)

SDK_VERSION = 1

@universe(id="etfs")
def etfs(context):
    return UniverseResult(symbols=context.universe)

@factor(id="momentum")
def momentum(context, *, window: int = 20):
    close = context.history("close", window=window + 1)
    return close.iloc[-1] / close.iloc[0] - 1

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

Keyword-only literal defaults are form-editable. Anything else displays as
custom. Structured edits must use LibCST and target the registered entrypoint
ID; never replace an arbitrary number or text match.

Literal `context.combine_factors(weights=..., normalization=...)` calls are the
form-editable multi-factor boundary. Signed weights encode factor direction.
Keep conditional or non-literal formulas in Python and project them as custom;
do not add an expression evaluator.

Adding a built-in factor merges the template's data requirements and inserts
its function before the registered signal with LibCST. Reject duplicate public
IDs and functions. Once installed, the project owns that Python copy; later
catalog edits do not rewrite saved strategy source.

## Validation and errors

Saving a revision performs parse, SDK version, registry uniqueness, signature,
literal metadata, factor dependency/cycle, compile/import, runtime requirement,
and synthetic output probes. Add new validation at the narrowest boundary and
return a phase: `parse`, `register`, `input`, `execute`, `output`, or `state`.

Output validation must remain core-owned: finite series/scores/weights,
in-universe symbols, concentration and gross limits, JSON state size, execution
policy, valid prices, liquidity, and cash.

## API conventions

Current authoring routes are under `/api/strategy`. Mutations require
`confirm_write`, saves require `confirm_save`, deletion requires
`confirm_delete`, and anything importing or invoking strategy source requires
`confirm_python_execution`.

Backtests use `/api/backtests/jobs` and require an explicit revision. A job pins
that revision before queueing. Historical result endpoints never read the
current draft.

Data recipe routes are under `/api/data-sync/recipes`. Source and no-code
parameter writes require `confirm_write`; invoking recipe source requires
`confirm_python_execution`. A sync job stores the exact recipe source and hash.
Do not implement a second arbitrary-Python runner for data acquisition.

## Frontend conventions

All six workbenches use `StrategySdkContext`. A full-source edit saves the same
draft; parameter and schedule forms call the CST edit endpoint. Factor field and
dependency buttons insert valid Python into the active factor function. Factor
tests and backtests are disabled for dirty drafts until a revision is frozen.
The factor template catalog may add only to a clean editable draft and must then
refresh the shared project context so Factor and Strategy views see the same
registered factors immediately.

The Data Workbench is the acquisition exception: it edits the project's one
data-recipe module, not the strategy module. Built-in and user-saved template
cards replace that exact source; date/symbol controls edit literal function
defaults; preview and sync execute the current editor source after saving it.
Keep the official RQData Python documentation link next to the editor.

All Python workbench inputs use
`dashboard/frontend/src/components/python/PythonEditor`. Do not instantiate a
second Monaco runtime. Full-source strategy views share the project's complete
`strategy.py` model. The Factor Workbench may use one derived `factor.py` model
per selected entrypoint, but it must contain exactly one complete `@factor`
function and save only through `replace_function`; it is never an executable or
persistent source of truth. Data recipes use a separate `recipe.py` URI through
the same component.

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

The request-only data-template migration is intentionally narrow: only source
that exactly matches a former built-in renderer is replaced with the equivalent
visible RQ command recipe. Any manually changed or custom source is preserved.

Inactive legacy widgets may not be registered in `widgetComponents`, a layout
preset, Agent workspace commands, or navigation.

## Verification

```powershell
python -m pytest tests -q --basetemp=data\pytest
python scripts\check_facade_imports.py
python -m compileall -q alphalab dashboard\backend
cd dashboard\frontend
npm run lint
npm run build
npm audit --omit=dev
```

Also run `git diff --check` and validate every Conexus JSON document. Generated
databases, caches, frontend builds, pytest scratch, and local environment files
must not be committed. Do not push unless explicitly requested.
