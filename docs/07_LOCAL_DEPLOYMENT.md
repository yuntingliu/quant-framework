# Local deployment

The local workstation includes the Python API, browser UI, strategy runtime,
and a pinned Conexus Core snapshot. One launcher starts the Python service and
the local Node Agent. No Conexus cloud account, enterprise publication, or
Canvas editor is needed for this mode.

## Requirements and installation

Use Python 3.12 for the tested installation path, Git, and Node.js 22.18 or later
in the Node 22 line, or Node 24. Use matching processor architectures for Python
and Node. Run commands from the repository root.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[app,dev,rq]"
npm --prefix apps/desktop ci
npm --prefix apps/desktop run build
.\.venv\Scripts\python.exe scripts/build_conexus_runtime.py
.\.venv\Scripts\python.exe -m alphalab.cli dev serve
```

macOS/Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[app,dev,rq]'
npm --prefix apps/desktop ci
npm --prefix apps/desktop run build
.venv/bin/python scripts/build_conexus_runtime.py
.venv/bin/python -m alphalab.cli dev serve
```

Open [the workstation](http://127.0.0.1:8000/app/). On subsequent launches only
the final command is needed. Stop the launcher with Ctrl+C; it shuts down its
own Agent child process. A forced launcher exit also closes the child's private
parent pipe and releases the workspace lock.

The frontend is built into `build/web`. Conexus compilation uses the disposable
`build/conexus-source` directory and produces `build/conexus`. The builder
verifies source checksums in `vendor/conexus` before building. It does not need
access to a private Conexus repository.

If Electron's binary download fails during a browser-only installation, set
`ELECTRON_SKIP_BINARY_DOWNLOAD=1` while running `npm ci`, then clear it. Electron
is optional; `npm --prefix apps/desktop run dev:desktop` and `build:desktop` are
separate desktop commands. A source build is not verification of a packaged
desktop installer.

A complete deployment archive may already contain `build/web/index.html` and
`build/conexus/UPSTREAM.json`. In that case the builds can be skipped. Node is
still required for the Agent; its first launch installs the runtime dependencies
for the target platform.

## Configure model providers

Open **Model providers** in the sidebar:

1. Add a display name, API base URL, and exact model ID.
2. Enter the provider's API key. A loopback HTTP model server may omit it.
3. Select **Test connection** to send a short request to that model.
4. Select **Save and use** to activate the profile.
5. Send an Agent request to verify the model's tool-calling behavior.

Multiple named profiles can be saved. Keys are stored in the backend's private
runtime directory and are never returned by the settings API or stored in browser
local storage. An empty key field preserves a saved key only when its endpoint
is unchanged. Enter a new key when changing the endpoint.

Switching the active profile takes effect for new Agent runs without restarting
AlphaLab. A running Agent retains the provider selected at its first model call.

The current adapter supports **OpenAI-compatible Chat Completions**, with an API
base URL ending before `/chat/completions`, commonly `/v1`. OpenRouter and
compatible local or remote gateways can be configured this way. Native Anthropic,
Gemini, and Responses-only endpoints are not implemented by this adapter;
use a compatible gateway or add a separately tested protocol adapter.

Choose a model that supports tool calling. A successful connection test proves
that the selected model responds; it does not prove a complete research workflow.
The test sends a short prompt and may incur a small provider charge. Research
requests are sent to the provider you configure.

An untracked `.env` remains supported:

```dotenv
CONEXUS_MODEL_BASE_URL=
CONEXUS_MODEL_ID=
CONEXUS_MODEL_API_KEY=
BRAVE_SEARCH_API_KEY=
```

A saved active profile takes precedence over the three model environment values.
Environment changes require a restart. Brave search is optional; its absence does
not disable local research, data, or backtest tools. Model weights and a local
model server are not included in the deployment package.

## Modes, ports, and state

```bash
# Default: included local Conexus runtime
alphalab dev serve --agent local

# Manual workbench without an Agent
alphalab dev serve --agent off

# Explicit connection to an external Conexus Web Host
alphalab dev serve --agent remote

# Separate Python and local Agent ports
alphalab dev serve --port 8100 --agent-port 8787
```

The Python service binds to loopback by default. The Agent also binds to loopback
and automatically chooses a free port unless specified. Ports must differ. Each
runtime data directory has one Agent owner; a duplicate launcher reports the
existing Host PID without replacing its credentials or state.

For remote mode, configure `CONEXUS_WEB_ORIGIN`,
`CONEXUS_PUBLICATION_SLUG`, and `CONEXUS_PUBLICATION_WORKSPACE_TOKEN`.
See [external Conexus configuration](../examples/deployment/conexus-cloud/README.md).
Local provider settings do not change an external server.

| Location | Contents |
| --- | --- |
| `data/runtime/app` | Current project, validation, and result databases |
| `data/runtime/conexus` | Agent graph, reports, Run history, and private model/service credentials |
| `data/runtime/editor` | Python editor mirrors |
| Other `data/runtime` partitions | Downloaded data and synchronization state |
| `data/app` | Legacy database location, imported when initializing a new runtime database |
| `data/cache` | Rebuildable caches |
| `build` | Rebuildable programs and test outputs |

Set `ALPHALAB_RUNTIME_DIR` and `ALPHALAB_CACHE_DIR` before starting Python to use
external locations. `ALPHALAB_APP_DATA_DIR` identifies the legacy application-data
location. Tests use their own isolated paths and a frozen migration fixture.

Back up runtime state, any legacy database, and configuration before upgrading.
Keep credentials and databases separate from source/build replacement. Do not
overwrite an existing `.env` or virtual environment with a deployment archive.

## Research data and verification

A fresh installation has no market-data cache. Configure RQ credentials locally,
create a research project from the system template, and use its Data Workbench
to inspect the recipe, synchronize data, and check coverage. A valid source file
does not imply that market data, warm-up history, financial fields, or execution
state is available for a requested backtest.

```bash
alphalab dev doctor
python -m pytest tests -q
python scripts/check_facade_imports.py
python scripts/check_repository_hygiene.py
python scripts/check_conexus_runtime.py
npm --prefix apps/desktop test
npm --prefix apps/desktop run lint
npm --prefix apps/desktop run build
```

Use the virtual-environment Python path on Windows. The Conexus test runner keeps
the event loop alive for hosted timeout tests, matching a running HTTP server;
it does not modify the pinned upstream source.

See the [acceptance procedure](08_LOCAL_DEPLOYMENT_VALIDATION.md) for the
difference between build, HTTP, controlled-model, and live-provider verification.

## Building a distributable archive

After building and validating the frontend and Conexus runtime:

```bash
python scripts/build_local_bundle.py
```

The ignored `artifacts` output includes a ZIP and checksum. The package contains
source and compiled programs, excludes user state, keys, virtual environments,
and `node_modules`, and records file hashes in `LOCAL_BUILD.json`. Each recipient
creates their own Python environment.

To update Conexus Core, import a reviewed export using
`scripts/sync_conexus_core.py --export-root <directory>`, then rebuild and test.
Do not maintain a second modified execution engine inside AlphaLab. Source
provenance and licensing status are recorded in [THIRD_PARTY.md](../THIRD_PARTY.md).
