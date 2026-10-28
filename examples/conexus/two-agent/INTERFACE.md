# Two-Agent interface contract

The handover document for the later multi-Agent work. Everything below was
verified by probe against the pinned core in `build/conexus`
(`UPSTREAM.json` revision `9cd8c69`), not inferred from the core's own
documentation.

The short version: **delegation between two Agent nodes already runs on the
bundled local Host.** It is not exposed, not observable, and it deadlocks if a
child Agent asks the user a question. Those are the three things to fix.

## What already works

Runtime Core gives every `agent` node three capabilities
([`capabilities/node-capability-contracts.ts`](../../../vendor/conexus/packages/runtime-core/src/capabilities/node-capability-contracts.ts),
`getNodeCapabilityContracts`):

| Capability | Effect |
| --- | --- |
| `agent.run` | Start the target Agent with `{ task, context? }`. Returns immediately. |
| `run.wait` | Block until that node's run reaches a terminal state, then return its outcome. |
| `run.cancel` | Abort that node's run. |

A lead Agent reaches them exactly like any other node capability:

```text
use(node_id: "worker", capability: "agent.run",  input: { task: "..." })
    -> { "success": true, "node_id": "worker", "status": "running" }
use(node_id: "worker", capability: "run.wait",   input: {})
    -> { "success": true, "node_id": "worker", "status": "done",
         "result": { "summary": "..." } }
```

`agent.run` does not block, so the lead can start a child and keep working.
Child runs are tracked as `session.backgroundRuns` in
[`hosted/hosted-runtime.ts`](../../../vendor/conexus/packages/runtime-core/src/hosted/hosted-runtime.ts)
(`trackBackgroundRun`), and `executeHostedRelease` awaits all of them in its
`finally` block before cleanup. A child that is started and never waited on
still finishes before the parent Run commits.

Nothing extra has to be passed to `createLocalHost` for this. `executeHostedRelease`
creates its own `AgentHostRuntimeSession` when the caller supplies no
`controlSession`, and the job registry that makes `run.wait` work comes with it.

## What does not work

### Verified defect: nested user input deadlocks the run

A child Agent calling `request_user_input` **hangs the whole Run.** This is the
one hard blocker, and it has to be fixed in Runtime Core or the local Host, not
worked around in an example.

What happens, in order:

1. The child's interaction is emitted as `agent.interaction_requested`.
2. [`apps/local-host/src/index.mjs`](../../../vendor/conexus/apps/local-host/src/index.mjs)
   forwards it with **no owner filter**, so it becomes the Run's
   `pendingInteraction` with `ownerNodeId` set to the *child* node. The client
   sees a question it has no transcript context for.
3. `POST /api/public/runs/:id/interactions/:interaction/answer` resolves
   interactions against `controllers.get(run.id)` — the **root**
   `AgentRunController`. The child run holds its own controller, created by
   `startAgentHostNodeRun`. The root controller does not know this interaction
   id, so `resolveInteraction` returns `false` and the endpoint answers
   **HTTP 409 `Interaction is no longer pending`**.
4. The Run stays `running` indefinitely. Observed: still `running` after the
   answer was rejected, with no timeout.
5. `POST /api/public/runs/:id/cancel` does recover the Host — the root abort
   signal propagates to the child — and the Run ends `cancelled` with
   `run_cancelled`.

So the failure is a permanent hang that only cancellation clears. Note the
asymmetry that causes it: `agent.assistant_turn` and `agent.tool_result` **are**
filtered to `ownerNodeId === bound.exposure.nodeId` and so a child's turns are
dropped, while `agent.interaction_requested` is not filtered and a child's
question is surfaced. One of those two decisions is wrong; they cannot both be
right.

**Required fix.** Route an answer to the controller that owns the interaction
rather than to the root controller. `AgentHostRuntimeSession` already tracks
per-job controllers, so the Host needs to resolve through the control session
instead of its own `controllers` map. Pick the owner filter deliberately at the
same time: either forward child turns too, or stop forwarding child questions.

Until this is fixed, a two-Agent example must keep child Agents free of
`request_user_input`, and a test must assert that rather than leaving it to
chance.

### Child runs are invisible to the client

Because `agent.assistant_turn` and `agent.tool_result` are filtered to the
exposed Agent node, a client streaming `/api/public/runs/:id/events` sees the
lead Agent's turns and nothing from the worker. The worker's tool calls, its
reasoning and its failures never reach the UI; only the lead's eventual summary
does.

For a single-Agent Harness that filter is correct — it keeps a nested Harness
Run from leaking into the transcript. For a two-Agent Harness it removes most of
what an operator needs. Deciding what a client should see is a design question
the multi-Agent work owns; it is not a bug with one obvious fix.

Whatever is chosen, `run.events` is capped at 1000 entries per Run
(`if (run.events.length > 1000) run.events.shift()`), and that cap was sized for
one Agent. Two chatty Agents will evict early events, and the SSE
`Last-Event-ID` resume path has no way to replay what was evicted.

### Top-level runs stay serial

Delegation is concurrent *inside* a Run. Runs themselves are not: the local Host
chains every run onto a single promise queue
(`queue = queue.then(() => execute(run))`), so one Host executes one Run at a
time regardless of how many Agents the graph contains.
`examples/conexus/minimal-agent/test/lifecycle.test.mjs` asserts this.

Multi-Agent work that needs concurrent *top-level* Runs needs a different
change: per-workspace queues, or an optimistic-concurrency story for the
workspace commit. The current commit model writes
`state.workspace`, `state.nodes` and a single `state.revision` wholesale at the
end of a Run, so two concurrent Runs on one workspace would silently lose one
side's changes. Do not relax the queue without replacing that commit.

### The Harness policy declares serial execution

`runtime.policy` in a Harness template carries `maxAttempts` and `allowParallel`.
Both example Harnesses and the AlphaLab bundle declare
`{ maxAttempts: 1, allowParallel: false }`. A multi-Agent Harness that intends
parallel work should say so in its own template rather than inheriting a copied
constant.

## Contract the implementation must satisfy

1. **One Agent owns the Run outcome.** `complete` from the exposed Agent ends
   the Run. A child's `complete` resolves only that child's `run.wait`. Do not
   introduce a second path to terminal status.
2. **One workspace commit.** Child runs mutate the same in-memory workspace and
   are included in the parent's single commit. A child must not commit
   independently; a cancelled parent must discard child writes too.
3. **Cancellation is hierarchical.** Cancelling the Run aborts children through
   the derived signal. Verified working today. `run.cancel` on a child must not
   abort the parent.
4. **Child failure is data, not a Run failure.** A child that fails or is
   aborted returns `status: error` / `aborted` through `run.wait`, and the lead
   Agent decides the Run outcome — the same rule tool failures follow.
5. **No duplicate execution.** `maxAttempts: 1` is the harness runtime policy.
   A lead Agent retrying a failed child is duplicate execution unless the
   policy says otherwise.

## Telling the two Agents apart in a test double

A scripted model double sees one flat `messages` array per call and must decide
which Agent is asking. Do not branch on node labels: a lead Agent's system
prompt includes the labels and descriptions of its connected nodes, so matching
the worker's label matches the lead's prompt too. That mistake silently makes
every turn look like the worker's.

Branch on a marker unique to each Agent's own `systemPrompt`, which is never
copied into another Agent's prompt:

```js
const system = request.messages.find((message) => message.role === 'system')?.content ?? ''
const isWorker = system.includes('MARKER_WORKER_PROMPT')
```

## Reproducing these findings

The probes behind this document are not committed — they would be a second Agent
loop in the repository, which this issue excludes. Reproduce them by building a
three-node graph (`harness`, exposed `lead` agent, unexposed `worker` agent,
one edge) with `compileHarnessWorkspace`, then scripting the lead to call
`agent.run` and `run.wait` as shown above. `examples/conexus/minimal-agent/host.mjs`
is the bootstrap to copy.
