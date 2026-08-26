# AlphaLab Barebone

AlphaLab Barebone is a small quant framework for developing factor research,
portfolio signals, backtests, and workstation extensions.

The repository is protocol-first and includes a compact real-data example so a
fresh checkout can inspect data, run strategies, generate signals, and simulate
orders without configuring a vendor connection.

## What Is Included

- `DataEngine` with pluggable market, instrument, fundamental and factor providers.
- `StrategyConfig` YAML templates for generic momentum, value, quality, growth,
  low-volatility and balanced styles.
- `SignalEngine.generate_targets()` and `run_backtest_detailed()` as the shared
  point-in-time parity point for research and workstation previews, with
  next-session execution, explicit cash, costs, slippage, impact and liquidity.
- Registered and safe expression factors with IC/ICIR, quantile, decay,
  turnover, coverage and bootstrap diagnostics.
- `ResultStore` for local SQLite state.
- Broker-neutral execution dataclasses plus paper trading helpers.
- FastAPI + React/Electron Dockview workstation shell using the original
  AlphaLab multi-mode GUI structure.
- A tracked 300-stock, five-year historical sample with point-in-time
  fundamentals, monthly factor returns, and six seeded backtests.
- An RQ-only runtime data control plane with partitioned parquet storage,
  PIT fundamentals, factor returns, quality checks, job records, CLI commands,
  APIs, and typed data tools.
- An optional Conexus Research Agent Harness that can combine the full typed
  data surface into reports, sortable tables, native charts, and active
  workstation components while preserving explicit mutation guardrails.
- Same-universe benchmarks, cost sensitivity, validation splits, bootstrap and
  multiple-testing checks, and explicit
  `research_candidate/watch/weak/invalid` research gates.
- Reproducible backtests and durable Agent reports with data, strategy and Git
  fingerprints plus per-period execution audits.
- A deterministic data-to-paper research workflow with local cash, positions,
  fills, NAV, and confirmed rebalance simulation. Realtime feeds and real
  orders are not configured.

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
  instruments/instruments.parquet     # optional dated listing snapshots
  fundamentals/fundamentals.parquet   # quarter, available_date, symbol, factors
  factors/factor_returns.parquet      # DatetimeIndex, one column per factor return
  app/alphalab.db                     # local SQLite state
  manifest.json                       # provenance, coverage, hashes, caveats
```

The sample is real historical data for development demonstration, not an
unbiased investable universe. Prices end on the manifest cutoff date and must
not be presented as realtime. Immutable parquet hashes are checked by the API.

## Collaborator RQ Setup / 协作者本地 RQ 数据

Each collaborator downloads RQ data to their own computer with their own RQData
account. Credentials and downloaded vendor data must never be committed or
shared. The commands below were verified against the `dev_liu` branch.

每位协作者使用自己的 RQData 账号，把数据下载到自己的电脑。账号、密码和下载的
数据都不会进入 Git；仓库里只共享框架代码。

If the repository has not been downloaded yet:

```powershell
git clone --branch dev_liu --single-branch https://github.com/yuntingliu/quant-framework.git
cd quant-framework
```

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[rq]"
Copy-Item .env.example .env
```

### Linux or macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[rq]"
cp .env.example .env
```

Open the new `.env` file and fill in the connection values supplied with the
collaborator's RQData account:

```dotenv
RQ_USER=
RQ_PASSWORD=
RQ_HOST=
```

Do not copy credentials from the deployed server. Keep `.env` local; it is
already ignored by Git.

This branch connects to RQ directly through `RQ_HOST`. It does not require or
start an SSH tunnel.

Preview the deterministic five-year, 300-symbol plan before making any provider
request, then run the download and validation:

```powershell
# Windows PowerShell
.\.venv\Scripts\alphalab.exe data status
.\.venv\Scripts\alphalab.exe data plan rq
.\.venv\Scripts\alphalab.exe data sync rq
.\.venv\Scripts\alphalab.exe data validate
.\.venv\Scripts\alphalab.exe data jobs
```

```bash
# Linux or macOS
.venv/bin/alphalab data status
.venv/bin/alphalab data plan rq
.venv/bin/alphalab data sync rq
.venv/bin/alphalab data validate
.venv/bin/alphalab data jobs
```

`data sync rq` is the single download command. By default it synchronizes
instruments, daily bars, historical suspension/ST state, daily market-cap/ROE
factors, monthly major-index components, point-in-time financial statements,
canonical fundamentals, and factor returns. Re-running it performs an
incremental update; use `data plan rq` first to inspect the dates and scope
without contacting RQ.

For the full historical A-share research cache, use the explicit large-job mode:

```powershell
alphalab data plan rq --datasets instruments,bars,market-state --universe all --start 2005-01-04 --end YYYY-MM-DD --force
alphalab data sync rq --datasets instruments,bars,market-state --universe all --start 2005-01-04 --end YYYY-MM-DD --force
alphalab data validate --datasets rq.instruments,rq.bars,rq.paused,rq.is_st,rq.daily_factors,rq.index_components --start 2005-01-04 --as-of YYYY-MM-DD --fail-on-gap
```

Replace `YYYY-MM-DD` with the latest completed A-share trading day.
If the initial full pull is interrupted after writing partitions, rerun the sync
without `--force` to continue from the persisted watermark overlap.
Archive an untrusted `data/runtime/` cache first, or use an empty
`ALPHALAB_RUNTIME_DIR`; `--force` does not delete unrelated legacy rows.

All downloaded files, checksums, checkpoints, and task state are written below
`data/runtime/`, which is ignored by Git. The checked-in example bundle is
never overwritten. `data status` may report `missing` before the first sync;
after a successful default sync, `data validate` should report `passed` for all ten
runtime datasets.

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
disclosures. Runtime factors use the prior-month characteristics and subsequent
monthly returns; the `rf` column comes from the RQ China 1M yield curve,
converted from annual yield to monthly return. It does not provide realtime
quotes or order execution. Network forwarding, machine details, private keys,
and downloaded vendor data are not part of this repository.

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

### Optional Research Agent

The framework does not require or bundle an LLM planner. If a separately
installed Conexus Web Host exposes a compatible published Research Harness,
its local origin and publication slug can be supplied as process variables:

```powershell
$env:CONEXUS_WEB_ORIGIN = "http://127.0.0.1:3000"
$env:CONEXUS_PUBLICATION_SLUG = "alphalab-research-agent"
```

Without that service, the Research Agent panel reports `not_configured` while
all deterministic Demo/DataIO/backtest/paper workflows remain available. See
[`docs/04_CONEXUS_AGENT.md`](docs/04_CONEXUS_AGENT.md) for the optional contract.
The sanitized, reviewable Harness source is included under
[`integrations/conexus/alphalab-research-agent`](integrations/conexus/alphalab-research-agent);
generated Canvas, publication, conversation, and run state remain ignored.
The published Harness uses 14 typed AlphaLab tools, including all six canonical
data operations, and can produce Markdown reports, bounded data tables, line,
bar, area, scatter, and pie charts, plus request-bound commands for active
Dockview widgets. The Agent may autonomously synchronize required RQ runtime
data; backtest execution and signal generation remain explicitly requested
operations. It has no real-order or arbitrary-shell tool.

Open [http://localhost:5173](http://localhost:5173). API docs are available at
[http://localhost:8000/docs](http://localhost:8000/docs).

The GUI preserves the AlphaLab workstation layout, command palette, right rail,
mode sidebar, and widget catalog. Its default Home, Data, Research, and Paper
layouts use real backend contracts. Optional vendor and live-trading panels are
disabled extension points. Data Center exposes explicit Demo and Local RQ
profiles, sync planning, background jobs, coverage, and validation. It never
starts a heavy sync during application startup.

Built-in strategy YAML is immutable. Clone a template in Strategy Editor to
create a local version below ignored runtime data. Backtest Workbench can run a
single backtest or the deterministic six-step research workflow:

```text
data status -> strategy validation -> backtest -> robustness gate
            -> signal -> paper risk preview
```

The workflow never confirms paper fills and never submits a broker order.
`research_candidate` means only that the configured research thresholds passed;
it is not an approval for live trading.

## Validation

```powershell
python -m pytest tests -q
python scripts/check_facade_imports.py
npm --prefix dashboard/frontend run build
```

See `docs/04_DATA_OPERATIONS.md` for runtime schemas, failure behavior, and API
contracts.
