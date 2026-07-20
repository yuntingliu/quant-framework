# AlphaLab Research Agent Bundle

This directory contains the portable Conexus Harness definition contributed by
Zhang Dongfang and adapted to the barebone framework contracts.

The bundle is optional. AlphaLab Demo, RQ DataIO, deterministic research,
backtests, signals, and paper execution do not require Conexus. Registering or
publishing this Harness writes only to the ignored local `.conexus/` directory.

The six tools use the stable AlphaLab FastAPI surface:

- workspace context
- strategy detail
- historical bars
- backtest detail
- explicitly requested backtest execution
- explicitly requested paper-signal generation

Market, backtest, and signal tools expose an explicit `demo` or `runtime`
profile. They never silently read Demo data for a requested Runtime operation.
There is no shell tool, source-editing tool, real-order tool, broker adapter, or
credential in this bundle.

See `docs/04_CONEXUS_AGENT.md` for registration and local startup instructions.
