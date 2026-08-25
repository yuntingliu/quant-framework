# AlphaLab Architecture

AlphaLab uses Strategy SDK v1 as its only active strategy-authoring and
execution contract. A project owns one complete Python module. Forms, Codex,
factor evaluation, previews, event backtests, and reports all refer to that
module through an immutable source revision and SHA-256.

The normative API is [02_STRATEGY_SDK_V1_CONTRACT.md](02_STRATEGY_SDK_V1_CONTRACT.md).

## System shape

```text
Project/Data ─┐
Factor        ├── one mutable Python draft ── validate/probe ── StrategySourcePackage
Strategy     ─┘                                          │
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

`DataEngine` remains provider-first. `demo` is the bundled deterministic sample;
`runtime` is the local RQ-backed profile. No operation silently switches
profiles. Context construction filters all dated rows at `as_of`; instrument
listing/delisting and current-session tradability are core-owned.

The bundled demo derives an explicit instrument snapshot from its market file
only because the shipped sample has no separate instrument parquet. Runtime
providers must supply their own instrument snapshots.

## Persistence and historical compatibility

New backtests persist `strategy_project_id`, `strategy_revision`,
`strategy_source_sha256`, the complete source, manifest, execution audit,
weights, returns, attribution, settings, and provenance.

Old pipeline tables and old BacktestRuns remain only for one-time migration and
read-only inspection. Legacy pipeline, factor-expression, and Python Lab routers
are not mounted, are not Agent tools, and cannot create a second authoritative
result.

## Code map

| Path | Responsibility |
| --- | --- |
| `alphalab/sdk/v1/` | Stable public strategy types and decorators |
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
