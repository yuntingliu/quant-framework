# Adapter contracts and substitution

What a benchmark harness needs and the other two examples do not: proof that
replacing AlphaLab's account-resolved adapters with test doubles does not
change what the Agent can do.

[Adapter contracts](../../../docs/13_CONEXUS_ADAPTER_CONTRACTS.md) for the
contracts themselves; [Extension guide](../../../docs/12_CONEXUS_EXTENSION_GUIDE.md)
for where an adapter lands in the first place.

## The problem

Conexus has no plugin system. An adapter is a plain function handed to
`createLocalHost` or `createLocalRuntime`, so substituting one is trivial —
and nothing declares what the original promised. A harness that quietly offers
a weaker search, fewer commands, or a different failure shape produces a score
that measures the harness.

## What is replaced, and what is not

| Adapter | Product | Here |
| --- | --- | --- |
| Model completion | `configuredCompletion` over a provider profile | `contractCompletion`, a script of turns |
| `web.search` capability | `configuredWebSearch` over Brave or OpenRouter | `contractSearch`, fixed sources |
| OS runtime | `createLocalRuntime` | **`createLocalRuntime`** — unchanged |

The runtime adapter stays real on purpose. Tool child processes, their
environment allowlist and their timeouts are what the Agent is measured
*through*; simulating them would measure the double. What substitution has to
prove instead is that the Host still publishes an equivalent profile — the same
node types, tool runtimes, permissions and capability ids — because that
profile is all `inspectHostedRelease` checks a Release against.

## Files

| File | Purpose |
| --- | --- |
| [`substitute.mjs`](substitute.mjs) | The replacement adapter set, plus the equivalence assertion |
| [`test/substitution.test.mjs`](test/substitution.test.mjs) | The same minimal task over both adapter stacks; the authorization contract |
| [`test/financial.test.mjs`](test/financial.test.mjs) | Every envelope the real financial Tool node returns, validated |

The doubles themselves are not example code. They live in
[`integrations/conexus/contracts/doubles.mjs`](../../../integrations/conexus/contracts/doubles.mjs)
so a benchmark imports the same backend the tests here use, rather than a
lookalike that drifts.

## Run it

```bash
python scripts/build_conexus_runtime.py
node --test 'examples/conexus/adapter-contracts/test/*.test.mjs'
```

The suites skip with a build hint when the pinned runtime has not been built,
so `node --test` stays usable on a fresh clone. The contract checks that need
no runtime at all are in
[`tests/conexus-adapter-contracts.test.mjs`](../../../tests/conexus-adapter-contracts.test.mjs):

```bash
node --test tests/conexus-adapter-contracts.test.mjs
```

## What the substitution test actually asserts

The minimal task — summarize a numeric series through a Tool node, save a note
— runs twice on the same Harness. Once through the shipped
`createModelCompletion` against a loopback OpenAI-compatible endpoint, which
exercises its URL rules, credential rules and 5 MiB cap. Once through
`contractCompletion` in process. The run status, summary, workspace revision,
node-change count and the saved note must be identical. They are compared with
one `deepEqual`, so a divergence anywhere in that set fails.

Three things that look like details and are not:

**A refusal must match, not just a success.** The search double and the real
search adapter are asserted to return the same code *and* the same `retryable`
value for an unusable query. Equivalent tool access means equivalent refusals;
an Agent that sees `retryable: true` from one backend and `false` from the
other behaves differently for reasons that have nothing to do with the task.

**The confirmation gate is enforced in the double.** `backtest.run` without
`confirm_python_execution` is refused by `contractFinancialTool` exactly as the
real tool refuses it, and a flag smuggled into `args` is rejected by the
request contract. A double that executed anyway would let a benchmark reward an
Agent for skipping the gate.

**Credentials are fixture strings, and their absence is asserted.** The Host
runs with a known workspace token and the model adapter with a known key;
`assertNoSecrets` then checks that neither appears in a response, an error
message, or a recorded completion request. A 401 says access was denied and
never what was expected.
