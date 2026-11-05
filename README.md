# AlphaLab

AlphaLab is a Python research workstation for data recipes, factors, stock-selection strategies, and validation. A shared daily event engine produces backtests with traceable source versions and results. Current version: **0.7.0**.

It is designed for researchers who need to inspect factor definitions, point-in-time data, trading rules, and backtest evidence. RQ integration is included; the Data SDK supports additional providers.

## Research workflow

```text
Data preparation → Factor research → Selection and portfolio construction → Backtesting and validation → Reports
```

| Workbench | Purpose |
| --- | --- |
| Project | Create research projects and manage canonical source and immutable versions |
| Data | Edit and run data recipes; inspect synchronization progress and coverage |
| Factors | Write factors and inspect cross-sectional snapshots, historical Rank IC, and other diagnostics |
| Strategy | Define the universe, rebalance schedule, signals, portfolio, and execution rules |
| Validation | Run backtests and inspect returns, trading constraints, and frozen source and results |
| Report | Use the optional Conexus Agent to organize and persist research documents |

A project contains `recipe.py`, `factors/<factor_id>.py`, `strategies/<strategy_id>.py`, and `validation.py`. Forms and editors update the same canonical source. Saving records a version, and backtests pin the strategy and validation versions. The daily engine supports daily, weekly, monthly, and custom schedules; actual fills also depend on target weights, trading constraints, and execution conditions.

## Quick start

Install Git, Python 3.12, and Node.js 22.18+ (see [`.node-version`](.node-version)). The commands below build the web client and start the Python backend and bundled local Conexus Agent. Opening the workstation does not require RQ or Conexus credentials. The Python `dev` extra includes Pyrefly and Ruff for the editor.

```bash
git clone https://github.com/yuntingliu/quant-framework.git
cd quant-framework
```

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[app,dev,rq]"
npm --prefix apps/desktop ci
npm --prefix apps/desktop run build:web
.\.venv\Scripts\python.exe scripts/build_conexus_runtime.py
.\.venv\Scripts\python.exe -m alphalab.cli dev serve
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[app,dev,rq]'
npm --prefix apps/desktop ci
npm --prefix apps/desktop run build:web
.venv/bin/python scripts/build_conexus_runtime.py
.venv/bin/python -m alphalab.cli dev serve
```

Open the [AlphaLab workstation](http://127.0.0.1:8000/app/). The [health endpoint](http://127.0.0.1:8000/api/health) should return `status: "ok"` and `frontend: "ready"`. A fresh installation has no market-data cache; follow the data preparation steps below. For frontend hot reload, see the [development guide](docs/03_DEVELOPMENT_GUIDE.md).

## Your first research project

1. Create a project in the Project Workbench. Built-in projects are read-only templates that illustrate the source structure.
2. Configure `RQ_USER`, `RQ_PASSWORD`, and `RQ_HOST` in an untracked `.env` at the repository root, then restart the backend. Your RQ account must have the required data permissions.
3. Select a template in Data. Start with a few symbols and dates covering the factor lookback and intended test period. Save the code and choose Run and Sync. Confirm completion and coverage. Leaving symbols empty requests the template's full scope.
4. Review, edit, and save the factor and strategy code. Choose the rebalance schedule, portfolio construction, and execution rules.
5. Run a backtest in Validation over the prepared data. Inspect trades and validation results before expanding the sample.

See [data operations](docs/04_DATA_OPERATIONS.md) for permissions, synchronization scope, financial definitions, and historical constituent limitations. The [SDK guide](docs/06_ALPHALAB_SDK_GUIDE.md) contains source layouts, runnable examples, and research commands. The workstation's documentation panel reads that same guide.

## Deployment and optional services

Runtime data defaults to the Git-ignored `data/runtime/` directory. For persistent deployments, use a separate writable directory for databases, caches, and editor mirrors by setting `ALPHALAB_RUNTIME_DIR` in the process environment. See [deployment](docs/05_DEPLOYMENT.md) for authentication, configuration, backups, and updates, and [Linux deployment](docs/guides/linux-deployment.md) for the release script and service management.

The bundled local Conexus Agent needs no Conexus cloud account or enterprise publication. Open **Model providers** in the sidebar to save a provider endpoint, model ID, and API key, test the connection, and choose the active profile. The current adapter uses OpenAI-compatible Chat Completions. Use `--agent off` for manual research or `--agent remote` for an external service. See [Local deployment](docs/07_LOCAL_DEPLOYMENT.md).

## Scope and limitations

Technical patterns, factor diagnostics, and portfolio methods are research tools. Feature support does not establish better returns. Comparisons must align the universe, information timing, rebalance rules, costs, and account model.

Project Python runs in local child processes with timeouts, logs, and contract checks, but it is not sandboxed. Execute trusted source only. Keep credentials, personal data, runtime databases, and generated research artifacts out of Git.

## Further reading

- [Documentation index](docs/README.md): usage, development, deployment, and release notes.
- [Architecture](docs/01_ARCHITECTURE.md): module boundaries and public interfaces.
- [Development guide](docs/03_DEVELOPMENT_GUIDE.md): development setup and checks for each change.
- [Contributing](CONTRIBUTING.md): contribution and documentation conventions.
