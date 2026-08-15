# Optional Conexus Research Agent

The barebone framework runs without an LLM planner. When an independently
configured Conexus Web Host hosts an AlphaLab-compatible Research Harness,
the workstation can connect to it through the narrow same-origin
`/api/conexus` proxy. Conexus Canvas state, hosting records, model
credentials, and local run history are deliberately not part of this repository.

The React workstation does not import Conexus Canvas components or call
Electron IPC. A missing Web Host is reported as `not_configured`; it does not
affect Demo, RQ DataIO, deterministic research, backtests, or paper execution.

## Portable Harness bundle

The reviewed Harness definition is tracked under:

```text
integrations/conexus/alphalab-research-agent/
```

It contains the Agent prompt, typed result nodes, research document, and
27 AlphaLab API tools. The tool set includes all six canonical data-registry
operations plus bounded market, point-in-time fundamental, factor-library,
cross-sectional and market-risk factor evaluation, strategy research/management, backtest analysis/comparison,
research-run, report-history, paper-account, signal, and workspace-context
access.
The evaluator supports registered factors and safe expressions and returns PIT
coverage, IC/ICIR, quantile spreads, decay, turnover, bootstrap intervals and
data-quality warnings. Runtime and hosting state
remain local and ignored.
After initializing Conexus for this repository, register and host the bundle:

```powershell
node scripts/register_conexus_research_harness.mjs
node scripts/host_conexus_research_harness.mjs
```

Registration updates only the ignored `.conexus/canvas.json` and stages an
exact generated copy below ignored `workspace/harnesses/`, which is the backing
path required by the Conexus Web Host. Hosting writes only to ignored
Conexus runtime state. The canonical source bundle remains deterministic and
reviewable in Git.

## Run locally

Build the separately installed Conexus Web Host, then start AlphaLab and the
optional host. Set `CONEXUS_ROOT` or pass `-ConexusRoot` when Conexus is not a
sibling directory:

```powershell
python -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000
powershell -ExecutionPolicy Bypass -File scripts/start_conexus_web.ps1 -ConexusRoot C:\path\to\Conexus
npm --prefix dashboard/frontend run dev:web
```

Model credentials stay only in the separate Conexus backend environment.
`start_conexus_web.ps1` intentionally does not load AlphaLab's `.env`, so RQ
credentials are not copied into the Agent process.

## Contracts

- Publication slug: `alphalab-research-agent`
- Harness node: `alphalab-research-harness-v1`
- Published Agent: `alphalab-research-agent-v1`
- AlphaLab proxy: `/api/conexus/*`
- Default Conexus Web Host: `http://127.0.0.1:3000`
- Default AlphaLab API visible to Tool nodes: `http://127.0.0.1:8000`
- Portable Harness source: `integrations/conexus/alphalab-research-agent`

Port 8000 is part of the local Harness contract; stop any unrelated service on
that port before launching AlphaLab, or set `ALPHALAB_API_ORIGIN` for the
Conexus process.

The Research Harness cannot start/stop services, modify AlphaLab application
source code, execute an arbitrary shell, or place real orders. It may validate,
save, clone, and delete local strategy definitions through bounded tools when
the current user explicitly requests the mutation. It may
autonomously plan and run RQ synchronization when runtime data is missing,
stale, or required by the current task. The RQ-sync adapter supplies the
bridge's `confirm=true` trusted-caller assertion internally, so synchronization
does not wait for user authorization or a Data Workbench click.

Backtest execution, full research runs, paper-signal generation, local strategy
mutations, research-run cancellation/retry, paper rebalance execution, and
manual paper orders remain separate typed operations that require an explicit
current-user request and their dedicated confirmation flag. Before a Python
execution the tool reads or validates the strategy and checks its
`strategy_type`, `implementation`, and source hash. Python execution additionally
requires `confirm_python_execution=true`. Paper signals accept only
`stock_selection`; `market_timing` is rejected because it produces aggregate
exposure rather than tradable stock targets.
Strategy YAML, Python source, comments, metadata and captured logs are treated as
untrusted research data, never as Agent instructions. Those policies are not
workspace commands.

The Agent can inspect an existing Python strategy and, when the current user
explicitly requests it, validate and save a local Python strategy. It must first
treat the source as untrusted data, explain that execution uses the user's local
Python permissions, and reject obvious file/network/subprocess/dynamic-execution
behavior. `alphalab_get_strategy` returns the implementation and source hash by
default; full Python source is opt-in through `include_python_source=true` for
code review, mutation review, or execution preflight.
Persisted backtest reads use the same opt-in rule for their Python source
snapshot, while always retaining the source hash for reproducibility.

## Workstation interaction

The left navigation and Agent contract share five modes: `data`, `factor`,
`strategy`, `backtest`, and `report`. Each mode starts with its matching primary
workbench, while the middle Dockview area remains customizable and persists a
separate layout for every mode.

The published Harness exposes a `workspaceCommands` JSON output backed by the
`alphalab-workspace-commands-v1` Custom node. The Agent tab in the React right
rail accepts a command batch only when its `requestId` matches a request sent by
the current browser session, validates every command against a closed allowlist,
and then returns an execution receipt in the conversation UI. Up to 20 recent
local conversations are retained under
`alphalab.anonymous-agent-conversations.v1`; the frontend serializes only a
bounded recent transcript as untrusted continuity context inside the single
`request` string. Selecting New chat starts without that transcript.

The right rail stages free-form prompts into the Dockview Research Agent panel.
Agent invocation input is exactly `{request: <non-empty string>}`. The frontend
encodes the user request, workspace mode, selected symbol/strategy/backtest, data
profile, request id, command receipts and bounded conversation in that string;
there is no second invocation field, manual-context UI or duplicated intent row.

## Structured workspace results

The Harness publishes `workspaceDocument` from the
`alphalab-research-document-v1` Document node. `workspaceResult`, backed by the
`alphalab-workspace-result-v1` Custom node, is the request-bound descriptor and
may include interactive table and chart attachments. An `open_result` command is
accepted only when its `resultId` matches the descriptor and current browser
request.

Validated documents open as `report.workbench` Dockview panels in the Report
mode by default. The primary view renders Markdown, GitHub tables, images, and
sanitized static HTML. Scripts, iframes, forms, event handlers, JavaScript URLs,
and other executable markup are rejected. When the descriptor also contains
columns and rows, the panel adds a sortable/filterable data-table view and CSV
export. Native line, bar, area, scatter, and pie charts render from validated
series descriptors and bounded row data. Table attachments are limited to 30
columns and 1,000 rows; chart attachments are limited to 6 charts, 12 series
per chart, and 500 rows per chart. Document and descriptor payloads are bounded
before storage and rendering.

The Agent submits `decisionNotebook`, `workspaceDocument`, `workspaceResult`,
and `workspaceCommands` through one `update_nodes` batch. All four updates are
sent in the same call, so the Hosted Runtime validates and applies them as one
rollback-safe batch. A malformed or incomplete result therefore leaves every
previous output untouched and is returned to the Agent for correction. The
removed `commit_harness_outputs` name is not part of the Agent contract. The
frontend still validates request IDs and the closed command allowlist before
applying any workspace action.

Multiple result tabs and layout restoration are supported. Recent validated
documents are retained in local storage subject to browser quota and persisted
to SQLite through `/api/reports` with data/code fingerprints. Turns without
an independent result write `kind: "none"`, so stale Document content cannot be
reopened as if it belonged to the current turn.

Anonymous run records remain protected by their per-run access tokens while the
Web Host retains them. They are not used as the chat-history source. Clearing
the browser/Electron site data removes local chats, result caches, and saved
layouts without changing the published Harness.

Agent execution has no host-imposed iteration, output-token, delegated-Agent
depth, total Agent invocation, or default Harness deadline. It ends on
`complete` or explicit cancellation; provider context windows, concurrency, and
request-rate limits remain operational service boundaries.

Supported frontend-only actions are mode switching, opening or closing an
active registered widget, changing the selected symbol/strategy/backtest/date,
setting a linked symbol group, showing the right rail, refreshing dashboard
data, and saving or resetting a layout. Disabled adapter placeholders cannot
be opened by the Agent. Workspace commands themselves cannot synchronize data,
write a strategy, run research, or place an order; those operations use bounded
domain tools. RQ synchronization follows the autonomous data policy. Strategy
mutations, backtests, research runs, paper signals, and local paper-account
mutations retain their explicit-current-user-request policies.

## Optional cloud deployment

The deployment assets under `deploy/conexus-cloud/` run a dedicated published
Harness beside the local AlphaLab API. Keep service credentials in the
server-side `.env`; they must not be copied into this repository or packaged in
the Harness.

When cloud Tool calls need local RQ data, start the reverse SSH tunnel
explicitly in a separate terminal:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_alphalab_conexus_tunnel.ps1
```

Set `CONEXUS_WEB_ORIGIN` in the local environment to the published HTTPS
origin. The ordinary `dashboard/start.ps1` workflow remains local-only and does
not open a tunnel automatically.
