# Extending and upgrading Conexus

How to add a tool, a model, search, a skill or an Agent to a Conexus Harness,
and how to move the pinned core to a new revision without losing the guarantees
that pinning buys.

[Framework architecture](10_CONEXUS_FRAMEWORK.md) for the vocabulary;
[Execution lifecycle](11_CONEXUS_EXECUTION_LIFECYCLE.md) for what a Run does
with any of it.

## The rule that decides where your change goes

Conexus does not have extension points in the plugin sense. There are exactly
three places a change can land, and picking the wrong one is the usual mistake:

| You want to | Change | Needs a core change |
| --- | --- | --- |
| Give the Agent a new ability | Add a node to the graph | no |
| Give the host a new ability nodes can call | Pass a `capabilities` entry | no |
| Change what a Run *means* | Runtime Core | yes — reviewed re-import |

The first two are ordinary application work. The third is a core revision; see
[Upgrading the pinned core](#upgrading-the-pinned-core). If you find yourself
wanting to change the Agent's seven native tools, the completion contract, or
when a workspace commits, you are in the third row and should stop and say so
rather than working around it.

## Adding a Tool node

A Tool node is code plus a schema. Start from
[`examples/conexus/minimal-agent/harness.mjs`](../examples/conexus/minimal-agent/harness.mjs)
for a pure tool, or
[`examples/conexus/financial-integration/harness.mjs`](../examples/conexus/financial-integration/harness.mjs)
for one that calls a business API.

```js
{
  id: 'series-stats',
  type: 'tool',
  parentId: HARNESS_NODE_ID,
  data: {
    toolName: 'series_stats',
    description: 'Return count, mean, min, and max for a numeric series.',
    runtime: 'node',           // node | python | bash | powershell | shell
    code: TOOL_CODE,           // defines `async function run(input, context)`
    exposeAsTool: true,        // false removes the tool.invoke capability
    sideEffects: 'none',       // none | read | write | external
    timeoutMs: 15000,
    inputSchema: { /* JSON Schema, validated before the body runs */ },
    outputSchema: { type: 'object' },
  },
}
```

Things worth getting right the first time:

**`inputSchema` is a real gate, not documentation.** It is validated before the
child process starts, and a violation returns a path-level error the model can
correct. Constrain what you want the runtime to reject; leave unconstrained what
your tool should reject itself with a better message. The example Tool node does
both deliberately — `values` is constrained, `statistic` is not.

**Return a bounded error, never a traceback.** Backend errors can carry file
paths, identifiers and request details. Reduce them to one short line.
AlphaLab's `Project-Run` tool additionally strips absolute paths and any 40–64
character hex token, so digests cannot round-trip through the Agent.

**A returned failure is still a successful invocation.** See the failure-layer
table in [Execution lifecycle](11_CONEXUS_EXECUTION_LIFECYCLE.md#tool-calls).
Say in the tool's `description` what a failure looks like.

**Keep confirmation flags top-level.** If a command executes code or mutates
state, put the flag on the tool contract itself —
`confirm_python_execution`, `confirm_write`, `confirm_delete` — never inside an
`args` object. Nested flags can be smuggled in by arguments copied from
untrusted text.

**Configuration arrives through `toolEnvironment`.** Tool processes get a small
allowlist of OS variables (`PATH`, `HOME`, `TEMP`, and platform equivalents)
plus whatever the launcher passes:

```js
createLocalRuntime({ projectRoot, toolEnvironment: { ALPHALAB_API_ORIGIN: origin } })
```

Nothing else leaks in. Do not read ambient configuration inside tool code.

**One Tool node can carry many commands.** AlphaLab exposes 44 commands through
two nodes using a `command` + `args` contract, rather than 44 nodes, so the
model's tool list stays small. Dispatch validates required arguments per command
and returns a stable, correction-oriented error.

## Adding a host capability

Use this when the ability belongs to the *host* rather than to a graph node —
something needing credentials the graph must never see, or an OS resource. The
capability becomes a runtime-managed node the Agent discovers with `find`.

```js
createLocalRuntime({ projectRoot, capabilities: { 'web.search': search } })
```

The function receives `{ runId, nodeId, capability, input, ownerNodeId,
workspace, signal }` and returns `{ result }`. An unknown capability id answers
`Capability is unavailable on this host.` rather than throwing.

`web.search` is AlphaLab's only one, in
[`integrations/conexus/web-search.mjs`](../integrations/conexus/web-search.mjs).
It is worth reading as a model: credentials are resolved from local settings and
**never passed through tool inputs**, the query is length-bounded, the response
is capped at 2 MiB, results are URL-validated, and a 45-second timeout is
combined with the run signal. Search snippets are returned as leads, not facts.

## Adding or changing a model

A model is configuration, not code. Nothing in the graph needs to change: the
Agent node's `model` field and the host's configured model must match, and
`createModelCompletion` refuses a request for any other model with
`model_not_allowed`.

For the bundled CLI, set `CONEXUS_MODEL_BASE_URL` (ending in `/v1`),
`CONEXUS_MODEL_ID` and optionally `CONEXUS_MODEL_API_KEY`.

AlphaLab instead stores profiles in `secrets/model-providers.json` and switches
between them through `PUT /api/model-providers`; see
[Local deployment](07_LOCAL_DEPLOYMENT.md#configure-model-providers). Two
behaviours of that layer matter:

- **Provider selection is frozen per Run.** `configuredCompletion` caches the
  resolved client against the Run's `AbortSignal`, so switching providers
  affects new Runs and never swaps the model under a Run in flight.
- **Strict-schema providers are accommodated.** Tool schemas with conditionally
  optional fields — `complete.blocker` applies only when blocked — are sent
  with `strict: false` so a provider routing Chat Completions through a strict
  Responses API does not reject them.

Any OpenAI-compatible endpoint works, including a loopback model server, which
is what makes credential-free local development possible. The examples use a
scripted endpoint rather than a model; see
[`examples/conexus/minimal-agent/doubles/scripted-model.mjs`](../examples/conexus/minimal-agent/doubles/scripted-model.mjs).

## Adding a skill

A skill is prompt text, not a node. The graph builder substitutes it into the
Agent's `systemPrompt` at compile time:

```js
for (const placeholder of ['{{ALPHALAB_SDK_SKILL}}', '{{ALPHALAB_WORKSPACE_COMMAND_SCHEMA}}']) {
  if (!agent.systemPrompt.includes(placeholder)) throw new Error(`Missing placeholder: ${placeholder}`)
}
```

The missing-placeholder check matters: a silent substitution failure ships an
Agent whose prompt is missing its whole command contract.
`tests/api/test_local_agent_integration.py` asserts no placeholder survives into
a live prompt and that known command names appear in it.

Deliberately, a skill is **not** a project file or a workspace node. Keeping it
out of the graph avoids a persistence dependency between Conexus state and
AlphaLab project state, so a skill revision ships with the code instead of
needing a data migration. Skills live under
[`integrations/conexus/alphalab-research-agent/skills/`](../integrations/conexus/alphalab-research-agent/skills/).

## Adding an Agent

Adding a second Agent node is supported by Runtime Core — and is not finished
work. `agent.run`, `run.wait` and `run.cancel` already function on the bundled
Host, but child turns are invisible to clients and a child Agent asking the user
a question deadlocks the Run.

Read [`examples/conexus/two-agent/INTERFACE.md`](../examples/conexus/two-agent/INTERFACE.md)
before starting. It records what works, what does not, the contract an
implementation must satisfy, and one verified defect with its fix.

## Compatibility checks

`inspectHostedRelease(release, undefined, adapter.profile)` validates a Release
against the host's actual abilities and returns `{ release, checksum,
compatibility }`. `createLocalHost` refuses to start when
`compatibility.deployable` is false, and the thrown error is the serialized
issue list.

The adapter profile is what it checks against:

```js
{
  name: 'conexus.local-node-host.v1',
  nodeTypes: ['tool', 'cli', 'files'],
  toolRuntimes: ['node', 'python', 'bash', 'shell'],   // powershell on Windows
  permissions: ['filesystem', 'network', 'shell'],
  isolation: ['shell'],
  capabilityIds: Object.keys(capabilities),
  runNodeTypes: ['tool'],
}
```

So a Harness requiring a `browser` node, a `python` tool on a host without
Python, or a capability the launcher did not supply fails at startup with a
named issue rather than mid-Run. Note `toolRuntimes` differs by platform: a
`bash` tool node will not deploy on Windows.

Verify a graph change without a model:

```bash
node --test 'examples/conexus/minimal-agent/test/*.test.mjs'
```

## Upgrading the pinned core

`vendor/conexus` is a **checksum-verified snapshot**, not a checkout. Every file
is digested in [`vendor/conexus/EXPORT.json`](../vendor/conexus/EXPORT.json) and
the revision is recorded in
[`integrations/conexus/runtime.lock.json`](../integrations/conexus/runtime.lock.json).
`scripts/build_conexus_runtime.py` re-verifies every digest and refuses to build
if one differs or if the lock and export disagree.

**Editing a file under `vendor/conexus` therefore breaks the build.** That is
the point: a core change must arrive as a reviewed export, so the snapshot
always corresponds to a known upstream revision.

### The sequence

```bash
python -m scripts.sync_conexus_core --export-root /path/to/conexus-export
python scripts/build_conexus_runtime.py
python scripts/check_conexus_runtime.py
python scripts/check_conexus_docs.py
node --test 'examples/conexus/minimal-agent/test/*.test.mjs'
node --test 'examples/conexus/financial-integration/test/*.test.mjs'
python -m pytest tests/api/test_local_agent_integration.py tests/contracts/test_conexus_distribution.py -q
```

What each step guarantees:

1. **`sync_conexus_core`** accepts only a clean export: `schema` must be
   `conexus.core-export.v1`, `sourceDirty` must be false, the package boundary
   must equal the four reviewed workspaces, and every file must be inside
   `src/`, `test/`, a package manifest, or the small root allowlist — with a
   matching SHA-256. Files dropped by the new export are removed. It rewrites
   `runtime.lock.json`. It never copies a private checkout.
2. **`build_conexus_runtime`** re-verifies digests, copies the snapshot into
   `build/conexus-source`, runs the boundary checker, then `npm ci` and
   `npm run build`, and assembles `build/conexus` with `*.test.*`, source maps
   and declarations excluded. The vendor tree stays read-only.
3. **`check_conexus_runtime`** runs the upstream test suites from the build —
   `runtime-core`, `node-host-runtime` and the local Host's own tests — and the
   boundary checker again.
4. **`check_conexus_docs`** re-resolves every path, symbol, command and
   complete-set claim in these documents against the new snapshot. A renamed
   export fails here instead of quietly invalidating the prose.
5. **The example suites** re-assert the lifecycle semantics. A core change that
   alters when a Run commits, how a tool failure is shaped, or what a restart
   does to an interrupted Run shows up as a failing named test.

Then update the quoted revision in these documents — `check_conexus_docs.py`
compares short revisions quoted in prose against `runtime.lock.json` and fails
on drift.

### Release status

The snapshot's packages are `private: true` and
`licenseStatus` is `pending-owner-selection`. It is a buildable review candidate,
not a published open-source release, and the export script neither selects a
license nor publishes. Do not add a public registry dependency on these
packages, and do not describe them as open source in user-facing material.
[`THIRD_PARTY.md`](../THIRD_PARTY.md) records the current attribution.

## Changing these documents

They are validated, so a change can fail CI:

```bash
python scripts/check_conexus_docs.py
```

It checks that every repository-relative link resolves, every symbol attributed
to a core file exists in it, every documented command target exists, the native
Agent tool list and the local Host's route list still match the code, quoted
core revisions match the lock file, and
[`examples/conexus/two-agent/`](../examples/conexus/two-agent/) stays
documentation-only.

That last check is this issue's scope boundary made mechanical: shipping a
working two-Agent example requires deleting the assertion, which is a decision
someone has to make on purpose.
