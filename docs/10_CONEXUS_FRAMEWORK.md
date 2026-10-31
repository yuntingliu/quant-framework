# Conexus framework architecture

What Conexus is, where its boundaries are, and which parts of a running
AlphaLab Agent belong to the framework rather than to AlphaLab. Read this
before [Execution lifecycle](11_CONEXUS_EXECUTION_LIFECYCLE.md), which covers
what happens during one Run, and
[Extension and upgrade](12_CONEXUS_EXTENSION_GUIDE.md), which covers changing
any of it.

Everything here describes the pinned snapshot recorded in
[`integrations/conexus/runtime.lock.json`](../integrations/conexus/runtime.lock.json),
currently core revision `9cd8c69`. The packages are `private: true` and their
license is not yet selected, so this is a buildable in-repo snapshot rather
than a dependency on a published release.

## The one-paragraph version

Conexus executes an **Agent** that is given a **graph of nodes** and a fixed set
of **tools for working on that graph**. A node can be a document, a Tool with
code, a data source, or another Agent. The Agent discovers nodes, reads them,
invokes the **capabilities** they declare, and writes its deliverables back into
the graph as nodes. A **Harness** is the publishable unit: a named subgraph with
a manifest, an entry point, and a compiled, immutable **Release**. The
**local Host** is a single-user HTTP service that holds one workspace, queues
Runs, and persists the result. AlphaLab supplies a graph and a business API; it
supplies no Agent loop.

## Module boundary

```text
Application (AlphaLab)
    integrations/conexus/local-host.mjs          launcher: graph + capabilities
    apps/api                                     business API the Tool nodes call
    alphalab/                                    quantitative SDK and engines
        |
        v
local-host          HTTP, loopback identity, run queue, durable workspace
    |
    +--> runtime-core        Agent loop, native tools, graph semantics, Releases
    +--> node-host-runtime   OS adapters: tool processes, shell, node storage
    |        |
    |        +--> runtime-core
    +--> runtime-protocol    runtime events, commands, graph types
```

Four exported workspaces, listed in
[`vendor/conexus/open-core.manifest.json`](../vendor/conexus/open-core.manifest.json):
`runtime-protocol`, `runtime-core`, `node-host-runtime`, `local-host`. The
Canvas editor, account service, billing and enterprise deployment are not part
of the snapshot.

The dependency direction is one-way: applications import the core, never the
reverse. [`vendor/conexus/scripts/check-open-core-boundaries.mjs`](../vendor/conexus/scripts/check-open-core-boundaries.mjs)
enforces it — it rejects imports of unexported `@conexus/*` packages, relative
imports that escape the four workspaces, symlinks, and a small denylist of
commercial identifiers. `scripts/build_conexus_runtime.py` runs that checker
before building, so a boundary violation fails the build rather than shipping.

### Who owns what

| Concern | Owner | Entry point |
| --- | --- | --- |
| Agent loop, turn/tool transcript | runtime-core | [`agent/agent-loop.ts`](../vendor/conexus/packages/runtime-core/src/agent/agent-loop.ts) |
| Completion semantics (`done` / `blocked`) | runtime-core | [`agent/agent-completion.ts`](../vendor/conexus/packages/runtime-core/src/agent/agent-completion.ts) |
| Cancellation, continuations, interactions | runtime-core | [`agent/agent-run-controller.ts`](../vendor/conexus/packages/runtime-core/src/agent/agent-run-controller.ts) |
| Release execution, native tool dispatch | runtime-core | [`hosted/hosted-runtime.ts`](../vendor/conexus/packages/runtime-core/src/hosted/hosted-runtime.ts) |
| Graph compilation into a Release | runtime-core | [`release/harness-workspace-release.ts`](../vendor/conexus/packages/runtime-core/src/release/harness-workspace-release.ts) |
| Capability contracts and validation | runtime-core | [`capabilities/node-capability-contracts.ts`](../vendor/conexus/packages/runtime-core/src/capabilities/node-capability-contracts.ts) |
| Running Tool code in a child process | node-host-runtime | [`tools/tool-node-executor.ts`](../vendor/conexus/packages/node-host-runtime/src/tools/tool-node-executor.ts) |
| HTTP surface, run queue, state file | local-host | [`apps/local-host/src/index.mjs`](../vendor/conexus/apps/local-host/src/index.mjs) |
| State file locking and atomic writes | local-host | [`apps/local-host/src/state.mjs`](../vendor/conexus/apps/local-host/src/state.mjs) |
| OS adapter wiring | local-host | [`apps/local-host/src/local-runtime.mjs`](../vendor/conexus/apps/local-host/src/local-runtime.mjs) |
| Model endpoint client | local-host | [`apps/local-host/src/model-provider.mjs`](../vendor/conexus/apps/local-host/src/model-provider.mjs) |
| The research graph, business tools, SDK skill | AlphaLab | [`integrations/conexus/`](../integrations/conexus/) |
| Quantitative computation | AlphaLab | [`alphalab/`](../alphalab/), [`apps/api`](../apps/api) |

The division to keep in mind: **AlphaLab decides what the Agent can do;
Conexus decides how a Run behaves.** AlphaLab has no say in when a Run commits,
how a tool failure is reported, or what `complete` means. Conexus has no
knowledge of factors, backtests or point-in-time correctness.

## Concepts

### Node

The unit of both state and capability. A node has an `id`, a `type`, optional
`parentId`, and a `data` object. Types the local Host persists are `note`,
`document`, `custom`, `image` and `files`; `agent`, `harness`, `tool`, `cli`
and `datasource` are executable or structural. There is no plugin registry: to
give an Agent a new ability you add a node to the graph.

### Capability

A typed operation a node declares, with an id, a JSON Schema for its input, an
`effect` (`none` / `read` / `write` / `external`), an `execution` mode
(`sync` / `async` / `interactive`), and a `schema_hash`. `getNodeCapabilityContracts`
derives them from node type:

| Node type | Capabilities |
| --- | --- |
| `tool` | `tool.invoke` (unless `exposeAsTool: false`) |
| `agent` | `agent.run`, `run.wait`, `run.cancel` |
| `harness` | `harness.run`, `run.wait`, `run.cancel` |
| `cli` | `cli.exec` |
| `datasource` | `datasource.query`, plus `datasource.execute` when `allowWrites` |

Host-supplied capabilities are separate: an application passes them to
`createLocalRuntime({ capabilities })` and they become runtime-managed nodes the
Agent can `find`. AlphaLab supplies exactly one, `web.search`.

### Tool

Two unrelated meanings, and conflating them causes confusion:

1. **Native Agent tools** — the fixed seven the model always sees:
   `find`, `observe`, `create`, `edit`, `use`, `request_user_input`, `complete`.
   This list is the Agent ABI and is not extended per application.
2. **Tool nodes** — graph nodes carrying `code` and a `runtime`
   (`node`, `python`, `bash`/`powershell`, `shell`). They are *not* exposed as
   model functions. The Agent reaches one through
   `use(node_id, 'tool.invoke', input)`.

A Tool node's code runs as its own file in a child process. The contract is a
top-level `async function run(input, context)` whose return value becomes the
tool result. `context.runNode(nodeId, args)` lets a tool invoke another node.
Timeouts and crashes are contained; **this is not a security sandbox** — see
[Trust boundary](#trust-boundary).

### Agent

A node with a `model`, a `systemPrompt`, an `objective` and an optional
`toolNames` list. Omitting `toolNames` means the host defaults. One Agent node
in a Harness is marked `exposeInHarness` with an `exposureId`; that is the entry
point a Run targets.

### Harness

A `harness` node plus its descendants. Its `data.template` carries the manifest
(id, version, exposures, permissions, dependencies) and
`runtime.policy` (`maxAttempts`, `allowParallel`). `compileHarnessWorkspace`
turns the live graph into a **Release**: a validated, checksummed artifact with
runtime fields and credential-shaped keys stripped. The `OMITTED_RUNTIME_FIELDS`
set in `harness-workspace-release.ts` drops transient state and anything named
`password`, `apiKey` or `connectionUrl`, and every key starting with `_`.

### Run

One execution of one exposure. Identified by a UUID, carries its own access
token, has a status in `queued` / `running` / `completed` / `blocked` /
`failed` / `cancelled`, and emits a monotonic event sequence. `completed` and
`blocked` are the Agent's own verdicts via `complete`; `failed` and `cancelled`
are the Host's.

## HTTP surface

The complete local Host API. Everything is bound to `127.0.0.1`, and a
non-loopback `Host` header is rejected with 403 before routing.

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | none | Liveness plus whether a model is configured |
| `GET /api/public/harnesses/:slug/descriptor` | none | Title, summary, exposures |
| `GET /api/public/harnesses/:slug` | workspace | Descriptor plus release version |
| `GET /api/public/harnesses/:slug/workspace` | workspace | Persisted node snapshot and revision |
| `POST /api/public/harnesses/:slug/runs` | workspace | Create a Run; 202 with a per-run `accessToken` |
| `GET /api/public/runs/:id` | run | Run record |
| `POST /api/public/runs/:id/cancel` | run | Abort a non-terminal Run |
| `POST /api/public/runs/:id/interactions/:interaction/answer` | run | Answer a pending question |
| `GET /api/public/runs/:id/events` | run | SSE stream, resumable with `Last-Event-ID` |
| `POST /api/local/shutdown` | workspace | Graceful stop |

Two distinct credentials. The **workspace token** is a long-lived secret in the
state directory and authorizes the graph and run creation. A **run access
token** is minted per Run, returned once from the create call, and stored only
as a SHA-256 hash; losing it means losing access to that Run's stream, and the
Host cannot reissue it. A wrong run token returns 404, not 401, so run ids are
not enumerable.

AlphaLab does not expose these to the browser. `apps/api/routers/conexus.py`
proxies them, holds the tokens server-side, and forwards `Last-Event-ID`.

## State on disk

The state directory is `CONEXUS_LOCAL_ROOT`, default `.conexus/local`, created
mode `0o700`. Under AlphaLab it is `<runtime>/conexus/project`.

| Path | Contents |
| --- | --- |
| `state.json` | Everything durable: release, workspace graph, persisted nodes, run history. Mode `0o600`. |
| `host.lock` | The owning process's PID. One Host per directory. |
| `host-token` | Workspace token, when the bundled CLI generates it. AlphaLab passes `CONEXUS_LOCAL_TOKEN` instead and keeps its own `secrets/alphalab-connection.json`. |

`state.json` is written whole, to a temporary file, then renamed — so a crash
mid-write leaves the previous state intact rather than a truncated file. Writes
are serialized behind one promise chain. There is no migration path: an
unrecognized `schema` is refused with existing data left untouched.

Run history is capped at 500 entries and only terminal runs are evicted; each
Run's event list is capped at 1000 and evicts from the front. Back up the whole
directory with the service **stopped** — there is no online backup.

Run access tokens never appear in `state.json`, only their hashes. The
workspace token never appears at all;
`examples/conexus/minimal-agent/test/lifecycle.test.mjs` asserts both.

## Trust boundary

Three claims to state plainly, because each one is routinely misread:

**Tool execution is trusted local code, not a sandbox.** Tool nodes run as
child processes under the current OS account with `filesystem`, `network` and
`shell` permissions available. Timeouts and crash containment are reliability
features. A Harness you did not write can do anything your user account can do.

**Everything the Agent reads is untrusted data.** Node contents, tool output,
stdout and stderr, market data, search results and conversation history are
data, never instructions. The Agent prompts say so, and a Harness that treats
tool output as authority has a prompt-injection hole.

**Confirmation flags are the write boundary.** AlphaLab's Tool nodes keep
`confirm_write`, `confirm_delete` and `confirm_python_execution` as top-level
fields of the tool contract, never nested inside an `args` blob, so they cannot
be smuggled in by arguments copied from untrusted text. Conversation history
can resolve a reference but never authorizes a write, a deletion or an
execution. `examples/conexus/financial-integration/` demonstrates the gate and
asserts that an unauthorized run reaches no backend.

Credentials: the local Host has no cloud login and no model gateway. A model
endpoint must be HTTPS or loopback HTTP, must carry no credentials in its URL,
and a non-loopback endpoint must have an API key. Provider error bodies are
reduced to a status code before surfacing, because they can echo keys. AlphaLab
stores provider settings in `secrets/model-providers.json` outside source
control.

## Current constraints

Honest limits of the pinned snapshot, all verified rather than assumed:

- **One Run at a time per Host.** Runs are chained onto a single promise queue.
  A second Run is accepted and sits `queued`. The queue depth limit is 32
  non-terminal runs, after which creation returns 429.
- **One Host per state directory**, enforced by `host.lock` with a stale-PID
  probe. A second launcher fails rather than corrupting state.
- **Workspace commits are whole-graph.** A Run's changes commit once, at the
  end, replacing `state.workspace` and `state.nodes` and bumping one
  `revision`. This is why the queue cannot simply be relaxed.
- **Interrupted Runs are never replayed.** On restart, non-terminal Runs become
  `cancelled` with `host_restarted`.
- **Serial execution is the single-Agent configuration's property, not a
  framework limit.** A second Agent node already delegates successfully inside
  one Run. It is unexposed, its turns are invisible to clients, and a child
  Agent asking a question deadlocks the Run. See
  [`examples/conexus/two-agent/INTERFACE.md`](../examples/conexus/two-agent/INTERFACE.md).
- **No Canvas.** The local snapshot has no editor or renderer; graphs are
  authored as code.

## Where to go next

- [Execution lifecycle](11_CONEXUS_EXECUTION_LIFECYCLE.md) — one Run end to end,
  including disconnection and restart.
- [Extension and upgrade](12_CONEXUS_EXTENSION_GUIDE.md) — adding tools, models,
  search, skills and Agents; upgrading the pinned core.
- [`examples/conexus/`](../examples/conexus/) — runnable examples with test
  doubles, and the validation suite behind these documents.
- [Conexus Research Agent](04_CONEXUS_AGENT.md) — what AlphaLab's own Agent
  does with all of this.
