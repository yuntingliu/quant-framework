# Testing Guide

```powershell
python -m pytest tests -q
python scripts/check_facade_imports.py
npm --prefix apps/desktop test
npm --prefix apps/desktop run build
```

Test groups:

- `tests/contracts`: package facade and removal gates.
- `tests/dataio`: provider and `DataEngine` behavior.
- `tests/strategy`: strategy schema, signal generation and backtests.
- `tests/api`: FastAPI smoke tests.
- `tests/collaboration`: contract/citation validation, immutable evidence, serial task state, root budgets, fixture/oracle separation and handover export integrity.
- `tests/conexus-collaboration-*.test.mjs`: actual Host permissions, delegation/result delivery, shared accounting and the scripted handover walkthrough. Build the pinned runtime before running these tests; supply `COLLABORATION_TEST_PYTHON` for a nonstandard Python environment.

The [collaboration walkthrough](../03_DEVELOPMENT_GUIDE.md#experimental-collaboration-foundation)
shows the opt-in public interfaces. Its scripted checks use disposable stores and
no model key. Ordinary tests skip the real-provider matrix; explicit `--live` or
`ALPHALAB_LIVE_TESTS=1` enables paid requests. Live integration evidence remains
separate from deterministic fixtures and financial-quality evaluation.

Keep the unchanged, version-controlled example-data files described in
[data/README.md](../../data/README.md) when preparing a clean source copy: default
SDK backtests use that sample. Exclude user databases, acquired data, credentials,
runtime stores and generated builds. The handover exporter checks source hashes
and includes only the repository's known bundled sample paths.

