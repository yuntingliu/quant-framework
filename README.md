# AlphaLab Barebone

AlphaLab Barebone is a small quant framework for developing factor research,
portfolio signals, backtests, and workstation extensions.

The repository is protocol-first and includes a compact real-data example so a
fresh checkout can inspect data, run strategies, generate signals, and simulate
orders without configuring a vendor connection.

## What Is Included

- `DataEngine` with pluggable market, fundamental and factor providers.
- `StrategyConfig` YAML templates for generic momentum, value, quality, growth,
  low-volatility and balanced styles.
- `SignalEngine.generate_targets()` and `run_backtest()` as the shared parity
  point for research and workstation previews.
- `ResultStore` for local SQLite state.
- Broker-neutral execution dataclasses plus paper trading helpers.
- FastAPI + React/Electron Dockview workstation shell using the original
  AlphaLab multi-mode GUI structure.
- A tracked 300-stock, five-year historical sample with point-in-time
  fundamentals, monthly factor returns, and six seeded backtests.
- An RQ-only runtime data control plane with partitioned parquet storage,
  quality checks, job records, CLI commands, APIs, and typed data tools.
- Paper-only signal and order APIs. Realtime feeds and real orders are not
  configured.

## Quick Start

```powershell
cd C:\Users\LYT\Documents\GitHub\quant-framework
pip install -e ".[dev,dashboard,rq]"
python -m pytest tests -q
python -c "import alphalab; print(alphalab.__version__)"
```

## Bundled Data

The default engine reads a generic local layout under `data/`:

```text
data/
  market/bars.parquet                 # date, symbol, open, high, low, close, volume, amount?
  fundamentals/fundamentals.parquet   # quarter, available_date, symbol, factors
  factors/factor_returns.parquet      # DatetimeIndex, one column per factor return
  app/alphalab.db                     # local SQLite state
  manifest.json                       # provenance, coverage, hashes, caveats
```

The sample is real historical data for development demonstration, not an
unbiased investable universe. Prices end on the manifest cutoff date and must
not be presented as realtime. Immutable parquet hashes are checked by the API.

## RQ Runtime Data

Install the optional RQ client and put the three connection values in the
gitignored project `.env`:

```powershell
pip install -e ".[rq]"
```

```dotenv
RQ_USER=
RQ_PASSWORD=
RQ_HOST=
```

Preview the deterministic five-year, 300-symbol plan before making a provider
request:

```powershell
alphalab data status
alphalab data plan rq
alphalab data sync rq --datasets instruments,bars,fundamentals
alphalab data validate
alphalab data jobs
```

All downloaded files, checksums, checkpoints, and task state are written below
`data/runtime/`, which is ignored by Git. The checked-in example bundle is
never overwritten.

After a successful sync, use the runtime engine explicitly:

```python
from alphalab import create_runtime_engine

engine = create_runtime_engine()
bars = engine.get_bars(["000001.SZ"], "2025-01-01", "2025-01-31")
fundamentals = engine.get_fundamentals(
    ["000001.SZ"],
    ["revenue", "net_profit"],
    "2024q1",
    "2024q4",
    asof_date="2025-03-31",
)
```

`create_rq_engine_from_env()` remains available for one-off direct queries. The
runtime synchronizer stores adjusted research OHLC with unadjusted `raw_close`,
retains PIT statement revisions, and derives canonical fundamentals from first
disclosures. It does not provide realtime quotes or order execution. Network
forwarding, machine details, private keys, and downloaded vendor data are not
part of this repository.

Maintainers can rebuild the sample from the full local research workspace:

```powershell
python scripts\build_example_data.py `
  --source-root C:\Users\LYT\Documents\GitHub\quant-framework-factors
```

## Dashboard

```powershell
python -m uvicorn dashboard.backend.main:app --reload --port 8000
npm --prefix dashboard/frontend run dev:web
```

Open [http://localhost:5173](http://localhost:5173). API docs are available at
[http://localhost:8000/docs](http://localhost:8000/docs).

The GUI preserves the AlphaLab workstation layout, command palette, right rail,
mode sidebar, and widget catalog. Its default Home, Data, Research, and Paper
layouts use real backend contracts. Optional vendor and live-trading panels are
disabled extension points. Data Center exposes explicit Demo and Local RQ
profiles, sync planning, background jobs, coverage, and validation. It never
starts a heavy sync during application startup.

## Validation

```powershell
python -m pytest tests -q
python scripts/check_facade_imports.py
npm --prefix dashboard/frontend run build
```

See `docs/04_DATA_OPERATIONS.md` for runtime schemas, failure behavior, and API
contracts.
