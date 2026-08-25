# AlphaLab Research Agent Bundle

This bundle publishes a conversational Agent for AlphaLab Strategy SDK v1.

It reads provider and workspace context, inspects or edits the one canonical
Python strategy source, evaluates registered factors, previews an immutable
revision, runs and analyzes frozen event backtests, saves reports, and controls
the six current workbench modes.

Current strategy tools:

- `alphalab_get_strategy_project`
- `alphalab_edit_strategy_source`
- `alphalab_preview_strategy`
- `alphalab_evaluate_strategy_factor`
- `alphalab_run_backtest`

Source mutations, deletion, trusted-local Python execution, data mutation,
report saving, and paper actions retain their explicit confirmation boundaries.
The bundle exposes no shell, source-tree mutation, deployment, legacy pipeline,
separate Python Lab, or real-broker tool.

See `docs/04_CONEXUS_AGENT.md` for the full contract.
