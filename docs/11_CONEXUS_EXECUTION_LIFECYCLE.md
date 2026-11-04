# Conexus execution lifecycle

One Run, end to end: how a request becomes model calls, tool calls, waits on
asynchronous work, a committed output and a delivered result — and what happens
when it is cancelled, when the browser goes away, or when the Host restarts.

Every semantic below has an executable counterpart in
[`examples/conexus/minimal-agent/test/lifecycle.test.mjs`](../examples/conexus/minimal-agent/test/lifecycle.test.mjs)
and
[`examples/conexus/financial-integration/test/research.test.mjs`](../examples/conexus/financial-integration/test/research.test.mjs).
Those tests are the specification; this document explains it. If the two
disagree, the tests are right and this file is stale — which is why
`scripts/check_conexus_docs.py` and the example suites both run in CI.

Read [Framework architecture](10_CONEXUS_FRAMEWORK.md) first for the vocabulary.

## The happy path

```text
POST /api/public/harnesses/:slug/runs
  |  validate input against the exposure schema
  |  mint a per-run access token
  |  append run.queued, reply 202 { run, accessToken }
  v
one promise queue (strictly serial per Host)
  |  append run.started, status -> running
  v
Agent loop (runtime-core)
  |  model call -> assistant turn
  |  tool calls, in order, one at a time
  |  ... repeat ...
  |  complete(status, summary)
  v
commit
  |  apply workspaceChanges to persisted nodes
  |  bump revision, stamp updatedByRunId
  |  status -> completed | blocked
  v
append run.completed (or run.blocked); SSE stream ends
```

### Request validation

`validateHostedExposureInvocation` checks the input against the exposure's
declared schema before a Run exists. A rejected input never reaches the queue.
Creation also fails closed when no model is configured (503) and when 32
non-terminal Runs are already present (429).

AlphaLab adds one gate of its own, in its launcher rather than in the core:
[`integrations/conexus/local-host.mjs`](../integrations/conexus/local-host.mjs)
installs a `preHandler` hook on the run-creation route that returns 503 when no
model provider is configured, so the workstation reports a configuration
problem instead of starting a Run that cannot call a model.

### Model calls

The Host passes a single `complete(request)` callback; Runtime Core owns
everything above it. The callback gets `messages`, `tools`, `toolChoice`,
`maxTokens`, `model` and a `signal`, and returns `{ content, tool_calls,
finish_reason }`. A callback that rejects ends the Run as `failed` unless the
Run was already aborted, in which case it ends `cancelled`.

The bundled client, `createModelCompletion`, is an OpenAI-compatible POST to
`<baseUrl>/chat/completions` with `stream: false`. Its rules exist for
containment, not convenience:

- HTTPS, or HTTP only for `localhost` / `127.0.0.1` / `[::1]`.
- No credentials in the URL.
- A non-loopback endpoint requires an API key; a loopback one does not.
- A requested model other than the configured one is refused with
  `model_not_allowed` — the Harness cannot redirect the host to another model.
- A non-2xx response surfaces as `Model endpoint returned HTTP <status>` with
  the body discarded, because provider error bodies can echo credentials.
- Responses are capped at 5 MiB.

### Tool calls

Tool calls within one assistant turn run **sequentially**, and the loop closes
the transcript on every exit path. Each tool call always gets a `tool` message,
even one that never ran: `appendUnexecutedToolResults` writes
`{ success: false, skipped: true, reason }` for the remainder when the Agent
completes, is aborted, or receives new user input mid-turn. An unmatched
`tool_call_id` would make the next model call invalid, so this is correctness,
not tidiness.

Malformed arguments are recovered where possible and otherwise reported as
`Invalid JSON tool arguments.` with details — the model gets a correctable
error rather than a crashed Run.

**Failure has three layers, and they look different to the Agent.** This
distinction matters more than it sounds: an Agent that checks only the envelope
will report a refused request as a result.

| Layer | What happened | What the Agent sees |
| --- | --- | --- |
| Contract | Input violated the capability's `input_schema` | `{"success": false, "error": "Invalid input for tool.invoke.", "details": [...]}` — the body never ran |
| Resolution | Node or capability does not exist | `{"success": false, "error": "Node X does not declare capability Y."}` |
| Invocation | The child process exited non-zero or timed out | `{"success": false, ...}` |
| Application | The tool ran and returned its own verdict | `{"success": true, "output": {"success": false, "error": "..."}}` |

The last row is the trap: a tool that *returns* a failure is a **successful
invocation**. The tool's own verdict is nested in `output`.

None of the four fails the Run. A tool failure is data; the Agent decides the
outcome. That is what makes `blocked` useful.

### Asynchronous work

Long work must not block a tool call, because a tool that outlives the
orchestration request window is indistinguishable from a hung one. The pattern
AlphaLab uses, demonstrated in
[`examples/conexus/financial-integration/`](../examples/conexus/financial-integration/):

1. `backtest.run` validates, pins the current source packages, submits one job,
   and returns **only a job id** immediately.
2. `backtest.status` reads current state once, without blocking.
3. `backtest.wait` polls to a terminal state under its own bounded timeout, and
   returns `timed_out: true` rather than hanging if it expires.
4. `backtest.summary` reads the frozen result.

Two consequences worth stating. The Agent must not treat a submitted job as a
finished one — `maxAttempts: 1` means a second submission is duplicate
execution, not a retry. And **cancelling a Run does not cancel backend work**:
the abort signal reaches tool child processes, not a job already accepted by
another service. Reclaiming it is the application's job; AlphaLab's answer is in
[AlphaLab's shared Run record](#alphalabs-shared-run-record).

### Output submission and commit

Deliverables are workspace nodes, not prose. The Agent writes them with
`create` and `edit`, and `edit` applies an ordered batch atomically — either
every operation lands or none does. AlphaLab relies on this to write its three
Harness output nodes in one batch so a client never observes a half-updated
result.

**Nothing commits until the Run is terminal.** `executeHostedRelease` mutates
an in-memory workspace and returns `workspaceChanges`; the Host applies them
only after a `completed` or `blocked` outcome, then bumps `revision` and stamps
`createdByRunId` / `updatedByRunId`. A `failed` or `cancelled` Run commits
nothing, even for edits that already succeeded inside the loop. Both example
suites assert this.

### Completion

Only `complete` ends a Run, and it takes `status` (`done` or `blocked`),
`summary`, and `blocker` when blocked. The validation is strict and
self-correcting: unknown fields, a missing summary, `blocker` with
`status: done`, or a missing `blocker` with `status: blocked` all return an
error that tells the Agent what to fix, and the loop continues. `done` becomes
the Run's `completed`; `blocked` stays `blocked`.

`blocked` is a first-class success-of-sorts: the Agent determined it cannot
proceed and said why. An Agent that fabricates an answer instead of blocking is
the failure mode the status exists to prevent.

### Result delivery

A terminal Run carries `status`, `summary`, `nodeChanges` (ids only, never
payloads), `workspaceRevision` and, for Tool exposures, `result`. Clients read
node content from the workspace endpoint, filtering to nodes whose
`updatedByRunId` matches the Run.

The SSE stream carries `run.queued`, `run.started`, `run.message`,
`run.interaction_requested`, `run.interaction_resolved` and one terminal
`run.<status>`. Each event has a monotonic `sequence` used as the SSE event id.
A heartbeat comment goes out every 15 seconds. The stream ends itself on the
terminal event.

Only the exposed Agent node's turns are forwarded. A nested Harness or Agent
run's assistant turns and tool results are filtered out by `ownerNodeId`; tool
results are reduced to `{ success, message? }` with the message truncated to
280 characters, so full tool payloads never reach the event log.

## Cancellation

`POST /api/public/runs/:id/cancel` on a non-terminal Run:

- **Running** — aborts the `AgentRunController`. The abort signal propagates to
  the model call, tool child processes and nested runs. Pending interactions
  settle. The loop closes the tool transcript and returns through `onAbort`.
  Status becomes `cancelled` with `run_cancelled`.
- **Queued** — marked `cancelled` directly; it never starts.
- **Terminal** — a no-op that returns the existing record.

Cancellation discards workspace changes, as above. On shutdown the Host cancels
queued Runs, aborts active controllers, drains the queue, then closes.

What cancellation does **not** do: stop work already handed to another service
(see [Asynchronous work](#asynchronous-work)), and undo a side effect a tool
already performed outside the workspace.

## Browser disconnection and reconnection

The Run is server-side state. A dropped connection is invisible to it: no
cancellation, no pause, no loss of events.

The SSE endpoint is resumable. A client reconnecting sends
`Last-Event-ID: <sequence>`, and the Host replays only events after it, then
continues live. Reconnecting after the Run finished yields the remaining
buffered events and an immediate close.
`examples/conexus/minimal-agent/test/lifecycle.test.mjs` drops a socket
mid-Run, reconnects, and asserts no event is repeated and none is skipped.

Two limits. Events are capped at 1000 per Run and evict from the front, so a
client gone long enough to miss evicted events cannot recover them from the
stream — it must reconcile from the Run record and workspace instead. And the
per-run access token is returned exactly once at creation; a client that loses
it cannot re-subscribe.

AlphaLab's frontend therefore treats the server Run as authoritative and
combines SSE with an authenticated two-second snapshot reconciliation loop.
Once either channel observes a terminal state, later stale snapshots cannot
regress the UI to `queued` or `running`. See
[Conexus Research Agent](04_CONEXUS_AGENT.md) for that delivery contract.

### Pending user input across a disconnection

`request_user_input` suspends the Run until an answer arrives. The pending
question is part of the Run record, not only the stream, so a client that
reconnects reads it from `GET /api/public/runs/:id` and answers normally. An
answer must be non-empty and at most 20000 characters; answering an interaction
that is no longer pending — including answering the same one twice — returns
409.

There is no timeout on a pending interaction. A Run waiting for an answer waits
indefinitely, and because the Host is serial it also blocks every queued Run
behind it. Cancel it to release the queue.

**One caveat.** A question raised by a *nested* Agent surfaces as the Run's
pending interaction but cannot be answered — the answer endpoint resolves
against the root controller only, so it returns 409 and the Run hangs until
cancelled. This is unreachable in AlphaLab today because its graph has one
Agent node. It is a hard blocker for multi-Agent work and is documented with its
fix in
[`examples/conexus/two-agent/INTERFACE.md`](../examples/conexus/two-agent/INTERFACE.md).

## Host restart

Starting a Host against an existing state directory:

1. **Lock.** `host.lock` is created exclusively with the owning PID. If it
   exists, the PID is probed: a dead owner's lock is removed and startup
   continues; a live owner fails with `A local Host is already using this data
   directory.` A forced parent exit that orphans a Host still releases the lock,
   and `tests/api/test_local_agent_integration.py` covers that.
2. **State.** `state.json` is read and its `schema` checked. An unrecognized
   schema refuses startup with existing data preserved — there is no migration.
3. **Release reconciliation.** If the release checksum differs, `mergeRelease`
   upgrades the graph field by field: a field the Agent changed since the last
   release keeps the Agent's value; an untouched field takes the new release's.
   Nodes added by the release appear; nodes it removed are deleted only if
   unmodified. Reports and other Agent-created nodes survive graph upgrades.
4. **Interrupted Runs.** Every non-terminal Run becomes `cancelled` with
   `host_restarted` and the message *"The previous Host stopped during this run.
   Start a new run to continue."* Pending interactions are dropped.
   **Nothing is replayed** — no model call, no tool call. A Run's work is not
   idempotent, so resuming it could duplicate a side effect that already
   happened. The lifecycle test asserts the restart performs no model call at
   all.

A restart therefore loses in-flight work and keeps everything committed. For
AlphaLab the Host also gets a new port on each launch; the connection file is
rewritten atomically and the Python API rereads it, so a restart is invisible to
the browser apart from the cancelled Run.

## AlphaLab's shared Run record

The sections above describe the Host. The Host forgets things AlphaLab must still
be able to explain: its event log is capped, its tool results are reduced, and it
holds no record of the jobs a tool started or of whether a result reached the
conversation. AlphaLab therefore keeps one server-side record per task in
[`apps/api/services/agent_conversation_service.py`](../apps/api/services/agent_conversation_service.py).
Network recovery lives in
[`agent_run_service.py`](../apps/api/services/agent_run_service.py) and the shared
vocabulary in [`agent_lifecycle.py`](../apps/api/services/agent_lifecycle.py).
Benchmarks and any future multi-Agent work read this record rather than keeping
their own. [`tests/api/test_agent_run_lifecycle.py`](../tests/api/test_agent_run_lifecycle.py)
replays the faults below at the HTTP boundary, and
[`tests/api/test_local_agent_integration.py`](../tests/api/test_local_agent_integration.py)
checks submission identity, job correlation and restart against the real Host.

### Task phases and stop reasons

Each Run payload the API returns carries `lifecycle`, which is what the UI renders:

```json
{
  "phase": "queued | running | awaiting_input | delivering | completed | blocked | failed | cancelled",
  "stop": { "category": "...", "code": "...", "retry": "...", "remediation": "...", "resumable": false },
  "delivery": "delivered | pending",
  "jobs": [],
  "activeJobs": 0
}
```

`delivering` means the Run has ended and its outputs have not yet reached the
conversation. `stop` is `null` for a Run that is active or completed. Every error
code the pinned core can raise is mapped to a category on purpose, and a test scans
the snapshot, so a Conexus upgrade that adds a code fails until it is placed.

| Category | Means | `retry` |
| --- | --- | --- |
| `cancelled` | Cancelled before finishing | `new_run` |
| `host_interrupted` | The Host stopped mid-Run; nothing was replayed | `new_run` |
| `lost` | The Host no longer recognizes the Run | `check_first` |
| `timeout` | Exceeded its time limit | `new_run` |
| `provider` | The model provider failed or answered unusably | `new_run` |
| `configuration` | The Agent cannot run as configured | `fix_setup` |
| `input` | The request was not accepted as written | `change_request` |
| `agent_blocked` | The Agent stopped and said why | `change_request` |
| `internal` | Anything else | `check_first` |

`resumable` is always `false`. No stopped task continues, for the reason given under
[Host restart](#host-restart). A terminal state never changes once recorded, and a
slow read cannot move a task backwards.

### One message, at most one Run

The browser generates a message ID once per send and reuses it when the same text is
sent again after an undecided attempt. `conversationId:messageId` is the submission
key, recorded in `agent_run_submissions` before anything is forwarded, and its digest
goes to the Host as `Idempotency-Key`.

The Host itself has no idempotency, so
[`integrations/conexus/run-submissions.mjs`](../integrations/conexus/run-submissions.mjs)
adds it from the launcher, without changing the vendored snapshot. It saves the Run
and its access token to a `0600` file before the 202 leaves, so a restart between the
two still replays the same Run. `GET /api/alphalab/submissions/:key` answers what
became of a key.

| What happened to `POST /api/conexus/runs` | Recorded as | A repeat of the same message |
| --- | --- | --- |
| Accepted | `accepted` | `200` with the same Run and `replayed: true` |
| Another attempt is in flight | `submitting` | `409 submission_in_progress` |
| Refused by the Host | row removed | Forwarded again |
| Connection never established | row removed | Forwarded again |
| Connection lost after sending | `unconfirmed` | Asks the Host; never resends while unknown |
| Host says it never saw the key | `not_started` | Forwarded again |

While a submission stays unconfirmed the API returns `409 submission_unconfirmed`. An
`unconfirmed` submission is settled by the Host's answer, in a request or by the
recovery worker. **Asking the Host is local mode only**, because a remote deployment
does not have the launcher route. There it stays unconfirmed and is never resent, so
the failure is a message the user must decide about, not a second billed Run.

### The recovery worker

`run_recovery_lifespan` runs for the life of the API process, so a task finishes
being recorded with no browser open. Every two seconds it reads open Runs, resolves
open submissions, and drains events by `Last-Event-ID` into the trace.

A Run is marked `failed` with `run_record_lost` only after `LOST_RUN_READS` (3)
consecutive answers of 401, 403, 404 or 410. An unreachable Host or a 503 is an
outage, says nothing about the Run, and never ends it. Lost is not reported as
cancelled, because nobody knows what the Run did.

### Trace

`GET /api/conexus/runs/{id}/trace` returns one account of a task: the message that
started it, Host timing, model turns, tool calls and results, linked jobs, and
output delivery. `seq` is the stable order AlphaLab recorded entries in, so a short
Run's delivery can precede its drained events; `upstreamSequence` orders Host events.

Tool calls keep the command name and call ID and never their arguments. Model turns
keep a character count and never their content. Interactions keep only an ID. A
failure message is kept only after `sanitize_text` removes credentials and internal
paths. Traces are pruned after `ALPHALAB_AGENT_TRACE_RETENTION_DAYS` (default 30);
open work and any task still owed its delivery or events are kept.

`complete` is `false` when the account has a known hole: `events.gap` if the Host
evicted events before they were read, `trace.truncated` past
`MAX_TRACE_ENTRIES_PER_RUN`, `trace.incomplete` if the Host disappeared first. A
trace can be incomplete; it does not claim otherwise.

Three limits. **Token usage is unavailable:** the pinned Host discards the provider's
usage, so `usage.available` is `false` and only model turns are counted. **Job
correlation needs the local launcher**, which gives tools their Run ID. A remote Host
does not, so its jobs are not linked to a Run. **Operation approvals are not traced:**
the local Host's approval gate raises no Host event, so a Run shows `awaiting_input`
while it waits, but the trace holds no entry for the question or the answer.

### Cancelling work that outlives the Run

`POST /api/conexus/runs/{id}/cancel` returns `{ run, jobs }`, and each job reports
what actually happened to it:

| Job | `outcome` |
| --- | --- |
| Queued and owned by this Run | `cancelled`; a worker that had already read it as queued cannot start it |
| Running backtest | `still_running`; it cannot be interrupted safely, and its result is kept |
| Running data sync | `cancelling`; it stops at its next batch |
| Deduplicated onto another requester's job | `still_running`; not this Run's to stop |
| Already finished | `already_finished` |

### Output reconciliation

When a terminal Run is delivered, the trace records `delivery.reconciled`: which
outputs were expected, which were delivered, which a later Run superseded, and which
were omitted by the size limit or missing from the workspace. With the linked jobs,
that accounts for a task's completed side effects after a fault.

## Quick reference

| Situation | Run status | Workspace | Replayed |
| --- | --- | --- | --- |
| `complete(done)` | `completed` | committed | — |
| `complete(blocked)` | `blocked` | committed | — |
| Tool failed, Agent recovered | `completed` | committed | — |
| Model call threw | `failed` | discarded | no |
| Cancelled while running | `cancelled` | discarded | no |
| Cancelled while queued | `cancelled` | untouched | no |
| Browser disconnected | unchanged | unchanged | n/a |
| Host restarted mid-Run | `cancelled` (`host_restarted`) | prior commits kept | no |
| Host no longer knows the Run (AlphaLab) | `failed` (`run_record_lost`) | unknown | no |
| Acknowledgement lost (AlphaLab) | whatever the Host recorded | unchanged | no |
