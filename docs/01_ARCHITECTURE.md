# AlphaLab Architecture

AlphaLab uses Strategy SDK v1 as its only active strategy-authoring and
execution contract. A project owns trading source (`strategy.py` plus zero or
more `factors/<factor_id>.py` units) and post-run research source
(`validation.py`). Before strategy execution, AlphaLab deterministically
assembles the trading units into one complete
runtime module. Forms, Codex, factor evaluation, previews, event backtests, and
reports pin the relevant source packages and hashes. A normal user save
automatically records an immutable internal source package so later results
remain reproducible without exposing a manual revision workflow.

The normative API is [02_STRATEGY_SDK_V1_CONTRACT.md](02_STRATEGY_SDK_V1_CONTRACT.md).

## System shape

The repository separates application code, reusable research code, vendored
dependencies, generated programs, and persistent user data:

| Directory | Responsibility |
| --- | --- |
| `apps/api/` | FastAPI routes and workbench services; Python import `apps.api` |
| `apps/desktop/` | React workbench and Electron shell, including the Vite configuration |
| `alphalab/` | Public SDKs, acquisition, strategy execution, validation, and persistence |
| `integrations/conexus/` | AlphaLab-owned Agent graph, tools, prompts, and local-host adapter |
| `vendor/conexus/` | Verified upstream source snapshot; changes enter through the export importer |
| `build/web/`, `build/electron/` | Generated browser and desktop entrypoints |
| `build/conexus-source/`, `build/conexus/` | Disposable core compilation workspace and runnable Agent distribution |
| `data/app/`, `data/runtime/`, `data/cache/` | Persistent application state, downloaded research data, and caches |
| `data/backups/` | Local migration backups and archived historical workspaces |
| `artifacts/` | Shareable deployment archives and desktop packages |
| `examples/deployment/` | Optional deployment examples, including the external Conexus host |

The `app` Python extra installs the API dependencies. Python application code is
packaged as `apps.api`; frontend dependencies remain local to `apps/desktop`.
Desktop package identity stays unchanged so existing Electron preferences keep
using the same user-data directory. Directory cleanup never rewrites project
databases, frozen runs, downloaded data, or Agent history.

The local launcher composes the Python workbench with the included Conexus
`local-host`. `vendor/conexus` is a verified upstream source snapshot;
`build/conexus` is its generated Node runtime. Agent execution remains in the
Conexus core; AlphaLab supplies the research graph, Python API tools and optional
direct search adapter. Reports and Run state persist under the local runtime data
directory. The included core has no Canvas UI or enterprise application dependency.
The existing remote service mode is selected explicitly with `--agent remote`.

```text
Data recipe ── execute/sync ── canonical research store
                                      │
Project ─────┐                        │
Factor      ├── strategy.py + factors/*.py ── deterministic assembly ── save/validate/probe
Strategy ───┘                                                                  │
                                                             internal StrategySourcePackage
                                                                              │
                                                         └── daily event backtest ── engine outputs
                                                                                         │
Validation ───── validation.py ── save/validate ── ValidationSourcePackage ──────────────┤
                                                                                         └── frozen Run ── report
```

The six workbench modes are `project`, `data`, `factor`, `strategy`,
`validation`, and `report`. They are views over shared `StrategySdkContext`
state, not independent strategy pipelines.

Quantitative reports are durable Conexus Document nodes in one AlphaLab
publication workspace. They currently form one global report history rather
than being partitioned by research project. The Agent updates a matching
Document and creates one only for a genuinely new research subject; a new chat
turn or backtest does not imply a new report. Frozen BacktestRuns and source
packages remain the immutable evidence underneath it. Hashes and execution
identifiers are not rendered in the quantitative Markdown.

Agent conversations also form one server-persisted history for the AlphaLab
publication. Browsers merge messages by conversation and message ID so stale
tabs cannot replace another browser's turns. Conversation and report history
are never read from or written to browser-local fallback storage.

Active Agent Run state is server-authoritative. The browser consumes the Run
event stream for low-latency updates and independently reconciles the
authenticated Run snapshot every two seconds so a closed or interrupted stream
cannot leave the interface active forever. Terminal states are monotonic in the
browser: a delayed `queued` or `running` snapshot cannot replace `completed`,
`blocked`, `failed`, or `cancelled`. Terminal reconciliation also closes any
still-running tool activity indicators whose final message was not delivered.

## Shared Python editor

Every Python input is rendered by the shared Monaco-based `PythonEditor`.
The Strategy Workbench opens the project's `strategy.py`; it contains universe, selection/scheduling,
portfolio, event-risk, and execution logic, but no `@factor` definitions. The
Factor Workbench opens the selected persistent `factors/<factor_id>.py` unit,
which contains exactly one complete `@factor` function. Factor saves use the
CST-aware function operation and may rename the public ID, which also renames
the source-unit path. The Data Workbench uses the same editor component but
opens the project's separate canonical `recipe.py`, because acquisition source
has a different SDK and lifecycle from strategy source.
The Validation Workbench opens a separate canonical `validation.py`. It owns
post-run metrics, Alpha/Beta attribution, and custom research outputs, while
the event clock, fills, costs, cash, and accounting remain core-owned.

The project database remains authoritative. Before a document is opened, the
backend writes a derived filesystem mirror under `data/runtime/editor/` for
language-server access. Mirrors are ignored by git, never become saved source,
and are never selected as execution input. Saves still use the strategy source
or data-recipe APIs and their existing validation and hashes.

Two fixed local language-server bridges enrich each Python model:

- Pyrefly provides completion, type diagnostics, hover, definition, references,
  and rename against the same local Python environment used by AlphaLab;
- Ruff provides formatting, lint diagnostics, safe fixes, and import actions;
- AlphaLab's Monaco providers add Context methods, live research fields,
  project factors, decorators, parameter contracts, and installed `rqdatac`
  operations, while backend contract diagnostics add SDK-specific markers.

The browser cannot choose a command or executable. The backend accepts only the
fixed `pyrefly lsp` and `ruff server` server IDs, validates document paths and
message sizes, and translates bounded WebSocket JSON messages to LSP stdio
framing. This is editor tooling, not another Python execution route.

## Canonical source

Authoring source is split by responsibility, but there is only one executable
contract. `strategy.py` owns imports, constants, helpers, universe, signal,
portfolio, event, execution-data fill, and execution registrations. Each factor unit owns one
registered factor. `assemble_strategy_source()` inserts factor functions in a
stable order before strategy entrypoints and validates the resulting module.
No source unit is executed independently.

The assembled module declares `SDK_VERSION = 1` and registers:

- exactly one `@universe`;
- zero or more `@factor` and `@schedule` functions;
- exactly one `@signal` and one `@portfolio`;
- at most one `@on_event` handler per event;
- at most one `@execution_data_fill` function;
- exactly one `@execution`.

`alphalab.sdk.v1` is the stable strategy-facing facade. Context objects expose
bounded point-in-time bars, fundamentals, instruments, calendar, prior
decision, actual portfolio, seeded randomness, factor dependencies, and shared
JSON state. They do not expose providers, database connections, file paths, or
brokers.

Recognized parameters and schedules are concrete-syntax-tree projections. A
form edit changes the corresponding Python node. Unrecognized logic remains
visible as custom Python and is never translated to an expression language.

## Source lifecycle

`StrategyRepository` persists four internal objects:

- `strategy_projects`: mutable metadata and the assembled draft artifact;
- `strategy_source_units`: current `strategy.py` and `factors/*.py` authoring
  units;
- `strategy_source_packages`: immutable revisions containing full source,
  source hash, registry manifest, literal parameters, requirements, validator
  version, and environment fingerprint;
- `strategy_source_package_units`: the exact authoring units frozen with each
  package.

The UI exposes one operation: save. Saving a strategy or factor unit assembles
all current units, performs static contract validation, imports the complete
module, runs bounded probes for the full path, every registered factor, and
every registered event handler, then updates the source units and records one
immutable package in the same transaction. A failed cross-file probe changes
neither the current source nor its revision. The assembled runtime supplies an
explicit `alphalab.sdk.v1` public prelude to every unit; other dependencies must
be imported inside the factor function when the factor unit needs them.
Runs pin that internal package; changing any source unit later cannot alter a
historical result. Revision numbers and hashes are audit metadata, not
user-managed authoring controls.

Agent-created projects copy the current read-only `sdk-v1-default` project as
their only starting point. Its `recipe.py`, `strategy.py`, initial
`factors/*.py`, and `validation.py` are copied together. Before the first save,
the Agent may merge structured data requirements, replace registered strategy
functions, instantiate built-in or generic factor templates, and set
recipe/validation parameters; it cannot submit a from-scratch module or select
another hidden project template. Strategy and validation are inserted with the
single initial strategy revision, while the separately stored recipe draft uses
compensating cleanup if its write fails. The default project therefore always
contributes the research data recipe, visible stock-type filter, editable
`@execution_data_fill`, and canonical validation source. After creation, every
copied file remains normal project-owned source that the user or Agent may edit
through the canonical project-file facade. Agent writes accept only the four
project path forms, run the same validation and probes as the workbenches, and
record the same immutable packages; they never expose an arbitrary filesystem
path.

`ValidationRepository` applies the same user-facing save model to
`validation_sources` and immutable `validation_source_packages`. A Run pins
both the strategy package and validation package before it enters the queue.
The default `validation.py` visibly implements headline performance metrics,
CAPM/multi-factor OLS with Newey-West errors, historical tail risk and
concentration, and research-evidence quality. Its keyword-only literal defaults
are CST-projected into the visual panel; custom Python stays intact. Existing
user projects are not rewritten; they receive the new source only through an
explicit migration that creates a new validation revision.

Deployment keeps statistical `research_evidence` separate from the editable
`research_quality` execution verdict. Both consume the same frozen diagnostics;
legacy deployment source can still read `run_diagnostics`. Earlier statistical
outputs named `research_quality` remain readable and executable, but lack the new
`passed` verdict and are therefore unassessed for new execution-quality summaries
until an explicit source migration. Frozen historical runs are never rewritten.

## Runtime and event engine

`SdkExecutionSession` loads the package once in a spawned local Python child
process. Static run data is transferred once; individual operations send only
event-local state. The process boundary provides timeout, crash containment,
bounded stdout/stderr, and structured errors.

This is trusted local execution, not a security sandbox. Source inspection
surfaces capability-sensitive imports and calls, and every execution endpoint
requires explicit `confirm_python_execution=true`. Docker is not used.

After the event engine has produced returns, benchmark returns, weights,
factor returns, executions, settings, and bounded run diagnostics, the pinned `validation.py` runs in a
separate local Python subprocess with a timeout and JSON-output size limit.
`ValidationContext` exposes copies of those frozen inputs and no engine mutation
API. Historical validation endpoints return the stored named outputs and never
re-execute them against current source. This process boundary contains crashes;
it does not restrict filesystem or network permissions of trusted local code.

The backtest engine enumerates provider sessions and applies this sequence:

1. construct a point-in-time Context and working State copy;
2. run due universe/factor/signal/portfolio logic;
3. run the current event handler and any decision handler;
4. validate and atomically commit desired target and JSON State;
5. activate orders only at the policy's later open/close event;
6. run the frozen project's optional `@execution_data_fill` Python over only
   missing state fields for the actual order rows;
7. validate positive price-limit values or the paired-zero “known no limit”
   marker, compare limits with the same-session unadjusted execution price,
   then enforce price, suspension, positive volume and amount,
   participation, cash, costs, and accounting;
8. deliver fill or rejection events with the resulting actual portfolio.

Target decisions never imply completed fills. Rejected or constrained orders
leave the unfilled portion in the actual portfolio.

## Data boundary

`DataEngine` remains provider-first. Current Strategy SDK projects use the
RQ-backed research store as their single data profile; the workbenches do not
offer a Demo/Runtime switch. The bundled deterministic sample remains only as
an internal test fixture and for reproducing historical sample runs. Context
construction filters all dated rows at `as_of`; instrument listing/delisting
and current-session tradability are core-owned. Backtest submission first pins
the project and validation revisions, persists a queued task, and immediately
returns its ID. Runtime preparation is part of the background backtest itself;
there is no separate full-range preflight phase before the event loop. Partial
full-universe execution-state coverage is a bounded `PARTIAL_MARKET_STATE`
warning rather than a task failure. Missing execution state never removes a
symbol from the signal universe. For actual order rows, the event engine first
runs the project's visible `@execution_data_fill` function when present, then
rejects trades whose required suspension or price-limit state remains missing.
A paired `limit_up == limit_down == 0` emitted by that validated function marks
a known no-limit session; raw provider non-positive values remain missing.
A held security reaching its delisting date is written off at zero and
recorded as a settlement event. Backtests hold shared dataset read locks, while
data publication takes an exclusive lock; concurrent backtests therefore do
not mistake one another for dataset writers. A separate per-job process lock
prevents the frontend and Agent API services from recovering and executing the
same persisted task twice.

The Data Workbench owns one Python acquisition recipe per research project.
Built-in RQ templates are complete `@data_recipe` source modules, and the date
and symbol form controls edit their keyword-only defaults with LibCST. Planning
and synchronization execute the exact saved source and retain its SHA-256 in
the job audit. Built-in recipes visibly call `rq.all_instruments()`,
`rq.get_price()`, `rq.get_pit_financials_ex()`, and related vendor operations;
`template=` is display metadata and never dispatches hidden acquisition logic.
Raw results pass through stable frame adapters and `context.publish()` before
entering the shared store. `RQSyncRequest` remains available only as an
explicit lower-level handoff for custom code and CLI compatibility.

Recipes use `context.sync_batches()` to derive date/symbol chunks from
per-symbol and per-dimension persisted watermarks. Provider calls remain
visible in `recipe.py`; the helper owns only deterministic batching and resume
semantics. Every completed batch is atomically published before the next
network call, so a later failure does not discard completed history. Runtime
contracts also include historical suspension/ST state, adjusted and unadjusted
daily OHLC, daily point-in-time factors, and dated index membership. The runtime
market provider joins suspension state into bars for execution, while Strategy
Context exposes daily factors and index membership through bounded point-in-time
methods.

`alphalab.data_sdk.v1.rq` is a lazy transparent proxy to the installed
`rqdatac` package, so the framework does not duplicate or lag the vendor API.
Recipes still publish only through known runtime dataset contracts. Advanced
custom sources can use the versioned provider facade and receive the same
`DataEngine` validation and cache behavior. Neither route creates a
provider-specific strategy API or a second backtest path. The contract is documented in
[05_DATA_SDK_V1_CONTRACT.md](05_DATA_SDK_V1_CONTRACT.md).

The bundled demo derives an explicit instrument snapshot from its market file
only because the shipped sample has no separate instrument parquet. Runtime
providers must supply their own instrument snapshots.

## Persistence and historical compatibility

New backtests persist `strategy_project_id`, `strategy_revision`,
`strategy_source_sha256`, the complete source, manifest, execution audit,
weights, returns, attribution, settings, provenance, and the pinned
`validation_source`, revision, hash, named outputs, and compact run diagnostics.
Run diagnostics retain per-rebalance signal counts, coverage, next-rebalance
rank IC, and aggregate execution counters; they never persist the full
cross-sectional score vectors used to calculate that evidence.

Agent-facing backtest submission returns only a task ID. Task polling keeps
status and stable error fields first. Job reads and frozen summaries share the
same top-level `warnings`, `execution_reliable`, execution-invalid reasons,
`research_valid`, research-invalid reasons, and frozen `research_assessment`,
execution fidelity, attempted/successful trade counts, synthetic-state counts,
and missing-state, suspension, price-limit, capacity, and cash rejection
counters. Filled execution-state values retain their `provider`,
`strategy_fill`, or `fallback` provenance in the paged execution audit.
`GET .../wait` holds one request for at most 30 seconds or until status changes. The default result read contains
metrics, counts, and small head/tail samples. Full daily and execution events
are stored separately and are exposed to the Agent only through explicit
bounded pages. A successful terminal transition atomically clears all earlier
error metadata; failed transitions clear any stale result metadata.

Execution reliability describes the engine's input checks; it does not judge
strategy performance. The project-owned `@analysis(id="research_quality")`
owns execution-quality thresholds. Default ordinary trading shortfalls produce
warnings while strict tracking and exit requirements are opt-in parameters.
Existing custom validation files without that analysis are explicitly unassessed
on new runs. Historical stored assessments are never re-evaluated implicitly.

Harness outputs are prepared by `workspace.outputs.prepare` using the single
published Harness JSON schema. The server checks request/report relationships
and derives display content before returning one atomic Conexus edit batch.
The Agent may create or update report Documents, but it never partially mutates
the Decision Notebook, Workspace Result, or Workspace Commands output nodes.
An `open_result` command is accepted only when the same atomic output contains a
complete current-request Document descriptor for that exact report node.
The workspace delivery proxy validates the outputs again, blocks invalid
navigation, and derives display text even if preparation was bypassed.

Default advancement is explicit. Migrating an editable project replaces only
default-owned universe and execution-data-fill functions and creates a new
immutable revision. Existing frozen revisions and BacktestRuns are never
rewritten.

Old pipeline tables and old BacktestRuns remain only for one-time migration and
read-only inspection. Migration converts every recognized legacy factor into an
SDK `@factor` function and preserves its signed blend weight; an unrecognized
legacy expression becomes an explicit review-required Python function instead
of silently falling back to another factor. Legacy pipeline,
factor-expression, and Python Lab routers are not mounted, are not Agent tools,
and cannot create a second authoritative result.

## Code map

| Path | Responsibility |
| --- | --- |
| `alphalab/sdk/v1/` | Stable public strategy types and decorators |
| `alphalab/data_sdk/v1/` | Stable public data-recipe and custom-provider facade |
| `alphalab/dataio/recipes.py` | Built-in recipe source, AST inspection, LibCST edits, local execution |
| `alphalab/dataio/rq_frames.py` | Stable adapters from raw rqdatac frames to runtime contracts |
| `alphalab/dataio/rq_templates.py` | Declarative RQ acquisition templates |
| `alphalab/strategy/factor_templates.py` | Built-in SDK Python factor templates and CST-aware installation |
| `alphalab/strategy/source.py` | Source-unit split/assembly, AST inspection, dependency checks, and LibCST edits |
| `alphalab/strategy/repository.py` | Authoring units, assembled drafts, immutable packages, probes, migration |
| `alphalab/strategy/sdk_runtime.py` | Trusted-local child-process runner and boundary validation |
| `alphalab/strategy/engine.py` | Preview, factor evaluation, daily events, fills, accounting |
| `alphalab/validation_sdk/` | Small public `ValidationContext` and `@analysis` facade |
| `alphalab/validation/` | Default validation.py, AST/CST contract, persistence, and bounded local runner |
| `docs/06_ALPHALAB_SDK_GUIDE.md` | Canonical versioned user guide for every AlphaLab SDK workbench |
| `apps/api/routers/strategy.py` | Current source/project/evaluation API |
| `apps/api/routers/validation.py` | Validation source and no-code parameter API |
| `apps/api/routers/backtests.py` | Confirmed background backtest jobs and frozen results |
| `apps/api/routers/conexus.py` | Same-origin proxy for durable published-Harness runs and workspace Documents |
| `apps/api/services/agent_conversation_service.py` | Server-side shared Agent conversation history and concurrent snapshot merging |
| `apps/api/routers/python_editor.py` | Fixed Pyrefly/Ruff WebSocket bridge and editor document endpoints |
| `apps/api/routers/sdk_docs.py` | Read-only topic API backed by the canonical SDK guide |
| `apps/api/services/python_editor_service.py` | Local server discovery, ignored source mirrors, framing, and SDK diagnostics |
| `apps/desktop/src/contexts/StrategySdkContext.tsx` | Shared project, draft, revision, and hash state |
| `apps/desktop/src/components/python/` | Lazy shared Monaco model, LSP runtime, AlphaLab completion, and Problems UI |
| `apps/desktop/src/components/shared/SdkDocumentation.tsx` | Shared factor/strategy/data/validation/report documentation drawer |
| `integrations/conexus/alphalab-research-agent/` | SDK-aware bounded Agent tools and prompt |

The package facade in `alphalab/__init__.py` intentionally exports only data,
source, repository, evaluation, backtest, and result-store entry points.
