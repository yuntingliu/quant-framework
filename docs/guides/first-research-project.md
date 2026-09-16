# Your first AlphaLab research project

AlphaLab combines data recipes, factors, portfolio construction, event backtests,
validation, and reports. Python source is the research definition; forms, editors,
and the Agent use the same saved source and immutable results.

Use [Local deployment](../07_LOCAL_DEPLOYMENT.md) for installation and the
[SDK guide](../06_ALPHALAB_SDK_GUIDE.md) for executable contracts.

## Repository and research project

The repository contains the application and research engine. A research project
is an application record with shared `recipe.py`, `factors/<factor_id>.py`,
`validation.py`, and named `strategies/<strategy_id>.py` sources. Select the
intended strategy before editing or running it. Frozen historical runs retain
their own strategy, factor, and validation packages after later edits.

## Install and start

Create a Python environment, install the `app`, `dev`, and optional `rq` extras,
build `apps/desktop`, and run `scripts/build_conexus_runtime.py`. Then start
`python -m alphalab.cli dev serve`. Ctrl+C stops the API and its local Agent.
The deployment guide covers operating-system commands, ports, backups, and
troubleshooting. Use `--agent off` for manual research or `--agent remote` for an
explicitly configured external Conexus service.

## Complete an initial experiment

State the universe, sample, factor definition, selection, weights, rebalance
schedule, execution timing, and costs before looking at results. Start with a
small explicit sample and enough earlier history for the factor lookback.

1. Create an editable project from the maintained system template.
2. Review the recipe's instruments, dates, and fields in Data.
3. Inspect the acquisition plan, synchronize, and verify coverage.
4. Review factor source; run cross-sectional and historical diagnostics.
5. Select a strategy and set its universe, factors, weights, schedule, and execution.
6. Save and inspect its decision preview.
7. Save validation parameters and run the complete event backtest.
8. Inspect returns, drawdown, exposure, turnover, fills, rejections, warnings,
   frozen source, and coverage.
9. Change one hypothesis at a time and compare frozen runs.
10. Generate a report after the underlying evidence is available.

A preview is a decision snapshot. Target weights are intended holdings, not
guaranteed fills. Zero trades or a 0% return can indicate insufficient data or
infeasible orders; inspect execution evidence. Avoid repeatedly tuning against
the intended holdout period.

## Follow a request through the backend

Routes in `apps/api/routers` receive browser requests. Services coordinate
projects, source inspection, acquisition jobs, and backtest jobs. The reusable
`alphalab` package owns the SDK, data preparation, execution, and analytics.

A save validates and assembles the selected strategy and factors and records an
immutable package. A background backtest pins that package and the validation
version. The UI receives a task ID and later reads its frozen result.

Point-in-time rules restrict data to what was available at the decision time,
including financial disclosures and historical constituents. The engine owns
fills, costs, cash, positions, and accounting. Validation analyzes the resulting
evidence without rewriting trades. Python child processes provide timeout and
crash containment; they are trusted local execution, not a security sandbox.

## Understand the frontend

`apps/desktop/src/App.tsx` and `Workspace.tsx` compose the six workbenches. Shared
context keeps project and strategy selection consistent. Visual controls edit
recognized Python parameters, while custom source remains editable. Save and
review changes before running; backend inspection is authoritative.

Language services provide Python diagnostics, formatting, and completion using
temporary editor mirrors. Jobs run asynchronously. Agent event streams are
reconciled with server state so a disconnected stream cannot leave a completed
Run permanently active. Browser and Electron modes use the same research API.

## Configure and use the Agent

Open **Model providers**, enter a compatible API base URL, model ID, and key,
test the connection, then save the active profile. Keys stay in the backend
runtime store. Loopback model servers can omit a key. The current protocol is
OpenAI-compatible Chat Completions; Agent research also requires tool calling.

Bounded requests include “Inspect this project's missing data,” “Compare these
two frozen runs,” and “Implement this factor hypothesis, validate it, and save a
report.” The Agent invokes the same Python APIs as the workbenches. Project
writes, execution, and deletion follow the documented authorization boundaries.

Reports live in the Conexus workspace; conversations, source packages, and
backtest evidence remain in their backend stores. Changing the model does not
move those stores.

## Contribute and verify

Read [Architecture](../01_ARCHITECTURE.md) and the
[Development guide](../03_DEVELOPMENT_GUIDE.md). Use an isolated checkout and
runtime directory, locate the correct route/service/core boundary, and keep
the public facade small.

Run the relevant Python, frontend, and Conexus checks. Inspect actual UI changes.
Do not commit keys, databases, downloaded data, temporary logs, dependencies, or
build output. A useful pull request explains the problem, resulting behavior,
tests, and remaining limitations.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Workstation does not open | Build output, API port, launcher log, and health |
| Agent model missing | Active profile, endpoint, model ID, and key |
| Model responds but Agent fails | Tool-call support, protocol, and Run error |
| Coverage error | Symbols, dates, warm-up history, and required fields |
| Empty factor output | Missing inputs and lookback length |
| Zero fills | Tradability, price limits, volume, cash, and activation |
| Missing reports after switching servers | Persistent workspace and instance identity |
| Editor unavailable | Python environment, language tools, and WebSockets |

See [Deployment acceptance](../08_LOCAL_DEPLOYMENT_VALIDATION.md) for evidence
required before calling an installation ready.
