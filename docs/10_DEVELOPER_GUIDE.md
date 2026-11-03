markdown
# Conexus Developer Guide

Welcome to **Conexus**, the framework behind **AlphaLab** (current version **0.7.0**), a Python research workstation for data recipes, factors, stock-selection strategies, and validation. This guide takes you from a fresh clone to a running local workstation, explains the core concepts, and states the system's current limitations.

> **Who is this for?** New contributors and developers who want to run, explore, and extend Conexus without private repositories or cloud accounts.

## Table of Contents

1. [Before You Begin: Critical Boundaries](#before-you-begin-critical-boundaries)
2. [Quick Start: Run the Workstation Locally](#quick-start-run-the-workstation-locally)
3. [What You Need (and Don't Need) Credentials For](#what-you-need-and-dont-need-credentials-for)
4. [Architecture Overview](#architecture-overview)
5. [Execution Lifecycle](#execution-lifecycle)
6. [Future Work: Multi-Agent Support](#future-work-multi-agent-support)
7. [Troubleshooting](#troubleshooting)
8. [Further Reading](#further-reading)

---

## Before You Begin: Critical Boundaries

Please read this section first. These are current, intentional limitations of the system.

> ### WARNING: No Sandbox
> **Local execution does not protect your host operating system from malicious or faulty code.**
> Project Python (`recipe.py`, `factors/*.py`, `strategies/*.py`, `validation.py`) runs in local child processes. These have timeouts, logs, and contract checks, but they are **not sandboxed** and run with the **same permissions as your user account**, including access to your files, environment variables, and network.
>
> - Execute **trusted source only**. Do not run untrusted projects, factors, strategies, or generated code.
> - Keep credentials, personal data, runtime databases, and generated research artifacts **out of Git**.
> - If in doubt, use a disposable virtual machine or container.

> ###  Serial Execution
> **The current Agent processes tasks one at a time (serially), not concurrently.**
> If you submit a second request while one is in progress, it waits until the first finishes. Plan your experiments accordingly and do not assume parallel throughput.

> ### Research Tool, Not Investment Advice
> Factor diagnostics, technical patterns, and portfolio methods are research tools. Feature support does not establish better returns.

---

## Quick Start: Run the Workstation Locally

The local workstation starts the Python backend and a **bundled local Conexus Agent**. Opening it requires **no Conexus cloud account, no enterprise publication, and no RQ credentials**.

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Git | any recent | To clone the repository |
| Python | **3.12** | |
| Node.js | **22.18+** | See [`.node-version`](../.node-version) |

The `dev` extra installs Pyrefly and Ruff for the in-app editor.

### Step 1: Clone the repository

```bash
git clone https://github.com/yuntingliu/quant-framework.git
cd quant-framework
Step 2: Install, build, and start
Windows PowerShell

powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[app,dev,rq]"
npm --prefix apps/desktop ci
npm --prefix apps/desktop run build:web
.\.venv\Scripts\python.exe scripts/build_conexus_runtime.py
.\.venv\Scripts\python.exe -m alphalab.cli dev serve
macOS / Linux

bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[app,dev,rq]'
npm --prefix apps/desktop ci
npm --prefix apps/desktop run build:web
.venv/bin/python scripts/build_conexus_runtime.py
.venv/bin/python -m alphalab.cli dev serve
What each step does:

Command	Purpose
pip install -e ".[app,dev,rq]"	Installs AlphaLab in editable mode with the app, developer tooling, and RQ integration extras
npm --prefix apps/desktop ci	Installs the exact frontend dependencies
npm --prefix apps/desktop run build:web	Builds the web client
scripts/build_conexus_runtime.py	Builds the bundled local Conexus runtime
python -m alphalab.cli dev serve	Starts the backend and local Agent
Step 3: Verify
Open the AlphaLab workstation.

Check the health endpoint. It should return status: "ok" and frontend: "ready".

Note: A fresh installation has no market-data cache. The workstation will open, but you must prepare data before running research. See the Your First Research Project section below.

For frontend hot reload, see the Development Guide.

What You Need (and Don't Need) Credentials For
Task	Credentials required?
Open the workstation and explore the UI	No
Run the bundled local Conexus Agent	No (no Conexus cloud account)
Browse built-in read-only template projects	No
Sync real market data via RQ	Yes: RQ_USER, RQ_PASSWORD, RQ_HOST, with the required data permissions on your RQ account
Use the Agent with a language model	Yes: a provider endpoint, model ID, and API key you configure yourself
Handling credentials safely

Put RQ settings in an untracked .env at the repository root, then restart the backend.

Never commit .env, API keys, runtime databases, or generated artifacts.

Because of the no-sandbox warning, only use credentials on a machine and with source code you trust.

Your First Research Project
Create a project in the Project Workbench. Built-in projects are read-only templates that illustrate the source structure.

Configure data access (see above) if you want to sync real data.

In Data, select a template. Start with a few symbols and dates covering the factor lookback and your test period. Save the code and choose Run and Sync, then confirm completion and coverage. (Leaving symbols empty requests the template's full scope.)

In Factors and Strategy, review, edit, and save the code. Choose the rebalance schedule, portfolio construction, and execution rules.

In Validation, run a backtest over the prepared data. Inspect trades and results before expanding the sample.

See Data Operations for permissions, sync scope, financial definitions, and historical constituent limitations. The SDK Guide covers source layouts, runnable examples, and research commands.

Architecture Overview
The workstation at a glance
AlphaLab follows a research workflow, with one workbench per stage:

text
Data preparation → Factor research → Selection & portfolio construction → Backtesting & validation → Reports
Workbench	Purpose
Project	Create research projects; manage canonical source and immutable versions
Data	Edit and run data recipes; inspect sync progress and coverage
Factors	Write factors; inspect cross-sectional snapshots, historical Rank IC, and diagnostics
Strategy	Define universe, rebalance schedule, signals, portfolio, and execution rules
Validation	Run backtests; inspect returns, trading constraints, and frozen source and results
Report	Use the optional Conexus Agent to organize and persist research documents
A project contains recipe.py, factors/<factor_id>.py, strategies/<strategy_id>.py, and validation.py. Forms and editors update the same canonical source. Saving records a version, and backtests pin the strategy and validation versions.

Core Conexus concepts
Concept	Role	Analogy
Agent	The AI worker that reasons about a request, calls a language model, and uses tools	The analyst
Harness	The control room that receives requests, coordinates the Agent, and tracks state and results	The control room
Node	The execution environment where tool calls and code actually run	The workbench
Agent. The Conexus Agent interprets a request, decides the next step, calls a model, and invokes tools. It is optional and runs in one of three modes, selected with the --agent option:

Bundled local (default for local development): no Conexus cloud account needed.

--agent off: manual research with no Agent.

--agent remote: connect to an external Agent service.

The current model adapter uses OpenAI-compatible Chat Completions. Configure it under Model providers in the sidebar (endpoint, model ID, API key), test the connection, and choose the active profile. See Local Deployment.

Harness. Sits between the UI and the Agent. It accepts requests, hands them to the Agent, tracks progress (including waits on slow operations), and returns output to the interface.

Node. Where work is performed. In local development the Node runs directly on your machine with no isolation, which is why the no-sandbox warning applies.

text
        ┌──────────┐      ┌───────────────┐      ┌──────────────┐
 User ─▶│ AlphaLab │─────▶│    Harness    │─────▶│    Agent     │
        │   UI     │◀─────│ (control room)│◀─────│ (AI worker)  │
        └──────────┘      └───────────────┘      └──────┬───────┘
                                                        │ tool calls
                                                        ▼
                                                 ┌──────────────┐
                                                 │     Node     │
                                                 │ (execution   │
                                                 │ environment) │
                                                 └──────────────┘
For module boundaries and public interfaces, see Architecture.

Execution Lifecycle
This is how a single Agent request (for example, from the Report workbench) moves through the system:

Request. The user submits a request through the AlphaLab UI. The Harness receives it and queues it for the Agent. (Execution is serial, so it waits if another task is running.)

Model call. The Agent sends context to the configured language model, which decides whether to answer directly or use a tool.

Tool call. If a tool is needed, the Agent asks the Node to run it and receives the result.

Async wait. Slow operations (data fetches, long computations) take time. The Harness tracks the pending operation and resumes the Agent when it completes. Steps 2–4 may repeat several times.

Output. Once the Agent has a final answer, the Harness returns it to the UI, where research documents can be persisted.

text
Request ─▶ Model call ─▶ Tool call ─▶ Async wait ─▶ Output
              ▲                           │
              └───────────────────────────┘
                 (repeat as needed)
Separate from the Agent: backtests run on a shared daily event engine (daily, weekly, monthly, and custom rebalance schedules). That is a different pipeline from the Agent request lifecycle above.

Future Work: Multi-Agent Support
Placeholder: Not Yet Implemented

Conexus is intended to eventually support multiple Agents working together. This section reserves the location and interface for that work. No multi-Agent functionality exists today, and this guide does not describe any.

Reserved location: docs/multi-agent/ (proposed; to be confirmed with maintainers)

Interface status: To be designed. Nothing should depend on it yet.

Contributors: please do not build against this placeholder. Open a discussion before proposing a design.

Until then, assume the single, serial Agent described above.

Troubleshooting
Symptom	Likely cause and fix
No module named alphalab	The virtual environment is not active, or the install step was skipped. Re-run the pip install -e ".[app,dev,rq]" step using the .venv Python.
Health endpoint shows frontend not "ready", or the app page is blank	The web client has not been built. Run npm --prefix apps/desktop ci and npm --prefix apps/desktop run build:web, then restart.
Agent or runtime errors on startup	Re-run scripts/build_conexus_runtime.py using the .venv Python, or start with --agent off to work without the Agent.
http://127.0.0.1:8000/app/ does not load	Confirm the server is running and nothing else is using port 8000.
Data sync fails or returns nothing	Check RQ_USER, RQ_PASSWORD, RQ_HOST in the untracked .env, confirm your RQ account's data permissions, then restart the backend.
Empty charts or "no data"	Fresh installs have no market-data cache. Run Run and Sync in the Data workbench first.
A second request seems "stuck"	Expected: the Agent is serial. Wait for the first task to finish.
Runtime data (databases, caches, editor mirrors) is stored in the Git-ignored data/runtime/ directory by default. For persistent deployments, set ALPHALAB_RUNTIME_DIR in the process environment. See Deployment.

Further Reading
Documentation index: usage, development, deployment, and release notes

Architecture: module boundaries and public interfaces

Development Guide: development setup and per-change checks

Data Operations: permissions, sync scope, and data limitations

Deployment and Linux Deployment: configuration, backups, updates

SDK Guide: source layouts, runnable examples, research commands

Contributing: contribution and documentation convention