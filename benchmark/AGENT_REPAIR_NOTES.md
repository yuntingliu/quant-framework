# Agent repair observations and next development goals

These development observations were collected on 2026-11-01 using only
`deepseek/deepseek-v4.1-flash-1017`, isolated synthetic fixtures, and the real
AlphaLab API / Conexus local host. They are not an official comparison result.

## Repairs

- Preserve explicitly bounded query rows (up to 50). Previously a 40-row response
  was reduced to six head/tail records while claiming it was not truncated.
  Preserve upstream truncation and continue bounding nested diagnostic content.
- Validate complete table contracts and inject the actual delivery schema.
- Enforce native tool budgets and per-operation authorization in the included
  local host, independently of model-supplied confirmation flags. Custom/remote
  hosts need equivalent integration; prompt registration alone is insufficient.
- Clarify freshness checks, sync-failure stopping, canonical identifiers,
  quantitative uncertainty, and the difference between successful trades and
  exact target-weight fulfillment.
- Clarify existing task requirements: gap counts are integers; missing-field
  diagnosis covers the historical January 2–May 7, 2025 window. Reference answers,
  fixtures, blocking checks and budgets were not relaxed.

## Observations and limits

The last frozen ten-task development run passed all blocking conditions on
8 tasks. One task incorrectly claimed all target weights were filled; one was
interrupted by a provider transport error and is inconclusive. The transport
failure was retained; only the two not-yet-attempted tasks were subsequently run.
A later instruction revision passed a separate one-task real regression of
exact target fulfillment. This is not a new 9/10 full-suite result.

Three predeclared PIT repetitions on the initial row-preserving version passed
with 14, 11 and 16 native calls; the later ten-task run also passed PIT. This is
narrow evidence that the missing-middle-record problem was repaired, not a
statistical guarantee of agent reliability.

Blocking checks do not establish full report correctness. Remaining prose errors
include unsupported causal explanations, incorrect frequency-scaling language,
and overconfidence about synthetic data and trading calendars. These need
independent numerical checks and human/rubric review.

The real-run adapter supplied trusted operation authorization from task policy.
It did not measure human approval latency. Development tasks were repeatedly
inspected while changing the Agent; they are unsuitable as independent evidence
of superiority over a general agent. Holdout tasks were not run. Generated
traces, runtime state, credentials, provider logs and fixture copies remain local
and are deliberately excluded from this branch.

## Next development goals

1. **Reproducible comparison runner (#8).** Run AlphaLab Agent and a general agent
   such as OpenCode against identical restored fixtures, requests and authorized
   operations. Use DeepSeek Flash for both where supported; record exact model,
   agent/host versions, settings and provider routing. If identical model access
   is unavailable, label the comparison as an end-to-end product comparison,
   not an isolated agent-system experiment. Give the baseline equivalent finance
   tools, documentation and a reasonable frozen configuration. Capture tool
   attempts, executed actions, delivered artifacts and terminal states. Enforce
   budgets outside the model and keep infrastructure failures distinct.
2. **Independent scoring and cost accounting (#9).** Score financial conclusions
   and evidence, not just formatting or successful tool retrieval. Verify units,
   annualisation, PIT joins, exact target fulfillment and refusal/stop behavior.
   Keep deterministic blocking checks separate from report-quality rubrics.
   Count all billed calls and retries, elapsed time and forbidden attempts.
   Grade baseline artifacts through an equivalent neutral output contract.
3. **Frozen confirmation set.** Freeze both systems before confirmation. Use
   untouched holdout tasks and newly authored variants across symbols, dates,
   missing-data patterns and report defects; validate reference answers without
   exposing them to either agent. Predeclare repetitions and seed/order policy;
   report every attempt and uncertainty rather than selecting best outcomes.
4. **Separate system and model effects.** First compare systems with the same
   model and tools. Then, separately, compare normal product configurations and
   ablate AlphaLab-specific guidance, tools and delivery controls. Report what
   each experiment can attribute; one fixed-model development run cannot identify
   a model advantage or establish superiority over OpenCode/Codex.
5. **Report correctness gate.** Add reusable evidence-backed checks for derived
   numerical statements and exact-weight claims; distinguish measured quantities,
   hypotheses and unknowns. Expand adversarial cases beyond the examples used to
   tune instructions. Success means materially fewer unsupported financial
   claims on frozen unseen cases, not merely a higher table-delivery rate.

A superiority claim should name the task population, comparator configuration,
quality metrics and cost/latency trade-off, with repeated paired results. Until
then, the supported claim is that specific integration defects were found and
repaired and that the remaining comparison infrastructure is still needed.
