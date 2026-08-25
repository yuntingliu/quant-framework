# AlphaLab Architecture

AlphaLab uses Strategy SDK v1 as its only active strategy-authoring and
execution contract. A project owns one complete Python module. Forms, Codex,
factor evaluation, previews, event backtests, and reports all refer to that
module through an immutable source revision and SHA-256.

The normative API is [02_STRATEGY_SDK_V1_CONTRACT.md](02_STRATEGY_SDK_V1_CONTRACT.md).

## System shape

```text
Data recipe ── execute/sync ── canonical research store
                                      │
Project ─────┐                        │
Factor      ├── one mutable Python strategy draft ── validate/probe ── StrategySourcePackage
Strategy ───┘                                                        │
                                                         ├── factor snapshot/history
                                                         ├── signal/portfolio/execution preview
                                                         └── daily event backtest ── frozen Run ── report
```

The six workbench modes are `project`, `data`, `factor`, `strategy`,
`validation`, and `report`. They are views over shared `StrategySdkContext`
state, not independent strategy pipelines.

## Canonical source

A valid module declares `SDK_VERSION = 1` and registers:

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

`StrategyRepository` persists two objects:

- `strategy_projects`: mutable metadata and one mutable draft;
- `strategy_source_packages`: immutable revisions containing full source,
  source hash, registry manifest, literal parameters, requirements, validator
  version, and environment fingerprint.

Draft edits perform static contract validation. Freezing a revision additionally
imports the complete module and runs bounded probes for the full path, every
registered factor, and every registered event handler. Runs always pin a
specific package; changing a later draft cannot alter a historical result.

## Runtime and event engine

`SdkExecutionSession` loads the package once in a spawned local Python child
process. Static run data is transferred once; individual operations send only
event-local state. The process boundary provides timeout, crash containment,
bounded stdout/stderr, and structured errors.

This is trusted local execution, not a security sandbox. Source inspection
surfaces capability-sensitive imports and calls, and every execution endpoint
requires explicit `confirm_python_execution=true`. Docker is not used.

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
weights, returns, attribution, settings, and provenance.

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
| `alphalab/strategy/source.py` | AST inspection, dependency checks, and LibCST edits |
| `alphalab/strategy/repository.py` | Drafts, immutable packages, probes, migration |
| `alphalab/strategy/sdk_runtime.py` | Trusted-local child-process runner and boundary validation |
| `alphalab/strategy/engine.py` | Preview, factor evaluation, daily events, fills, accounting |
| `dashboard/backend/routers/strategy.py` | Current source/project/evaluation API |
| `dashboard/backend/routers/backtests.py` | Confirmed background backtest jobs and frozen results |
| `dashboard/frontend/src/contexts/StrategySdkContext.tsx` | Shared project, draft, revision, and hash state |
| `integrations/conexus/alphalab-research-agent/` | SDK-aware bounded Agent tools and prompt |

The package facade in `alphalab/__init__.py` intentionally exports only data,
source, repository, evaluation, backtest, and result-store entry points.
