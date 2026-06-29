# AlphaLab Barebone

AlphaLab Barebone is a small quant framework for developing factor research,
portfolio signals, backtests, and workstation extensions.

The repository is intentionally protocol-first. It ships the core abstractions
and a generic local parquet layout, while real data vendors and broker adapters
belong in separate extension packages or project-local modules.

## What Is Included

- `DataEngine` with pluggable market, fundamental and factor providers.
- `StrategyConfig` YAML templates for generic momentum, value, quality, growth,
  low-volatility and balanced styles.
- `SignalEngine.generate_targets()` and `run_backtest()` as the shared parity
  point for research and workstation previews.
- `ResultStore` for local SQLite state.
- Broker-neutral execution dataclasses plus paper trading helpers.
- FastAPI + React/Electron Dockview workstation shell using the original
  AlphaLab multi-mode GUI structure. Concrete vendor/broker panels are kept as
  visible adapter slots and render disabled placeholders until plugins are
  installed.

## Quick Start

```powershell
cd C:\Users\LYT\Documents\GitHub\quant-framework
python -m pytest tests/contracts -q
python -c "import alphalab; print(alphalab.__version__)"
```

Optional editable install:

```powershell
pip install -e ".[dev,dashboard]"
```

## Local Data Layout

The default engine reads a generic local layout under `data/`:

```text
data/
  market/bars.parquet                 # date, symbol, open, high, low, close, volume, amount?
  fundamentals/fundamentals.parquet   # quarter, symbol, ep, bp, roe, ...
  factors/factor_returns.parquet      # DatetimeIndex, one column per factor return
  app/alphalab.db                     # local SQLite state
```

Generated data is ignored by git. To use a real data source, implement the
provider protocols in `alphalab.dataio.providers.protocol` and register the
adapter with `DataEngine`.

## Dashboard

```powershell
python -m uvicorn dashboard.backend.main:app --reload --port 8000
npm --prefix dashboard/frontend run dev:web
```

Open [http://localhost:5173](http://localhost:5173). API docs are available at
[http://localhost:8000/docs](http://localhost:8000/docs).

The GUI intentionally preserves the AlphaLab workstation layout, command
palette, right rail, mode sidebar, and widget catalog. Legacy data/vendor/live
trading panels do not connect to bundled implementations; they show
`adapter disabled` placeholders and are activation points for later plugins.

## Validation

```powershell
python -m pytest tests -q
python scripts/check_facade_imports.py
npm --prefix dashboard/frontend run build
```
