# AlphaLab Barebone

AlphaLab is a local quantitative-research framework with provider-backed data,
a unified Python strategy SDK, point-in-time factor evaluation, a stateful daily
event backtester, immutable source revisions, a FastAPI/React workstation, and
an optional Conexus research Agent.

**中文零基础教程：[从第一个实验到读懂整个项目](文档.md)**，涵盖安装、首次研究、后端、前端、Agent 与贡献流程。

The core idea is simple: one strategy project has one assembled Python runtime
module, authored as `strategy.py` plus individual `factors/*.py` units.
The parameter forms, factor editor, signal model, event logic, execution policy,
Codex edits, tests, previews, and backtests all modify or invoke that same source.

## Quick start

For student installations on macOS or Windows, follow
[Local deployment](docs/07_LOCAL_DEPLOYMENT.md). The browser workbench uses one
Python process after building the frontend; Electron is optional.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[app,dev,rq]"

.\.venv\Scripts\python.exe -m alphalab.cli dev doctor
```

Build the browser UI and start the local service:

```powershell
npm --prefix apps/desktop ci
npm --prefix apps/desktop run build
.\.venv\Scripts\python.exe scripts/build_conexus_runtime.py
.\.venv\Scripts\python.exe -m alphalab.cli dev serve
```

Open <http://127.0.0.1:8000/app/>. On later launches, only `alphalab dev serve`
is needed with the virtual environment activated. For frontend development use
`npm --prefix apps/desktop run dev:web` alongside the backend.

For the Electron desktop window, run `npm --prefix apps/desktop run dev:desktop`
in a second terminal while the backend is running. The same UI is used by the
browser and desktop; Electron defaults to 125% zoom.

Application code lives in `apps/api` and `apps/desktop`; `alphalab` contains the
reusable research core. AlphaLab's Agent integration is in `integrations/conexus`,
and the verified Conexus dependency is in `vendor/conexus`. Generated programs
live in `build`, user data in `data`, and distributable packages in `artifacts`.
See [Architecture](docs/01_ARCHITECTURE.md) for the directory responsibilities.

The workstation exposes the single `runtime` data profile. Configure
`RQ_USER`, `RQ_PASSWORD`, and `RQ_HOST` in an untracked `.env`, then use the
Data Workbench to populate the local runtime store. Bundled sample data remains
available only to internal tests and examples.

Before starting the workbench, the read-only doctor reports local Python,
Node, RQData, editor tools, runtime datasets, and port readiness without
printing credential values:

```powershell
alphalab dev doctor
```

## Runtime RQ data

The Data Workbench and CLI use the same visible Python recipe and the same
partitioned store below ignored `data/runtime/`. Inspect a plan before a large
request, then run and validate the selected scope:

```powershell
alphalab data templates
alphalab data plan rq --template rq.etf_daily
alphalab data sync rq --template rq.a_share_research
alphalab data validate --datasets rq.bars,rq.paused,rq.is_st --fail-on-gap
```

Daily RQ requests are split by symbols and dates. Each completed chunk is
persisted immediately; rerunning resumes from per-symbol/per-field/per-index
watermarks with an overlap refresh. Runtime research data includes adjusted
bars with `raw_open`/`raw_high`/`raw_low`/`raw_close`, suspensions, ST state,
daily factors, historical index
membership, PIT financials, and attribution factors. See
[Data operations](docs/04_DATA_OPERATIONS.md) for exact templates and schemas.

## Strategy SDK v1

Strategy source imports `alphalab.sdk.v1` and registers one universe, any number
of factors, one scheduled signal, one portfolio function, optional stateful
event handlers, and one execution policy:

```python
from alphalab.sdk.v1 import (
    ExecutionPolicy, Monthly, PortfolioDecision, SignalResult, UniverseResult,
    execution, execution_data_fill, factor, portfolio, signal, universe,
)

SDK_VERSION = 1

@universe(id="all_available")
def all_available(context):
    return UniverseResult(symbols=context.universe)

@factor(id="momentum_20d", inputs=["close"])
def momentum_20d(context, *, window: int = 20):
    close = context.history("close", window=window + 1)
    return close.iloc[-1] / close.iloc[0] - 1

@signal(id="top1", schedule=Monthly.last_trading_day(at="close"))
def top1(context, state, *, top_n: int = 1):
    scores = context.factor("momentum_20d", window=20).dropna()
    selected = list(scores.nlargest(top_n).index)
    return SignalResult(selected=selected, scores=scores, state=state)

@portfolio(id="full_weight")
def full_weight(context, signal, state):
    return PortfolioDecision(
        target_weights={signal.selected[0]: 1.0} if signal.selected else {},
        state=state,
    )

@execution_data_fill(id="fill_missing_market_state")
def fill_missing_market_state(context, rows):
    # Project-owned Python may fill only missing execution-state values.
    return rows

@execution(id="next_open")
def next_open(context, decision):
    return ExecutionPolicy(activation="next_session_open")
```

Stateful rules such as MA gates, profit locks, month-level re-entry freezes, and
volatility cuts use `@on_event(Event.SESSION_CLOSE, ...)` and the shared JSON
`State`. Custom schedules use `@schedule`. Factors can call other registered
factors through `context.factor(...)`; dependencies are tracked and cycles are
rejected.

## One source, several views

- Project/Data: metadata, data profile, requirements, and `@universe`.
- Factor: edit `@factor` functions, insert data fields/dependencies, run frozen
  snapshot and history evaluations.
- Strategy: edit schedule, signal, portfolio, event handlers, and execution in
  `strategy.py`, or preview the saved strategy.
- Validation: select a project, edit its `validation.py`, run the full event
  backtest using automatically pinned sources, and inspect frozen results.
- Report: read durable Conexus Agent documents backed by frozen Run evidence.
  Reports currently share one publication-wide history.

Recognized form fields edit exact Python syntax nodes with LibCST. Arbitrary
Python remains editable as custom source. There is no expression-to-Python
translation, generated three-stage script, candidate promotion step, or second
Python Lab lifecycle.

## Local Python runtime

AlphaLab directly invokes the current local Python environment in a spawned
child process. It provides timeouts, crash containment, bounded logs, static
warnings, explicit execution confirmation, and strict input/output contracts.
It is not a security sandbox; only run source you trust. Docker is not required
or used by the strategy path.

## Reproducibility and safety

A saved `StrategySourcePackage` freezes the full source, SHA-256, SDK and
validator versions, entrypoint manifest, literal parameters, requirements, and
environment fingerprint. Every preview, factor evaluation, and backtest names
the exact revision and hash it invokes.

The core—not custom source—owns point-in-time filtering, calendar ordering,
symbol/listing checks, validation of known suspension and price-limit fields, positive
volume/amount, participation, cash, fees, fills, rejection events, accounting,
and output/state validation. Project source may implement one bounded
`@execution_data_fill` function for missing state values; a target is not a fill.

## Useful commands

```powershell
python -m pytest tests -q --basetemp=data\pytest
python scripts\check_facade_imports.py
python scripts\build_example_data.py --source-root <local-source>

python scripts\build_conexus_runtime.py
alphalab dev serve
```

Architecture and contribution rules:

- [Architecture](docs/01_ARCHITECTURE.md)
- [Strategy SDK v1 contract](docs/02_STRATEGY_SDK_V1_CONTRACT.md)
- [Development guide](docs/03_DEVELOPMENT_GUIDE.md)
- [Data operations](docs/04_DATA_OPERATIONS.md)
- [Conexus Agent](docs/04_CONEXUS_AGENT.md)
- [Local deployment with Conexus Core](docs/07_LOCAL_DEPLOYMENT.md)

The local deployment includes Conexus Core and its single-user service, with no
Canvas editor or enterprise components. Its source provenance and pending license
status are recorded in [Third-party source](THIRD_PARTY.md).

Local data, SQLite databases, caches, test scratch, and generated frontend
artifacts must stay out of Git. The repository does not push remotely unless
explicitly requested.
