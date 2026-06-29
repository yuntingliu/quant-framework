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

## Adding Data

Do not hard-code a vendor in the framework core. Create an adapter that
implements the relevant provider protocol, then register it:

```python
from alphalab import DataEngine

engine = DataEngine()
engine.register_market("my_source", MyMarketProvider(), default=True)
```

Dashboard vendor pages should stay present as GUI slots, but they must remain
mapped to disabled placeholders until a separate adapter/plugin package owns the
real connection.

## Adding Strategies

Generic templates live in `alphalab/strategies/`. A strategy is data plus YAML;
add Python only when the framework needs a new reusable behavior.

## Quality Gate

Use the smallest useful gate first:

```powershell
python -m pytest tests/contracts tests/dataio tests/strategy tests/dashboard -q
python scripts/check_facade_imports.py
npm --prefix dashboard/frontend run build
```

When a backtest produces unusually strong results, inspect alignment and
lookahead risk before expanding the feature.
