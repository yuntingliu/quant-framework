# AlphaLab Research Agent Bundle

This directory contains the portable Conexus Harness definition contributed by
Zhang Dongfang and adapted to the barebone framework contracts.

The bundle is optional. AlphaLab Demo, RQ DataIO, deterministic research,
backtests, signals, and paper execution do not require Conexus. Registration
and publication write only to ignored local `.conexus/` state and the generated
`workspace/harnesses/` staging directory.

The 14 tools use the stable AlphaLab FastAPI surface:

- workspace context
- strategy detail
- historical bars
- point-in-time fundamentals
- factor returns
- backtest detail
- data catalog and status
- data-sync planning and explicitly confirmed execution
- data validation and bounded runtime queries
- explicitly requested backtest execution
- explicitly requested paper-signal generation

Market, backtest, and signal tools expose an explicit `demo` or `runtime`
profile. They never silently read Demo data for a requested Runtime operation.
The structured result contract supports reports, sortable/exportable tables,
and native line, bar, area, scatter, and pie charts. Workspace commands can
open only active registered widgets and cannot invoke mutating tools. There is
no shell tool, source-editing tool, real-order tool, broker adapter, or
credential in this bundle.

See `docs/04_CONEXUS_AGENT.md` for registration and local startup instructions.
