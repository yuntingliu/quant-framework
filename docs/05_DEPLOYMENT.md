# Deployment

This guide covers building AlphaLab from source and keeping it running. See the [README](../README.md) for first startup, [development guide](03_DEVELOPMENT_GUIDE.md) for hot reload, and [Linux deployment](guides/linux-deployment.md) for service configuration.

## Build and start

From the repository root, create a Python virtual environment and use the Node.js major version specified in `.node-version`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[app,dev,rq]'
npm --prefix apps/desktop ci
npm --prefix apps/desktop run build:web
.venv/bin/python scripts/build_conexus_runtime.py
.venv/bin/python -m alphalab.cli dev serve --host 127.0.0.1 --port 8000
```

On Windows, use `.\.venv\Scripts\python.exe`. The built client is served at `/app/`. Use one backend worker to keep background tasks and in-process state consistent. Use an operating-system service manager for persistent startup and restart.

## Configuration

| Setting | Purpose |
| --- | --- |
| `ALPHALAB_RUNTIME_DIR` | Persistent runtime directory; defaults to `data/runtime/`; set before starting the process |
| `ALPHALAB_ENV_FILE` | Local configuration file; existing process environment values take precedence |
| `ALPHALAB_WEB_AUTH_ENABLED` | Enable HTTP Basic authentication |
| `ALPHALAB_WEB_USERNAME`, `ALPHALAB_WEB_PASSWORD` | Workstation credentials |
| `RQ_USER`, `RQ_PASSWORD`, `RQ_HOST` | RQ data access |
| `ALPHALAB_INSTANCE_ID` | Instance identity for bound Agent tools |
| `CONEXUS_WEB_ORIGIN`, `CONEXUS_PUBLICATION_SLUG`, `CONEXUS_PUBLICATION_WORKSPACE_TOKEN` | External Conexus connection in remote mode |

Keep the runtime directory separate from release source. Configuration files, credentials, and runtime data do not belong in Git. For external access, forward a TLS reverse proxy or tunnel to the loopback listener and preserve WebSocket and event-stream support. Authentication must protect API access as well as HTML.

## Persistence

| Content | Location |
| --- | --- |
| Projects, source packages, frozen backtests | `runtime/app/alphalab.db` |
| Data recipes, sync jobs, partition registry | `runtime/app/dataio.db` |
| Agent conversations and research checkpoints | `runtime/app/agent-conversations.sqlite3` |
| Market, financial, and derived data | Dataset partitions beneath runtime |
| Rebuildable language-server mirrors | `runtime/editor/` |
| Local Conexus reports, Runs, and provider keys | `runtime/conexus/` |

Here, `runtime` means the configured runtime directory. Each independent instance needs its own writable databases. Migrate SQLite using online backups or consistent backups taken while the application is stopped. Migrate Conexus reports separately. Upgrading code does not synchronize the data of different instances.

## Updates

1. Select a specific source commit; install and build it in a separate directory.
2. Run the checks required by the [development guide](03_DEVELOPMENT_GUIDE.md).
3. Check background-task state and back up databases and research data.
4. Switch to the verified release and restart the relevant services.
5. Verify the application version, data access, and required Agent capabilities. Roll back the code release if verification fails.

The deployment environment owns release paths, service configuration, domains, tunnels, and rollback records. A code rollback must not overwrite research data produced after the update.

## Operational checks

Verify the application first, then its external services. An empty runtime directory can start the application but cannot support research with real market data. Verify RQ and Conexus independently.

| Check | What it establishes |
| --- | --- |
| `/api/health` | `status: "ok"`, `frontend: "ready"`, version, and result-store state; no full coverage scan |
| `/app/` | HTML, scripts, and styles load; projects and editors can be opened |
| `/api/data/providers`, `/api/data-sync/health` | Data directories, freshness, and sync state |
| `/api/python-editor/capabilities` | Local Python editor tools are available |
| `/api/agent/identity` | Agent tool requests reach the intended instance |
| Complete read-only Agent run | Model access, tools, event stream, and delivery work together |

Use authenticated requests when authentication is enabled. Check data coverage over the intended research period. Reading a Conexus manifest is not a substitute for a completed run; see [Conexus integration](../examples/deployment/conexus-cloud/README.md).

## Acceptance criteria

| Layer | Real verification |
| --- | --- |
| Installation and build | Install a specific commit into an empty virtual environment; run `python -m pip check`; build with Node 22 and `npm ci` |
| Application | Open the client and create an editable project; anonymous HTML/API requests return 401 when authentication is enabled, and valid credentials work |
| Editor | Open Python source; verify Pyrefly and Ruff WebSocket initialization and diagnostics; confirm mirrors use `runtime/editor/` |
| Persistence and updates | Save a project, restart or upgrade, and compare the canonical source; runtime state survives release changes |
| Data research | Configure an authorized RQ account; sync a small scope, check coverage, and complete a backtest |
| Optional Agent | Configure Conexus; complete a read-only run and verify the tool target, model, event stream, and document persistence |

Automated tests verify interfaces and execution rules; installation and startup still need real checks. A healthy application does not establish data or Agent readiness. Record the commit, Python/Node versions, results, and unverified items. Keep machine inventories, private domains, credentials, and field logs in the deployment environment rather than project documentation.
