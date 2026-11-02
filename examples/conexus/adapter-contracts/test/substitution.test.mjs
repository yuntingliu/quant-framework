// Issue #11's first acceptance criterion, executable:
//
//   "Existing model, search, and financial tools remain compatible under the
//    new contracts. A replacement test adapter can execute the same minimal
//    task."
//
//   node --test examples/conexus/adapter-contracts/test/
//
// The same minimal task — summarize a numeric series through a Tool node and
// save a note — runs twice on the same Harness:
//
//   over HTTP   through the real createModelCompletion, against a loopback
//               OpenAI-compatible endpoint. Exercises the shipped adapter's
//               URL rules, credential rules and 5 MiB cap.
//   in process  through contractCompletion, the replacement test adapter.
//
// Both runs must produce the same observable outcome, and the substitute Host
// must publish an equivalent adapter profile. If they diverge, a benchmark
// built on the doubles is measuring its own harness.
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { BUILD_HINT, loadConexus, runtimeIsBuilt } from '../../lib/runtime.mjs'
import { startMinimalHost, SLUG } from '../../minimal-agent/host.mjs'
import { EXPOSURE_ID, TOOL_NODE_ID } from '../../minimal-agent/harness.mjs'
import { startModelEndpoint, toolCall } from '../../minimal-agent/doubles/scripted-model.mjs'
import { substituteAdapters, assertSubstitutable } from '../substitute.mjs'
import { CONTRACT_DOCUMENT, assertContract } from '../../../../integrations/conexus/contracts/adapters.mjs'
import { TEST_CREDENTIALS, toolTurn } from '../../../../integrations/conexus/contracts/doubles.mjs'
import { assertNoSecrets } from '../../../../integrations/conexus/contracts/redact.mjs'

const skip = runtimeIsBuilt() ? false : BUILD_HINT
const VALUES = [0.021, -0.004, 0.013, 0.008, -0.011]
const SOURCES = [{ url: 'https://example.com/momentum', title: 'Momentum', snippet: 'A lead, not a fact.' }]
const HOST_PROFILE = CONTRACT_DOCUMENT.contracts['runtime.adapter.profile'].examples.valid[0]

/**
 * The turn sequence of the minimal task, independent of adapter shape.
 *
 * `turn` is the only difference between the two stacks: the HTTP endpoint
 * needs OpenAI wire turns, the in-process adapter needs AgentLLMResult turns.
 * Everything the script reads — the tool result the Host fed back — is
 * identical, which is the compatibility claim being tested.
 */
function taskScript(turn) {
  return [
    () => turn('use', { node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { values: VALUES } }),
    request => {
      const stats = lastToolMessage(request)
      assert.equal(stats.success, true, JSON.stringify(stats))
      return turn('create', { nodes: [{
        type: 'note', label: 'Series summary', description: 'Adapter substitution output',
        content: `count=${stats.output.count} mean=${stats.output.mean}`,
      }] })
    },
    () => turn('complete', { status: 'done', summary: 'Summarized the series and saved a note.' }),
  ]
}

const lastToolMessage = request => JSON.parse(request.messages.findLast(message => message.role === 'tool').content)

async function waitForRun(host, started) {
  // Windows CI can spend ~19 seconds starting each real PowerShell tool
  // process. The task starts one, and it runs once per adapter stack, so keep
  // each run's budget well above a single start.
  const deadline = Date.now() + (process.platform === 'win32' ? 90000 : 20000)
  while (Date.now() < deadline) {
    const response = await host.app.inject({
      url: `/api/public/runs/${started.run.id}`,
      headers: { authorization: `Bearer ${started.accessToken}` },
    })
    const { run } = response.json()
    if (['completed', 'blocked', 'failed', 'cancelled'].includes(run.status)) return run
    await delay(20)
  }
  throw new Error('The run did not reach a terminal state in time.')
}

/** Runs the minimal task and returns the parts both adapter stacks must agree on. */
async function executeTask(host) {
  const created = await host.app.inject({
    method: 'POST',
    url: `/api/public/harnesses/${SLUG}/runs`,
    headers: { authorization: `Bearer ${host.token}` },
    payload: { exposureId: EXPOSURE_ID, input: { request: 'Summarize the sample return series.' } },
  })
  assert.equal(created.statusCode, 202, created.body)
  const run = await waitForRun(host, created.json())
  const workspace = (await host.app.inject({
    url: `/api/public/harnesses/${SLUG}/workspace`,
    headers: { authorization: `Bearer ${host.token}` },
  })).json().workspace
  const note = workspace.nodes.find(node => node.label === 'Series summary')
  return {
    status: run.status,
    summary: run.summary,
    workspaceRevision: run.workspaceRevision,
    created: run.nodeChanges.created.length,
    note: note?.values.content,
  }
}

test('the replacement test adapter executes the same minimal task', { skip }, async () => {
  const overHttp = await mkdtemp(join(tmpdir(), 'conexus-adapters-http-'))
  const inProcess = await mkdtemp(join(tmpdir(), 'conexus-adapters-double-'))
  let endpoint
  let real
  let substitute

  try {
    // The shipped adapter path: createModelCompletion against a loopback
    // OpenAI-compatible endpoint, with a key it must send and never echo.
    endpoint = await startModelEndpoint(taskScript(toolCall))
    real = await startMinimalHost({
      projectRoot: overHttp, model: endpoint.model, baseUrl: endpoint.baseUrl, apiKey: TEST_CREDENTIALS.modelApiKey,
    })
    const realOutcome = await executeTask(real)
    assert.equal(realOutcome.status, 'completed', JSON.stringify(realOutcome))

    // The replacement path: the same script, the same Harness, no endpoint.
    const adapters = substituteAdapters({ script: taskScript(toolTurn), sources: SOURCES })
    substitute = await startMinimalHost({ projectRoot: inProcess, ...adapters })
    const substituteOutcome = await executeTask(substitute)

    assert.deepEqual(substituteOutcome, realOutcome)
    assert.equal(substituteOutcome.note, `count=${VALUES.length} mean=0.0054`)

    // Each completion request the core sent conformed, and neither adapter
    // path put the configured key into a request the model double recorded.
    for (const request of adapters.complete.requests) {
      assertContract('model.completion.request', JSON.parse(JSON.stringify(request)))
    }
    assertNoSecrets(adapters.complete.requests, Object.values(TEST_CREDENTIALS), 'the substituted completion requests')
  } finally {
    await substitute?.close()
    await real?.close()
    await endpoint?.close()
    await rm(overHttp, { recursive: true, force: true })
    await rm(inProcess, { recursive: true, force: true })
  }
})

test('the substitute Host publishes an equivalent adapter profile', { skip }, async () => {
  const { createLocalRuntime } = await loadConexus()
  const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-adapters-profile-'))
  const runtime = createLocalRuntime({ projectRoot, capabilities: { 'web.search': () => ({ result: { success: true } }) } })
  try {
    assertContract('runtime.adapter.profile', runtime.adapter.profile)
    // toolRuntimes differ by platform — bash off Windows, powershell on it —
    // so equivalence is checked against the live profile's own platform set.
    const reference = { ...HOST_PROFILE, toolRuntimes: runtime.adapter.profile.toolRuntimes }
    assertSubstitutable(runtime.adapter.profile, reference)

    const narrowed = { ...runtime.adapter.profile, capabilityIds: [] }
    assert.throws(() => assertSubstitutable(narrowed, reference), /does not offer capabilityIds: web\.search/)
  } finally {
    runtime.close()
    await rm(projectRoot, { recursive: true, force: true })
  }
})

test('an unavailable capability answers in contract instead of throwing', { skip }, async () => {
  const { createLocalRuntime } = await loadConexus()
  const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-adapters-capability-'))
  const runtime = createLocalRuntime({ projectRoot, capabilities: {} })
  try {
    const response = await runtime.adapter.executeNodeCapability({ capability: 'web.search', input: { query: 'x' } })
    // A missing capability is a refusal the Agent can read, which is why a
    // benchmark can detect "this host cannot search" without a failed Run.
    assert.equal(response.result.success, false)
    assert.match(response.result.error, /Capability is unavailable/)
  } finally {
    runtime.close()
    await rm(projectRoot, { recursive: true, force: true })
  }
})

test('the Host refuses unauthorized access in contract and discloses no token', { skip }, async () => {
  const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-adapters-auth-'))
  let host
  try {
    host = await startMinimalHost({
      projectRoot,
      ...substituteAdapters({ script: taskScript(toolTurn), sources: SOURCES }),
      token: TEST_CREDENTIALS.workspaceToken,
    })
    const workspaceUrl = `/api/public/harnesses/${SLUG}/workspace`

    for (const headers of [{}, { authorization: 'Bearer wrong-token-000000000000' }, { authorization: 'Basic abc' }]) {
      const refused = await host.app.inject({ url: workspaceUrl, headers })
      assert.equal(refused.statusCode, 401, refused.body)
      assertContract('authorization.failure', refused.json())
      // The refusal says access was denied, never what was expected.
      assertNoSecrets(refused.body, Object.values(TEST_CREDENTIALS), 'the 401 body')
    }

    // The Host strips an optional `Bearer ` prefix rather than requiring it,
    // so a bare token authorizes too. Asserted rather than relied on: a client
    // should always send the scheme, and a core change here should be visible.
    for (const authorization of [`Bearer ${TEST_CREDENTIALS.workspaceToken}`, TEST_CREDENTIALS.workspaceToken]) {
      assert.equal((await host.app.inject({ url: workspaceUrl, headers: { authorization } })).statusCode, 200)
    }

    // A run token is scoped to one run, and a wrong one is indistinguishable
    // from an unknown run on purpose: 404, not 403.
    const unknownRun = await host.app.inject({
      url: '/api/public/runs/run-does-not-exist',
      headers: { authorization: `Bearer ${TEST_CREDENTIALS.workspaceToken}` },
    })
    assert.equal(unknownRun.statusCode, 404, unknownRun.body)
    assertContract('authorization.failure', unknownRun.json())

    // A non-loopback Host header is rejected before any route runs, so the
    // Host cannot be reached through a proxy that rewrites it.
    const remote = await host.app.inject({
      url: workspaceUrl,
      headers: { host: 'alphalab.example.com', authorization: `Bearer ${TEST_CREDENTIALS.workspaceToken}` },
    })
    assert.equal(remote.statusCode, 403, remote.body)
    assertContract('authorization.failure', remote.json())
    assert.match(remote.json().error.message, /loopback/)
  } finally {
    await host?.close()
    await rm(projectRoot, { recursive: true, force: true })
  }
})
