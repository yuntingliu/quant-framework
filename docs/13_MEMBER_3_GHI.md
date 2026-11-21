# Member 3 — End-to-end verification, benchmark and final submission (G–I)

**Owner:** Angel (Member 3)  
**Target issue:** GitHub Issue #24  
**Scope:** Verify the Member 1 and Member 2 handovers, evaluate three configurations fairly, and prepare a reproducible final contribution.

> **Status note:** This document describes the repository contents and the validation actually available from the supplied handover archive. It does not treat developer-reported test totals, scripted model replies, or fixture expectations as fresh live-model benchmark results. The final three-arm live benchmark and a complete regression run must be recorded from the group's merged checkout and configured environment.

## G. End-to-end verification and regression coverage

### G.1 Verification approach

Member 3 verifies the integrated feature from a clean checkout rather than assuming that passing component tests proves the complete workflow. The test strategy has four layers:

1. **Contract and component tests:** validate task, research-package, evidence, review, correction, final-output and run-record contracts.
2. **Host/workflow tests:** exercise the real Conexus Host, researcher/reviewer identities, read-only reviewer permissions, shared budget and lifecycle transitions with scripted responses.
3. **Persistence tests:** exercise the ControlStore, EvidenceStore, finalization service, Local Host and FastAPI boundary, including recovery and version conflicts.
4. **Regression and live checks:** retain the existing single-agent behavior, then run a separately enabled real-provider smoke test. Scripted tests remain the normal CI path and do not call a paid model.

### G.2 Verification matrix

| Area | Required scenario | Existing test or runner in the handover |
|---|---|---|
| Research/review contracts | Missing fields, malformed payloads, invalid IDs, mismatched package versions and invalid evidence references are rejected | `tests/collaboration/test_contracts.py`, `tests/collaboration/test_acceptance.py`, `scripts/check_collaboration_contracts.py` |
| Frozen evidence | A reviewer reads only manifest-approved evidence; evidence hash changes are detected; historical snapshots remain stable | `tests/collaboration/test_evidence.py`, `tests/collaboration/test_production_candidate.py`, `scripts/check_collaboration_evidence.py` |
| Host permissions | Reviewer evidence reads and typed review submissions succeed; project writes and unauthorised capabilities are denied | `integrations/conexus/collaboration/host.mjs`, `tests/conexus-collaboration-host.test.mjs`, `scripts/check_collaboration_handover.mjs`, `scripts/run_member2_review.mjs --probe-reviewer-write` |
| Financial review cases | Formula, PIT timing, net/gross or report/result mismatch, unsupported conclusions, clean report and incorrect reviewer accusation | `tests/fixtures/collaboration/dev/`, `tests/collaboration/test_member2_runner.py`, `tests/collaboration/test_member2_reviewer_prompt.py` |
| Correction lifecycle | Finding disposition, corrected candidate, new review tied to new revision, bounded rounds and unresolved disagreement | `tests/collaboration/test_correction_loop.py`, `scripts/check_member2_correction_loop.mjs` |
| Failure/lifecycle handling | Missing evidence, invalid model output, model error, timeout, cancellation, exhausted budget and late child output | `tests/collaboration/test_control.py`, `tests/collaboration/test_budget.py`, `tests/collaboration/test_correction_loop.py`, `tests/conexus-collaboration-budget.test.mjs` |
| Publication/versioning | Stale review, stale destination, duplicate delivery, conflicting replay, interrupted staging, crash/recovery and no partial publication | `tests/collaboration/test_publication_control.py`, `test_publication_service.py`, `test_publication_recovery.py`, `test_publication_live_host.py`, `tests/api/test_collaboration_finalization_api.py` |
| Existing behavior | Single-agent workflow and existing reports remain usable | `tests/api/test_local_agent_integration.py`, `tests/contracts/test_conexus_distribution.py`, `scripts/measure_collaboration_baseline.py` |
| Real model behavior | Tool calling by the real researcher and reviewer, both roles' usage, actual findings, model/settings and limitations | `scripts/check_collaboration_live.mjs` (explicit `--live` required); `scripts/run_member2_review.mjs` (requires `--live --confirm-paid`) |

The four seeded financial-error classes in the development corpus are: (1) incorrect compounding/formula, (2) point-in-time availability after the decision time, (3) mismatch between report and archived result, and (4) unsupported research conclusion. The corpus also includes a clean case, missing-evidence case, a false reviewer allegation, and a stale-review/version conflict. These cases are suitable for development and regression testing, not the final holdout benchmark.

### G.3 Reproducible commands

Run from the repository root after installing the documented dependencies and building the patched Conexus runtime.

**Focused collaboration and publication tests**

```bash
python -m pytest -q tests/collaboration
python -m pytest -q \
  tests/api/test_collaboration_finalization_api.py \
  tests/collaboration/test_collaboration_finalization_service.py \
  tests/collaboration/test_publication_control.py \
  tests/collaboration/test_control.py \
  tests/collaboration/test_correction_loop.py \
  tests/collaboration/test_publication_service.py \
  tests/collaboration/test_publication_recovery.py \
  tests/collaboration/test_publication_live_host.py
```

**Existing single-agent and application regression**

```bash
python -m pytest -q \
  tests/api/test_local_agent_integration.py \
  tests/contracts/test_conexus_distribution.py
python scripts/measure_collaboration_baseline.py --scenario all \
  --output build/member3/single-agent-baseline
```

**Scripted end-to-end Host scenarios**

```bash
node scripts/check_collaboration_handover.mjs \
  --python .venv/bin/python --output build/member3/handover
node scripts/check_collaboration_foundation.mjs \
  --mode scripted --scenario all --python .venv/bin/python \
  --output build/member3/foundation
```

On Windows, replace `.venv/bin/python` with `.venv/Scripts/python.exe`.

**Patched Local Host tests**

```bash
python scripts/build_conexus_runtime.py
npm --prefix build/member-2/conexus-source test --workspace @conexus/local-host
```

**Full regression and repository checks**

```bash
python -m pytest -q
python scripts/check_facade_imports.py
python scripts/check_conexus_docs.py
python -m ruff check alphalab apps/api tests scripts
python scripts/check_repository_hygiene.py
python -m compileall -q alphalab apps/api scripts
git diff --check
git diff --exit-code
```

A skipped real-process test is not a pass. The test output and skip reason must be retained. The CI quality workflow also runs frontend dependency audit, tests, lint and build; Member 3 should confirm those jobs on the merged pull request.

### G.4 Validation evidence and limitations

The Member 2 handover reports the following earlier results: Local Host suite **9 passed**; real Python-to-Node Local Host acceptance **2 passed**; finalization-service tests **7 passed**; publication-focused Python regression **20 passed**; combined collaboration regression **47 passed**. It also records a full-project run of **634 passed, 3 skipped and one documentation-contract failure**. The missing documentation route rows were then added and the isolated documentation checker/test passed. The full suite was **not rerun** after that correction and the final directory reorganisation. These are results reported by the previous developer, not a fresh Member 3 execution.

In the supplied execution environment, the fresh command `python -m pytest -q tests/collaboration` could not collect the tests because `pyarrow` is missing. The package imports `pyarrow` at application startup. The archived project also requires Node.js 22.18 or later, while the supplied runtime reports Node.js 22.16.0. The ZIP intentionally excludes `.git`, `.venv`, `node_modules`, generated Conexus build output, runtime databases and provider credentials. Network access was unavailable for installing the missing dependency. Therefore, the complete G regression gate and live model verification have not been independently passed in this environment.

The following Member 3 utility tests were run successfully from the supplied archive: `python -m pytest -q tests/test_member3_confirmation.py tests/test_member3_benchmark_analysis.py` — **7 passed**. The frozen confirmation-corpus verifier also returned `status: passed` for all eight cases. These tests verify the additional holdout and analysis utilities only; they do not substitute for the integrated Host, persistence or model tests.

**G completion gate:** merge the Member 1 and Member 2 work into the shared branch; install all dependencies; build the patched runtime; rerun the complete suite above; keep all failures and skips; fix any newly exposed defects; and attach sanitized logs/JSON results. Run the paid/live checks separately and retain the exact provider/model settings (never the key), task, outcome, both-role usage, and limitations.

## H. Three-way benchmark and results analysis

### H.1 Configurations

The benchmark compares three arms on the same paired task inputs:

| Arm | Configuration | Resource allowance |
|---|---|---|
| A — `single` | Existing single researcher agent, without an additional reviewer pass | Baseline allowance **B** |
| B — `single_self_check` | One researcher agent with an additional self-check allowance | **B + R** |
| C — `two_agent` | Researcher followed by a separately permissioned reviewer and bounded correction/re-review | Researcher allowance **B** plus reviewer allowance **R**, under the shared task budget |

Arm B receives the same additional allowance **R** as the reviewer in Arm C. This is essential: otherwise the two-agent arm would receive more reasoning/model resources than the single-agent comparison and the quality/cost comparison would be unfair. The exact call, token, time and monetary limits must be selected from the group's actual provider and spending allowance, documented, and frozen before confirmation trials. Do not invent a hard monetary or token cap where provider usage bounds are unavailable.

All arms use the same frozen task/evidence input, model and provider, base model settings, output schema, scoring rules, environment and run order policy. Prompt differences required by the roles are versioned and recorded. The two-agent Host budget is shared across researcher and reviewer and must not reset between correction rounds.

### H.2 Confirmation set prepared for Member 3

A separate confirmation corpus has been added at `tests/fixtures/collaboration/confirmation/`. It contains eight new cases and is separate from the development corpus:

| Case | Purpose | Expected independent disposition |
|---|---|---|
| `CONF-FORMULA-01` | 12% followed by −5%; report claims 10% | Flag formula; compound return is 6.4% |
| `CONF-PIT-01` | Data available on 4 February; decision on 3 February | Flag PIT timing |
| `CONF-RETURNS-01` | Report says 15% net; archived net result is 12% | Flag return/report inconsistency |
| `CONF-SUPPORT-01` | 18 in-sample sessions and no independent out-of-sample trials; report guarantees future outperformance | Flag unsupported conclusion |
| `CONF-CLEAN-01` | Correct 7.5% net return and information available before decision | No finding in the declared categories |
| `CONF-FALSE-ALARM-01` | Reviewer allegation says fees excluded, but the definition says fees included | Reject the incorrect allegation; no finding |
| `CONF-MISSING-01` | PIT timing evidence is absent | Mark incomplete/blocked; do not infer availability |
| `CONF-STALE-01` | Candidate revision 2 carries a review for revision 1 | Reject stale review/version reference |

The manifest records SHA-256 hashes and permitted evidence IDs. The oracle is in a separate scoring-only file. An Agent run must receive only its individual `inputs/{fixture_id}.json` payload and the approved evidence derived from it; it must never receive `manifest.json`, `oracle.json`, expected categories, rationale or answer labels. The verifier checks file hashes, input shape, evidence allowlists, JSON-pointer citations and independent arithmetic/timing facts:

```bash
python scripts/verify_member3_confirmation.py
```

The frozen manifest SHA-256 for version 1 is `3d737812f7fc27fd1a6c32d253fef15a87a9364a658417ba9512ed69415034f5`.

The verifier does not claim that a language model has passed the qualitative cases. Qualitative judgements such as an unsupported guarantee or a false reviewer accusation must be independently adjudicated against the frozen rubric.

### H.3 Scoring rubric

Each run receives a separate independent adjudication. The assessor should compare the final report, structured findings, evidence citations, correction response, final reviewed candidate version and terminal task state against the confirmation oracle and task-specific acceptance rule. Do not use one model as the sole judge of another model.

Each adjudication records:

- `qualified`: whether the run reached the correct task-specific outcome. A correctly blocked missing-evidence task can qualify; a report that merely exists cannot qualify if the evidence is missing or the result is wrong.
- `seeded_error_opportunities`: number of planted errors in the task.
- `errors_detected`: seeded errors correctly identified with the appropriate category and inspectable evidence.
- `errors_corrected`: detected errors that were actually corrected and verified against a new candidate version. A reasoned, evidence-backed disagreement is not counted as a correction.
- `false_alarms`: findings contradicted by the frozen evidence, including unnecessary accusations on clean/false-alarm cases.
- `unsupported_conclusions_remaining`: unsupported conclusions left in the final candidate.

A task is qualified only if its required checks are correct, evidence references resolve, the final decision matches the task's expected disposition, and no prohibited side effect occurs. For the clean and false-alarm controls, unnecessary findings reduce quality. For missing evidence, an explicit incomplete/blocked outcome is preferable to fabricated certainty. For stale candidate versions, a prior approval must not be accepted.

### H.4 Measurement and analysis

Every run is preserved as a version 1 `record.json` using `alphalab/collaboration/schemas/run-record.v1.json`. Record the task and confirmation-manifest hashes, arm, repetition, model/provider/prompt/schema/policy versions, source commit, environment, budgets, run IDs, start/end times, each role's calls and usage, retries, tool calls, total cost status, human interventions, terminal status and artifacts. The two-agent record must report researcher and reviewer separately as well as their totals.

The following measures are reported for each arm:

- Qualified task-completion rate.
- Seeded-error detection rate and correction rate.
- False alarms and unsupported conclusions remaining.
- Total elapsed time and its distribution.
- Model calls, tokens and costs for all roles, including failed calls and retries.
- Failures, timeouts, retries and human interventions.
- Cost per qualified task, calculated as total cost across all attempts divided by the number of qualified tasks. It is **undefined** when no task qualifies and **unavailable** if any included attempt has unknown cost; missing provider data must not be treated as zero.

A Member 3 analysis utility is provided:

```bash
python scripts/analyze_member3_benchmark.py \
  --records build/member3/benchmark/records \
  --adjudications build/member3/benchmark/adjudications.json \
  --output build/member3/benchmark/analysis
```

It validates each record against the run-record schema, requires all three arms for each paired task/repetition, checks identical paired input hashes and model/provider settings, enforces equal total allowance between `single_self_check` and `two_agent`, includes every supplied attempt, and produces `summary.json` and `summary.md`. It computes paired completion/time differences and a deterministic 10,000-resample percentile bootstrap interval (seed 24). Confidence intervals from a small sample should be interpreted descriptively, not as strong statistical proof.

The adjudication file is keyed by `record_id` and must contain the five integer count fields above, a Boolean `qualified`, and a short audit basis. Keep the adjudication file outside the Agent workspace. Example shape:

```json
{
  "schema_version": 1,
  "adjudications": {
    "run-record-id": {
      "qualified": true,
      "quality_scores": {
        "seeded_error_opportunities": 1,
        "errors_detected": 1,
        "errors_corrected": 1,
        "false_alarms": 0,
        "unsupported_conclusions_remaining": 0
      },
      "basis": "Compared with the frozen oracle and final reviewed candidate."
    }
  }
}
```

### H.5 Benchmark status and interpretation

The supplied Member 2 ZIP contains run-record validation, a development oracle checker, a scripted single-agent baseline harness and an opt-in live reviewer harness. These components are useful foundations, but they are **not** a complete three-arm confirmation benchmark: the live scripts use development cases, the baseline task is a local-context/report smoke test rather than the same financial review tasks, and no dedicated `single_self_check` benchmark arm is supplied. The new confirmation corpus and analysis utility close the holdout/scoring preparation gap, but the arm adapters and repeated live runs still need to be connected and executed on the team's merged branch.

No actual model-quality result is reported in this handover because no group provider credentials or live run records were supplied. Scripted model responses can demonstrate orchestration, permission enforcement, correction, stopping and storage; they cannot be reported as evidence of financial reasoning quality. The final recommendation must be based on the paired confirmation results. If the two-agent approach does not produce a repeatable, meaningful quality improvement that justifies extra time, cost and human effort, retain the single-agent default as Issue #24 permits.

## I. Runnable demonstration, documentation and final submission

### I.1 Environment setup

Requirements:

- Python 3.10 or later (use the same interpreter for installation and tests).
- Node.js 22.18 or later and npm.
- Git CLI, for applying and verifying the Conexus patch.
- A virtual environment and the project's dependencies.

From a fresh checkout:

```bash
python -m venv .venv
# macOS/Linux
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[all,dev]"
# Windows PowerShell: use .venv\\Scripts\\python.exe instead of .venv/bin/python
npm --prefix apps/desktop ci
python scripts/build_conexus_runtime.py
```

For the Member 2 publication changes, the build creates a disposable patched source tree under `build/member-2/conexus-source` and a runtime under `build/member-2/conexus-runtime`. The original vendored Conexus export remains unchanged; the Member 2 patch is applied to a generated copy.

### I.2 Supplying model access

The professor does not supply model weights or an API key. Each group member or the group must supply an OpenAI-compatible Chat Completions provider, the exact model ID, the provider base URL and credentials where required. In the workstation, open **Model and search → Research model**, add the base URL/model ID/API key, test the model, and select **Save and use**. A tool-calling-capable model is required; a successful text-only connection test is not sufficient evidence of tool calling.

For local environment configuration, start from `.env.example`. Keep actual values in an untracked `.env` or `.env.local` and keep provider profile files under the private runtime directory. Do not commit credentials or place keys in agent messages, task payloads, logs, fixtures or screenshots. The model key is Host-managed and is not passed from researcher to reviewer. Market-data credentials (such as RQ credentials) are separate from model credentials. The publication HMAC secret must be different from the Local Host token.

The current adapter supports OpenAI-compatible Chat Completions. Native Responses-only or incompatible provider APIs are not supported by this adapter unless a separate adapter is implemented and tested. Live model calls may incur charges.

### I.3 Free scripted demonstrations

The following commands use scripted responses and do not require a real model API key:

```bash
# Check the separate confirmation pack's integrity and independent facts.
python scripts/verify_member3_confirmation.py

# Run the scripted end-to-end scenarios through the actual collaboration Host.
node scripts/check_collaboration_foundation.mjs \
  --mode scripted --scenario correction \
  --python .venv/bin/python --output build/member3/demo-correction
node scripts/check_collaboration_foundation.mjs \
  --mode scripted --scenario missing-evidence \
  --python .venv/bin/python --output build/member3/demo-unresolved

# Run focused tests.
python -m pytest -q tests/collaboration
```

The correction scenario demonstrates a seeded finding, researcher correction, a new candidate version, re-review and final-output preflight. The missing-evidence scenario demonstrates an explicit blocked/incomplete outcome rather than an unsupported approval. The acceptance runner's finalization is a preflight unless the dedicated real Local Host publication acceptance is run; do not describe a preflight as a durable production publication.

### I.4 Separately enabled live verification

Live tests must be opt-in and run only after a valid provider profile has been configured. Example:

```bash
node scripts/check_collaboration_live.mjs \
  --live --scenario clean,seeded-formula \
  --python .venv/bin/python \
  --provider-settings data/runtime/conexus/secrets/model-providers.json \
  --output build/member3/live
```

Use a small pilot first and confirm the model, provider, maximum request count, output-token settings and expected spend before extending the scenario list. The script requires an active HTTPS provider profile for normal live cases and keeps the oracle out of Agent prompts. A generic provider without verified usage bounds/pricing cannot promise a strict monetary or total-token cap. Missing usage/cost receipts remain unknown. The live runner covers development scenarios in this handover; the new confirmation cases still need to be connected to the three-arm runner before they can be used for final benchmark claims.

### I.5 Permissions, budget, evidence and persistence notes

- Reviewer permissions are enforced by the collaboration Host at the execution boundary; a prompt instruction alone is not treated as security enforcement.
- The reviewer may read only approved frozen evidence and submit a typed review. It cannot write project files, mutate final report/workspace outputs, run unauthorised code, trade or deploy.
- The task budget is shared across researcher and reviewer. Calls, tokens, costs and role identity must be recorded. Unknown provider usage is not zero.
- Each review is tied to a specific candidate and evidence manifest. A revised candidate requires a new review.
- Final output is requested through the trusted FastAPI finalization route. The caller supplies an idempotency key only; approval, evidence and destination versions are loaded from trusted stores.
- ControlStore and Local Host are separate durable stores. Recovery uses idempotent reconciliation and a durable receipt; the handover does not claim a distributed ACID transaction.
- Publication rejects stale/conflicting versions and duplicate deliveries; history is preserved. The API route inherits optional workstation Basic authentication. Any non-loopback deployment must enable and configure authentication.

### I.6 Known limitations to disclose

1. Live model review and the final paired three-arm benchmark remain unverified until the group's provider and records are available.
2. Provider token/cost reporting may be incomplete; totals must remain unknown where receipts are missing.
3. The separate confirmation corpus is prepared and hash-verified, but qualitative scoring still requires independent adjudication and an execution adapter for each benchmark arm.
4. The Host and ControlStore/Local Host stores are separate systems; recovery is idempotent reconciliation rather than a distributed database transaction.
5. The Conexus source export records `licenseStatus: pending-owner-selection`. Do not describe it as a published open-source release or change its license metadata without owner approval.
6. The archive omits Git metadata and generated/private runtime files. The final pull request must be made from the shared merged checkout with the true commit and validation records.

### I.7 Final pull request checklist and contribution breakdown

Before submission:

- [ ] Member 1 A–C and Member 2 D–F changes are merged into the agreed integration branch.
- [ ] Record the exact base and final working commit hashes; identify any newer base selected instead of the issue's reference baseline.
- [ ] Fresh-checkout setup succeeds with documented Python and Node versions.
- [ ] Scripted tests pass without a provider key; live tests are separately enabled and their model/settings/outcomes are recorded.
- [ ] All deterministic Host, lifecycle, permission, budget, evidence, versioning and storage checks pass.
- [ ] Existing single-agent behavior and report history remain intact.
- [ ] Confirmation tasks, manifest, scoring rubric, budgets, model settings and decision criteria were frozen before confirmation trials.
- [ ] All three benchmark arms are present for every paired task/repetition; failed runs, retries, timeouts, human interventions and unknown costs remain included.
- [ ] The final recommendation follows the measured quality/time/cost evidence. Keep single-agent as default when collaboration's benefit is not worthwhile.
- [ ] No API keys, private datasets, runtime databases, sensitive logs or oracle files are included in Agent inputs or public submission artifacts.
- [ ] Repository-required Python, frontend and Local Host checks pass; `git diff --check` is clean.

**Contribution summary:** Member 1 supplied baseline/setup, contracts, evidence snapshots, Host permissions and shared budgets (A–C). Member 2 supplied researcher/reviewer integration, correction/re-review, stopping and final-output publication/recovery (D–F). Member 3 owns end-to-end regression and live verification, confirmation-set benchmark/scoring and analysis, user-facing setup/demo documentation, and the final PR/evaluation recommendation (G–I).

**Suggested final PR title:** `Complete Issue #24: verify and evaluate researcher–reviewer workflow`.

**Suggested PR description:** Link Issue #24; identify the merged A–F implementation commits and Member 3's G–I commit; list the exact setup/test commands; attach sanitized successful-correction and unresolved-outcome demonstration records; map each acceptance criterion to its test or benchmark artifact; report the three-arm paired results including failures/uncertainty/cost/human effort; and state all known limitations. Use `Closes #24` only if this final PR genuinely completes the issue. Earlier member PRs should use `Related to #24`.
