# Conexus Research Agent Integration

AlphaLab uses a published Conexus Harness as its only Agent execution boundary.
The React workstation does not import Conexus Canvas components or call Electron
IPC. FastAPI exposes a narrow same-origin proxy below `/api/conexus`, and the
published release calls the stable AlphaLab API through an allowlisted
`ALPHALAB_API_ORIGIN`. In the cloud container this points to the loopback end of
a restricted SSH reverse tunnel, so the local database and RiceQuant connection
do not need to be exposed publicly.

## Build and publish

From `E:\Conexus`:

```powershell
npm run build:web
npm --prefix backend run build
```

From `E:\quant-framework`:

```powershell
node scripts/install_conexus_research_harness.mjs
node scripts/publish_conexus_research_harness.mjs
```

Publication writes an immutable release below:

```text
.conexus/publications/alphalab-research-agent/
```

The AlphaLab publication is deliberately `anonymous` with `publisher` billing.
The workstation therefore does not open a Conexus account gate: model calls use
the Web Host's server-side `OPENROUTER_API_KEY` with `openai/gpt-oss-120b`, while chat history remains in
the local browser/Electron profile.

## Run the workstation

The normal startup path uses the isolated cloud Web Host and starts the local
backend, secure data tunnel, and frontend together:

```powershell
cd E:\quant-framework
powershell -ExecutionPolicy Bypass -File .\dashboard\start.ps1
```

The cloud Web Host is available at
`https://alphalab.43.154.239.41.nip.io`. Its publisher model key and Web Host
token stay in the server-side `/opt/conexus/alphalab/.env`; neither credential
is stored in AlphaLab, Canvas nodes, Harness files, or frontend storage. The
dedicated tunnel key is stored outside the repository at
`%USERPROFILE%\.ssh\alphalab_conexus_ed25519` and is restricted server-side to
listen only on `127.0.0.1:18000`.

For Conexus development only, `scripts/start_conexus_web.ps1` remains available
as a local Web Host fallback.

## Contracts

- Publication slug: `alphalab-research-agent`
- Harness node: `alphalab-research-harness-v1`
- Published Agent: `alphalab-research-agent-v1`
- AlphaLab proxy: `/api/conexus/*`
- AlphaLab Conexus Web Host: `https://alphalab.43.154.239.41.nip.io`
- Cloud Tool-to-AlphaLab endpoint: `http://127.0.0.1:18000`
- Local AlphaLab API: `http://127.0.0.1:8000`

Port 8000 remains part of the local AlphaLab contract. The tunnel forwards only
the server loopback port 18000 to that local API; it does not publish port 8000
on the Internet.

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

Context, Agent, and Activity share a 420px default right-rail width. On desktop,
drag the rail's left edge to resize it between 320px and 720px; the selected
width is stored in local storage. The Agent composer exposes `brief`, `draft`,
`risk`, and `next` research intents plus explicit symbol, strategy, backtest,
and data-status context switches. Those controls alter the Workspace Context
sent to the Harness. The latest structured decision notebook is rendered in
the Context tab.

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

The Agent submits `decisionNotebook`, `workspaceDocument`, `workspaceResult`,
and `workspaceCommands` through one `commit_harness_outputs` call. The Hosted
Runtime validates the complete declared output schema before changing any bound
node, then commits all four values together. A malformed or incomplete result
therefore leaves every previous output untouched and is returned to the Agent
for correction. The frontend still validates request IDs and the closed command
allowlist before applying any workspace action.

Multiple result tabs and layout restoration are supported. Recent validated
documents are retained in local storage subject to browser quota. Turns without
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

Supported frontend-only actions are mode switching, opening or closing a known
widget, changing the selected symbol/strategy/backtest/date, setting a linked
symbol group, showing the right rail, refreshing dashboard data, and saving or
resetting a layout. These commands cannot run a backtest, generate a signal, or
place an order; those actions remain separate Harness tools with their existing
explicit-user-request policy.
