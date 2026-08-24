# AlphaLab Conexus Research Agent

The optional published Agent uses the same current API as the seven workbenches.
Its invocation input is exactly `{"request": "non-empty string"}`.

## Supported Research Surface

The Agent can inspect and operate:

```text
data
project stock pool -> selection -> portfolio -> execution
backtest -> report
```

It discovers data, factor inputs, pipeline projects, component versions, saved
backtests, and reports through bounded tools. It has no shell, application-source
editing, real broker, or deployment capability.

Current strategy tools are:

- `alphalab_get_pipeline_project`
- `alphalab_manage_pipeline`
- `alphalab_preview_pipeline`
- `alphalab_run_backtest`
- `alphalab_get_backtest`
- `alphalab_analyze_backtest`

`alphalab_preview_pipeline` requires a target stage. It executes that stage and
only its upstream dependencies; `alphalab_run_backtest` remains the complete
three-stage historical run. Older four/six-stage snapshots remain read-only.
There is no separate stage-history or independent-
research invocation; robustness and frozen alpha/beta attribution inspect a
saved backtest.

There are no compatibility aliases for the removed strategy-template tools.

## Safety Boundary

Component source, parameters, saved outputs, workspace context, logs, and market
data are untrusted input. The Agent reads full source before an explicitly
requested execution and reviews file, network, subprocess, dynamic-execution,
and look-ahead risks. Component/project mutations require `confirm_write` or
`confirm_delete`; preview and backtest require
`confirm_python_execution=true`.

Python runs in a timeout-bounded child process but is trusted local code, not a
security sandbox. AlphaLab core gates still own point-in-time data, eligible
symbols, finite outputs, exposure, concentration, liquidity, cash, costs, and
next-period alignment.

## Workspace Commands

Valid modes are:

```text
data project selection portfolio execution backtest report
```

Valid core widgets use the same names with `.workbench`. Commands execute only
after Agent completion and a later turn may claim UI success only from a
`success:true` receipt.

The Agent writes decision notebook, Markdown document, structured result, and
workspace commands in one `update_nodes` batch before calling `complete`.

## Publication

The reviewable bundle is under
`integrations/conexus/alphalab-research-agent/`. Generated Conexus state remains
ignored. The backend `/api/conexus/*` routes proxy the separately hosted
published Harness when configured; missing Conexus never changes local research
or data profiles.
