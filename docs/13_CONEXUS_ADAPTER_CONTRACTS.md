# Conexus adapter contracts

The versioned boundary between AlphaLab and the pinned Conexus core: what each
adapter promises, which side owns each error code, how to substitute an adapter
without changing what the Agent can do, and how to move the core underneath it
all.

[Framework architecture](10_CONEXUS_FRAMEWORK.md) for the vocabulary;
[Execution lifecycle](11_CONEXUS_EXECUTION_LIFECYCLE.md) for what a Run does
with an adapter's answer; [Extension guide](12_CONEXUS_EXTENSION_GUIDE.md) for
deciding where a change lands before you write it.

## Why a declaration is needed at all

Conexus has no plugin system, and that is the whole reason this document
exists. An adapter is a plain function the launcher passes in:

```js
const runtime = createLocalRuntime({ projectRoot, capabilities: { 'web.search': webSearch } })
const host = await createLocalHost({ projectRoot, release, slug, token, model,
  runtimeAdapter: runtime.adapter, complete: configuredCompletion(providerPath, createModelCompletion) })
```

Substitution is therefore trivial, and drift is invisible: nothing in that call
records what `complete` or `webSearch` promised. Three things go wrong as a
result. A core upgrade can change a shape and the application keeps compiling.
A benchmark harness can offer a narrower surface than the product and still
produce a number that looks comparable. And a provider's error body can reach
a transcript because no layer was obliged to bound it.

The contracts are the record. They live in
[`integrations/conexus/contracts/adapter-contracts.v1.json`](../integrations/conexus/contracts/adapter-contracts.v1.json)
as data rather than in code, so the Node adapters and the Python benchmark
suites validate the same declaration instead of two that drift apart.

## The adapter boundary

| Adapter | Supplied as | Shipped implementation | Contracts |
| --- | --- | --- | --- |
| Model completion | `complete` to `createLocalHost` | [`model-providers.mjs`](../integrations/conexus/model-providers.mjs) | `model.completion.request`, `model.completion.response` |
| Host capability | `capabilities` to `createLocalRuntime` | [`web-search.mjs`](../integrations/conexus/web-search.mjs) | `capability.response`, `search.response` |
| Financial Tool node | a `tool` node in the graph | [`alphalab-research-agent/tools/`](../integrations/conexus/alphalab-research-agent/tools/) | `financial.tool.request`, `financial.tool.response` |
| OS runtime | `runtimeAdapter` | core `createLocalRuntime` | `runtime.adapter.profile` |
| State storage | the Host's `projectRoot` | core `openState` | `state.snapshot` |
| Authorization | the Host's `token` | core `createLocalHost` | `authorization.failure` |

`error.envelope` spans all of them: it is how any adapter reports a failure it
cannot answer in band.

Note what is *not* on this list. AlphaLab's research logic — the 44 commands,
the SDK skill, the workspace output schema — is business rule, not adapter.
It stays in the SDK and in the Tool nodes; execution and lifecycle stay with
the framework. The contracts describe the seam, not either side of it.

## The contract document

```json
{
  "schema": "alphalab.conexus.adapter-contracts.v1",
  "version": 1,
  "verifiedCoreRevision": "9cd8c6934b4fac54cc513cba4f6e7960b57688c3",
  "errorCodes": { "model": { "owner": "core", "source": "…", "codes": ["…"] } },
  "retryableCodes": ["…"],
  "contracts": { "<id>": { "title": "…", "owner": "…", "direction": "…",
                           "notes": "…", "schema": { }, "examples": { "valid": [], "invalid": [] } } }
}
```

Four fields carry weight.

**`verifiedCoreRevision`** is the core revision the contracts were checked
against. It must equal the revision in
[`runtime.lock.json`](../integrations/conexus/runtime.lock.json), and
`scripts/check_conexus_upgrade.py` fails when it does not. That is what turns
"compatibility tested" into something a pull request can fail: moving the pin
without re-verifying the contracts is a failure, not an omission.

**`owner`** says whether a code or a shape is the core's or the application's.
A core-owned shape changes only through a reviewed export. An
application-owned one is ordinary work.

**`examples`** are not documentation. Every contract carries valid instances
and invalid instances with the path and keyword each must fail on. That corpus
is replayed through two independent validators, which is the only reason a
hand-written validator is trustworthy here — see
[Two validators](#two-validators).

**`direction`** records who hands the value to whom, because the same contract
is a gate in one direction and a promise in the other. A `core-to-adapter`
shape is what an adapter must accept; an `adapter-to-core` shape is what it
must produce.

### Versioning rule

Adding an optional field, or a new error code, is a revision of v1: an adapter
pinned to v1 keeps passing. Removing a field, tightening a type, or changing
what an existing code *means* requires `adapter-contracts.v2.json`, because a
benchmark adapter written against v1 must not silently start failing a task it
used to pass. The version lives in the filename as well as the document, so
both can exist at once during a migration.

### Two validators

The contract checks have to run on a fresh clone, before
`python scripts/build_conexus_runtime.py` has installed anything, so they
cannot reach the `ajv` the core itself depends on. Hence
[`validate.mjs`](../integrations/conexus/contracts/validate.mjs): a validator
covering exactly the keywords the document uses and refusing any other, so an
unsupported keyword is an error rather than a silent pass.

A hand-written validator is only worth anything if something independent
agrees with it.
[`tests/contracts/test_conexus_adapter_contracts.py`](../tests/contracts/test_conexus_adapter_contracts.py)
replays the entire example corpus through `jsonschema` and compares verdicts
with the Node validator's, so a divergence fails a test instead of passing a
malformed response.

## Model completion

The request is `ModelCompletionRequest`; the response is `AgentLLMResult`,
the shape `parseOpenAiCompletion` produces.

Everything except `messages` is optional, and an adapter must not require any
of it. `signal` is an `AbortSignal` carried out of band — the contract declares
the field so a whole request object validates, not to describe its shape.

Two behaviours of AlphaLab's layer are part of the contract, not incidental:

- **Provider selection is frozen per Run.** `configuredCompletion` caches the
  resolved client against the Run's `AbortSignal`. Switching providers affects
  new Runs and never swaps the model under a Run in flight.
- **Conditionally optional tool fields survive a strict provider.** Tool
  schemas whose fields apply only in some states — `complete.blocker` applies
  only to a blocked Run — are sent with `strict: false` so a provider routing
  Chat Completions through a strict Responses API does not reject them. An
  explicitly strict schema is preserved rather than overwritten.

On the response side, `content` is `null` on a tool-call turn, `tool_calls` is
*absent* rather than empty when there are none, and `arguments` is a string the
core parses itself. A model that returns a parsed object there is a contract
violation, not a convenience.

## Host capability and search

A capability returns `{ result }`, and a refusal is a **successful invocation
carrying `success: false`**. Throwing instead turns a configuration problem
into a failed Run, which is the wrong outcome: the Agent can read and act on a
refusal.

`capability.response` therefore requires three things on a failure — `code`,
`error`, `retryable` — so a caller can branch without parsing prose and knows
whether retrying can change the answer. `search.response` narrows it further:
a provider from a closed set, results whose urls are `http(s)` with no embedded
credentials, and bounded title and snippet lengths. Search snippets are leads,
not facts.

`search_not_configured` and `search_disabled` are not retryable; a missing
credential does not become present because the Agent tried again. An unknown
capability id answers `Capability is unavailable on this host.` rather than
throwing, which is how a benchmark detects "this host cannot search" without a
failed Run.

## Financial Tool nodes

`financial.tool.request` is the command-and-args contract. Three parts of it
are load-bearing:

**Confirmation flags are top-level.** `confirm_python_execution`,
`confirm_write`, `confirm_delete` and `confirm_cancel` are exactly the four the
shipped bundle exposes, and the contract *forbids* any of them inside `args`.
A nested flag could be smuggled in by an argument blob the model copied from
untrusted text; the `not` rule on `args` rejects that outright, and
`tests/contracts/test_conexus_adapter_contracts.py` asserts the shipped tool
schemas accept no top-level field the contract rejects.

**`args` is otherwise unconstrained.** Each command validates its own
arguments and returns a correction-oriented error. Constraining `args`
centrally would move 44 commands' worth of validation into one schema and give
the model a worse message than the command can give it.

**A refusal is one bounded line.** Backend errors carry file paths, identifiers
and request details. `financial.tool.response` caps `error` at 400 characters,
and the real `Project-Run` tool additionally strips absolute paths and any
40–64 character hex token so digests cannot round-trip through the Agent.

Asynchronous work has its own rule. Submission returns `job_id` and `status`
immediately and never blocks; a wait that reaches its deadline returns
`success: true` with `timed_out: true`. An unfinished job is an answer, not a
failed call — an Agent told the invocation failed would retry the submission
and run the work twice.

## State storage

`state.snapshot` is what `openState` will accept back from disk. A snapshot
that fails it is refused with existing data preserved rather than migrated in
place, so a storage adapter must round-trip it unchanged.

Run access tokens are stored only as hashes. A snapshot carrying a bearer token
verbatim is a defect, which is why the credential assertions below cover
exports as well as responses.

## Authorization failures

The body is a stable code and one sentence: never the expected token, the
supplied token, or how far the comparison got. The status is the discriminator.

| Status | When | Why that status |
| --- | --- | --- |
| 401 | Workspace token missing or wrong | The workspace is the thing being refused |
| 404 | Run token wrong | Indistinguishable from an unknown run, so a probe learns nothing |
| 403 | Non-loopback `Host` header | Rejected before any route runs |

The Host strips an optional `Bearer ` prefix rather than requiring it, so a
bare token also authorizes. A client should always send the scheme;
`examples/conexus/adapter-contracts/test/substitution.test.mjs` asserts the
current behaviour so a core change to it is visible rather than surprising.

## Equivalent capabilities and permissions

`runtime.adapter.profile` is what `inspectHostedRelease` checks a Release
against, and it is what makes two adapters comparable:

```js
{
  name: 'conexus.local-node-host.v1',
  nodeTypes: ['tool', 'cli', 'files'],
  toolRuntimes: ['node', 'python', 'bash', 'shell'],   // powershell on Windows
  permissions: ['filesystem', 'network', 'shell'],
  isolation: ['shell'],
  capabilityIds: ['web.search'],
  runNodeTypes: ['tool'],
}
```

`assertEquivalentProfiles(reference, substitute)` passes when the substitute
offers **no less** than the reference. The direction is deliberate: a Harness
that deploys against the real Host must deploy against the substitute, and
`inspectHostedRelease` only checks what the profile claims. Offering *more* is
fine — a double that answers a capability the real host lacks cannot make a
benchmark task unreachable.

`toolRuntimes` differs by platform, so equivalence is checked against the live
profile's own platform set rather than a hard-coded list.

## Cancellation, timeouts and the error taxonomy

Cancellation and timeout are **codes**, not exception types: `run_cancelled`
and `run_timed_out` in `error.envelope`. A Run that was cancelled is therefore
distinguishable from one whose provider broke, which matters when a benchmark
has to decide whether a task was attempted.

The taxonomy is split by owner, and every family names the file it comes from:

| Family | Owner | Examples |
| --- | --- | --- |
| `model` | core | `model_not_allowed`, `model_provider_error`, `invalid_model_response` |
| `modelParse` | core | `missing_choices`, `malformed_tool_call` |
| `search` | application | `search_not_configured`, `search_no_sources` |
| `lifecycle` | core | `run_cancelled`, `run_timed_out`, `invalid_run_input` |
| `deployment` | core | `release_checksum_mismatch`, `missing_tool_adapter` |
| `authorization` | core | `local_host_error` |

Two checks keep it from going stale. The search family must equal exactly the
set of codes `web-search.mjs` can return — neither more nor less. And the core
families must cover every code the pinned snapshot raises, so an upgrade that
introduces a code fails the contract test until the taxonomy records it.

## Credentials

Adapter responses, logs and exports must not disclose a key, and the adapters
are built that way: `web-search.mjs` resolves the Brave key locally and never
echoes it, `createModelCompletion` reports only a provider's HTTP status rather
than its error body, and credentials are never passed as Agent messages or as
business parameters to a tool.

What was missing is proof that it stays that way.
[`redact.mjs`](../integrations/conexus/contracts/redact.mjs) supplies both
halves:

```js
import { assertNoSecrets, redact } from '../integrations/conexus/contracts/redact.mjs'

redact(profile)                                    // structural, for output you keep
assertNoSecrets(response, [key, token], 'the search response')   // value-based, for tests
```

`redact` does not need to know the secret values: any property whose *name*
looks like a credential is replaced whole, and every remaining string is
scrubbed for bearer tokens, URL userinfo and 40–64 character hex runs — the
same shape `Project-Run` strips, so the two agree.

`assertNoSecrets` needs the real values and is for tests. The contract suites
configure each adapter with recognizable fixture credentials, drive every
failure path including a provider error body that repeats the key back, and
assert none of them reached a response, an error message, or a recorded
request. Fixtures shorter than eight characters are rejected, because
asserting the absence of `"a"` passes vacuously and reads as coverage.

## Using the contracts

```js
import { assertContract, checkContract, withContracts }
  from '../integrations/conexus/contracts/adapters.mjs'

checkContract('search.response', value)    // → [] when it conforms
assertContract('search.response', value)   // throws AdapterContractError
withContracts(adapter, { request: 'model.completion.request',
                         response: 'model.completion.response' })
```

`AdapterContractError.details` uses Runtime Core's own error-detail shape —
`{ path, keyword, message }` — so a contract violation reads like the schema
violations the Agent already sees from a Tool node's `inputSchema`.

`withContracts` belongs in tests and in a benchmark harness, not in the shipped
launcher. A production Host should not fail a Run because a provider added a
field, and validating every completion costs something on the hot path.

## Substituting an adapter

The doubles are in
[`doubles.mjs`](../integrations/conexus/contracts/doubles.mjs), not in the
examples, so a benchmark imports the same backend the contract tests use rather
than a lookalike that drifts from it.

| Double | Stands in for | Keeps |
| --- | --- | --- |
| `contractCompletion` | `configuredCompletion` | Request checking, `model_not_allowed`, cancellation |
| `contractSearch` | `configuredWebSearch` | Query bounds, result bounds, refusal codes, cancellation |
| `contractFinancialTool` | a financial Tool node | The command surface, the confirmation gate, async job states |
| `memoryStateStore` | `openState` | Snapshot validation on every save |

[`examples/conexus/adapter-contracts/`](../examples/conexus/adapter-contracts/README.md)
is the worked example: the same minimal task runs over the shipped
`createModelCompletion` against a loopback endpoint and over the in-process
double, and the two outcomes are compared with one `deepEqual`.

The OS runtime adapter is deliberately *not* substituted. Tool child processes,
their environment allowlist and their timeouts are what the Agent is measured
through, so simulating them would measure the double.

```bash
node --test tests/conexus-adapter-contracts.test.mjs
node --test 'examples/conexus/adapter-contracts/test/*.test.mjs'
```

The first needs no built runtime. The second skips with a build hint until
`python scripts/build_conexus_runtime.py` has run.

## Upgrading the pinned core

[The extension guide](12_CONEXUS_EXTENSION_GUIDE.md#upgrading-the-pinned-core)
has the import sequence. This section is the part that blocks.

`vendor/conexus` is a checksum-verified export, not a checkout, which creates
two distinct failure modes. `scripts/check_conexus_upgrade.py` is the gate for
both.

**Between upgrades**, an application change can edit the snapshot. Run with no
arguments, the gate re-verifies every digest, confirms the lock and the export
agree, and confirms the contracts were verified against the pinned revision.
It needs no Node build, so it runs in the ordinary Python quality job and a
modified vendor file fails a pull request rather than a release:

```bash
python scripts/check_conexus_upgrade.py
```

**At upgrade time**, a candidate export has to be reviewed before it is
imported. `sync_conexus_core.py` already refuses a dirty or out-of-boundary
export, but it does not say what changed — and the changes that matter most are
in the files AlphaLab's adapters are written against:

```bash
python scripts/check_conexus_upgrade.py --export-root /path/to/conexus-export
```

This prints the file-level diff and, for each changed interface file, names the
application file to review and the contracts whose shape may have moved:

```
  apps/local-host/src/model-provider.mjs
      review: integrations/conexus/model-providers.mjs
      contracts: model.completion.request, model.completion.response, error.envelope
```

A candidate touching one of those files **exits non-zero** until a reviewer
re-runs with `--accept-interface-change`. That is the "identify changes that
belong in application adapters" step made mechanical: it cannot be skipped by
not noticing.

Then run the whole chain. It stops at the first failure, so a verification that
fails blocks the upgrade instead of being stepped over:

```bash
python scripts/check_conexus_upgrade.py --verify
```

| Step | Guarantees |
| --- | --- |
| rebuild from the pinned export | The snapshot still builds, with every digest re-verified |
| upstream core tests | The core's own suites pass from the build |
| framework documentation | Every path, symbol, command and complete-set claim still resolves |
| adapter contracts | Both validators agree, the taxonomy is complete, the tool bundle conforms |
| adapter conformance | The shipped adapters still satisfy the contracts offline |
| lifecycle and financial examples | Run semantics, failure shapes and restart behaviour are unchanged |
| adapter substitution | A replacement adapter still executes the same task identically |

Finally, move `verifiedCoreRevision` in the contract document to the new
revision. Until it moves, the contract test fails — which is the point: the
pin and the verification travel together.

## Changing these contracts

```bash
python -m pytest tests/contracts/test_conexus_adapter_contracts.py -q
node --test tests/conexus-adapter-contracts.test.mjs
python scripts/check_conexus_docs.py
```

CI enforces, in addition to the two validators agreeing: every contract is a
valid JSON Schema and declares an owner, a direction, notes, and at least one
valid and one invalid example; `retryableCodes` is a subset of the declared
codes; the search family matches the adapter exactly; the core families cover
everything the snapshot raises; and the shipped tool bundle accepts no
top-level field the request contract rejects.

A new keyword in a contract schema is a deliberate step: `validate.mjs` reports
an unsupported keyword as an error rather than ignoring it, so adding one means
implementing it and extending the example corpus that proves the two
validators still agree.
