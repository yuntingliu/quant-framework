# AlphaLab Research Agent Bundle

This directory contains the portable Conexus Harness definition contributed by
Zhang Dongfang and adapted to the barebone framework contracts.

The bundle is optional. AlphaLab Demo, RQ DataIO, deterministic research,
backtests, signals, and paper execution do not require Conexus. Registration
and publication write only to ignored local `.conexus/` state and the generated
`workspace/harnesses/` staging directory.

The 15 tools use the stable AlphaLab FastAPI surface:

- workspace context
- strategy detail with explicit stock-selection, market-timing, or
  allocation-rotation domain,
  configured/Python implementation, source hash, and opt-in Python source
- historical bars
- point-in-time fundamentals
- factor returns
- backtest detail
- data catalog and status
- data-sync planning and autonomous RQ execution when runtime data is needed
- data validation and bounded runtime queries
- explicitly requested backtest execution
- explicitly requested paper-signal generation

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
no shell tool, strategy/source-writing tool, real-order tool, broker adapter, or
credential in this bundle.

See `docs/04_CONEXUS_AGENT.md` for registration and local startup instructions.
