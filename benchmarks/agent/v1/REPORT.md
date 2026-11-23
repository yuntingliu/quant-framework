# Issue #22 report — Agent context, tools, and runtime efficiency

Closes [issue #22](https://github.com/yuntingliu/quant-framework/issues/22).
Frozen tasks and gates: `tasks.json`, `thresholds.json`, `FROZEN.sha256`.
Detailed run logs: `BASELINE.md`, `RESULTS.md`, `GRADING.md`.

## Summary

We published an isolated baseline, tried four optimizations one at a time, and
kept the ones that improved cost and call count without breaking quality gates.
Context compaction was implemented and measured, then left **off by default**
because it made runs worse. A wait-budget cap closes the remaining hole where
the runner cancelled mid-wait.

| Stack kept on the branch | Result vs isolated baseline (33 runs, T08 excluded) |
|---|---|
| Server-side `backtest.wait` / `sync.wait` | Fewer polling turns; wall time rose |
| Explicit run-budget stop (wrap-up + last call → `complete`) | Cost **−24%**, model calls **−41%**, input tokens **−67%**; auto checks equal (26/33) |
| Cap waits to remaining work time | T12 pilot: **0** runner cancels (v2 had 1); all three runs `blocked` with disclosure |

Wall time is still dominated by backtest duration (~70%). The registered
“no metric worse by >10%” rule fails on wall for T09/T12 when those tasks now
finish work the baseline cut off. Reported honestly; not claimed as full
acceptance of that rule.

## What we measured

- Model: `deepseek-flash`, DeepSeek off-peak prices ($0.15 / $0.003 / $0.60 per
  1M uncached in / cached in / out).
- Isolation: restore snapshot + fresh `alphalab dev serve` per task
  (`scripts/benchmark_isolation.py`).
- Formal comparisons: 11 tasks × 3 (T08 excluded), except the wait-cap pilot
  (T12 × 3 only).

### Bottlenecks (baseline)

Waiting on jobs ~44% of wall; large prompts from tool results; cancelled runs
with no explicit stop. Full breakdown in `BASELINE.md`.

### Accepted path (baseline → waiting → budget-stop-v2)

| Measure | Baseline | Budget stop v2 |
|---|---|---|
| Auto checks | 26/33 | 26/33 |
| Wall | 154 min | 187 min (+21%) |
| Cost | $0.52 | $0.39 (−24%) |
| Model calls | 609 | 361 (−41%) |
| Human questions | 1 | 0 |
| Permission violations | 0 | 0 |

Proposed grader rate (`GRADING.md`, team confirm): **28/33** vs **25/33**.
No fabricated numbers found.

### Rejected: context compaction

Optional (`ALPHALAB_AGENT_CONTEXT_COMPACTION=1`). Unit tests cover evidence
preservation, cache reuse/invalidation, and cross-run isolation. Full 33-run
comparison vs v2: auto 25/33, wall +26%, cost +64%, calls +29%. **Not
accepted;** default remains off.

### Wait cap (T12 pilot)

Under v2, T12 rep 1 was runner-cancelled during a ~200 s wait because the
budget only acted at model calls. After `budgetAwareExecuteTool`, T12 × 3 all
ended `blocked` through `complete` with auto pass, under the 1200 s budget,
and summaries listed unevaluated candidates. Caveat: the pilot shell still had
compaction env set; see `RESULTS.md` §6.

## Code changes (high level)

| Area | Change |
|---|---|
| `apps/api` backtest/sync wait | Blocking wait up to 300 s (`backtest.wait`, `sync.wait`) |
| `scripts/benchmark_isolation.py` | Drop SQLite `-wal`/`-shm`/`-journal` on restore |
| `integrations/conexus/run-budget.mjs` | Budget phases, last-call tools=`complete` only (no `tool_choice`), wait cap by remaining work time |
| `integrations/conexus/model-providers.mjs` | Wire budget + optional compaction |
| `integrations/conexus/local-host.mjs` | Wrap `executeTool` with wait cap |
| `integrations/conexus/context-compaction.mjs` | Optional prompt-side compaction (off by default) |
| `integrations/conexus/agent-trace.mjs` | `budget_phase`, compaction stats |
| `scripts/agent_benchmark.py` | Pass task budget; stop on provider 401/402/403; record phase |
| Benchmark docs | `BASELINE.md`, `RESULTS.md`, `GRADING.md`, this report |
| Tests | `tests/conexus-run-budget.test.mjs`, `tests/conexus-context-compaction.test.mjs`, contract updates |

`vendor/conexus` and `build/conexus` were not modified.

## Issue checklist

1. **Baseline + bottlenecks (incl. failures)** — done.
2. **Compression + evidence / isolation / thresholds** — implemented and tested;
   efficiency thresholds **not** met; left off by default; record kept.
3. **Equal-budget quality; cost/time/effort; thresholds pre-registered** — done;
   wall-time rule miss on T09/T12 documented.
4. **Explicit stop; no fabrication; rollback failed opts** — done (budget stop +
   wait cap; compaction rejected in place).

## How to reproduce

```powershell
.venv\Scripts\python.exe scripts\agent_benchmark.py --label <name> --repeat 3
# optional compaction:
$env:ALPHALAB_AGENT_CONTEXT_COMPACTION = "1"
node scripts/agent_trace_summary.mjs build/traces/<name> --price-in 0.15 --price-cached 0.003 --price-out 0.60
```

Do not set the compaction env for normal runs. Leave the repo untouched while a
benchmark is running.
