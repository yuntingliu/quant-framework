# Minimal Agent example

The smallest Conexus graph that does something: one Harness, one Agent, one
Tool node. Start here.

```bash
python scripts/build_conexus_runtime.py        # once
node examples/conexus/minimal-agent/main.mjs
```

Expected output, with ephemeral ports:

```text
local Host: http://127.0.0.1:62058
run 6c2e4ca5-... queued
  [1] run.queued (queued)
  [2] run.started (running)
  ...
  [9] run.completed (completed)
status: completed
summary: Summarized the series and saved a note.

# Series summary

count=5 mean=0.0054 min=-0.011 max=0.021
```

Those numbers are computed by the Tool node in a real child process, read back
out of the tool result by the scripted model, and written into a workspace note
that outlives the Run.

## What is in it

| File | Role |
| --- | --- |
| [`harness.mjs`](harness.mjs) | The graph: harness node, Agent node, Tool node, one edge |
| [`host.mjs`](host.mjs) | Compiles the graph and starts the local Host |
| [`main.mjs`](main.mjs) | End-to-end demo over real HTTP, including SSE |
| [`doubles/scripted-model.mjs`](doubles/scripted-model.mjs) | Deterministic model stand-in, in-process and over HTTP |
| [`test/lifecycle.test.mjs`](test/lifecycle.test.mjs) | The lifecycle validation suite |

## The three things to notice

**Capability comes from the graph.** The Agent sees seven native tools —
`find`, `observe`, `create`, `edit`, `use`, `request_user_input`, `complete` —
and nothing else. The statistics tool is reached through
`use('series-stats', 'tool.invoke', { values: [...] })`. There is no tool
registry to register with.

**The Tool node makes all three failure layers reachable.** `values` is
constrained in `inputSchema`, so a bad series is rejected before the child
process starts. `statistic` is deliberately unconstrained, so the tool body
owns that check and returns its own bounded error — which arrives as a
*successful* invocation with the failure nested in `output`. Passing
`statistic: 'raise'` throws, which fails the invocation itself. Three tests, one
per layer.

**Deliverables are nodes.** The Agent's answer is a `note` node committed to the
workspace when the Run reaches a terminal status, with `updatedByRunId` stamped.
Nothing commits for a cancelled or failed Run, even for a `create` that already
succeeded inside the loop.

## The validation suite

```bash
node --test 'examples/conexus/minimal-agent/test/*.test.mjs'
```

Twelve named tests, each the executable form of a section in
[Execution lifecycle](../../../docs/11_CONEXUS_EXECUTION_LIFECYCLE.md):
successful execution; the three tool-failure layers; an unreachable node; a
blocked run; user input, including rejecting a double answer; cancellation
discarding changes; `Last-Event-ID` stream resumption after a dropped socket;
Host restart cancelling an interrupted run without replaying it; the
single-Host lock and token handling; and the serial run queue.

They skip with a build instruction if `build/conexus` is absent.
