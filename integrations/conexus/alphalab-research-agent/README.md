# AlphaLab Research Agent Bundle

This bundle publishes a conversational Agent for AlphaLab's current six-stage
Python strategy pipeline.

It can inspect data and risk factors, evaluate cross-sectional signal evidence,
manage immutable Python component versions and revisioned projects, preview the
complete composed strategy, run and analyze frozen backtests, read reports, and
control the nine current workbench modes.

Current pipeline tools:

- `alphalab_get_pipeline_project`
- `alphalab_manage_pipeline`
- `alphalab_preview_pipeline`
- `alphalab_run_backtest`

The complete strategy source is composed from exactly six pinned versions:
universe, selection, timing, portfolio, risk, and execution. Pure selection and
pure timing use explicit identity components. YAML is neither an Agent input nor
a runtime representation.

Project/component writes and deletes require explicit confirmation. Preview and
backtest require explicit trusted-local Python execution confirmation. The
bundle has no shell, source-tree mutation, deployment, or real-broker tool.

See `docs/04_CONEXUS_AGENT.md` for the contract and publication boundary.
