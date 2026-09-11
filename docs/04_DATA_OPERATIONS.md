# Runtime Data Operations

## Profiles

`runtime` is the only application-facing profile and reads local RQ partitions
below `ALPHALAB_RUNTIME_DIR` (default `data/runtime`). Set an external runtime
path in the process environment before starting Python. The tracked example
files remain internal test fixtures;
the application never exposes or falls back to them as an active data profile.

Runtime datasets are:

| Dataset | Primary key | Partition |
| --- | --- | --- |
| `rq.instruments` | `snapshot_date, symbol` | snapshot date |
| `rq.bars` | `date, symbol` | year and month |
| `rq.paused` | `date, symbol` | year and month |
| `rq.is_st` | `date, symbol` | year and month |
| `rq.daily_factors` | `date, symbol, field` | year and month |
| `rq.index_components` | `date, index_symbol, symbol` | year |
| `rq.financials.income` | `symbol, quarter, info_date, if_adjusted` | report year |
| `rq.financials.balance` | `symbol, quarter, info_date, if_adjusted` | report year |
| `canonical.fundamentals` | `symbol, quarter` | available year |
| `runtime.factor_returns` | `date` | year |

Runtime bars contain adjusted `open/high/low/close` for research and
unadjusted `raw_open/raw_high/raw_low/raw_close` for execution rules and market
capitalization. Runtime factor returns use
the same MKT/SMB/HML/MOM/RMW definitions as the bundled sample. RQ's China 1M
yield curve is converted from an annual yield to the monthly `rf` return.

Daily synchronization requests `get_price(fields=None)` and preserves every
field returned by the installed RQ SDK in addition to the canonical OHLCV names
and unadjusted OHLC. Instrument snapshots likewise retain provider metadata
columns, while categorical `board_type` is normalized to a nullable string
across stocks and ETFs before publication. Numeric market and canonical PIT
fields are discovered from Parquet metadata and become safe factor-expression
inputs without a frontend whitelist update.

RQ's `is_suspended` endpoint accepts common stocks only. Stock recipes use that
endpoint directly; ETF, LOF, and index recipes compare `get_price` keys with
`skip_suspended=False` and `True`, marking only provider-filled sessions as
suspended. They never send an empty or non-stock batch to the stock-status API.

The built-in bar recipe declares unadjusted OHLC as required persisted columns.
If an older `rq.bars` partition only contains `raw_close`, its date watermark is
not reused: the next normal synchronization replays the configured interval and
atomically upgrades those partitions. Operators do not need to set `force=true`
for this schema migration.

Submitting a saved built-in recipe also applies known, narrow template
migrations before Python starts. The migration preserves its editable date and
symbol parameters; custom recipe logic is never overwritten.

## RQData templates

The Data Workbench and CLI expose four built-in acquisition templates:

| Template | RQ instrument types | Default datasets |
| --- | --- | --- |
| `rq.a_share_daily` | `CS` | instruments, bars, market state |
| `rq.etf_daily` | `ETF` | instruments, bars, suspensions |
| `rq.exchange_fund_daily` | `ETF`, `LOF`, `INDX` | instruments, bars, suspensions |
| `rq.a_share_research` | `CS` | daily data, market state, daily factors, index membership, PIT fundamentals, attribution factors |

New data-recipe drafts, CLI sync commands without `--template`, direct RQ
engines, and connection checks all use `rq.a_share_research` as the single
default. The lighter daily templates remain explicit choices and never lose
their editable Python source.

A template defines source scope and allowed datasets only. All templates use
the same acquirer, local partitions, quality checks, DataEngine contracts, and
backtest profile. Unsupported combinations fail validation; for example the ETF
daily template cannot silently request A-share PIT financial statements.

Omitting `symbols` means all instruments in the selected template, not the
300-symbol bundled demo manifest. A read-only plan reuses a matching local RQ
instrument snapshot when available; otherwise it marks the count and batch
estimate as pending. The job resolves the exact symbols from live dated
`all_instruments(...)` snapshots before downloading data. An explicit `symbols`
list remains the bounded custom-scope path. Initial sync covers five years;
incremental bars overlap seven calendar days (at least five trading days) and
financials overlap eight quarters.

Each instrument sync writes a dated reference snapshot. Historical research
chooses the latest snapshot no later than the signal date. If history predates
the first stored snapshot, the earliest later snapshot is filtered by listing
intervals and the run is marked with a future-snapshot warning; collecting
snapshots over time is therefore preferable. With no instrument snapshots, the
engine records that it used bar-history membership instead.

Daily RQ calls use at most 200 instruments and 366 calendar days per batch.
Each completed date chunk is written before the next request. Per-symbol
watermarks make newly listed or newly selected instruments backfill from the
requested/listing date while existing instruments use an overlap update.
Daily factors keep separate field watermarks; index membership keeps separate
index watermarks. Index templates store month-end snapshots plus the requested
range endpoints and must not be described as daily membership.

RQ calls use at most 200 stocks and 50 quarters per batch. Financial acquisition
requests `statements="all"` and retains revisions. The canonical transform uses
the earliest original disclosure and exposes `available_date`, preventing a
historical request from reading a later publication.

Writes use a temporary sibling file and atomic replacement. Existing and new
rows are merged by dataset primary key. Empty provider responses are errors and
never overwrite a partition.

Each runtime catalog entry also reports its provider API, research role, and
field policy. Adding another RQ data family starts by registering a dataset
contract and partition policy; factor-facing fields are still discovered from
the physical Parquet schema rather than duplicated in a frontend list.

## Interfaces

Run the CLI with the active checkout's Python environment, for example
`.venv/bin/python -m alphalab.cli` on macOS/Linux or
`.\.venv\Scripts\python.exe -m alphalab.cli` on Windows. `alphalab` below is the
equivalent entrypoint when that environment is activated. Example dates are
historical bounds; choose the interval required by the research request.

CLI:

```powershell
alphalab data catalog
alphalab data status
alphalab data templates
alphalab data plan rq --template rq.etf_daily
alphalab data sync rq --template rq.a_share_daily
alphalab data validate
alphalab data validate --datasets rq.bars,rq.paused --start 2021-01-01 --as-of 2026-08-25 --fail-on-gap
alphalab data jobs
```

FastAPI exposes templates, health, an explicit connection probe, plan, job,
cancel, and validation routes below `/api/data-sync`. The backend uses one
in-process worker and performs no startup sync. Jobs left queued or running
during a restart become `interrupted`.

Large daily validation streams Parquet partitions instead of concatenating the
full market. `--fail-on-gap` checks requested bounds, suspension/ST coverage
against stored bar keys, factor trading-date coverage, and per-index snapshot
freshness. A non-passing CLI report exits non-zero.

`data validate` has no `--symbols` option: it examines the selected datasets in
the local store. A single-symbol sync followed by dataset validation can expose
gaps in other stored symbols. Sync completion, application health, and a dataset
freshness summary are different checks. `/api/health` intentionally does not
scan research data; inspect data health and then validate the relevant bounds.

The Data Workbench keeps symbol selection and the daily market view as its
primary workflow. Local update planning, synchronization, catalog inspection,
bounded queries, job history, and detailed quality reports live under its
collapsed advanced data-management section.

The typed tool registry exposes `data.templates`, `data.catalog`, `data.status`,
`data.plan_sync`, `data.run_sync`, `data.validate`, and `data.query`. The query
tool is bounded to 1,000 rows. The same registry is discoverable and invokable
through `/api/agent/data-tools`; `data.run_sync` additionally requires
`confirm=true` at that bridge. No embedded LLM provider or autonomous planner
is enabled in AlphaLab itself. Under an authorized research request, the
optional Conexus Harness plans and runs the project's visible `recipe.py`
through the canonical project tools, monitors the job, and verifies the needed
fields. Current-user write and Python-execution authorization remains explicit
at that boundary; conversation history alone does not authorize execution.
See [Conexus Agent](04_CONEXUS_AGENT.md) for the maintained research loop.

## Arbitrary Python data sources

RQData is an official adapter, not a closed data boundary. Trusted local Python
can implement the small provider protocols and register them through
`alphalab.data_sdk.v1`. This is useful for CSV/Parquet, an internal database,
another vendor SDK, or a proprietary HTTP service. It is not a second backtest
path: the resulting object is the ordinary `DataEngine` used everywhere else.

See [05_DATA_SDK_V1_CONTRACT.md](05_DATA_SDK_V1_CONTRACT.md) for schemas and a
complete example. User provider code runs as trusted local Python; it is not a
security sandbox, and strategy `Context` still never receives provider handles,
credentials, connections, or file paths.

## Failure Semantics

Missing configuration is `not_configured`. A failed configured connection is
`unavailable`; the latest redacted error is retained in the local operations
database. Missing or invalid runtime data produces explicit API errors, not an
empty successful response. Cancellation is cooperative at provider batch
boundaries.
