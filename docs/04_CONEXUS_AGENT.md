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
2. inspect runtime coverage, project files, source hashes, jobs, and historical
   Runs;
3. create a project by copying `sdk-v1-default`, or clone an existing project;
4. read, edit, parameterize, plan, and submit the project's exact `recipe.py`;
5. monitor resumable data jobs and validate/query published datasets;
6. add or edit one complete factor source unit and evaluate that registered
   function as a snapshot or history;
7. edit and save `strategy.py` through exact-source or CST-aware operations;
8. read or edit `validation.py`, whose saved package is pinned by the next Run;
9. preview the saved strategy, run complete event backtests, analyze
   performance, attribution, robustness and signals, compare candidates, and
   create or update a durable report Document.

These capabilities are exposed through eleven AlphaLab intent-level tools plus
the native Conexus graph tools rather than
one tool per backend endpoint. Each mutable or executable tool uses a closed
`action` enum, so consolidation does not weaken write, delete, or trusted-local
Python confirmation boundaries:

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

web_search

list_nodes / observe_nodes / describe_node_type / create_nodes / update_nodes / delete_node
```

`web_search` is deliberately added to the Agent's explicit allowlist instead
of enabling every Conexus host default. Search snippets are leads, not verified
factor definitions. Before implementing a discovered factor, the Agent checks
the original or authoritative source, records its URL and retrieval date, and
verifies formula, lag/point-in-time requirements, and runtime data coverage.
Shell and browser-control tools remain unavailable.

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
hashes, counts, dates, failures, and evidence required for an auditable report.

For a recipe-owned fixed universe, strategy source intersects that constant
pool with `context.universe` at each point in time. It must not return the whole
runtime instrument master or emit not-yet-listed symbols merely because they
appear in the recipe's end-date snapshot.

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

Browser-local conversation history and its bounded research checkpoint may be
used to resolve conversational references, but they never authorize writes,
deletion, or Python execution. Live project, data, job, and Run facts are
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

Backtests are asynchronous at the Agent boundary: `action=run` validates and
pins the current strategy/validation packages, submits one job, and returns its
ID immediately. The Agent then uses `action=job` until a terminal state before
reading compact analysis. A long research run therefore cannot be mistaken for
a failed tool call merely because it exceeds the orchestration request window.

The public AlphaLab publication remains anonymous and publisher-funded for the
browser. The AlphaLab backend authenticates to the Conexus Web Host with a
dedicated server-side publication-workspace token scoped to the AlphaLab
publication, binding runs and reads to one durable workspace. The token is
never sent to the browser. Conexus returns
the passive nodes changed by the current Run as `workspaceOutputs`, while
`GET /api/public/harnesses/:slug/workspace` supplies the durable report history.
The browser keeps only bounded conversation context and derived report views.

Ordinary explanations, history restatements, and read-only questions do not
modify nodes. Report writes follow the native Document contract and are made
only when the current request actually produces or revises a quantitative
report.

## Registration and publication

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
