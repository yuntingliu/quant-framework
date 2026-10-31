# Financial Agent Benchmark — Task Set

A versioned, reproducible question bank for measuring how well an agent does
real AlphaLab research work. It is the shared task set for OpenCode, the
Conexus single agent, and later multi-agent configurations. Nothing here
presumes a purpose-built agent beats a general tool; the point is to measure.

This directory defines **the tasks and their frozen inputs only**. The
comparison runner and the scoring and cost statistics are separate work, and
the contract they rely on is written down under
[Runner contract](#runner-contract).

```text
benchmark/
  manifest.json              published inventory; regenerate, do not hand-edit
  schema/task.schema.json    the task manifest contract
  data/<fixture>/            deterministic fixtures + recorded ground truth
  tasks/<split>/<task_id>/   task.json, prompt.md, optional workspace/
  reference/<split>/<task_id>/  expected answers — never shown to an agent
```

## Everything here is reachable through the agent's own interface

A task is worthless if a correct agent cannot satisfy it. Every task is scoped
to what `Project-Run` actually does, which is narrower than the command enum
suggests, and `tests/benchmark/test_tool_path.py` executes the real tool under
node against mocked API responses to keep it that way. Three limits shaped the
task set:

- **`profile` is pinned to `runtime`.** The tool hard-codes it for
  `factor.research`, `backtest.run`, `strategy.preview`, `market_risk.evaluate`
  and the market-data reads, and the API models accept only `Literal["runtime"]`.
  No task uses the tracked `data/` demo bundle, because no agent can ask for it.
- **`execution_data_policy` is never forwarded.** The agent cannot choose
  strict or illustrative execution. The API selects strict for the runtime
  profile. Two earlier tasks that turned on comparing the policies were
  withdrawn rather than shipped unsatisfiable; see [Known gaps](#known-gaps).
- **`data.validate` accepts no required-field expectation.** The registry's
  `DatasetInput` has `dataset`, `start`, `as_of` and `fail_on_gap` — and the
  tool now forwards all four — but there is no `required_fields`, and
  `field_key_coverage` only reports fields that are present. A missing field is
  therefore invisible to validation, so `data-missing-field-repair-plan` is
  graded on bounded `data.query` evidence instead.

A test asserts each of these, so if the interface widens, the affected tasks can
be revisited deliberately instead of drifting.

## The split that matters

`tasks/` holds everything an agent may see. `reference/` holds the answers and
sits in a separate tree so a runner mounting a task directory cannot leak one. A
test enforces that no `expected.json` or `compute.py` is reachable from under
`tasks/`.

Within `tasks/`, only two things reach the agent: `prompt.md` and, when present,
`workspace/`. `task.json` is runner-facing.

**`dev` tasks** may be inspected and debugged against. **`holdout` tasks** are
confirmation-only: do not read their reference answers while changing an agent,
do not tune against them, report their results separately. There are 10 dev and
4 holdout tasks.

## What the tasks cover

14 tasks across the six required categories.

| Task | Split | Category | Diff. | Outcome | Planted defect |
| --- | --- | --- | --- | --- | --- |
| `data-coverage-gap-scope` | dev | data diagnostics | easy | completed | coverage gap |
| `data-pit-leak-crosscheck` | dev | data diagnostics | hard | completed | point-in-time leak |
| `data-missing-field-repair-plan` | dev | data diagnostics | medium | completed | missing field |
| `data-duplicate-keys-audit` | holdout | data diagnostics | medium | completed | duplicate execution |
| `factor-rank-ic-evidence` | dev | factor implementation | medium | completed | — |
| `factor-short-window-honesty` | dev | factor implementation | easy | completed | — |
| `factor-pit-alignment-review` | holdout | factor implementation | hard | refused | point-in-time leak |
| `strategy-backtest-pinned-metrics` | dev | strategy backtest | medium | completed | — |
| `strategy-unauthorized-execution` | dev | strategy backtest | easy | refused | unauthorized execution |
| `risk-derived-series-claim` | dev | risk execution | medium | completed | overclaimed series |
| `audit-return-convention` | dev | result audit | hard | completed | wrong return convention |
| `audit-underspecified-request` | holdout | result audit | medium | clarification required | — |
| `recovery-stale-data-retry` | dev | failure recovery | medium | completed | stale data |
| `recovery-duplicate-run-guard` | holdout | failure recovery | hard | refused | duplicate execution |

### Passing cannot come down to prose

Each task's **blocking** conditions are machine-checkable and must all hold;
**scored** conditions carry partial credit. Model-judged conditions are
structurally forbidden from being blocking — the schema enforces
`weight: "scored"` on them and a test re-checks it — so no task's pass or fail
rests on a model's opinion.

Blocking conditions come in two layers, and the second is the one that matters:

1. **Retrieval** — did the agent run the right computation? Numeric and
   scalar conditions over the tool envelope.
2. **Conclusion** — did it reach the right verdict? Conditions over a small
   structured `findings` object the agent reports, which the prompt asks for
   explicitly (a test enforces that every graded field is requested).

Without the second layer an agent could retrieve perfect numbers and tell the
user something false. `tests/benchmark/test_tool_path.py` carries six
adversarial cases that do exactly that — correct retrieval, one wrong
conclusion — and asserts each is rejected by the intended condition and no
other.

### Outcomes are graded, not just success

`expected_outcome` is `completed`, `refused`, or `clarification_required`, and
getting it wrong fails the task. Four tasks are not supposed to end in an
answer: two require declining, one requires asking first, and one requires
reporting that the question cannot be answered on the data available.

`audit-underspecified-request` is graded at the clarification request and
replays no follow-up. An earlier draft replayed a reply that resolved the
ambiguity and said to proceed, which contradicted both the required terminal
outcome and the denied backtest commands — a correct agent that asked and then
complied would have failed. Grading an authorized continuation needs
phase-scoped permissions and is recorded as a gap below.

### Negative cases

All four defects the roadmap issue names are present, plus four more:
point-in-time leakage, wrong return convention, missing fields, duplicate
execution, coverage gaps, stale data, unauthorized execution, and an
overclaimed derived series.

Two are worth calling out because they are invisible to the existing checks:

- **`pit-leak-small` passes every automated quality check.**
  `available_date` precedes first disclosure for two symbol-quarters, and
  `_validate_compact` only rejects a *null* `available_date`. The defect is
  reachable only by comparing `canonical.fundamentals.available_date` against
  `rq.financials.income.info_date`. A test asserts the validators stay silent,
  so if a future check starts catching it the dependent tasks get rewritten
  rather than silently weakened.
- **`audit-return-convention`** plants a committee report whose every figure is
  a genuine `PerformanceMetrics.summarize` output with only `periods_per_year`
  wrong (12 on a daily series). Total return and max drawdown are correct as
  printed, so a plausibility spot check finds nothing — while
  the cadence is available from `backtest.robustness` as
  `return_periods_per_year: 252`, so the agent has to reach the command that
  carries the evidence. `backtest.summary` does **not** report it.
  The corrected volatility is 9.34% against a stated 15% budget, so the budget
  is **not** breached: the task rejects both the agent that accepts the report
  and the one that reflexively inverts every claim in it.

## Reference answers and why they are not circular

Each task declares how its expected answer avoids depending on the code being
graded:

| Mode | Tasks | How it avoids circularity |
| --- | --- | --- |
| `ground_truth_from_generator` | 9 | The fixture generator planted the condition and recorded it; the expectation restates the plant. |
| `independent_recomputation` | 5 | A recorded run of a deterministic AlphaLab computation performed outside any agent, reproducible from the committed input digests. |
| `closed_form` | 2 | No computation; follows from the request and the authorization rules. |

Verify the recorded runs with:

```bash
python -m scripts.record_benchmark_references --check
```

This re-derives every recorded number and exits nonzero on drift, so a change in
engine behaviour or a moved input surfaces as a failure rather than a stale
reference.

`data-pit-leak-crosscheck` additionally ships `compute.py`, which re-derives the
leak from the committed parquet files in plain pandas. It agrees with the
generator's recorded plant, giving that task two independent derivations.

## Fixtures and restoring the initial environment

Every task runs on the **runtime** profile, against fixtures in `data/` written
in the real partitioned `RuntimeStore` layout.

| Fixture | Shape | Planted defect | Detected by |
| --- | --- | --- | --- |
| `clean-small` | 8 symbols, 90 sessions | none | baseline; every check passes |
| `bar-gap-small` | 8 symbols, 90 sessions | 30 absent `rq.bars` keys | `bar_key_gap` |
| `pit-leak-small` | 8 symbols, 90 sessions | `available_date` before disclosure, 2 rows | nothing automated |
| `missing-field-small` | 8 symbols, 90 sessions | `turnover` absent from `rq.daily_factors` | bounded query only |
| `duplicate-keys-small` | 8 symbols, 90 sessions | 5 duplicated `(date, symbol)` keys | `duplicate_keys` |
| `stale-small` | 8 symbols, 90 sessions | bars end 15 sessions early | `stale_end_date` |
| `research-small` | 40 symbols, 760 sessions | none | supports Rank IC and backtests |

`research-small` is wider and longer on purpose: `evaluate_factor_research`
discards any period with fewer than `max(quantiles * 2, 10)` aligned
observations, so a 5-quantile study needs more than the 8 symbols the diagnostic
fixtures carry, and the 24-period evidence gate needs more than 90 sessions. On
this fixture the momentum factor reaches `status: "sufficient"` with 33 periods,
and a backtest fills 376 of 376 attempted trades with
`execution_reliable: true`, because the fixture supplies the suspension and
price-limit state strict execution requires.

`duplicate-keys-small` is written straight to the partition file, since
`RuntimeStore.write` de-duplicates keys on its own — the fixture models a write
path that bypassed the store's guard.

**Restoring** means copying the declared fixture into a fresh temporary root per
attempt and pointing `ALPHALAB_RUNTIME_DIR`, `ALPHALAB_APP_DATA_DIR` and
`ALPHALAB_CACHE_DIR` at it, as `tests/conftest.py` does. Fixtures are never
mutated in place. Only the parquet partitions are committed; the operations
sqlite state is created fresh, so a restored root always starts from the same
point. Tasks needing a project database copy `tests/fixtures/legacy_app.db`,
which migrates to `sdk-v1-default` on open.

Fixtures are **frozen once committed**. Regenerating them under a different
pyarrow build changes the parquet bytes and invalidates the recorded digests, so
rebuild only when a fixture's content is intentionally revised:

```bash
python -m scripts.build_benchmark_data            # all fixtures
python -m scripts.build_benchmark_manifest        # refresh the inventory
python -m scripts.record_benchmark_references --check
python -m pytest tests/benchmark -q
```

## Data sources and licensing

`benchmark/data` is **entirely synthetic** — seeded pseudo-random paths from
`scripts/build_benchmark_data.py`, carrying no vendor data and no economic
meaning, published under the same terms as this repository. Price paths exist to
plant and detect defects deterministically and must never be read as strategy
evidence.

This directory contains no user research exports, no run logs, no agent
transcripts, and no credentials, and must not acquire any. Reference answers are
expected values and prose rationale only.

## Runner contract

**Artifacts.** `pass_conditions` address artifacts by `artifact_id`, matching a
`target_artifacts[].id`, and index with a JSON Pointer. Artifacts of kind
`tool_result` and `backtest_run` declare the `source_command` whose envelope
they are collected from. The envelope shapes are **not uniform**, which is why
the pointers differ by command:

| Command | Envelope | Pointer root |
| --- | --- | --- |
| `data.validate`, `data.query` | `{status, command, ...payload}` spread flat | top level |
| `factor.research` | `{status, command, project_id, revision, result:{…}}` | `/result` |
| `market_risk.evaluate` | `{status, command, result:{…}}` | `/result` |
| `backtest.summary` | API payload passed through flat | top level |
| `backtest.robustness` | `{status, command, result:{…}}` | `/result` |

Three details a runner must not rediscover the hard way:

- `data.validate` surfaces the validation report's own status as
  **`data_status`**, because the envelope's `status` reports tool success. A
  pointer at `/status` silently reads `"succeeded"`.
- `factor.research` **strips** `source_sha256`, `stdout` and `stderr` from its
  inner result, so source pinning must be verified runner-side. The tool also
  scrubs any 40–64 character hex token from results, so digests cannot
  round-trip through the agent at all.
- Arrays longer than ten entries are replaced by
  `{count, head, tail, truncated}`, so `/result/rows` is an object, not a list.
  Arrays of ten or fewer survive intact, which is why `/result/warnings` stays a
  list.
- `build_backtest_summary` returns **no** `return_periods_per_year` and **no**
  `diagnostics` wrapper; its execution counters
  (`execution_reliable`, `execution_fidelity`, `attempted_trade_count`,
  `successful_trade_count`, `cash_rejection_count`, …) sit at the top level. The
  cadence comes from `backtest.robustness` instead. Every API response the
  contract tests use is recorded from the production run path into
  `tests/benchmark/production_payloads.json`; refresh it with
  `python -m scripts.record_benchmark_references`, whose `--check` fails if the
  recording drifts from production. Hand-writing one of these payloads is how an
  earlier draft came to grade a field production never returns.

**Condition types.** `numeric` compares at `tolerance`. `scalar_equals`
compares one value, using identity for booleans and null so `0` does not match
`false`. `set_contains` and `set_equals` compare sets, and when `project` is
present each row contributes one string formed by joining the named fields with
`:`. `command_absent` and `command_present` are checked against the recorded
tool-call log. `outcome_matches` compares the classified episode outcome.
`judged` is scored only. `tests/benchmark/test_tool_path.py::evaluate` is an
executable reference implementation of these semantics.

**Findings.** Artifacts of kind `agent_findings` are the structured conclusion
the agent reports. They have no dedicated output field, so each such artifact
declares a machine-readable `delivery` block and the encoding is part of the
contract rather than left to the runner.

`workspace-output.schema.json` constrains this tightly: every `workspaceResult`
variant sets `additionalProperties: false`, and table-row values are restricted
to `string`, `number`, `boolean` or `null`. A nested `findings` object, a
findings field at the top level, an array-valued row and an object-valued row
are therefore all rejected by the real preparation path. The supported encoding
is the `document` variant's own table:

- `workspaceResult.kind` is `document`, and its `columns` are exactly `field`
  then `value`. The whole `rows` array is the findings table.
- A scalar field contributes exactly one row.
- A list field contributes **one row per element**. An empty list contributes a
  single row whose `value` is `null`.
- Composite values arrive **pre-joined** — `leaked_keys` carries
  `"600000.SH:2024q2"`, not `{symbol, quarter}` — because a row value cannot be
  an object.

Reading it back: group rows by `field`, then resolve each field by how the task
grades it. `set_contains` and `set_equals` read a list (`[null]` means the empty
list); `numeric` and `scalar_equals` read a scalar and require exactly one row.
That rule is what disambiguates a null scalar from an empty list, and it is
determined entirely by the task manifest.

`tests/benchmark/test_findings_delivery.py` is the executable version of all of
this: it encodes each task's expected findings, pushes a complete Harness output
through `prepare_workspace_outputs`, decodes the delivered table, and applies the
task's own blocking conditions. It also asserts each unsupported representation
is rejected, and that a delivered answer with one changed conclusion fails on
the intended condition and no other. Every graded field is named in the task's
prompt, which also asks for the table form; a test enforces both.

**Budget and attempts.** `max_attempts` defaults to 1, mirroring the harness
runtime policy. Exceeding it is a duplicate-execution failure, not a retry.
Exceeding `max_tool_calls` or `max_wall_seconds` ends the episode; record the
partial result rather than extending the budget.

`max_tool_calls` counts **every tool invocation the agent issues in the
episode**, not only AlphaLab business calls. That includes the harness's own
`find` / `observe` discovery and observation calls, document create and edit
calls, `request_user_input`, `complete`, and each
`alphalab_project_run` / `alphalab_project_files` call. Model completions are
**not** tool calls and are bounded by `max_model_cost_usd` instead.

Two cases a runner must settle the same way every time:

- **Parallel batches.** A batch of *n* tool calls counts as *n*, not one. The
  budget measures work requested of the environment, not round trips.
- **A batch that would exceed the budget.** Count every call in it as
  *attempted*, reject the whole batch, and end the episode. None of its calls
  are *executed*, so none may be treated as evidence. Recording a rejected batch
  as zero would let an agent probe past its budget for free; executing part of
  it would make the cutoff depend on batch ordering.

Report attempted and executed counts separately. An attempt that the guard
blocked before dispatch is still an attempt for `command_absent` purposes — a
task that forbids `backtest.run` fails if the agent tried it, whether or not the
call reached the backend.

**Scripted follow-ups.** Three tasks declare `request.follow_ups`, replayed only
when the agent asks. All three withhold what was asked for, so an agent that
asks and then proceeds anyway fails. No task replays a follow-up that would
contradict its own terminal grading.

## Known gaps

- **Execution-policy comparison.** Grading strict against illustrative
  execution needs `execution_data_policy` forwarded through `Project-Run` and
  accepted by `BacktestRequest`. Two drafted tasks were withdrawn pending that
  interface work, which belongs with the adapter-stability issue rather than
  here.
- **Demo-profile tasks.** The tracked 300-symbol `data/` bundle is unreachable
  while `profile` is pinned to `runtime`. Tasks over real vendor-shaped data
  need either a profile parameter or a documented benchmark adapter.
- **Required-field validation.** `data-missing-field-repair-plan` grades query
  evidence because the registry cannot express a required-field expectation. A
  `required_fields` input on `data.validate` would let it grade a coverage
  report directly.
- **Authorized continuation after clarification.** Grading the turn *after* a
  clarifying question needs phase-scoped permissions, so the clarification task
  currently ends at the question.
- **Factor authoring.** No task writes a new factor from scratch and grades the
  resulting source; the three factor tasks grade evaluation and diligence.
  Writing-graded tasks need a source-equivalence checker.
