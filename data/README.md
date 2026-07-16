# Bundled Example Data

This directory contains a compact real-data development sample built from the
maintainer's local QMT, RQ, and CSMAR caches. It covers 300 liquid non-ST,
non-financial A-shares over the latest five-year window in `manifest.json`.

The sample is for framework development and UI demonstration. It is not an
unbiased investable universe, a live feed, or a recommendation. Financial
values are filtered by their first-release `available_date` so historical
signals cannot read statements that had not yet been published.

Rebuild it from the full local research workspace with:

```powershell
python scripts\build_example_data.py `
  --source-root C:\Users\LYT\Documents\GitHub\quant-framework-factors
```

Only the canonical files listed in `manifest.json` are tracked. Raw vendor
files, credentials, caches, logs, and runtime artifacts remain excluded.
