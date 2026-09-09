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

`data/app/alphalab.db` is an ignored local application database, initialized
from the schema and default templates on first use. It is never copied from
the historical migration fixture in `tests/fixtures/legacy_app.db`. Before
updating an older checkout that tracks `data/app/alphalab.db`, stop the backend
and back up that database; restore it after updating to retain local projects.

RQ runtime downloads use `data/runtime/`. That directory contains partitioned
parquet files plus `app/dataio.db` for checksums, watermarks, quality runs,
sync jobs, and checkpoints. It is always ignored and must remain absent from
`git ls-files`.
