# AlphaLab Research Agent Bundle

This directory contains the portable Conexus Harness definition contributed by
Zhang Dongfang and adapted to the barebone framework contracts.

The bundle is optional. AlphaLab Demo, RQ DataIO, deterministic research,
backtests, signals, and paper execution do not require Conexus. Registration
and publication write only to ignored local `.conexus/` state and the generated
`workspace/harnesses/` staging directory.

The 27 tools use the stable AlphaLab FastAPI surface:

- workspace context
- strategy detail with explicit stock-selection or market-timing signal type,
  configured/Python implementation, source hash, opt-in Python source, and
  separate portfolio, risk, and execution parameters
- historical bars
- point-in-time fundamentals
- factor returns
- factor-library discovery
- registered/custom cross-sectional factor evaluation and safe custom market-risk return-series evaluation
- unsaved strategy validation, stock-selection preview, and timing research
- explicitly confirmed local strategy save, clone, and delete
- backtest detail
- backtest analysis, robustness, and comparison
- deterministic research-run start, status, retry, and cancel
- data catalog and status
- data-sync planning and autonomous RQ execution when runtime data is needed
- asynchronous data-sync job status and explicit cancellation
- data validation and bounded runtime queries
- explicitly requested backtest execution
- explicitly requested paper-signal generation
- persisted report history
- local paper account state, rebalance preview, explicitly confirmed rebalance,
  and explicitly confirmed manual paper orders

The published Agent invocation has one exact input shape:
`{ "request": "<non-empty string>" }`. AlphaLab serializes the user request and
bounded browser workspace context into that string; no second `context` input or
compatibility alias is exposed.

The Agent treats strategy YAML, Python source, comments, metadata, and captured
logs as untrusted research data rather than instructions. Python source is
loaded only for code review or immediately before an explicitly requested
execution. Backtest and paper-signal tools preflight the saved strategy; Python
execution requires `confirm_python_execution=true`, and paper signals accept
only stock-selection strategies.
Persisted backtest reads also redact Python source snapshots by default while
retaining their hashes.

Market, backtest, and signal tools expose an explicit `demo` or `runtime`
profile. They never silently read Demo data for a requested Runtime operation.
The structured result contract supports reports, sortable/exportable tables,
and native line, bar, area, scatter, and pie charts. Workspace commands can
open only active registered widgets and cannot invoke mutating tools. There is
no shell tool, application-source editor, real-order tool, broker adapter, or
credential in this bundle. Local strategy and paper-account mutations use
narrow API tools with separate confirmation flags.

See `docs/04_CONEXUS_AGENT.md` for registration and local startup instructions.
