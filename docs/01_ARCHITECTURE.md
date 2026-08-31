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
portfolio, event, and execution registrations. Each factor unit owns one
registered factor. `assemble_strategy_source()` inserts factor functions in a
stable order before strategy entrypoints and validates the resulting module.
No source unit is executed independently.

The assembled module declares `SDK_VERSION = 1` and registers:

- exactly one `@universe`;
- zero or more `@factor` and `@schedule` functions;
- exactly one `@signal` and one `@portfolio`;
- at most one `@on_event` handler per event;
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
every registered event handler, then records an immutable package automatically.
Runs pin that internal package; changing any source unit later cannot alter a
historical result. Revision numbers and hashes are audit metadata, not
user-managed authoring controls.

`ValidationRepository` applies the same user-facing save model to
`validation_sources` and immutable `validation_source_packages`. A Run pins
both the strategy package and validation package before it enters the queue.
The default `validation.py` visibly implements headline performance metrics and
CAPM/multi-factor OLS with Newey-West errors. Its keyword-only literal defaults
are CST-projected into the visual panel; custom Python stays intact.

## Runtime and event engine

`SdkExecutionSession` loads the package once in a spawned local Python child
process. Static run data is transferred once; individual operations send only
event-local state. The process boundary provides timeout, crash containment,
bounded stdout/stderr, and structured errors.

This is trusted local execution, not a security sandbox. Source inspection
surfaces capability-sensitive imports and calls, and every execution endpoint
requires explicit `confirm_python_execution=true`. Docker is not used.

After the event engine has produced returns, benchmark returns, weights,
factor returns, executions, and settings, the pinned `validation.py` runs in a
separate local Python subprocess with a timeout and JSON-output size limit.
`ValidationContext` exposes copies of those frozen inputs and no engine mutation
API. This process boundary contains crashes; it does not restrict filesystem or
network permissions of trusted local code.

The backtest engine enumerates provider sessions and applies this sequence:

1. construct a point-in-time Context and working State copy;
2. run due universe/factor/signal/portfolio logic;
3. run the current event handler and any decision handler;
4. validate and atomically commit desired target and JSON State;
5. activate orders only at the policy's later open/close event;
6. enforce valid price, suspension/price-limit fields, positive volume and
   amount, participation, cash, costs, and accounting;
7. deliver fill or rejection events with the resulting actual portfolio.

Target decisions never imply completed fills. Rejected or constrained orders
leave the unfilled portion in the actual portfolio.

## Data boundary

`DataEngine` remains provider-first. Current Strategy SDK projects use the
RQ-backed research store as their single data profile; the workbenches do not
offer a Demo/Runtime switch. The bundled deterministic sample remains only as
an internal test fixture and for reproducing historical sample runs. Context
construction filters all dated rows at `as_of`; instrument listing/delisting
and current-session tradability are core-owned.

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
contracts also include historical suspension/ST state, daily point-in-time
factors, and dated index membership. The runtime market provider joins
suspension state into bars for execution, while Strategy Context exposes daily
factors and index membership through bounded point-in-time methods.

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
`validation_source`, revision, hash, and named outputs.

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
| `dashboard/backend/routers/strategy.py` | Current source/project/evaluation API |
| `dashboard/backend/routers/validation.py` | Validation source and no-code parameter API |
| `dashboard/backend/routers/backtests.py` | Confirmed background backtest jobs and frozen results |
| `dashboard/backend/routers/conexus.py` | Same-origin proxy for durable published-Harness runs and workspace Documents |
| `dashboard/backend/routers/python_editor.py` | Fixed Pyrefly/Ruff WebSocket bridge and editor document endpoints |
| `dashboard/backend/services/python_editor_service.py` | Local server discovery, ignored source mirrors, framing, and SDK diagnostics |
| `dashboard/frontend/src/contexts/StrategySdkContext.tsx` | Shared project, draft, revision, and hash state |
| `dashboard/frontend/src/components/python/` | Lazy shared Monaco model, LSP runtime, AlphaLab completion, Problems, and Diff UI |
| `integrations/conexus/alphalab-research-agent/` | SDK-aware bounded Agent tools and prompt |

The package facade in `alphalab/__init__.py` intentionally exports only data,
source, repository, evaluation, backtest, and result-store entry points.
