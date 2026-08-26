# Runtime Data Operations

## Profiles

`demo` is the default and reads the tracked example files. `runtime` reads only
local RQ partitions below `data/runtime`. The application never falls back from
an incomplete runtime profile to demo data.

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
unadjusted `raw_close` for market capitalization. Runtime factor returns use
the same MKT/SMB/HML/MOM/RMW definitions as the bundled sample. RQ's China 1M
yield curve is converted from an annual yield to the monthly `rf` return.

## Synchronization

The default universe is the 300 symbols recorded in the tracked manifest. A
request may supply another explicit list or use `--universe all`. The all-share
mode resolves every common stock whose listing interval overlaps the requested
range from an RQ instruments snapshot; it does not use today's membership as a
historical universe. Initial sync covers five years unless `--start` is supplied.
Incremental bars overlap seven calendar days, market state overlaps one day,
and financials overlap eight quarters.

Each instrument sync writes a dated reference snapshot. Historical research
chooses the latest snapshot no later than the signal date. If history predates
the first stored snapshot, the earliest later snapshot is filtered by listing
intervals and the run is marked with a future-snapshot warning; collecting
snapshots over time is therefore preferable. With no instrument snapshots, the
engine records that it used bar-history membership instead.

RQ calls use at most 200 stocks and 50 quarters per batch. Financial acquisition
requests `statements="all"` and retains revisions. The canonical transform uses
the earliest original disclosure and exposes `available_date`, preventing a
historical request from reading a later publication.

Writes use a temporary sibling file and atomic replacement. Existing and new
rows are merged by dataset primary key. Empty provider responses are errors and
never overwrite a partition.

Daily bars, suspension/ST state, and daily factors are requested in date-major
chunks. Each complete date chunk is written and checkpointed before the next
provider call. A failed rerun therefore resumes from the persisted watermark
with a small overlap instead of restarting the full history. The default state
factors are `market_cap` and `roe`; the default component snapshots are monthly
for CSI 300, CSI 500, and CSI 1000.

RQ access on this branch is direct. Only `RQ_USER`, `RQ_PASSWORD`, and `RQ_HOST`
are read from the local environment. No SSH tunnel, jump-host, or machine-specific
network configuration belongs in this repository.

## Interfaces

CLI:

```powershell
alphalab data catalog
alphalab data status
alphalab data plan rq
alphalab data sync rq
alphalab data validate
alphalab data jobs
```

Full A-share daily research cache from 2005:

```powershell
alphalab data plan rq --datasets instruments,bars,market-state --universe all --start 2005-01-04 --end YYYY-MM-DD --force
alphalab data sync rq --datasets instruments,bars,market-state --universe all --start 2005-01-04 --end YYYY-MM-DD --force
alphalab data validate --datasets rq.instruments,rq.bars,rq.paused,rq.is_st,rq.daily_factors,rq.index_components --start 2005-01-04 --as-of YYYY-MM-DD --fail-on-gap
```

The initial clean pull uses `--force` so an existing recent watermark cannot hide
older gaps. If a long pull fails after some partitions are written, rerun without
`--force` to resume from the persisted watermark overlap.
For an untrusted old cache, archive `data/runtime/` first or set
`ALPHALAB_RUNTIME_DIR` to an empty directory. `--force` re-fetches the requested
range but deliberately does not delete unrelated legacy rows.

Always inspect the plan before the full-universe command. The plan is local and
does not contact RQ; when no instruments snapshot exists, its symbol count is an
explicit estimate and the exact listing-overlap universe is resolved at run time.
Replace `YYYY-MM-DD` with the latest completed A-share trading day.

FastAPI exposes health, catalog, plan, job, cancel, and validation routes below
`/api/data-sync`. The backend uses one in-process worker and performs no startup
sync. Jobs left queued or running during a restart become `interrupted`.

The Data Workbench keeps symbol selection and the daily market view as its
primary workflow. Local update planning, synchronization, catalog inspection,
bounded queries, job history, and detailed quality reports live under its
collapsed advanced data-management section.

The typed tool registry exposes `data.catalog`, `data.status`,
`data.plan_sync`, `data.run_sync`, `data.validate`, and `data.query`. The query
tool is bounded to 1,000 rows. The same registry is discoverable and invokable
through `/api/agent/data-tools`; `data.run_sync` additionally requires
`confirm=true` at that bridge. No embedded LLM provider or autonomous planner
is enabled in AlphaLab itself. The optional published Conexus Harness is
authorized to plan and run required RQ synchronization autonomously; its
RQ-sync adapter supplies the bridge assertion internally instead of asking the
user to click Data Workbench.

## Failure Semantics

Missing configuration is `not_configured`. A failed configured connection is
`unavailable`; the latest redacted error is retained in the local operations
database. Missing or invalid runtime data produces explicit API errors, not an
empty successful response. Cancellation is cooperative at provider batch
boundaries.
