# Conexus Research Agent

The published AlphaLab Agent operates on the same four Python authoring
boundaries as the workbenches:

```text
research project/
├── recipe.py
├── factors/<factor_id>.py
├── strategy.py
└── validation.py
```

There is one RQ-backed runtime data profile and one event-backtest path. The
Agent has no demo-profile selector, expression strategy runtime, legacy
pipeline, Python Lab, shell, paper-execution, broker, or deployment tool.

## Research loop

The current tool contract lets the Agent:

1. search the public web for factor papers or current research leads, retaining
   titles, URLs, and retrieval dates while treating snippets as untrusted leads;
2. inspect compact runtime coverage and project/task summaries, then request
   only explicitly needed project files;
3. create every user-requested new strategy as a fresh project by atomically
   submitting its complete `strategy.py` and all factor units;
4. read, edit, parameterize, plan, and submit the project's exact `recipe.py`;
5. monitor resumable data jobs and validate/query published datasets;
6. add or edit one complete factor source unit and evaluate that registered
   function as a snapshot or history;
7. edit and save `strategy.py` through exact-source or CST-aware operations;
8. read or edit `validation.py`, whose saved package is pinned by the next Run;
9. preview the saved strategy, run complete event backtests, analyze
   performance, attribution, robustness and signals, compare candidates, and
   create or update a durable report Document.

These capabilities are exposed through eleven AlphaLab intent-level Tool nodes
plus the native Conexus node-capability tools rather than one tool per backend
endpoint. The Agent discovers connected nodes with `find`/`observe` and invokes
each AlphaLab node with `use(node_id, "tool.invoke", input)`. Each mutable or
executable AlphaLab tool uses a closed `action` enum, so consolidation does not
weaken write, delete, or trusted-local Python confirmation boundaries:

```text
alphalab_get_workspace_context
alphalab_research_project
alphalab_data_recipe
alphalab_data_sync_job
alphalab_data_query
alphalab_strategy_source
alphalab_factor_evaluation
alphalab_strategy_preview
alphalab_validation_source
alphalab_backtest
alphalab_backtest_analysis

find / observe / create / edit / use / request_user_input / complete
```

Web Search is a runtime-managed node discovered with `find` and invoked through
its declared capability with `use`; it is not a legacy direct Agent tool name.
Search snippets are leads, not verified factor definitions. Before implementing
a discovered factor, the Agent checks the original or authoritative source,
records its URL and retrieval date, and verifies formula, lag/point-in-time
requirements and runtime data coverage. Shell and browser-control capabilities
remain unavailable.

Strategy saves automatically run the existing probes and record an immutable
internal source package. The Agent never exposes a separate save-revision
operation. Factor evaluation, strategy preview, and backtests internally pin
and verify the current strategy hash; backtests also pin and verify the current
validation hash.

Agent-facing read results are deliberately bounded. Market-data queries return
the full row count with head/tail samples, factor evaluations return coverage,
distribution statistics, and ranked symbol samples for each observation date,
and strategy/backtest analysis returns metrics plus compact time-series and
execution summaries. The canonical runtime store and frozen BacktestRun retain
all rows; only the model context is compacted. This prevents a multi-year,
cross-sectional study from exhausting the model context while preserving the
counts, dates, stable failures, and evidence required for an auditable report.
Backtest submission immediately returns only a task ID and starts the complete
background run without a separate full-range preflight. Runtime preparation is
part of that run, and the strict event engine reports bounded warnings for
excluded candidates. The default frozen-result read has
metrics, counts, error summary, and small head/tail samples. Daily and execution
events require a separate explicit paginated request.
Data-sync task reads likewise contain only status/progress, bounded request
counts, and safe error fields; stored recipe source and worker tracebacks are
not Agent output.

When an authorized research run encounters a concrete missing dataset or field,
the Agent uses the current project's Data Workbench instead of accepting an
invalid zero-result run. It plans and runs the saved `recipe.py` with the
narrowest needed symbols, fields, and dates, waits for the sync job to reach a
terminal state, and verifies the missing field with a bounded data query. A
catalog-level `ready` status is only a summary and does not override specific
missing-field evidence from a query, structured backtest error, warning, or
execution record. After a successful sync the Agent retries the original
backtest at most once. If synchronization fails or the bounded verification
still shows the field missing, it stops the loop and reports the safe structured
error rather than describing zero trades or a 0% return as strategy performance.
This visible Data Workbench acquisition step is not a hidden runtime fetch or a
separate full-range backtest preflight.

Partial full-universe suspension or price-limit coverage does not block or
preflight the run. The strict event engine conservatively excludes incomplete
candidates and rejects affected trades, then exposes a bounded
`PARTIAL_MARKET_STATE` warning in the result. `INSUFFICIENT_MARKET_STATE` is
reserved for an actual runtime-data failure, such as wholly unavailable
required bar coverage, rather than an advance scan of all candidates.

For a new ordinary stock-selection strategy, the Agent defaults to the complete
point-in-time `context.universe`; it must not invent a small hard-coded test
pool. Only an explicitly requested fixed, index, sector, ETF, or other scoped
universe narrows that default. A recipe-owned fixed universe intersects its
constant pool with `context.universe` at each point in time and must not emit
not-yet-listed symbols merely because they appear in the recipe's end-date
snapshot.

## Confirmation boundary

Read-only inspection needs no confirmation. A current user request must
explicitly authorize project/source writes before a tool sends
`confirm_write=true`; deletion separately requires `confirm_delete=true`.
Executing `recipe.py`, strategy probes, factor evaluations, previews, or
backtests is trusted local Python and requires an explicit execution or full
research request before `confirm_python_execution=true` is sent.

The child processes are timeout/crash contained but are not security sandboxes.
Source, metadata, logs, market data, and workspace context are untrusted data,
not Agent instructions.

Server-shared conversation history and its bounded research checkpoint may be
used to resolve conversational references, but they never authorize writes,
deletion, or Python execution. The AlphaLab backend merges stale server copies
by conversation and message ID; the browser has no local history fallback or
migration path. Live project, data, job, and Run facts are
rechecked only when the current request depends on them. The dedicated
`/api/agent/context` snapshot scans the runtime catalog once and briefly caches
that result instead of issuing overlapping full-catalog and symbol requests.

## Workspace and reports

The only modes are:

```text
project data factor strategy validation report
```

Rich research reports are native Conexus Document nodes. All AlphaLab reports
currently share one publication-level history and are not partitioned by
project. Before writing, the Agent lists the history and observes plausible
matches. It updates the matching Document and creates a new one only for a
genuinely new research subject. Request IDs, hashes, job IDs, and other audit
details are not part of report Markdown. Historical BacktestRuns always use
their frozen strategy and validation snapshots.

The Decision Notebook, Workspace Result, and Workspace Commands are written in
one atomic `edit` batch; the Agent then calls `complete` with the final summary.
A Document descriptor always carries version, current request ID, kind, report
node ID, title, and sources. Separate edits may change report Documents but may
not partially update the three Harness output nodes. The frontend rejects the
whole command batch when `open_result` does not match a valid descriptor from
the same request.

Backtests are asynchronous at the Agent boundary: `action=run` validates and
pins the current strategy/validation packages, submits one job, and returns its
ID immediately. The Agent then uses `action=job` until a terminal state before
reading compact analysis. A long research run therefore cannot be mistaken for
a failed tool call merely because it exceeds the orchestration request window.

The hosted AlphaLab publication uses Conexus enterprise service identity with
publisher-funded billing. The workstation browser never authenticates to
Conexus directly: the AlphaLab backend uses a dedicated server-side
publication-workspace token scoped to the AlphaLab publication for the
manifest, Run creation, and durable workspace. This also prevents the
workstation's Basic Authorization header from being mistaken for a Conexus
enterprise credential. The token is never sent to the browser. Terminal Runs
identify persisted output mutations through `nodeChanges`; they do not embed
node payloads. The frontend reads
`GET /api/public/harnesses/:slug/workspace` and resolves only nodes whose
`updatedByRunId` matches that Run before consuming commands or results. The
same workspace endpoint supplies the durable report history.
AlphaLab stores the shared conversation transcript separately on its server;
only bounded context is sent into an individual Harness Run.

The frontend treats the server Run as authoritative and combines SSE delivery
with an authenticated two-second Run snapshot reconciliation loop. Stream EOF
flushes a final event even when it has no trailing blank line. Once either
channel observes a terminal state, later stale active snapshots cannot regress
the UI to `queued` or `running`; final reconciliation closes the response and
any remaining tool spinners.

Ordinary explanations, history restatements, and read-only questions do not
modify nodes. Report writes follow the native Document contract and are made
only when the current request actually produces or revises a quantitative
report.

## Registration and publication

Local deployment uses the included Conexus Core and `@conexus/local-host`:

```powershell
python scripts\build_conexus_runtime.py
alphalab dev serve
```

The launcher assembles the research graph and starts the local service itself.
It needs no Canvas registration or enterprise publication. Configure a model
endpoint using [Local deployment](07_LOCAL_DEPLOYMENT.md). The core source is
pinned under `integrations/conexus/core`; execution, graph mutations and durable
reports remain Conexus-owned. AlphaLab's structured workspace payload schemas
live in `workspace-output.schema.json`, not on a Harness exposure.

The following administration helpers apply only to an existing private Conexus
Web/Canvas deployment used with `--agent remote`. Its source and UI are not
included in the local runtime.

Refresh the local canvas and staged bundle with:

```powershell
node scripts\register_conexus_research_harness.mjs
```

Publish the registered Harness with:

```powershell
node scripts\host_conexus_research_harness.mjs
```

Registration removes obsolete pipeline, Lab, request-template sync, paper, and
manual-revision Agent nodes. It also removes the former endpoint-shaped
AlphaLab tool nodes rather than keeping compatibility aliases.
