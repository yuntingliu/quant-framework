# Agent benchmark v1 — optimization results (issue #22)

Each optimization is compared with the isolated baseline in `BASELINE.md`, one
change at a time, on the same 11 tasks (T08 excluded) × 3 repetitions, with
`deepseek-flash` at DeepSeek off-peak prices. Costs use $0.15 / $0.003 / $0.60
per 1M uncached input / cached input / output tokens, which reproduce the
baseline's $0.52. Gates and targets are those in `thresholds.json`.

## 1. Server-side waiting (`opt-waiting`)

| | |
|---|---|
| Change | `backtest.wait` blocks up to 300 s on the server (was 30 s); new blocking `sync.wait`; SKILL.md and Project-Run updated |
| Code | commit `76a19cd` (29 runs) and `aa4f3fa` (4 reruns); the agent is identical in all 33: same prompt, agent, skill and tools digests, clean tree |
| Dates | 2026-11-14 and 2026-11-17, all runs off-peak |
| Applies to | tasks that wait on jobs: T04, T05, T07, T12 (and T08, excluded) |

**Excluded and rerun.** The first T04, T05, T06 and T07 repetition-2 runs
failed because `alphalab.db` was corrupted (`database disk image is
malformed`): a write-ahead log left by an interrupted workstation was replayed
onto the restored database. The agent reported the error and fabricated
nothing, but the runs measured the environment, not the change. They are kept
in `build/benchmarks/opt-waiting/excluded.jsonl` and
`build/traces/opt-waiting/excluded/`. The restore now removes SQLite `-wal`,
`-shm` and `-journal` files (`0a9caad`), and the four runs were rerun with no
further database errors.

### Totals (33 runs each)

| Measure | Baseline | Waiting | Change |
|---|---|---|---|
| Outcomes | 27 completed, 1 blocked, 4 budget exhausted, 1 failed | 28 completed, 2 blocked, 3 budget exhausted | |
| Automatic checks | 26 / 33 | 28 / 33 | +2 |
| Permission violations | 0 | 0 | |
| Human questions | 1 | 0 | |
| Wall time | 154.1 min | 218.7 min | **+42%** |
| Cost | $0.521 | $0.460 | −12% |
| Model calls | 609 | 479 | −21% |
| Input tokens | 31.0M | 19.0M | −39% |
| Output tokens | 0.50M | 0.48M | −3% |
| Calls only to poll | 158 (35.4% of input tokens) | 30 (9.1%) | −81% |
| Waiting share of time | 51.9% | 61.4% | |

Without T12: wall time +11%, cost −17%, model calls −24%.

### Per task (median of 3)

| Task | Wall s, base → opt | Model calls | Cost | Auto, base → opt |
|---|---|---|---|---|
| T01 | 48 → 38 (−21%) | 9 → 9 | −26% | 2/3 → 2/3 |
| T02 | 30 → 19 (−37%) | 8 → 5 (−38%) | −44% | 3/3 → 3/3 |
| T03 | 71 → 102 (+44%) | 8 → 12 (+50%) | +46% | 2/3 → 3/3 |
| **T04** | 299 → 358 (+20%) | 20 → 20 | +6% | 2/3 → 2/3 |
| **T05** | 690 → 834 (+21%) | 37 → 18 (**−51%**) | −19% | 3/3 → 3/3 |
| T06 | 146 → 179 (+22%) | 16 → 13 (−19%) | −22% | 3/3 → 3/3 |
| **T07** | 1179 → 1184 (0%) | 58 → 33 (**−43%**) | **−29%** | 2/3 → 3/3 |
| T09 | 86 → 116 (+35%) | 14 → 11 (−21%) | −11% | 3/3 → 3/3 |
| T10 | 28 → 44 (+58%) | 6 → 9 (+50%) | +77% | 3/3 → 3/3 |
| T11 | 39 → 42 (+7%) | 7 → 7 | −3% | 3/3 → 3/3 |
| **T12** | 431 → 1298 (**+201%**) | 26 → 19 (−27%) | +24% | 0/3 → 0/3 |

Bold tasks are the ones the change applies to.

### Gates

| Gate | Result |
|---|---|
| Automatic check pass rate ≥ baseline | Pass: 28/33 vs 26/33 |
| No permission violations | Pass: 0 |
| Human questions ≤ baseline | Pass: 0 vs 1 |
| Explicit stop on budget | Unchanged: T12 runs end with no answer at all, as in the baseline, so no unverified conclusions are presented, but there is no explicit stop either |
| Grader pass rate ≥ baseline; no fabrication | **Pending** manual grading of both runs |
| At least one −20% target on applicable tasks | Met: model calls on T05, T07, T12; cost on T07 |
| No other metric worse by more than 10% | **Fails**: wall time on T04 (+20%), T05 (+21%) and T12 (+201%); cost on T12 (+24%) |

### Reading

- The change removes most polling: −81% polling calls, −39% input tokens,
  −21% model calls overall.
- The wall-time increase does not come from the wait itself: the server returns
  within 0.25 s of a job changing status, and T07, the longest waiting task, is
  unchanged. Wall time varies widely between repetitions (T04 312–596 s, T06
  144–352 s); the four reruns ran in a different session from the other 29.
  With 3 repetitions, a 20% wall-time difference is within that spread.
- T12 is longer for a known reason: with fewer polling calls it no longer
  exhausts its 25-call budget and runs into the 1,200 s time limit instead.
  The explicit budget stop is the fix for that task, not this change.
- T03 and T10 do not wait on jobs; their changes are run-to-run variation.

### Decision

Not accepted yet. By the registered rule the change fails on wall time, and the
manual grading is outstanding. The registered rule is not relaxed after the
fact.

Kept on the branch, not accepted (2026-11-18). The explicit budget stop is
built on top of it and compared against `opt-waiting`; both are judged again
against the baseline once the budget stop is measured.
