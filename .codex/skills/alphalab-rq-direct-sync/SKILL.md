---
name: alphalab-rq-direct-sync
description: Plan, run, resume, or validate direct RQData synchronization on AlphaLab's dev_liu branch, including full A-share bars and historical market state. Do not use for tunnel-based RQ access or realtime quotes.
---

# AlphaLab Direct RQ Sync

Use the repository's fixed `alphalab data` commands. Core acquisition, checkpoint,
storage, and validation behavior belongs in `alphalab/dataio/`; do not recreate it
inside the skill.

## Operating Rules

1. Read `docs/04_DATA_OPERATIONS.md` before changing scope or data contracts.
2. RQ is reached directly with local `RQ_USER`, `RQ_PASSWORD`, and `RQ_HOST`.
   Never add tunnel logic, print credentials, or commit `.env` or `data/runtime/`.
3. Run `alphalab data plan rq ...` before every full-universe or long-history job.
4. Keep the default sample universe for ordinary development. Use
   `--universe all` only when full A-share coverage is actually requested.
5. Let failed jobs retain completed date partitions. Inspect `alphalab data jobs`,
   then rerun without `--force`; watermark overlap resumes from persisted data.
6. Finish with the selected-dataset validation command below when the user
   requires complete historical coverage.

## Full Daily Research Cache

```powershell
alphalab data plan rq --datasets instruments,bars,market-state --universe all --start 2005-01-04 --end YYYY-MM-DD --force
alphalab data sync rq --datasets instruments,bars,market-state --universe all --start 2005-01-04 --end YYYY-MM-DD --force
alphalab data validate --datasets rq.instruments,rq.bars,rq.paused,rq.is_st,rq.daily_factors,rq.index_components --start 2005-01-04 --as-of YYYY-MM-DD --fail-on-gap
```

Use `--force` for the first clean full-range pull. If it fails after writing some
date partitions, rerun the same sync command without `--force` to resume.
When replacing an untrusted cache, archive `data/runtime/` or point
`ALPHALAB_RUNTIME_DIR` at an empty directory first; `--force` does not delete
out-of-contract legacy rows.

Treat the plan's full-universe symbol count as an estimate when no local RQ
instruments snapshot exists. The run resolves the exact listing-overlap universe
directly from RQ before downloading bars or state.
