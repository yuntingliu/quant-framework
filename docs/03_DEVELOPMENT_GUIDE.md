# AlphaLab Barebone Development Guide

## Local Workflow

Run commands from the repository root:

```powershell
python -m pytest tests/contracts -q
python scripts/check_facade_imports.py
```

Start the workstation:

```powershell
python -m uvicorn dashboard.backend.main:app --reload --port 8000
npm --prefix dashboard/frontend run dev:web
```

## Runtime Data Workflow

The repository ships a checked-in example bundle. Its immutable files and
provenance are declared in `data/manifest.json`. RQ downloads, runtime
partitions, task records, quality reports, and caches remain ignored below
`data/runtime/`.

Use the same service through CLI or FastAPI. Always preview first:

```powershell
alphalab data plan rq
alphalab data sync rq --datasets instruments,bars,fundamentals,factors
alphalab data validate
```

Sync code may write only through `RuntimeStore`; empty responses must never
replace existing partitions. Provider-specific acquisition belongs in
`rq_sync.py`, generic schemas and storage remain provider-neutral, and
`available_date` must be enforced at historical query boundaries.

Rebuild the maintained sample with:

```powershell
python scripts\build_example_data.py `
  --source-root C:\Users\LYT\Documents\GitHub\quant-framework-factors
```

The builder reads the full workspace without modifying it, fixes the universe
before the sample period, adjusts OHLC for corporate actions, uses first-release
financial statements, seeds six backtests and signals, and writes only the
canonical barebone files.

Do not hard-code a vendor in the framework core. Create an adapter that
implements the relevant provider protocol, then register it:

```python
from alphalab import DataEngine

engine = DataEngine()
engine.register_market("my_source", MyMarketProvider(), default=True)
```

The maintained RQ information adapter is optional:

```python
from alphalab import create_rq_engine_from_env

engine = create_rq_engine_from_env()
```

Its credentials stay in the ignored local `.env`; tests must inject a fake RQ
module and must never require a live vendor connection.

For repeatable research, sync first and use the partitioned engine:

```python
from alphalab import create_runtime_engine

engine = create_runtime_engine()
```

The typed registry in `alphalab.tools` is the canonical agent-facing data
surface. The `/api/agent/data-tools` bridge describes and invokes that same
registry; do not duplicate tool behavior in a separate agent adapter. Tools
return bounded rows, counts, statuses, and references rather than large
serialized DataFrames. Mutating tools require an explicit `confirm=true` at the
bridge boundary. The embedded LLM planner remains deliberately unconfigured;
the optional Conexus Harness supplies external planning.

Dashboard vendor pages should stay present as GUI slots, but they must remain
mapped to disabled placeholders until a separate adapter/plugin package owns the
real connection.

## Adding Strategies

Generic templates live in `alphalab/strategies/`. A strategy is data plus YAML;
add Python only when the framework needs a new reusable behavior.

Package templates are immutable through the API. Dashboard edits are stored as
local YAML below `data/runtime/app/strategies`. Strategy ids and YAML `name`
must match. Validate every local definition before a backtest.

Every persisted backtest can produce a same-universe equal-weight benchmark and
a robustness report. The report checks data/weight integrity, calendar and
rolling outcomes, turnover, concentration, and 10/20/50 bps cost assumptions.
Its labels are research triage labels, not trading authorization.

The deterministic research runner persists each step and supports cancel,
retry, and restart interruption states. It may create a signal and paper
rebalance preview, but only `/api/paper/rebalance/execute` with `confirm=true`
can change the local paper account.

## Quality Gate

Use the smallest useful gate first:

```powershell
python -m pytest tests/contracts tests/dataio tests/strategy tests/dashboard -q
python scripts/check_facade_imports.py
npm --prefix dashboard/frontend run build
```

When a backtest produces unusually strong results, inspect alignment and
lookahead risk before expanding the feature.
