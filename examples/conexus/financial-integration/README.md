# Financial integration example

The AlphaLab shape, reduced to what the framework requires and run against a
stub API. Read [the minimal example](../minimal-agent/README.md) first.

```bash
python scripts/build_conexus_runtime.py        # once
node examples/conexus/financial-integration/main.mjs
```

```text
coverage: rq.bars 2024-01-02..2024-12-31 (244 sessions)
unauthorized run refused: backtest.run requires confirm_python_execution=true.
backtest submitted: bt_000001
first status read: running
terminal status: succeeded
summary: total_return=0.1432 vol=0.0934

status: completed
summary: Ran one authorized backtest and recorded its pinned result.
```

## What this adds over the minimal example

**A business API behind the tool.** The Tool node calls
`ALPHALAB_API_ORIGIN`, injected through
`createLocalRuntime({ toolEnvironment })` — the only channel an application has
for configuring tool processes. [`doubles/api-stub.mjs`](doubles/api-stub.mjs)
answers the same routes with fixed numbers, so no Python backend, project
database or runtime data store is needed.

**An execution gate.** `confirm_python_execution` is a top-level field of the
tool contract, never nested inside `args`, so arguments copied from untrusted
text cannot authorize execution. The demo attempts an unauthorized run first and
shows it refused; a test asserts the backend saw no request at all.

**Asynchronous work at the Agent boundary.** `backtest.run` returns a job id
immediately and nothing else. `backtest.status` reads once without blocking,
`backtest.wait` polls to a terminal state under its own bounded timeout and
reports `timed_out` rather than hanging, and only then does `backtest.summary`
read the result. This is what keeps a long run from being mistaken for a hung
tool call.

**A structured deliverable.** The conclusion goes into a `custom` node whose
JSON Schema the application owns, written in one atomic `edit`, rather than
into prose. A client never observes a half-updated result.

## The validation suite

```bash
node --test 'examples/conexus/financial-integration/test/*.test.mjs'
```

| Test | Asserts |
| --- | --- |
| submitted, polled, waited on, read | submission returns only a job id; one status read reports `running`; exactly one job is ever submitted |
| execution without the confirmation flag | refused in the tool, no backend request, result node untouched |
| unreachable backend | one bounded single-line error, Run ends `blocked`, not `failed` |
| cancelling during a wait | the structured output is discarded even though the `edit` succeeded |

The last test also records a limit worth knowing: cancelling a Run does **not**
cancel work already accepted by the backend. The abort signal reaches tool child
processes, not another service's queue. Reclaiming that is the application's
responsibility.

## Differences from the real bundle

[`integrations/conexus/alphalab-research-agent/`](../../../integrations/conexus/alphalab-research-agent/)
is the production graph. It carries 44 commands across two Tool nodes, an
injected SDK skill, three Harness output nodes written as one atomic batch, a
`web.search` host capability, and the data-acquisition retry loop described in
[Conexus Research Agent](../../../docs/04_CONEXUS_AGENT.md). This example carries
five commands and no skill, because the point here is the contract rather than
the command surface.

The stub's numbers are arbitrary and carry no economic meaning, and must never
be read as strategy evidence. They exist only to make a Run deterministic; this
example asserts the *shape* of a research Run, never a financial result.
