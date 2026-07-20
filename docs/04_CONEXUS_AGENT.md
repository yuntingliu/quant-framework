# Optional Conexus Research Agent

The barebone framework runs without an LLM planner. When an independently
configured Conexus Web Host publishes an AlphaLab-compatible Research Harness,
the workstation can connect to it through the narrow same-origin
`/api/conexus` proxy. Conexus Canvas state, publication records, model
credentials, and local run history are deliberately not part of this repository.

The React workstation does not import Conexus Canvas components or call
Electron IPC. A missing Web Host is reported as `not_configured`; it does not
affect Demo, RQ DataIO, deterministic research, backtests, or paper execution.

## Portable Harness bundle

The reviewed Harness definition is tracked under:

```text
integrations/conexus/alphalab-research-agent/
```

It contains the Agent prompt, typed context/result nodes, research document, and
six AlphaLab API tools. Runtime and publication state remain local and ignored.
After initializing Conexus for this repository, register and publish the bundle:

```powershell
node scripts/register_conexus_research_harness.mjs
node scripts/publish_conexus_research_harness.mjs
```

Registration updates only the ignored `.conexus/canvas.json`. Publication
writes only to ignored Conexus runtime state. The source bundle remains
deterministic and reviewable in Git.

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

The Research Harness cannot start/stop services, modify source code, execute an
arbitrary shell, or place real orders. Those remain administrator-only tasks in
Conexus.

## Workstation interaction

The published Harness exposes a `workspaceCommands` JSON output backed by the
`alphalab-workspace-commands-v1` Custom node. The Agent tab in the React right
rail accepts a command batch only when its `requestId` matches a request sent by
the current browser session, validates every command against a closed allowlist,
and then returns an execution receipt in the conversation UI. Up to 20 recent
local conversations are retained under
`alphalab.anonymous-agent-conversations.v1`; the Harness receives only a bounded
recent transcript as untrusted continuity context. Selecting New chat starts
without that transcript.

The right rail stages prompts into the Dockview Research Agent panel. The Agent
composer exposes `brief`, `draft`, `risk`, and `next` intents plus explicit
symbol, strategy, backtest, and data-status context switches. The latest
structured decision notebook is reflected back into the right rail.

## Structured workspace results

The Harness publishes `workspaceDocument` from the
`alphalab-research-document-v1` Document node. `workspaceResult`, backed by the
`alphalab-workspace-result-v1` Custom node, is the request-bound descriptor and
may include an interactive table attachment. An `open_result` command is
accepted only when its `resultId` matches the descriptor and current browser
request.

Validated documents open as `research.result-viewer` Dockview panels in the
middle workspace. The primary view renders Markdown, GitHub tables, images, and
sanitized static HTML. Scripts, iframes, forms, event handlers, JavaScript URLs,
and other executable markup are rejected. When the descriptor also contains
columns and rows, the panel adds a sortable/filterable data-table view and CSV
export. Table attachments are limited to 30 columns and 1,000 rows; document
and descriptor payloads are bounded before storage and rendering.

Multiple result tabs and layout restoration are supported. Recent validated
documents are retained in local storage subject to browser quota. Turns without
an independent result write `kind: "none"`, so stale Document content cannot be
reopened as if it belonged to the current turn.

Anonymous run records remain protected by their per-run access tokens while the
Web Host retains them. They are not used as the chat-history source. Clearing
the browser/Electron site data removes local chats, result caches, and saved
layouts without changing the published Harness.

Supported frontend-only actions are mode switching, opening or closing a known
widget, changing the selected symbol/strategy/backtest/date, setting a linked
symbol group, showing the right rail, refreshing dashboard data, and saving or
resetting a layout. These commands cannot run a backtest, generate a signal, or
place an order; those actions remain separate Harness tools with their existing
explicit-user-request policy.
