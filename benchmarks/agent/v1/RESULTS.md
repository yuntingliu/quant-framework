# Agent benchmark v1 — optimization results (issue #22)

Each optimization is compared with the isolated baseline in `BASELINE.md`, one
change at a time, on the same 11 tasks (T08 excluded) × 3 repetitions, with
`deepseek-flash` at DeepSeek off-peak prices. Costs use $0.15 / $0.003 / $0.60
per 1M uncached input / cached input / output tokens, which reproduce the
baseline's $0.52. Gates and targets are those in `thresholds.json`.

## 1. Server-side waiting (`opt-waiting`) — PROVISIONAL, 29 of 33 runs

| | |
|---|---|
| Change | `backtest.wait` blocks up to 300 s on the server (was 30 s); new blocking `sync.wait`; SKILL.md and Project-Run updated |
| Code | commit `76a19cd`, clean tree in every trace |
| Dates | 2026-11-14 and 2026-11-17, all runs off-peak |
| Applies to | tasks that wait on jobs: T04, T05, T07, T12 (and T08, excluded) |

**Excluded runs.** T04, T05, T06 and T07 repetition 2 failed because
`alphalab.db` was corrupted (`database disk image is malformed`): a write-ahead
log left by an interrupted workstation was replayed onto the restored database.
The agent reported the error and fabricated nothing, but the runs measure the
environment, not the change. Their rows are kept in
`build/benchmarks/opt-waiting/excluded.jsonl` and their traces in
`build/traces/opt-waiting/excluded/`. The restore now removes SQLite `-wal`,
`-shm` and `-journal` files; the four runs are to be rerun with `--resume`.
Until then T04–T07 have 2 runs each.

### Totals

| Measure | Baseline, same 29 runs | Waiting | Change | Baseline, all 33 |
|---|---|---|---|---|
| Outcomes | 23 completed, 1 blocked, 4 budget exhausted, 1 failed | 24 completed, 2 blocked, 3 budget exhausted | | 27 / 1 / 4 / 1 |
| Automatic checks | 22 / 29 | 25 / 29 | +3 | 26 / 33 |
| Permission violations | 0 | 0 | | 0 |
| Human questions | 1 | 0 | | 1 |
| Wall time | 115.6 min | 173.7 min | **+50%** | 154.1 min |
| Cost | $0.402 | $0.366 | −9% | $0.521 |
| Model calls | 474 | 389 | −18% | 609 |
| Input tokens | 22.6M | 15.2M | −33% | 31.0M |
| Output tokens | 0.39M | 0.38M | −2% | 0.50M |
| Calls only to poll | — | 25 (9.7% of input tokens) | | 158 (35.4%) |
| Waiting share of time | — | 62.5% | | 51.9% |

### Per task (median)

| Task | Runs | Wall s, base → opt | Model calls | Cost | Auto, base → opt |
|---|---|---|---|---|---|
| T01 | 3 | 48 → 38 (−21%) | 9 → 9 | −26% | 2/3 → 2/3 |
| T02 | 3 | 30 → 19 (−37%) | 8 → 5 (−38%) | −44% | 3/3 → 3/3 |
| T03 | 3 | 71 → 102 (+44%) | 8 → 12 (+50%) | +46% | 2/3 → 3/3 |
| T04 | 2 | 299 → 335 (+12%) | 20 → 18 (−10%) | −8% | 2/3 → 2/2 |
| T05 | 2 | 690 → 762 (+10%) | 37 → 20 (**−46%**) | **−25%** | 3/3 → 2/2 |
| T06 | 2 | 146 → 161 (+10%) | 16 → 12 (−22%) | −24% | 3/3 → 2/2 |
| T07 | 2 | 1179 → 1422 (+21%) | 58 → 34 (**−42%**) | **−25%** | 2/3 → 2/2 |
| T09 | 3 | 86 → 116 (+35%) | 14 → 11 (−21%) | −11% | 3/3 → 3/3 |
| T10 | 3 | 28 → 44 (+58%) | 6 → 9 (+50%) | +77% | 3/3 → 3/3 |
| T11 | 3 | 39 → 42 (+7%) | 7 → 7 | −3% | 3/3 → 3/3 |
| T12 | 3 | 431 → 1298 (**+201%**) | 26 → 19 (−27%) | +24% | 0/3 → 0/3 |

### Provisional reading

- Polling is largely gone: calls made only to poll fell from 158 to 25, and
  input tokens by a third.
- On the tasks the change applies to, the model-call target (−20%) is met for
  T05, T07 and T12 and the cost target for T05 and T07; T04 meets neither.
- Wall time did not improve. The waiting itself is backtest run time, which the
  change does not shorten. T12 is three times longer: with fewer polling calls
  it no longer exhausts its 25-call budget and now runs into the 1,200 s time
  limit instead. T07 (+21%) and T12 break the "no other metric worse by more
  than 10%" rule as things stand.
- T03 and T10 do not wait on jobs; their changes are run-to-run variation at
  3 repetitions, not an effect of this change.
- T12 still fails 3/3, as registered in `thresholds.json`: it needs the
  explicit budget stop.

### Still to do for this comparison

- [ ] Rerun T04–T07 repetition 2 and update the tables above.
- [ ] Manual grading of every answer against its grader rule, for the baseline
  and this run (grader-pass and fabrication gates).
- [ ] Decide accept or roll back against the gates, and record why.
