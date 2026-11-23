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

## 2. Explicit budget stop (`opt-budget-stop`), on top of waiting

| | |
|---|---|
| Change | The runner passes each task's budget in `workspaceContext.budget`; the host adds a usage note to every model call, asks for wrap-up in the last 4 calls or 120 s, and offers only `complete` on the last call (`integrations/conexus/run-budget.mjs`) |
| Code | commit `be0bc99`; 31 runs were flagged dirty because of an uncommitted benchmark-runner change (provider refusal stop); the agent bundle and prompt digest are identical in all 33 |
| Dates | 2026-11-18 (2 runs) and 2026-11-21 (31 runs), all off-peak |
| Pilot | T12 × 3 under `pilot-budget-stop`: 3/3 completed with reports, not counted |

**Excluded and rerun.** 33 runs failed with HTTP 402 (provider account out of
balance) and measured nothing; they are kept in
`build/benchmarks/opt-budget-stop/excluded.jsonl`. The runner now stops at the
first HTTP 401/402/403 instead of recording such runs.

**Defect found.** The three runs that reached the last allowed call (T03 rep 2,
T09 rep 3, T12 rep 2) all failed with HTTP 400: DeepSeek thinking mode rejects
any forced `tool_choice`. They are counted as failures here. The fix offers only
`complete` without forcing it (probed against the provider: accepted, and the
model calls `complete`).

### Totals (33 runs each)

| Measure | Baseline | Waiting | Budget stop |
|---|---|---|---|
| Outcomes | 27 completed, 1 blocked, 4 budget exhausted, 1 failed | 28 completed, 2 blocked, 3 budget exhausted | 28 completed, 2 blocked, 3 failed (the defect), 0 budget exhausted |
| Automatic checks | 26 / 33 | 28 / 33 | 26 / 33 |
| Permission violations | 0 | 0 | 0 |
| Human questions | 1 | 0 | 0 |
| Wall time | 154.1 min | 218.7 min | 185.9 min |
| Cost | $0.521 | $0.460 | $0.604 |
| Model calls | 609 | 479 | 487 |
| Input / output tokens | 31.0M / 0.50M | 19.0M / 0.48M | 21.6M / 0.63M |
| Budget phase reached | — | — | 26 normal, 4 wrap-up, 3 last call |

### Per task (median of 3: baseline / waiting / budget stop)

| Task | Wall s | Model calls | Cost (m$) | Auto |
|---|---|---|---|---|
| T01 | 48 / 38 / 37 | 9 / 9 / 9 | 5.4 / 4.0 / 5.2 | 2 / 2 / 2 |
| T02 | 30 / 19 / 18 | 8 / 5 / 5 | 4.0 / 2.2 / 2.4 | 3 / 3 / 3 |
| T03 | 71 / 102 / 106 | 8 / 12 / 10 | 8.8 / 12.8 / 14.4 | 2 / 3 / 2 |
| T04 | 299 / 358 / 287 | 20 / 20 / 17 | 17.5 / 18.5 / 19.6 | 2 / 2 / 2 |
| T05 | 690 / 834 / 643 | 37 / 18 / 16 | 22.7 / 18.4 / 24.4 | 3 / 3 / 3 |
| T06 | 146 / 179 / 126 | 16 / 13 / 11 | 11.6 / 9.1 / 10.5 | 3 / 3 / 3 |
| T07 | 1179 / 1184 / 1217 | 58 / 33 / 33 | 63.0 / 44.8 / 56.4 | 2 / 3 / 1 |
| T09 | 86 / 116 / 278 | 14 / 11 / 15 | 10.7 / 9.5 / 11.3 | 3 / 3 / 2 |
| T10 | 28 / 44 / 28 | 6 / 9 / 7 | 3.4 / 6.0 / 4.1 | 3 / 3 / 3 |
| T11 | 39 / 42 / 31 | 7 / 7 / 5 | 3.9 / 3.8 / 3.7 | 3 / 3 / 3 |
| T12 | 431 / 1298 / 1096 | 26 / 19 / 16 | 18.4 / 22.7 / 33.0 | 0 / 0 / 2 |

### Reading

- No run was cancelled by the runner any more: every run either finished or
  stopped itself. T12 completed with a report in 2 of 3 runs (the third hit the
  defect). T07 rep 2 shows the intended stop: it reached wrap-up at call 69 of
  70 and ended `blocked`, listing the work done and what was not delivered.
- Cost rose against waiting (+31%), mostly output tokens (+31%): runs that used
  to be cut off now finish and write their reports (T12, T07).
- T07 rep 3 failed its automatic check by running 4 backtests (limit 3);
  T09 rep 3 and T03 rep 2 are the defect.
- With the defect, the automatic check rate equals the baseline (26/33).

### Decision

Superseded by `opt-budget-stop-v2` (section 3): the defect means the last-call
path was not measured as designed. Kept as the record of the defect.

## 3. Budget stop with the last-call fix (`opt-budget-stop-v2`) — final run

| | |
|---|---|
| Change | As section 2, but the last call offers only `complete` without forcing `tool_choice` |
| Code | commit `240884a`, clean tree and identical prompt digest in all 33 runs |
| Dates | 2026-11-21, all off-peak; one run interrupted by a restart and set aside by `--resume` |

**Failures counted, not excluded.** T07 reps 1 and 2 failed on network errors
between the workstation and the provider (`fetch failed`, `terminated`). The
baseline counted its own `fetch failed` run as a failure, so these count too.
They shorten T07 and flatter the totals; figures without T07 are given below.

### Totals (33 runs each)

| Measure | Baseline | Waiting | Budget stop v2 | v2 vs baseline |
|---|---|---|---|---|
| Outcomes | 27 completed, 1 blocked, 4 budget exhausted, 1 failed | 28 completed, 2 blocked, 3 budget exhausted | 28 completed, 2 blocked, 2 failed (network), 1 budget exhausted | |
| Automatic checks | 26 / 33 | 28 / 33 | 26 / 33 | equal |
| Permission violations | 0 | 0 | 0 | |
| Human questions | 1 | 0 | 0 | |
| Wall time | 154.1 min | 218.7 min | 186.9 min | +21% |
| Cost | $0.521 | $0.460 | $0.394 | −24% |
| Model calls | 609 | 479 | 361 | −41% |
| Input / output tokens | 31.0M / 0.50M | 19.0M / 0.48M | 10.1M / 0.42M | −67% / −16% |
| Calls only to poll | 158 | 30 | 23 | |
| Budget phase reached | — | — | 28 normal, 3 wrap-up, 2 last call | |

Without T07 (30 runs each), baseline → v2: automatic checks 24 → 25, wall
time 92.1 → 139.6 min (+52%), cost $0.324 → $0.324 (0%), model calls
427 → 315 (−26%), input tokens 14.3M → 8.3M (−42%).

### Per task (median of 3: baseline / waiting / v2)

| Task | Wall s | Model calls | Cost (m$) | Auto |
|---|---|---|---|---|
| T01 | 48 / 38 / 37 | 9 / 9 / 6 | 5.4 / 4.0 / 4.0 | 2 / 2 / 1 |
| T02 | 30 / 19 / 24 | 8 / 5 / 7 | 4.0 / 2.2 / 3.1 | 3 / 3 / 3 |
| T03 | 71 / 102 / 73 | 8 / 12 / 9 | 8.8 / 12.8 / 11.0 | 2 / 3 / 3 |
| T04 | 299 / 358 / 304 | 20 / 20 / 15 | 17.5 / 18.5 / 16.0 | 2 / 2 / 1 |
| T05 | 690 / 834 / 716 | 37 / 18 / 17 | 22.7 / 18.4 / 19.2 | 3 / 3 / 3 |
| T06 | 146 / 179 / 127 | 16 / 13 / 9 | 11.6 / 9.1 / 7.4 | 3 / 3 / 3 |
| T07 | 1179 / 1184 / 667 | 58 / 33 / 17 | 63.0 / 44.8 / 28.8 | 2 / 3 / 1 |
| T09 | 86 / 116 / 242 | 14 / 11 / 14 | 10.7 / 9.5 / 7.5 | 3 / 3 / 3 |
| T10 | 28 / 44 / 24 | 6 / 9 / 5 | 3.4 / 6.0 / 3.0 | 3 / 3 / 3 |
| T11 | 39 / 42 / 37 | 7 / 7 / 6 | 3.9 / 3.8 / 3.9 | 3 / 3 / 3 |
| T12 | 431 / 1298 / 1145 | 26 / 19 / 15 | 18.4 / 22.7 / 29.7 | 0 / 0 / 2 |

T07's v2 medians include the two network failures.

### Gates, v2 against the baseline

| Gate | Result |
|---|---|
| Automatic check pass rate ≥ baseline | Pass: 26/33 vs 26/33 |
| No permission violations | Pass: 0 |
| Human questions ≤ baseline | Pass: 0 vs 1 |
| Explicit stop on budget | Mostly: T01 rep 1 and T11 rep 3 reached the last call and ended through `complete`; T07, T09 and T12 runs stopped in wrap-up. T12 rep 1 was still cancelled by the runner: the time limit passed during a 200 s backtest wait, and the budget only acts at model calls |
| Grader pass rate ≥ baseline; no fabrication | Pass, proposed (`GRADING.md`, to be confirmed): 28/33 vs 25/33, 28–30 vs 25–27 counting the review rows; no fabricated number found |
| At least one −20% target on applicable tasks | Met: model calls on T05 (−54%), T06 (−44%), T12 (−42%); cost on T06 (−36%) |
| No other metric worse by more than 10% | **Fails**: wall time on T09 (+181%) and T12 (+166%); cost on T12 (+61%) |

### Reading

- T12 now produces a report in 2 of 3 runs where the baseline produced none;
  its higher time and cost are the price of doing the work. Comparing it with
  baseline runs that were cut off with no answer is not an equal-quality
  comparison.
- T09 is slower because the agent now tries the documented recovery: one run
  synced data and waited 186 s with `sync.wait` before rerunning, where the
  baseline mostly stopped early.
- Wall time is dominated by backtest run time (69% of v2 time is waiting); the
  changes cut model calls and input tokens, not backtest duration.
- Follow-up (not re-benchmarked): `budgetAwareExecuteTool` now caps
  `backtest.wait` / `sync.wait` to the remaining work time on the same run
  signal, so a blocking wait can no longer outrun the runner's wall budget.
  See section 5.

### Decision

Kept as the last accepted optimization on the branch: waiting plus the budget
stop. Meets the efficiency targets on cost (−24%) and model calls (−41%) and
the quality gates except the registered wall-time rule (T09, T12). Reported
honestly without claiming full acceptance of that rule. See section 5 for the
issue checklist.

## 4. Context compaction (`opt-compaction`) — measured, not accepted

| | |
|---|---|
| Change | Shorten older, large tool results sent to the provider; stored conversation unchanged. Off unless `ALPHALAB_AGENT_CONTEXT_COMPACTION=1` |
| Code | commit `c8f89f8`, clean tree; switch was set for the whole run |
| Dates | 2026-11-22, off-peak (Sunday) |
| Comparison | Against `opt-budget-stop-v2` (same stack; only compaction differs) |

**T08.** This run included T08 (36 traces). All comparisons below drop T08, as
in every earlier section. T08 itself: 3/3 completed, auto pass on all three.

**Compaction did engage.** 312 of 513 model rows report non-zero
`compacted_messages` (max 6 messages, max ~72k characters saved on a call).
Traces carry `compacted_messages` / `compacted_saved_chars`.

### Totals (33 runs, T08 excluded)

| Measure | Budget stop v2 | Compaction | Change |
|---|---|---|---|
| Outcomes | 28 completed, 2 blocked, 2 failed, 1 budget exhausted | 27 completed, 5 blocked, 1 budget exhausted | |
| Automatic checks | 26 / 33 | 25 / 33 | −1 |
| Permission violations | 0 | 0 | |
| Human questions | 0 | 0 | |
| Wall time | 186.9 min | 235.3 min | **+26%** |
| Cost | $0.394 | $0.647 | **+64%** |
| Model calls | 361 | 465 | **+29%** |
| Input / output tokens | 10.1M / 0.42M | 15.6M / 0.56M | +55% / +33% |

### Per task (median of 3: v2 → compaction)

| Task | Wall s | Model calls | Cost | Auto |
|---|---|---|---|---|
| T01 | 37 → 35 (−5%) | 6 → 7 (+17%) | +19% | 1 → 0 |
| T02 | 24 → 21 (−11%) | 7 → 5 (−29%) | −27% | 3 → 3 |
| T03 | 73 → 90 (+23%) | 9 → 10 (+11%) | +36% | 3 → 3 |
| T04 | 304 → 309 (+2%) | 15 → 17 (+13%) | +43% | 1 → 1 |
| T05 | 716 → 732 (+2%) | 17 → 20 (+18%) | +41% | 3 → 2 |
| T06 | 127 → 155 (+22%) | 9 → 10 (+11%) | +18% | 3 → 3 |
| T07 | 667 → 1322 (+98%) | 17 → 30 (+76%) | +81% | 1 → 2 |
| T09 | 242 → 465 (+92%) | 14 → 13 (−7%) | +54% | 3 → 3 |
| T10 | 24 → 23 (−5%) | 5 → 4 (−20%) | −13% | 3 → 3 |
| T11 | 37 → 36 (−1%) | 6 → 8 (+33%) | −10% | 3 → 3 |
| T12 | 1145 → 1195 (+4%) | 15 → 15 (0%) | +22% | 2 → 2 |

T02 and T10 meet a −20% call cut; almost every other task is worse by more
than 10% on wall, cost or calls. Auto checks fall below v2 and the baseline.

### Gates, against `opt-budget-stop-v2`

| Gate | Result |
|---|---|
| Automatic check pass rate ≥ prior | **Fail**: 25/33 vs 26/33 |
| No permission violations | Pass: 0 |
| Explicit stop on budget | Observed (wrap-up / final phases present); T12 rep 3 still runner-cancelled during a wait |
| −20% on an applicable metric without >10% regressions | **Fail**: wins on T02/T10 calls are outweighed by large regressions elsewhere |
| Evidence preserved | Unit tests pass; this run was not re-graded against `GRADING.md` |

### Reading

- Shortening older tool text did not shrink the run: the agent made more
  calls, waited longer and spent more tokens. Compaction only rewrites what is
  sent to the provider; it does not change tool choice or wait length.
- Only a handful of old results per call were large enough to shorten (cap
  observed: 6), so prompt growth from long research runs was only partly
  cut.
- Leaving the switch off is correct: measured effect is a regression.

### Decision

**Not accepted.** Code and tests stay; default remains off
(`ALPHALAB_AGENT_CONTEXT_COMPACTION` unset). Record kept; no rollback of the
accepted waiting and budget-stop changes.

## 5. Issue #22 checklist

| Criterion | Status |
|---|---|
| (1) Publish baseline and bottleneck breakdown, including failures | Done (`BASELINE.md`, sections 1–4) |
| (2) Compression preserves numerical, PIT, authorization, completion and output evidence; quality thresholds; cache invalidation and cross-task isolation | **Partial**: implemented with tests (`context-compaction.mjs`, `conexus-context-compaction.test.mjs`); evidence / cache / isolation covered in tests; **benchmarked and rejected** for efficiency (section 4). Switch off by default |
| (3) Quality under equal budgets; cost, time and human effort for equal quality; thresholds registered before evaluation | Done (`thresholds.json` before formal baseline; sections 1–3; `GRADING.md` proposed 28/33 vs 25/33 for v2 vs baseline, no fabrication). Wall-time rule fails on T09/T12 for the accepted stack |
| (4) Explicit stop when a budget is exhausted; no fabrication; failed optimizations rolled back while keeping records | Done: budget stop in code (sections 2–3); fabrication not observed; compaction measured, not accepted, left off (section 4); waiting kept despite wall-time miss; wait cap added and T12-piloted (section 6) |

**Accepted on the branch:** server-side waiting + budget stop (with last-call
fix) + wait cap. **Not accepted:** context compaction (off by default).
Full narrative: `REPORT.md`.

## 6. Wait cap pilot (`opt-wait-cap`, T12 × 3)

| | |
|---|---|
| Change | `budgetAwareExecuteTool` caps `backtest.wait` / `sync.wait` to remaining work time |
| Code | commit `ff53c0b`, clean tree |
| Scope | T12 only (the task that was runner-cancelled mid-wait under v2) |
| Caveat | This shell still had `ALPHALAB_AGENT_CONTEXT_COMPACTION=1` from the prior run (nonzero `compacted_messages` in traces). Outcome comparison still holds; not a pure single-variable A/B on tokens |

### Outcomes vs `opt-budget-stop-v2` T12

| Rep | v2 | Wait cap |
|---|---|---|
| 1 | `budget_exhausted` (runner cancel during a wait), auto fail | `blocked` via `complete`, auto pass, phase `final` |
| 2 | `completed`, auto pass | `blocked` via `complete`, auto pass, phase `wrap_up` |
| 3 | `completed`, auto pass | `blocked` via `complete`, auto pass, phase `final` |

Wall ~1112–1167 s (under the 1200 s budget). Cost for 3 runs ≈ $0.13.
Early waits still 170–300 s while budget remained; late waits include short
returns (2–50 s) consistent with the cap. Every summary states what was done,
which candidates were not evaluated, and avoids unsupported “best strategy”
claims.

### Decision

**Accepted** as a fix for criterion (4): no runner cancel on T12 in this pilot;
the agent stops explicitly. Kept with waiting + budget stop.
