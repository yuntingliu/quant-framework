# AlphaLab Strategy SDK v1 Contract

> Design status: accepted and versioned contract.
>
> Implementation status: implemented. Strategy SDK v1 is the only active
> authoring and execution contract. Legacy pipeline tables are retained only
> for one-time migration and frozen historical records.

## 1. Purpose

AlphaLab Strategy SDK v1 makes Python the single source of truth for strategy
logic. Parameter forms, expression builders, factor research, signal modeling,
portfolio policy, execution settings, Codex edits, previews, and backtests are
different views or invocations of the same saved Python source package. The
authoring project contains `strategies/<strategy_id>.py` and shared independent
`factors/*.py` units; each runtime package deterministically assembles exactly
one selected strategy with the shared factor library into one complete module.
`strategy.py` remains a compatibility alias for the migrated `main` strategy.

The SDK must preserve both of these properties:

1. common workflows remain editable through structured controls;
2. any supported strategy hook can be replaced with custom Python.

The SDK is a correctness and integration boundary. In `trusted_local` mode it
is not an operating-system security sandbox.

Normative terms such as **MUST**, **MUST NOT**, **SHOULD**, and **MAY** describe
requirements for the v1 implementation.

## 2. Core Decisions

SDK v1 fixes the following architectural decisions:

- A project owns one or more canonical UTF-8 strategy files under `strategies/`
  plus zero or more shared `factors/<factor_id>.py` units. Each strategy retains
  one universe, signal, portfolio and execution contract; strategies are never
  combined into a single module or a shared portfolio implicitly.
- Python source is the only source of truth for executable strategy logic.
- Forms modify exact Python syntax nodes; they do not maintain a parallel JSON
  strategy implementation.
- Decorators and function signatures expose machine-editable metadata and
  parameters.
- Unrecognized valid Python remains executable and is displayed as `custom` in
  structured views.
- Saving creates an immutable strategy revision.
- Every evaluation and backtest executes the exact deterministically assembled
  saved module and records its SHA-256.
- Factor, cross-sectional, stage, and full backtest evaluation differ only by
  the SDK entrypoint invoked.
- The editable source set contains strategy code only. Data access enforcement,
  event delivery, fills, accounting, persistence, and broker authorization stay
  in the core engine.
- Routine authoring does not use a candidate/promotion lifecycle. Scratch code
  enters a project through a normal source edit and revision save.
- Historical runs remain frozen read-only records. A breaking SDK change uses
  a new major SDK version and an explicit persisted-source migration, not
  runtime aliases or permanent compatibility branches.

## 3. Conceptual Flow

```text
Data Workbench       edits the separate acquisition recipe
Factor Workbench     edits persistent factors/<factor_id>.py units
Strategy Workbench   edits strategy.py (@universe/@signal/@portfolio/events/@execution_data_fill/@execution)
Codex                edits those same source units
                              |
                              v
                 deterministic assembly
                              |
                              v
                    validate + save revision
                              |
                              v
                  frozen StrategySourcePackage
                   /          |              \
          factor test   cross-section       backtest
                              |
                              v
                     Run + Artifact + Report
```

There is no separate expression runtime, generated strategy implementation,
Lab strategy, or backtest-only strategy source. Source units are authoring
boundaries, not independent runtimes.

## 4. Public Facade

User modules MUST import supported objects from one versioned facade:

```python
from alphalab.sdk.v1 import (
    Daily,
    Event,
    ExecutionContext,
    ExecutionPolicy,
    FactorContext,
    Monthly,
    Parameter,
    PortfolioOptimizationError,
    PortfolioOptimizationResult,
    PortfolioContext,
    PortfolioDecision,
    SignalContext,
    SignalResult,
    State,
    StrategyContext,
    UniverseContext,
    UniverseResult,
    Weekly,
    execution,
    execution_data_fill,
    factor,
    neutralize_factor_scores,
    on_event,
    portfolio,
    optimize_portfolio,
    schedule,
    signal,
    universe,
)
```

The facade SHOULD remain small. Internal repositories, data providers, stores,
engines, database connections, and broker clients MUST NOT be exported through
the SDK.

Every strategy module MUST declare:

```python
SDK_VERSION = 1
```

The runner MUST reject a missing or unsupported SDK version before executing
user code.

A module MAY declare literal capability requirements:

```python
DATA_REQUIREMENTS = {
    "bars": ["open", "high", "low", "close", "volume", "amount"],
    "fundamentals": ["roe", "market_cap"],
    "daily_factors": ["roe"],
    "index_components": ["000300.SH"],
}

RUNTIME_REQUIREMENTS = {
    "numpy": ">=2,<3",
    "pandas": ">=2.2,<3",
}
```

Requirements are runtime-preparation contracts, not installation commands. The
background runner MUST fail before invoking strategy code when the selected
data profile or runtime cannot satisfy them. It MUST NOT install packages or
switch data profiles implicitly, and submission MUST NOT perform a separate
full-range preflight.

## 5. Canonical Source and Module Contract

`strategy.py` MUST contain `SDK_VERSION`, imports, shared constants/helpers,
and all non-factor registrations. It MUST NOT contain a registered `@factor`
function. Each factor unit MUST contain exactly one complete registered
`@factor` function and no second top-level statement. The factor public ID
determines its canonical path. Imports or helpers needed by factor bodies belong
in `strategy.py` and are available after assembly.

The assembler MUST insert factor functions in a stable stored order before the
first signal, portfolio, event, or execution registration. It MUST then validate
the complete result. No source unit may be imported or executed by itself.

A valid assembled project strategy module MUST contain:

- exactly one `@universe` function;
- zero or more `@factor` functions;
- zero or more `@schedule` functions;
- exactly one `@signal` function;
- exactly one `@portfolio` function;
- zero or one `@on_event` function for each event key;
- zero or one `@execution_data_fill` function;
- exactly one `@execution` function;
- arbitrary private helper functions and classes;
- imports available in the configured runtime.

Registered public IDs MUST be unique. By default the Python function name is
the public ID. A literal decorator `id=` MAY provide a stable ID independent of
the function name.

The following is a valid assembled module outline:

```python
from alphalab.sdk.v1 import *

SDK_VERSION = 1


@universe(id="etf_universe")
def etf_universe(context: UniverseContext) -> UniverseResult:
    ...


@factor(id="momentum_20d")
def momentum_20d(context: FactorContext, *, window: int = 20):
    ...


@signal(id="monthly_top1", schedule=Monthly.last_trading_day(at="close"))
def monthly_top1(context: SignalContext, state: State, *, top_n: int = 1):
    ...


@portfolio(id="target_weights")
def target_weights(context: PortfolioContext, signal: SignalResult, state: State):
    ...


@on_event(Event.SESSION_CLOSE, id="daily_risk")
def daily_risk(context: StrategyContext, state: State):
    ...


@execution_data_fill(id="fill_missing_market_state")
def fill_missing_market_state(context: ExecutionContext, rows: pandas.DataFrame):
    ...


@execution(id="next_open")
def next_open(context: ExecutionContext, decision: PortfolioDecision):
    ...
```

The core runner and backtest engine are not appended as editable source. They
load this module, inspect its registry, and invoke its entrypoints.

## 6. Decorators and Metadata

Decorators serve two purposes:

1. register an entrypoint with the SDK runner;
2. expose literal metadata to workbench forms.

The workbench MUST recognize literal decorator arguments. For example:

```python
@signal(
    id="monthly_top1",
    label="月末 Top1",
    schedule=Monthly.last_trading_day(at="close"),
)
```

The Signal Workbench can display `每月 / 最后交易日 / 收盘`.

Valid Python that is not a recognized literal remains supported:

```python
@signal(schedule=my_custom_schedule)
```

The workbench MUST display the schedule as `custom`. It MUST NOT execute the
function to guess its meaning and MUST NOT overwrite it through a preset
control without explicit user confirmation.

Decorators MUST preserve the wrapped function's identity and signature. They
MUST register metadata without silently changing strategy results.

## 7. Parameters

Keyword-only function defaults are the canonical parameter definition:

```python
@factor()
def momentum(context: FactorContext, *, window: int = 20, log_return: bool = False):
    ...
```

SDK v1 structured controls MUST support literal defaults of these types:

- `bool`
- `int`
- finite `float`
- `str`
- `None`
- `Literal[...]`
- SDK schedule value objects

Type annotations SHOULD determine editor type and validation. Optional
`Annotated` metadata MAY provide labels, bounds, step sizes, and descriptions.

```python
from typing import Annotated


def momentum(
    context: FactorContext,
    *,
    window: Annotated[int, Parameter(label="窗口", minimum=2, maximum=500)] = 20,
):
    ...
```

A form edit MUST update the exact default-value syntax node. It MUST NOT search
the source for a matching number or string.

Dynamic defaults or unsupported annotations remain valid Python but are shown
as `custom` and edited in source mode.

## 8. Source Editing and Round Trips

The Python source set is canonical. Workbench forms are projections of its
concrete syntax tree; the assembled complete module is the canonical validation
and execution artifact.

The implementation MUST use a concrete-syntax-tree editor such as LibCST or an
equivalent comment-preserving mechanism. Regular expressions and global text
replacement MUST NOT be used for semantic edits.

A structured edit follows this sequence:

```text
load current source units and assemble them
  -> locate registered function/decorator/parameter by stable syntax identity
  -> replace one syntax node
  -> preserve unrelated formatting, comments, and custom code
  -> split the result back into the same source-unit boundaries
  -> parse and validate the complete assembled module again
  -> run cross-file import, registry, and output probes
  -> atomically update all affected units and record one immutable revision
```

Clicking a data field inserts SDK Python at the active cursor, for example:

```python
close = context.history("close", window=window + 1)
roe = context.fundamental("roe")
```

Clicking another factor inserts a registry-resolved dependency:

```python
short = context.factor("momentum_20d", window=20)
long = context.factor("momentum_60d", window=60)
```

Registry calls are preferred over direct calls because they make dependency,
parameter, caching, provenance, and cycle handling explicit.

After a `strategy.py` or factor-unit edit, every workbench MUST reparse the
assembled module:

- recognized metadata and defaults return to normal controls;
- supported but unrecognized code is displayed as `custom`;
- syntax or contract errors are displayed without overwriting the source;
- stale previews and evaluations are invalidated.

## 9. Point-in-Time Context

SDK Context objects are immutable capability views. They MUST NOT expose a raw
provider, mutable DataFrame cache, file path, database handle, ResultStore,
PipelineRepository, engine instance, or broker client.

All data methods MUST enforce the current event time. User code cannot request
or observe data later than `context.as_of`.

Common read-only properties include:

```python
context.event
context.as_of
context.calendar
context.universe
context.portfolio
context.last_decision
context.random
```

Common data methods include:

```python
context.current("close")
context.history("close", window=120)
context.history(["open", "high", "low", "close", "volume"], window=120)
context.fundamental("roe")
context.daily_factor("roe")
context.index_components("000300.SH")
context.factor("momentum_20d", window=20)
context.combine_factors(
    weights={"momentum_20d": 0.6, "low_volatility": -0.4},
    normalization="rank",
)
```

`context.random` MUST be a run-seeded random source. Strategy code SHOULD use it
instead of global randomness. `context.as_of` MUST be used instead of the
machine clock.

Missing or unavailable fields MUST fail explicitly. Demo and runtime profiles
MUST never be silently mixed.

`daily_factor()` returns the latest provider value whose date is no later than
`context.as_of`. `index_components()` chooses the latest membership snapshot no
later than that time and intersects it with the core-owned point-in-time
instrument universe. Historical suspension state is joined to market rows as
`paused`/`is_suspended`; the execution engine rejects fills when
`is_suspended` is true.

An optional project-owned `@execution_data_fill` function receives a bounded
`ExecutionContext` and a `pandas.DataFrame` containing only `date`, `symbol`,
`is_suspended`, `limit_up`, and `limit_down` for actual order candidates. It
returns that same frame after filling missing values. The function cannot add,
remove, reorder, or rename rows; cannot replace known values; and cannot edit
prices, volume, amount, instruments, or accounting inputs. Its Context market
history ends before the execution date, so its visible Python cannot fill from
future bars. Any fields it leaves missing remain non-tradable under strict
execution. Price-limit values are finite positive prices, except that a paired
`limit_up == limit_down == 0` explicitly means the session has no price limit.
A single zero is invalid. Provider-side non-positive values are normalized to
missing before the project hook runs, so only validated project code can emit
the paired marker.

## 10. Universe Contract

The universe entrypoint defines strategy eligibility preferences over the
point-in-time instruments made available by the core:

```python
@universe(id="major_etfs")
def major_etfs(context: UniverseContext) -> UniverseResult:
    candidates = context.instruments(asset_type="ETF")
    candidates = candidates[candidates["symbol"].isin(MAJOR_ETFS)]
    return UniverseResult(symbols=candidates["symbol"])
```

`context.universe` is the complete point-in-time instrument set, not an implicit
asset-type filter. Every new project starts from `sdk-v1-default`, whose universe
intersects that set with `context.instruments()` rows where `asset_type == "CS"`.
An Agent creating a fixed, index, sector, ETF, or otherwise restricted strategy
replaces that registered universe function before the initial save; restricted
pools are still intersected with `context.universe`.

The function MAY filter or rank instruments but MUST NOT introduce a symbol not
present in the point-in-time instrument snapshot.

The core remains responsible for data availability, listing dates, stale data,
and non-bypassable tradability eligibility.

## 11. Factor Contract

The canonical factor signature is:

```python
@factor(id="factor_id")
def factor_name(context: FactorContext, *, parameter=default) -> pandas.Series:
    ...
```

The return value MUST be a one-dimensional numeric `pandas.Series` indexed by
symbol.

Validation rules:

- duplicate symbols are invalid;
- infinite values are invalid;
- `NaN` is allowed to represent missing coverage;
- symbols outside the active universe are rejected or removed by the core and
  reported diagnostically;
- the output is immutable after it crosses the SDK boundary;
- the factor MUST be deterministic for the same Context, parameters, and
  runtime fingerprint.

Factor dependencies MUST use `context.factor(...)` for registry tracking.
Cycles fail before evaluation.

Single-factor evaluation MUST load the complete saved module, resolve the
registered factor ID, and invoke that exact function. It MUST NOT translate the
function into another expression runtime.

`context.combine_factors(...)` is the standard Python form for visual
multi-factor selection. It resolves the same registered factor functions as
`context.factor(...)`, aligns them to the active universe, applies `raw`,
cross-sectional `rank`, or `zscore` normalization, and combines them after
normalizing absolute weights to one. Positive weights prefer larger values;
negative weights prefer smaller values. Optional per-factor overrides use
`parameters={"factor_id": {"parameter": value}}`.

The workbench MAY structurally edit literal `weights` and `normalization` in
this call. A single literal `context.factor(...)` call MAY be converted to this
form while preserving literal call parameters. Conditional blends,
neutralization, regime switching, grouping, and arbitrary formulas remain
ordinary Python and MUST display as `custom`; the workbench MUST NOT interpret
them through a second expression language.

The built-in factor library is a catalog of complete SDK Python templates, not
a second factor runtime. Adding a template MUST create a new factor source unit
and merge its declared data requirements into `strategy.py`. From that point the
copied function is ordinary canonical project source: forms edit its literal
defaults, the source editor may replace any supported logic, and evaluation and
backtest invoke that exact function. Template catalog changes MUST NOT mutate a
factor already copied into a project.

## 12. Signal Contract

The canonical signal signature is:

```python
@signal(id="signal_id", schedule=...)
def signal_name(
    context: SignalContext,
    state: State,
    *,
    parameter=default,
) -> SignalResult:
    ...
```

`SignalResult` contains:

```python
SignalResult(
    selected: Sequence[str],
    scores: Mapping[str, float] | pandas.Series,
    state: JsonObject | None = None,
    diagnostics: JsonObject | None = None,
)
```

Rules:

- selected symbols MUST be unique and belong to the current eligible universe;
- selected scores MUST be finite;
- diagnostics MUST be JSON serializable;
- a returned state replaces the signal handler's previous state atomically;
- an exception commits neither output nor state.

Cross-sectional evaluation invokes this same signal function with one point-in-
time Context. Historical backtests invoke it on every scheduled decision event.

## 13. Schedule Contract

Built-in schedules are Python SDK value objects:

```python
Daily.at("close")
Weekly.last_trading_day(at="close")
Monthly.first_trading_day(at="open")
Monthly.last_trading_day(at="close")
```

Schedule values are interpreted by the core calendar. Strategy code MUST NOT
construct a separate wall-clock loop.

A custom schedule is a registered Python function:

```python
@schedule(id="custom_recompute")
def custom_recompute(calendar, session) -> bool:
    return calendar.is_month_end(session) or calendar.is_quarter_end(session)
```

The workbench displays a registered custom schedule as `custom`. The core still
controls session enumeration and event delivery.

## 14. Portfolio Contract

The canonical portfolio signature is:

```python
@portfolio(id="portfolio_id")
def portfolio_name(
    context: PortfolioContext,
    signal: SignalResult,
    state: State,
    *,
    parameter=default,
) -> PortfolioDecision:
    ...
```

`PortfolioDecision` may contain:

```python
PortfolioDecision(
    target_weights: Mapping[str, float],
    state: JsonObject | None = None,
    reason: str | None = None,
    diagnostics: JsonObject | None = None,
)
```

The strategy may choose weights, cash, and declared defensive assets. The core
MUST revalidate:

- finite and non-negative weights;
- selected/allowed membership;
- maximum single-name weight;
- gross and cash limits;
- availability of any defensive asset;
- consistency with the current portfolio snapshot.

Returning a target weight does not assert that a trade will fill.

SDK v1 also exposes `optimize_portfolio(...)` with explicit `equal_weight`,
`minimum_variance`, `risk_parity`, `hrp`, and `max_sharpe` methods. Every method
is long-only, honors `target_gross <= 1`, a single-name cap, and an optional
cash-inclusive turnover cap. `max_sharpe` MUST receive explicit expected
returns. Infeasible constraints, insufficient observations, and solver failure
MUST raise `PortfolioOptimizationError`; they MUST NOT silently select equal
weight. The returned `PortfolioOptimizationResult` records covariance repair,
dropped securities, observation count, convergence, risk contributions, and
expected turnover. The strategy still returns a `PortfolioDecision`, and the
core MUST repeat all hard membership and exposure checks.

`neutralize_factor_scores(...)` is the public point-in-time cross-sectional
residualization helper. Missing rows are excluded rather than imputed and its
result records fitted coefficients, sample size, exclusions, and fit quality.

## 15. Event and Stateful Policy Contract

SDK v1 is event driven. At minimum the runner exposes:

```python
Event.SESSION_OPEN
Event.SESSION_CLOSE
Event.DECISION
Event.FILL
Event.REJECTION
```

One handler MAY be registered for each event key:

```python
@on_event(Event.SESSION_CLOSE, id="daily_risk_control")
def daily_risk_control(
    context: StrategyContext,
    state: State,
) -> PortfolioDecision | None:
    ...
```

Returning `None` means no target change. Returning a decision replaces the
current desired target, subject to core validation and execution.

There is at most one handler per event key in v1. Complex behavior belongs in
helper functions called by that handler; v1 does not define ambiguous handler
priority or merge semantics.

Handlers receive actual portfolio and fill state. A failed or rejected order is
reflected through subsequent Context snapshots and events. Strategy code MUST
NOT assume that a target was filled merely because it was returned.

## 16. State Contract

`State` is one shared JSON object persisted by the core between events.

State values MUST be composed only of:

- `null`
- booleans
- finite numbers
- strings
- lists of supported values
- dictionaries with string keys and supported values

DataFrames, Series, functions, classes, database objects, open files, and Python
pickles are forbidden state.

One project strategy has one shared `State`. Signal, portfolio, and event
handlers receive the same event-local working copy in deterministic execution
order, so a daily protection handler can set a lock that the next monthly signal
reads. An entrypoint MAY return a replacement state in its result. In-function
mutations are not committed until the complete event succeeds. On validation
failure or exception, neither the event's state nor its decisions are committed.

The implementation MUST bound serialized state size and include state hashes in
run diagnostics.

## 17. Execution Contract

The canonical execution signature is:

```python
@execution(id="execution_id")
def execution_name(
    context: ExecutionContext,
    decision: PortfolioDecision,
    *,
    parameter=default,
) -> ExecutionPolicy:
    ...
```

`ExecutionPolicy` may declare supported assumptions and intents, including:

- next-session open or close activation;
- commission and slippage assumptions;
- volume/amount participation limits;
- market-impact parameters;
- ordered fallback candidates;
- supported target-weight order intent.

The core owns order creation, valid-price checks, stop/suspension handling,
limit-up/limit-down behavior, cash, fills, fees, accounting, and broker
authorization. Returning an `ExecutionPolicy` MUST NOT bypass those controls.

The optional execution-state fill signature is:

```python
@execution_data_fill(id="fill_missing_market_state")
def fill_missing_market_state(
    context: ExecutionContext,
    rows: pandas.DataFrame,
    *,
    main_board_limit_rate: float = 0.10,
    star_market_limit_rate: float = 0.20,
    chinext_limit_rate: float = 0.20,
    etf_limit_rate: float = 0.10,
    ipo_unlimited_sessions: int = 5,
) -> pandas.DataFrame:
    ...
```

The function contains the project's actual Python for filling missing
`is_suspended`, `limit_up`, and `limit_down` values. It returns the same rows
directly; there is no decision wrapper or hidden policy branch. The core rejects
changes to known values, row identity, other market fields, or row count. The
shipped function derives limit prices from the previous trading day's
unadjusted `raw_close`, reads point-in-time asset type, `is_st`, and listing
dates, and keeps board, ETF, ST-rule-change, and stock IPO-session rules as
editable keyword-only defaults. For a known no-limit stock IPO session it sets
both limit columns to zero.

The engine compares those unadjusted limits only with the matching unadjusted
execution field (`raw_open` for open activation and `raw_close` for close
activation). Adjusted `open`/`close` remain the research and accounting series;
mixing an adjusted execution field with an unadjusted price limit is invalid.

New broker order types or data feeds require an explicit SDK/core capability.
Arbitrary Python cannot create capabilities the core does not expose.

### 17.1 Event Ordering

For each event timestamp, the runner MUST use this order:

1. core builds one point-in-time Context and one working State copy;
2. when a registered signal schedule is due, run `@universe`, required
   `@factor` dependencies, `@signal`, then `@portfolio`;
3. run the single `@on_event` handler registered for the current event key;
4. a non-`None` event-handler decision replaces the base portfolio decision;
5. run `@execution` for the final decision;
6. validate all outputs, then atomically commit State and desired targets;
7. at the policy's permitted later event, run `@execution_data_fill` for actual
   order rows when the project defines it;
8. the core validates the returned state frame and creates or rejects fills.

If the signal schedule is not due, the base desired target is the previously
committed target. Returning `None` from the event handler preserves it.

## 18. Determinism and Side Effects

For reproducible research, strategy code SHOULD be a pure function of:

- the provided Context;
- declared parameters;
- persisted State;
- the frozen runtime environment.

Strategy code MUST NOT rely on wall-clock time, mutable module globals, hidden
local files, ambient environment variables, or unseeded randomness as part of a
reproducible result.

In trusted-local mode the SDK cannot technically prevent file, network, or
subprocess access. Static review, explicit execution approval, process timeout,
bounded output, and environment fingerprinting remain required. The product
MUST describe trusted-local code accurately and MUST NOT call it sandboxed.

## 19. Validation Pipeline

Saving a strategy revision requires all of these checks:

1. parse every source unit and verify its boundary;
2. deterministically assemble the complete module;
3. verify `SDK_VERSION`;
4. build the decorator registry;
5. verify required entrypoints and uniqueness;
6. verify supported signatures;
7. validate literal metadata and parameters;
8. resolve registered dependencies and reject cycles;
9. compile the complete module;
10. run bounded contract probes with synthetic Contexts;
11. compute the assembled source SHA-256 and package manifest.

The assembled runtime provides every public name in `alphalab.sdk.v1` through
an explicit stable prelude. A factor unit therefore does not depend on a
decorator import in `strategy.py`; any non-SDK dependency used by a factor MUST
be imported inside that factor function. All checks complete before any source
unit or revision is written.

Runtime boundary validation repeats on every entrypoint output. Static
validation never substitutes for output validation.

Warnings for file, network, subprocess, dynamic execution, global state, or
machine-clock access MUST be visible before trusted-local execution. A warning
does not convert trusted-local execution into a sandbox.

## 20. Evaluation Semantics

Every research operation loads one immutable StrategySourcePackage:

| Operation | Entrypoint |
| --- | --- |
| Factor snapshot | selected `@factor` at one as-of event |
| Single-factor history | selected `@factor` over scheduled PIT events |
| Factor research | selected `@factor`, next-session open forward returns, fixed evidence report |
| Signal cross-section | `@universe`, dependencies, selected `@signal` |
| Portfolio preview | signal followed by `@portfolio` and applicable event handler |
| Execution preview | portfolio decision followed by `@execution` |
| Execution-state fill | actual order rows passed through `@execution_data_fill` |
| Backtest | full event loop over the same module |

The operation result MUST record the package hash and invoked entrypoint IDs.
An evaluation MUST NOT copy or translate a function into an alternate runtime.
Factor research MUST use the first open strictly after each close-time signal,
retain missing pairs as missing, report Rank IC/ICIR/Newey-West/bootstrap,
1/3/6-period decay, quantile and long-short returns, coverage, turnover, and a
chronological 70/30 split, and mark fewer than 24 valid cross-sections as
`insufficient`.

## 21. StrategySourcePackage and Persistence

Saving a draft creates an immutable package containing:

```text
project id
strategy revision
exact strategy.py and factors/*.py authoring units
deterministically assembled complete Python source
assembled source SHA-256
SDK version
compiler/validator version
registered entrypoint manifest
literal parameter snapshot
declared runtime dependencies
runtime environment fingerprint
created timestamp
parent revision
```

Current authoring source may be replaced, while a saved revision is immutable.
A successful save replaces the affected source units and creates at most one
new revision in the same transaction. A failed validation or probe leaves both
the authoring source and revision unchanged; no persisted dirty intermediate
version is created.

A Run freezes:

```text
StrategySourcePackage identity and hash
data profile and data snapshot fingerprints
requested entrypoint or full-backtest operation
run parameters and date range
event/state/output diagnostics
returns, fills, holdings, attribution, and artifacts as applicable
```

Reports cite the frozen Run, not the project's later draft or revision.

## 22. Workbench Contract

The target user-facing boundaries are:

| Surface | SDK source owned |
| --- | --- |
| Project/Data | project metadata and the separate acquisition recipe |
| Factor Research | persistent `factors/*.py` units and their tests |
| Strategy | `strategy.py`: `@universe`, `@signal`, `@portfolio`, `@on_event`, `@execution_data_fill`, `@execution` |
| Validation | assembled-source inspection, immutable revision selection, evaluations, backtests |
| Report | saved Run artifacts and provenance |

The `strategy.py` editor MAY be available from Strategy and Validation. A
source-unit save always updates the project draft, reparses the assembled module
for every workbench, and invalidates stale results.

Validation MAY edit a new draft based on the selected revision. It MUST NOT
mutate the source attached to an existing Run.

Python Lab is not a separate authoritative strategy lifecycle. A scratch editor
MAY exist, but saving useful code inserts it into the canonical project source
and follows the normal revision workflow. No candidate/promotion object is
required for ordinary authoring.

The Strategy surface projects a recognized `context.combine_factors(...)` call
as the multi-factor selector: factor membership, signed direction/weight, and
normalization. Applying the form changes that call in the same signal function;
single-factor tests, cross-sectional previews, and backtests continue to invoke
the registered Python factor functions from the same revision.

The Factor surface lists both factors already registered in the project and the
built-in Python template catalog. Installing a template is a confirmed draft
write and is unavailable while the browser has unsaved source edits, preventing
one editor save from overwriting another.

## 23. Codex and Agent Contract

Codex and published Agents edit the same project draft as the workbenches.

They MUST:

- create every new project by copying `sdk-v1-default`, which initializes
  `recipe.py`, `strategy.py`, `factors/*.py`, and `validation.py`; Agent create
  requests carry neither a project-template selector nor a complete module;
- install or instantiate factor templates before changing their parameters or
  function body, and limit recipe/validation writes to template operations;
- inspect the current source-unit inventory, relevant unit, assembled
  inspection, and revision before editing;
- use CST-aware source operations or replace an explicitly identified function;
- never patch a generated number by global text matching;
- preserve unrelated custom code and comments;
- validate after each source mutation;
- obtain explicit confirmation before saving, executing, or deleting;
- report only the resulting revision, changed files, validation summary, and
  compact manifest for a normal write;
- never claim that trusted-local Python is sandboxed.

Agent tools SHOULD be generated from the backend OpenAPI contract rather than
hand-maintaining duplicate request schemas and confirmation semantics.

## 24. Full ETF Rotation Example

The following abbreviated module shows how a monthly selector and daily
stateful protection share one SDK source:

```python
from alphalab.sdk.v1 import (
    Event,
    ExecutionPolicy,
    Monthly,
    PortfolioDecision,
    SignalResult,
    UniverseResult,
    execution,
    factor,
    on_event,
    portfolio,
    signal,
    universe,
)

SDK_VERSION = 1

ETF_POOL = ("510300", "512100", "513100", "511260")
DEFENSIVE = "511260"


@universe(id="major_etfs")
def major_etfs(context):
    return UniverseResult(symbols=ETF_POOL)


@factor(id="momentum")
def momentum(context, *, window: int = 20):
    close = context.history("close", window=window + 1)
    return close.iloc[-1] / close.iloc[0] - 1


@signal(
    id="monthly_top1",
    schedule=Monthly.last_trading_day(at="close"),
)
def monthly_top1(context, state, *, top_n: int = 1):
    if state.get("locked_until_month_end"):
        state["locked_until_month_end"] = False
    scores = context.factor("momentum", window=20).drop(labels=[DEFENSIVE])
    selected = list(scores.nlargest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)


@portfolio(id="top1_or_bond")
def top1_or_bond(context, signal, state):
    target = signal.selected[0] if signal.selected else DEFENSIVE
    return PortfolioDecision(target_weights={target: 1.0}, state=state)


@on_event(Event.SESSION_CLOSE, id="daily_protection")
def daily_protection(context, state):
    holding = context.portfolio.primary_holding
    if holding is None or holding.symbol == DEFENSIVE:
        return None

    state["peak_return"] = max(
        float(state.get("peak_return", 0.0)),
        float(holding.return_since_entry),
    )
    close = context.history("close", symbols=[holding.symbol], window=100)
    below_ma100 = holding.close < float(close[holding.symbol].mean())
    profit_lock = (
        state["peak_return"] > 0.20
        and state["peak_return"] - holding.return_since_entry > 0.08
    )
    if below_ma100 or profit_lock:
        state["locked_until_month_end"] = True
        return PortfolioDecision(
            target_weights={DEFENSIVE: 1.0},
            state=state,
            reason="ma100_gate" if below_ma100 else "profit_lock",
        )
    return None


@execution(id="next_open")
def next_open(context, decision):
    return ExecutionPolicy(
        activation="next_session_open",
        commission_rate=0.00025,
        slippage_rate=0.0002,
    )
```

The public names and semantics in this contract are normative for v1. Any
pre-cutover correction MUST update this document, tests, workbench parsers,
runner, and Agent schemas together. After cutover they are versioned API.

## 25. Core-Owned Boundaries

No SDK v1 strategy may override these core responsibilities:

- point-in-time data visibility and fundamental availability dates;
- session calendar and event ordering;
- symbol existence, listing, delisting, and stale-data checks, including a
  zero-value write-off for a held instrument on its delisting date;
- valid price and positive-volume checks;
- suspension, price-limit, and liquidity handling;
- next-period alignment and fill creation;
- cash, commission, slippage, impact, and accounting;
- finite output, membership, concentration, and gross-exposure gates;
- durable state, Run, Artifact, report, and provenance storage;
- paper/live environment separation;
- broker connection and order authorization.

The SDK may expose supported options and the bounded `@execution_data_fill`
hook for these systems. It cannot replace their implementation or bypass their
post-fill validation.

## 26. Error Contract

Internal SDK diagnostics MUST retain:

- strategy revision and source hash;
- entrypoint ID;
- event/as-of time;
- contract phase (`parse`, `register`, `input`, `execute`, `output`, `state`);
- a bounded traceback for custom Python failures;
- whether any state or output was committed.

Agent- and user-facing errors MUST instead return a stable error code, bounded
safe summary, relevant phase/entrypoint fields, and an internal log reference.
They MUST NOT expose absolute server paths, release directories, environment or
source hashes, or tracebacks. Stable backtest runtime codes include
`MISSING_DELISTING_SETTLEMENT`, `INSUFFICIENT_MARKET_STATE`, and
`NO_TRADABLE_CANDIDATES`.

Preflight MUST NOT reject a run merely because an unrelated full-universe
symbol/date has incomplete execution state. If at least one candidate has
complete state, partial coverage is returned as the structured warning
`PARTIAL_MARKET_STATE` and the job may be queued. Missing state does not alter
the signal universe. When an actual order is activated, the frozen project's
optional `@execution_data_fill` Python runs first; any suspension or price-limit
state that remains missing is treated as non-tradable. A validated paired zero
means that the session is known to have no price limit and therefore skips only
the limit-price check. The result retains
bounded fill/exclusion counts and samples. Missing execution state for every
actual order can therefore complete as a zero-trade result with explicit warnings;
`INSUFFICIENT_MARKET_STATE` is reserved for a required runtime dataset or field
with no usable coverage at all.

Background job reads and frozen result summaries MUST expose one compact
top-level contract. It always includes `warnings`, `research_valid`,
`research_invalid_reasons`, `execution_fidelity`, `attempted_trade_count`,
`successful_trade_count`, `execution_data_fill_count`,
`synthetic_state_count`, `market_state_rejection_count`,
`suspension_rejection_count`, `limit_up_rejection_count`,
`limit_down_rejection_count`, `capacity_rejection_count`, and
`cash_rejection_count`; callers do not unwrap a second result-summary object.
Each synthetic value in the full execution audit records whether it came from
the provider, project `strategy_fill`, or an explicit engine fallback. While a job has no result,
`research_valid` is `null` and all counters are zero. A wait endpoint MUST hold
one request for no more than 30 seconds and return when the persisted status
changes or the timeout expires.

`research_valid` MUST be false when execution state is missing, the same exit
repeatedly fails, or the actual portfolio remains materially different from
the requested target across rebalances. `PERSISTENT_EXIT_FAILURE` and
`LOW_EXECUTION_FIDELITY` are stable invalid-reason codes. A single aggregate
success-rate threshold is not sufficient because an unfilled exit is more
material than an ordinary partial buy.

Each signal evaluation MAY use the complete in-memory score vector while the
backtest is running, but a frozen Run stores only compact per-rebalance
evidence: universe/scored/selected counts, coverage, selection turnover, and
rank IC measured to the following signal date. The score vector MUST NOT be
stored in summary diagnostics. Analytics for SDK Runs MUST consume this native
evidence before considering a read-only legacy stage-output fallback. Fewer
than three paired securities MUST return `INSUFFICIENT_CROSS_SECTION`, not a
misleading claim that only one signal period exists.

Return annualization MUST be selected from the frozen return index's observed
calendar cadence. Signal or rebalance frequency describes decision timing and
MUST NOT be reused as the return frequency. Portfolio limits for SDK analytics
MUST come from the parsed frozen `@portfolio` keyword defaults before any
legacy settings fallback.

User exceptions fail the current operation. The runner MUST NOT silently fall
back to an older revision, default factor, demo profile, previous output, or
alternate implementation.

## 27. Acceptance Criteria

SDK v1 is complete only when all of these are demonstrated by automated tests:

1. Changing a recognized form parameter changes one Python syntax node and the
   saved source hash.
2. A `strategy.py` or factor-unit edit reparses into forms where recognizable
   and displays `custom` elsewhere without losing source.
3. Clicking a data field or factor inserts valid SDK Python at the cursor.
4. Factor snapshot and historical factor evaluation invoke the same saved
   factor function.
5. Signal cross-section and backtest invoke the same saved signal function.
6. Stateful daily rules receive deterministic prior State and actual portfolio
   snapshots.
7. A rejected fill does not appear as a completed position change.
8. Future data cannot be retrieved through any Context method.
9. Invalid weights, symbols, state, and non-finite outputs fail at the core
   boundary.
10. A Run remains reproducible after the project draft changes.
11. Workbench, Codex, backend, and Agent use one assembled source revision and
    hash while editing the same persistent source units.
12. No active expression, Lab, or legacy pipeline runtime can produce a second
    authoritative result.

## 28. Cutover Requirements

The implementation should proceed behind focused development branches, but the
product cutover is atomic at the public contract level:

1. implement `alphalab.sdk.v1` and its event runner;
2. implement CST source inspection and edits;
3. adapt every workbench to the canonical project source;
4. route factor, cross-section, preview, and backtest operations to the SDK
   runner;
5. expose an explicit template-component migration that creates a new editable
   project revision without modifying any historical frozen revision;
6. retain old saved BacktestRuns as read-only frozen records;
7. remove current active factor-expression, three-stage authoring, and Python
   Lab promotion branches;
8. update API schemas, Conexus tools, prompts, docs, and contract tests in the
   same cutover.

The final system MUST NOT keep deprecated active tool names, hidden runtime
fallbacks, or parallel authoring paths for convenience.
