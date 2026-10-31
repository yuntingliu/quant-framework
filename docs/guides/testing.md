# Testing Guide

```powershell
python -m pytest tests -q
python scripts/check_facade_imports.py
npm --prefix apps/desktop run build
```

Test groups:

- `tests/contracts`: package facade and removal gates.
- `tests/dataio`: provider and `DataEngine` behavior.
- `tests/strategy`: strategy schema, signal generation and backtests.
- `tests/api`: FastAPI smoke tests.
- `tests/benchmark`: agent benchmark task schema, frozen fixture digests and
  planted-defect reproduction. See [`benchmark/README.md`](../../benchmark/README.md).

The benchmark fixtures are frozen once committed. `tests/benchmark` re-hashes
them, so rebuild them only to revise a fixture deliberately:

```bash
python -m scripts.build_benchmark_data
python -m scripts.build_benchmark_manifest
python -m scripts.record_benchmark_references --check
```

