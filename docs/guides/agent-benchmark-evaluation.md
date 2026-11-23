# Agent benchmark evaluation

[Issue #9](https://github.com/yuntingliu/quant-framework/issues/9) accepts two file-based inputs. It scores the common trial record after a run and applies the same artifact and policy checks to OpenCode and Conexus.

| Producer | Input contract | Evaluator option |
| --- | --- | --- |
| Issue #7 | Directory containing `benchmark/manifest.json`, `benchmark/tasks/`, `benchmark/reference/`, `benchmark/schema/task.schema.json`, and the checksummed `benchmark/data/` fixtures. | `--suite-root` before `freeze` or `evaluate` |
| Issue #8 | Neutral trial records with task and configuration identifiers, repeat index, runtime status and observed outputs, resource use, and evidence. Export one JSON file per record or a JSON array. | `evaluate --records`; use `--observations` for trusted evidence collected separately |

The evaluator can read the task directory and recorded results from independent checkouts. When the task suite is in the evaluator checkout, `--suite-root` defaults to that checkout. The checked-in offline example uses a small synthetic suite and JSON records on the base project. Issue #7 and #8 implementations can develop and land separately. Formal evaluation still requires real task data and genuine, attributable run records from the agreed contracts. [Issue #7 task inputs](https://github.com/yuntingliu/quant-framework/pull/26) · [Issue #8 run records](https://github.com/yuntingliu/quant-framework/pull/39)

## Dependency status and known gaps

Issue #9 depends on Issue #7 (task set) and Issue #8 (common runner). Neither is merged into `main` yet, so this evaluator consumes their **file contracts** and exercises them with a synthetic suite. The following are intentionally left as gaps until those inputs land:

- **Issue #7 task set**: `benchmark/manifest.json`, `benchmark/tasks/`, `benchmark/reference/`, `benchmark/schema/task.schema.json` and the frozen `benchmark/data/` fixtures. Until it is merged, `--suite-root tests/fixtures/agent_benchmark` supplies a synthetic development suite only. A missing manifest or schema fails `freeze` with a message pointing at the Issue #7 layout.
- **Issue #8 records**: real neutral `TrialRecord` exports and trusted `--observations` sidecars. Until they are merged, the demo uses scripted records; absent cells appear in `protocol.missing_trials` and never count as qualified.
- **Paid adapters**: `preflight-paid` refuses `runner_supports_paid=False` stub adapters, so the default CI path performs no real calls or charges.
- **Human review samples**: without real blinded reviews, `human_argument_score` stays unassessed and confirmation thresholds do not pass.

Development reports keep `protocol.claim_status = development_evidence_only`. No comparison or speedup conclusion is claimed. After Issue #7 and #8 merge into `main`, point `--suite-root` at the real task set, evaluate the real records, re-freeze and regenerate the regression baseline, then raise the version and claim status.

## Run the offline regression

Install `.[app,dev]` and run these commands from the repository root.

```sh
python -m scripts.evaluate_agent_benchmark --suite-root tests/fixtures/agent_benchmark freeze --plan benchmark/evaluation/demo_plan.json --out build/benchmark-evaluation/lock.json
python -m scripts.evaluate_agent_benchmark --suite-root tests/fixtures/agent_benchmark evaluate --lock build/benchmark-evaluation/lock.json --records benchmark/evaluation/demo_records.json --observations benchmark/evaluation/demo_observations.json --out build/benchmark-evaluation/report.json --regression-out build/benchmark-evaluation/snapshot.json --system-version issue9-demo
python -m scripts.evaluate_agent_benchmark regress --baseline benchmark/evaluation/demo_regression_baseline.json --candidate build/benchmark-evaluation/snapshot.json --out build/benchmark-evaluation/diff.json
python -m pytest tests/comparison -q
```

`preflight-paid` never contacts a provider or consumes a plan until `--enable` is passed. Without it the command prints `disabled` and exits `0`. With `--enable` it requires `--plan` and `--total-budget-usd`, validates the full plan (task IDs, repetitions, configurations, limits and thresholds), and checks every planned task against the `benchmark/manifest.json` under `--suite-root`. It also requires `source_class: live`, a positive `cost_usd` cap for every configuration, a finite total budget covering every scheduled trial, and verified real adapters (`--runner-supports-paid`); any missing gate exits `2`. The synthetic offline suite is a subset of the six-category paid plan, so preflight `benchmark/evaluation/paid_extension_plan.json` against the real Issue #7 task set, or another complete task set that indexes those task IDs.

The scripted example has eight trials. Five qualify, one misses a planted PIT-leak row, one attempts a forbidden backtest, and one is interrupted by a simulated provider failure. Reported times are scripted fixture values. CI compares the eight task, configuration and repeat outcomes to the checked-in baseline and runs the independent grader tests. The regression command exits `1` for a regression or a changed task/grader contract that needs a new baseline. It exits `2` on input or processing errors.

## Register an experiment

1. Use the task IDs and split in `benchmark/manifest.json`. Choose repeats, the interleaving seed, cache condition, observation mode, configurations, budgets and comparison pairs. The plan schema is exemplified by `benchmark/evaluation/demo_plan.json`. A configuration records the #8 base configuration, the resource limits and all five feature switches. Use distinct variant names for ablations of the same base configuration.
2. Freeze the plan before opening run outcomes. The lock fingerprints the selected task tree, prompt, reference, schema and fixture values. It also freezes the threshold for qualification by reviewers, the quality floor, the equivalence margin and the confidence-interval settings. Preserve the printed digest in the experiment record separately from the lock. The evaluator requires that separately recorded digest with `--expected-protocol-sha256` for confirmation designs.
3. Use `--suite-root` before the subcommand to point to a separately maintained evaluator checkout for unseen confirmation tasks. It must have its own task-set identifier and follow the input layout above. The published task set and synthetic suite provide development evidence only. Confirmation requires variants kept unseen during both system and evaluator tuning. Supply only the prompt, permitted resources and task controls to the Agent. Keep reference files, the evaluator and the scored results outside the Agent workspace.
4. Use `same_model` for a pinned model/provider/reasoning comparison, `product` for a comparison of configured products, and `ablation` for a pair where exactly one of `financial_tools`, `skills`, `context_compression`, `recovery` or `multi_agent` changes. Both sides of a pair use the same tasks and repeat indices. For confirmation, build the runner plan in repetition, sorted variant ID, then declared task order, before applying the seeded shuffle. Preserve the observed `plan_size` and `interleaved_order`. Run cold and warm experiments separately. Run automated and user sessions separately.

## Collect evidence

Pass one JSON file per #8 `TrialRecord` or a JSON array. The Python runner can write the result of `TrialRecord.to_dict()` as JSON. The evaluator reads the record using its `trial_id`, `task_id`, `task_category`, `config`, `conditions` and `reproducibility`. Keep one record per task, configuration and repeat. Capture every retry in that record, including cancelled or billed failed work. Trial `execution.status` remains the runtime outcome. `results.semantic_outcome` is the independently observed task behavior (`completed`, `refused`, or `clarification_required`). A task-defined stop can qualify when its other checks pass, including where the runtime reports `blocked`. Provider interruptions and failures remain failed outcomes with billed costs when available.

For persisted records requiring a trusted observation sidecar, use `--observations`. It takes a JSON object keyed by trial ID. Each value contains `record_sha256` (computed with `evaluation_protocol.sha256_json` over the exact input record) and `observed` (the allowed evidence field sections). Ingest records and sidecars only from the runner, backend, or experiment observer. A digest correlates the files. It does not establish who authored the evidence.

| Section | Observer evidence |
| --- | --- |
| `execution` | `observation_complete`, ordered `tool_calls` with `tool`, `command`, `executed`, `sequence`, `args` and observed `result` where applicable. Include pre-dispatch attempts, denied calls and approvals. An approval carries `flag`, `granted`, `actor: user`, and a sequence strictly before use. Initial user authorization and observed side effects also belong in this section. |
| `results` | `semantic_outcome` and `delivered_artifacts`, keyed by the artifact ID in the task. Give the actual #7 `workspaceResult` table, committed Harness node, observed tool result or project file. For a known provider transport failure record `failure_kind: provider_transport` or the observed error code. |
| `resources` | `cost_ledger_complete`, `cost_ledger`, `end_to_end_seconds`, `human_active_minutes`, and `human_intervention_count`. Measure end-to-end time from trial initiation and fixture restore through committed delivery or terminal failure. Give every charge a stable `charge_id`, a `kind`, a `cost_usd`, an `attempt_id`, and a `role`. Include model work, search, retry calls and auxiliary roles. Mark unresolved billing as incomplete. |
| `conditions`, `reproducibility`, `initial_state` | Actual runtime pins for each variant, framework and task versions, experiment ID and repetition, initial fixture hash, limits and cache. A trusted sidecar can restore a redacted SHA-256 field without replacing a previously observed value. Confirmation uses actual run observations. |

`committed_workspace_artifacts(task, run, workspace)` reuses the AlphaLab workspace validator and reads fresh Run-owned nodes, including the report document used for structured findings. It rejects an output that was only prepared or points to a stale report. Capture the observation from the trusted run and workspace APIs. Use the authoritative tool result to grade a backtest, its timing and executions. The supplied #8 branch is a stub and does not populate the full observation itself.

## Read the report

- `trial_outcomes` retains every provided record, including failures, timeouts and unauthorized attempts. `protocol.missing_trials` names planned cells without a record. `grading` separates financial correctness, evidence retrieval, delivery, policy, and argument review. The #7 expectations remain independent of the Agent answer. A claimed successful fill count cannot substitute for evidence that the requested allocation was met.
- `summaries` gives qualified completion rate, median and P90 elapsed time, human effort, infrastructure failures, reproducibility and safety events by configuration and stratum. Cost per qualified task divides all charged attempts in the stratum by its qualified tasks. A zero denominator is undefined. An unverified bill remains unknown and its observed portion is reported separately.
- `comparisons` shows all paired outcomes and missing sides. Repeat-level differences are aggregated within each task and uncertainty is estimated by a seeded bootstrap over task clusters. The unit remains the task even when there are several repeats. Small task sets have limited inferential resolution. Quality at the same resource budget and expense/time at comparable qualified-task rates appear in separate sections. Qualifying latency comparisons use observer-measured end-to-end time for all paired outcomes. The narrower Issue #8 runner window remains visible when that observation is missing.
- `grading` can include anonymous human review and supplementary model review. Use `grading.review_rubric(task)` to prepare review sheets that hide system identity. Review each report's causal support, uncertainty provenance and numerical derivations in addition to task-specific criteria. The `--reviews` file maps trial IDs to arrays containing `source`, opaque `reviewer_id`, `condition_id`, `passed`, and `blinded`. Human votes determine the narrative result. A tie or missing human review is unassessed. Model votes are displayed separately.

Public regression snapshots allow development tasks and select only task/configuration/repeat keys, versions, gate outcomes and safety counts. Share the offline demo report and methods. Keep raw traces, evidence sidecars, private task IDs and confirmation reports in the authorized private experiment workspace.

## Opt-in extension

`benchmark/evaluation/paid_extension_plan.json` lists a development smoke task from each of the six financial categories and the A/B/C variants. Freeze this plan against the Issue #7 task set. Run `python -m scripts.evaluate_agent_benchmark --suite-root "$ISSUE7_SUITE_ROOT" preflight-paid --enable ...` or call `regression.check_paid_extension` directly. It requires explicit enablement, a finite positive total budget, a cap for every scheduled trial and verified runner support for real adapters and aggregate enforcement. Caps cover failed attempts, retries, search and all roles. Set `runner_supports_paid=False` for the scripted PR #39 adapters. The evaluator scores recorded inputs offline. A real comparison needs provider-enabled adapters, the verified reset, complete instrumentation, independently registered confirmation samples and blinded review.
