# Conexus examples

Runnable Conexus Harnesses with test doubles for the model and the business
API, so the whole set runs from a clean checkout with **no Conexus account, no
provider credential and no network egress**.

These are also the validation behind
[Framework architecture](../../docs/10_CONEXUS_FRAMEWORK.md),
[Execution lifecycle](../../docs/11_CONEXUS_EXECUTION_LIFECYCLE.md),
[Extension and upgrade](../../docs/12_CONEXUS_EXTENSION_GUIDE.md) and
[Adapter contracts](../../docs/13_CONEXUS_ADAPTER_CONTRACTS.md). Each
lifecycle semantic and each adapter contract those documents describe has a
named test here.

```text
examples/conexus/
  lib/runtime.mjs            resolves the built core under build/conexus
  minimal-agent/             one Harness, one Agent, one Tool node
  financial-integration/     the AlphaLab shape against a stub API
  adapter-contracts/         the same task over substituted adapters
  two-agent/                 reserved; documentation only
```

## Prerequisites

Node.js 22.18+ or 24, and the pinned core built once:

```bash
python scripts/build_conexus_runtime.py
```

That verifies every digest in `vendor/conexus/EXPORT.json`, builds the four
workspaces, and installs runtime dependencies into `build/conexus`. The examples
import from `build/conexus`, never from `vendor/conexus` — the vendor tree is a
read-only verified snapshot with no installed dependencies.

Without the build, the test suites skip with that instruction rather than
failing, so `node --test` stays usable on a fresh clone.

## Run them

```bash
node examples/conexus/minimal-agent/main.mjs
node examples/conexus/financial-integration/main.mjs
```

Each starts a loopback model endpoint and a local Host on ephemeral ports, drives
one Run over real HTTP including its SSE stream, prints what the Agent wrote,
and cleans up its temporary state directory.

## Validate them

```bash
node --test 'examples/conexus/minimal-agent/test/*.test.mjs'
node --test 'examples/conexus/financial-integration/test/*.test.mjs'
node --test 'examples/conexus/adapter-contracts/test/*.test.mjs'
python scripts/check_conexus_docs.py
```

The six situations issue #6 requires reproducible checks for, and where each
one lives:

| Situation | Test |
| --- | --- |
| Successful execution | `minimal-agent` — invokes a Tool node and commits its workspace change |
| Tool failure | `minimal-agent` — three tests, one per failure layer |
| User input | `minimal-agent` — suspends until an answer arrives |
| Cancellation | `minimal-agent` — discards workspace changes; `financial-integration` — discards structured output |
| Browser reconnection | `minimal-agent` — resumes the stream with `Last-Event-ID` |
| Host restart | `minimal-agent` — cancels interrupted runs and replays no tools |

Plus the serial-execution constraint, workspace-token handling, asynchronous
job submission, the execution-confirmation gate, and an unreachable backend.

[`adapter-contracts/`](adapter-contracts/README.md) adds what issue #11
requires: the same minimal task executed over the shipped model adapter and
over a replacement test adapter, with identical outcomes; every envelope the
real financial Tool node returns validated against
`financial.tool.response`; and the Host's authorization refusals validated
against `authorization.failure`.

## The test doubles

**The model** is a script, not a model. The Host needs only a
`complete(request)` callback, so
[`minimal-agent/doubles/scripted-model.mjs`](minimal-agent/doubles/scripted-model.mjs)
provides a list of turns. Entries may be functions of the request, so a script
can read the tool result the Host just fed back and assert on it mid-Run — which
is how these tests verify that real tool output reached the Agent rather than
trusting that it did.

Two shapes: `scriptedCompletion` is the in-process callback the tests use;
`startModelEndpoint` serves the same script over a loopback
OpenAI-compatible endpoint, so the demos also exercise `createModelCompletion`
and its URL, credential and size rules.

**The tools** are real. The minimal example's Tool node executes as an actual
child process through the real adapter; its arithmetic is deterministic so no
fixture service is needed.
[`financial-integration/doubles/api-stub.mjs`](financial-integration/doubles/api-stub.mjs)
stands in for the AlphaLab HTTP API with fixed numbers and a job that reports
`running` before it reports `succeeded`.

The stub's numbers are arbitrary and carry no economic meaning. They exist to
make a Run deterministic, and are not fixtures with recorded ground truth.

## What these examples are not

They do not exercise AlphaLab's quantitative engines, its project database, or
its runtime data store — [`tests/api/test_local_agent_integration.py`](../../tests/api/test_local_agent_integration.py)
covers the real integration, including a real Python backend and a real tool
process reaching it.

They are also not a security boundary demonstration. Tool nodes here run as
trusted local code under the current OS account, exactly as in production; see
[Trust boundary](../../docs/10_CONEXUS_FRAMEWORK.md#trust-boundary).
