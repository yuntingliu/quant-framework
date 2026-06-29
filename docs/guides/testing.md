# Testing Guide

```powershell
python -m pytest tests -q
python scripts/check_facade_imports.py
npm --prefix dashboard/frontend run build
```

Test groups:

- `tests/contracts`: package facade and removal gates.
- `tests/dataio`: provider and `DataEngine` behavior.
- `tests/strategy`: strategy schema, signal generation and backtests.
- `tests/dashboard`: FastAPI smoke tests.

