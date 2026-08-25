# AlphaLab Barebone

AlphaLab is a local quantitative-research framework with provider-backed data,
a unified Python strategy SDK, point-in-time factor evaluation, a stateful daily
event backtester, immutable source revisions, a FastAPI/React workstation, and
an optional Conexus research Agent.

The core idea is simple: one strategy project has one complete Python module.
The parameter forms, factor editor, signal model, event logic, execution policy,
Codex edits, tests, previews, and backtests all modify or invoke that same source.

## Quick start

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dashboard,dev]"

python -m uvicorn dashboard.backend.main:app --reload --port 8000
```

In another terminal:

```powershell
cd dashboard\frontend
npm install
npm run dev
```

The bundled `demo` profile works offline. Configure `RQ_USER`, `RQ_PASSWORD`,
and `RQ_HOST` in an untracked `.env` to use the `runtime` profile.

## Strategy SDK v1

Strategy source imports `alphalab.sdk.v1` and registers one universe, any number
of factors, one scheduled signal, one portfolio function, optional stateful
event handlers, and one execution policy:

```python
from alphalab.sdk.v1 import (
    ExecutionPolicy, Monthly, PortfolioDecision, SignalResult, UniverseResult,
    execution, factor, portfolio, signal, universe,
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
- Strategy: edit schedule, signal, portfolio, event handlers, execution, or the
  full module.
- Validation: select an immutable revision, preview it, run the full event
  backtest, and inspect frozen results.
- Report: persist Agent documents and evidence tied to a Run.

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
symbol/listing checks, suspension and explicit price-limit fields, positive
volume/amount, participation, cash, fees, fills, rejection events, accounting,
and output/state validation. A target is not a fill.

## Useful commands

```powershell
python -m pytest tests -q --basetemp=data\pytest
python scripts\check_facade_imports.py
python scripts\build_example_data.py --source-root <local-source>

node scripts\register_conexus_research_harness.mjs
```

Architecture and contribution rules:

- [Architecture](docs/01_ARCHITECTURE.md)
- [Strategy SDK v1 contract](docs/02_STRATEGY_SDK_V1_CONTRACT.md)
- [Development guide](docs/03_DEVELOPMENT_GUIDE.md)
- [Data operations](docs/04_DATA_OPERATIONS.md)
- [Conexus Agent](docs/04_CONEXUS_AGENT.md)

Local data, SQLite databases, caches, test scratch, and generated frontend
artifacts must stay out of Git. The repository does not push remotely unless
explicitly requested.
