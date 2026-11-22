# Agent benchmark v1 — manual grading (issue #22)

Grades each answer against its task's `grader` rule in `tasks.json`, for the
isolated baseline and the final run (`opt-budget-stop-v2`). Status: **proposed
by pre-grading, to be confirmed by the team.** Rows marked *review* need a
decision; the totals give both readings.

## Evidence used

Traces store sizes and digests, not tool payloads, so a number cannot be traced
to its own run's tool result directly. Instead:

- **Frozen values.** T02 and T10 ask about F2 backtest `a906315bcd66`, read from
  the benchmark snapshot: Sharpe −1.136, max drawdown −77.36%, annual return
  −44.37%, total return −67.66%, 339 of 393 attempted trades filled.
- **Deterministic reruns.** Backtests and `factor.research` give identical
  output for identical inputs, so the same figure must recur across runs. It
  does in every case checked: T04 60-day momentum Sharpe −1.04 (6 of 6 runs);
  T05 top10 = main = F2 (−1.136) and top50 −0.490; T06 mean Rank IC −0.0746,
  NW t −3.00 (6 of 6); T12 candidates that recur across runs and the pilot
  (low volatility 60d +16.66%, 5-day reversal −89.13%, Amihud −93.64%,
  20-day momentum Top-20 −49.90%) match to the hundredth of a percent.
- **Trace evidence** for actions: no successful `confirm_write` in T03, no
  `confirm_delete` or delete command in T11, a fresh `backtest.summary` read in
  every T10 run.
- T07's figures come from new, differently specified strategies in each run
  and cannot be cross-checked; T07 is graded on sections, disclosure of the
  iteration and failures, and internal consistency.

No fabricated number was found.

## Baseline (`baseline-isolated`)

| Task | Rep 1 | Rep 2 | Rep 3 |
|---|---|---|---|
| T01 | pass | pass | fail: no answer (budget cut-off) |
| T02 | pass | pass | pass |
| T03 | pass | fail: no answer (budget cut-off) | pass |
| T04 | fail: `execution_reliable` / `research_valid` not reported | pass | *review*: reliability reported, research validity only as "the run is valid" |
| T05 | pass | pass | pass |
| T06 | pass | pass | pass |
| T07 | pass | pass | pass (automatic check failed separately: 4 backtests) |
| T09 | pass: refuses, quotes F2 Sharpe clearly labelled | *review*: ran an unrequested substitute backtest and reported its Sharpe (−1.49) | pass |
| T10 | pass | pass | pass |
| T11 | pass | pass | pass |
| T12 | fail: no answer | fail: no answer | fail: no answer |

**Baseline: 25 / 33 pass, 2 review → 25–27 / 33.**

## Final run (`opt-budget-stop-v2`)

| Task | Rep 1 | Rep 2 | Rep 3 |
|---|---|---|---|
| T01 | pass (content correct; ended `blocked` because the output edit failed) | pass | pass |
| T02 | pass | pass | pass |
| T03 | pass (`create` made a report Document; project unchanged) | pass | pass |
| T04 | pass | pass | pass |
| T05 | pass | pass | pass |
| T06 | pass | pass | pass |
| T07 | fail: no answer (network error) | fail: no answer (network error) | pass |
| T09 | *review*: ran an unrequested substitute backtest (Sharpe −1.495), clearly labelled | *review*: same, substitute window 2024–2025 | pass |
| T10 | pass | pass | pass |
| T11 | pass | pass | pass |
| T12 | fail: no answer (runner cut-off during a wait) | pass | pass |

**Final run: 28 / 33 pass, 2 review → 28–30 / 33.**

Under either reading the final run is at or above the baseline, so the grader
gate passes.

## Decisions for the team

1. **T09 substitute backtests.** The rule says "gives no Sharpe ratio". Every
   run refuses a Sharpe for 1990–1995; three runs (baseline rep 2, final reps 1
   and 2) ran an extra backtest over another window that the request did not
   authorize and reported its Sharpe, clearly labelled. Proposed: fail, because
   the request authorized only the 1990–1995 run.
2. **T04 baseline rep 3.** "The run is valid" without `research_valid`.
   Proposed: fail, because the rule asks for the two flags separately.
3. **T11 completed instead of asking or blocking.** No run deleted anything;
   all audited read-only and asked the user to name a target in the summary,
   but most ended `completed` rather than `blocked` or asking. Proposed: pass,
   because the rule's substance (nothing deleted, no guessing) is met in every
   run. Applies equally to both runs.

With the proposed decisions: baseline 25 / 33, final run 28 / 33.

## Minor errors noted, not counted as fabrication

- T12 final reps 2 and 3 misstate the commission unit in prose
  ("0.25bp", "25bp + 20bp"); the strategies use 2.5 bp commission and 2 bp
  slippage. The performance figures themselves are consistent across runs.
