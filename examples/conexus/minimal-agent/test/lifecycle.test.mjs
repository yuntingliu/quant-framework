// Reproducible checks for the six lifecycle situations issue #6 requires:
// successful execution, tool failure, user input, cancellation, browser
// reconnection, and Host restart.
//
//   node --test examples/conexus/minimal-agent/test/
//
// Each test is the executable form of a section in
// docs/11_CONEXUS_EXECUTION_LIFECYCLE.md. If the runtime changes a semantic
// the document describes, the matching test fails rather than the document
// quietly going stale.
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { runtimeIsBuilt, BUILD_HINT } from '../../lib/runtime.mjs'
import { startMinimalHost, SLUG } from '../host.mjs'
import { scriptedCompletion, toolCall } from '../doubles/scripted-model.mjs'
import { TOOL_NODE_ID, EXPOSURE_ID } from '../harness.mjs'

const skip = runtimeIsBuilt() ? false : BUILD_HINT

async function withHost(options, body) {
  const projectRoot = options.projectRoot ?? await mkdtemp(join(tmpdir(), 'conexus-example-'))
  const host = await startMinimalHost({ ...options, projectRoot })
  try {
    return await body(host, projectRoot)
  } finally {
    await host.close()
    // Windows can retain a child process's directory handle briefly after close.
    if (!options.projectRoot) await rm(projectRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  }
}

function workspaceHeaders(host) {
  return { authorization: `Bearer ${host.token}` }
}

async function startRun(host, request = 'Summarize the sample series.') {
  const response = await host.app.inject({
    method: 'POST',
    url: `/api/public/harnesses/${SLUG}/runs`,
    headers: workspaceHeaders(host),
    payload: { exposureId: EXPOSURE_ID, input: { request } },
  })
  assert.equal(response.statusCode, 202, response.body)
  return response.json()
}

async function waitForRun(host, started, done = (run) => ['completed', 'blocked', 'failed', 'cancelled'].includes(run.status)) {
  // Windows CI can spend ~19 seconds starting each real PowerShell tool process.
  // Recovery cases invoke two tools; keep their overall budget above both starts.
  const deadline = Date.now() + (process.platform === 'win32' ? 90000 : 20000)
  while (Date.now() < deadline) {
    const response = await host.app.inject({
      url: `/api/public/runs/${started.run.id}`,
      headers: { authorization: `Bearer ${started.accessToken}` },
    })
    const run = response.json().run
    if (done(run)) return run
    await delay(20)
  }
  throw new Error('The run did not reach the expected state in time.')
}

test('successful execution invokes a Tool node and commits its workspace change', { skip }, async () => {
  const complete = scriptedCompletion([
    () => toolCall('use', { node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { values: [1, 2, 3, 4] } }),
    (request) => {
      const observed = JSON.parse(request.messages.findLast((message) => message.role === 'tool').content)
      assert.equal(observed.success, true, JSON.stringify(observed))
      // The tool really executed in a child process; these are its numbers.
      assert.deepEqual(
        { count: observed.output.count, mean: observed.output.mean, min: observed.output.min, max: observed.output.max },
        { count: 4, mean: 2.5, min: 1, max: 4 },
      )
      return toolCall('create', { nodes: [{
        type: 'note', label: 'Series summary', description: 'Minimal example output',
        content: `mean=${observed.output.mean}`,
      }] })
    },
    () => toolCall('complete', { status: 'done', summary: 'Series summarized.' }),
  ])

  await withHost({ complete }, async (host) => {
    const run = await waitForRun(host, await startRun(host))
    assert.equal(run.status, 'completed', JSON.stringify(run))
    assert.equal(run.summary, 'Series summarized.')
    assert.equal(run.nodeChanges.created.length, 1)
    // Workspace changes commit once, at the end of the run, not per tool call.
    assert.equal(run.workspaceRevision, 1)

    const workspace = (await host.app.inject({
      url: `/api/public/harnesses/${SLUG}/workspace`, headers: workspaceHeaders(host),
    })).json().workspace
    const note = workspace.nodes.find((node) => node.label === 'Series summary')
    assert.equal(note.values.content, 'mean=2.5')
    assert.equal(note.updatedByRunId, run.id)
  })
})

test('a schema violation is rejected before the Tool body runs', { skip }, async () => {
  const complete = scriptedCompletion([
    // values must be a non-empty array of numbers, per the Tool node inputSchema.
    () => toolCall('use', { node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { values: [] } }),
    (request) => {
      const observed = JSON.parse(request.messages.findLast((message) => message.role === 'tool').content)
      assert.equal(observed.success, false, JSON.stringify(observed))
      assert.equal(observed.error, 'Invalid input for tool.invoke.')
      // The rejection names the offending path, so the Agent can correct it.
      assert.equal(observed.details[0].path, '/values')
      assert.equal(observed.details[0].keyword, 'minItems')
      return toolCall('use', { node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { values: [2, 4] } })
    },
    (request) => {
      const observed = JSON.parse(request.messages.findLast((message) => message.role === 'tool').content)
      assert.equal(observed.success, true, JSON.stringify(observed))
      return toolCall('complete', { status: 'done', summary: 'Recovered from an invalid tool call.' })
    },
  ])

  await withHost({ complete }, async (host) => {
    const run = await waitForRun(host, await startRun(host))
    // A rejected tool call is a turn in the transcript, not a run outcome.
    assert.equal(run.status, 'completed', JSON.stringify(run))
    assert.equal(run.error, undefined)
  })
})

test('a Tool body failure reaches the Agent as bounded, correctable data', { skip }, async () => {
  const complete = scriptedCompletion([
    // `statistic` passes the schema, so the child process runs and rejects it.
    () => toolCall('use', {
      node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { values: [1, 2, 3], statistic: 'sharpe' },
    }),
    (request) => {
      const observed = JSON.parse(request.messages.findLast((message) => message.role === 'tool').content)
      // The INVOCATION succeeded, so the envelope says success. The tool's own
      // verdict is nested in `output`. An Agent that reads only the envelope
      // will mistake a rejected request for a result.
      assert.equal(observed.success, true, JSON.stringify(observed))
      assert.equal(observed.output.success, false, JSON.stringify(observed))
      assert.match(observed.output.error, /Unsupported statistic/)
      return toolCall('use', {
        node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { values: [1, 2, 3], statistic: 'mean' },
      })
    },
    (request) => {
      const observed = JSON.parse(request.messages.findLast((message) => message.role === 'tool').content)
      assert.equal(observed.output.success, true, JSON.stringify(observed))
      assert.equal(observed.output.mean, 2)
      return toolCall('complete', { status: 'done', summary: 'Recovered from a rejected statistic.' })
    },
  ])

  await withHost({ complete }, async (host) => {
    assert.equal((await waitForRun(host, await startRun(host))).status, 'completed')
  })
})

test('a crashed Tool process fails the invocation itself', { skip }, async () => {
  const complete = scriptedCompletion([
    () => toolCall('use', {
      node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { values: [1], statistic: 'raise' },
    }),
    (request) => {
      const observed = JSON.parse(request.messages.findLast((message) => message.role === 'tool').content)
      // An unhandled throw exits the child non-zero, so the envelope itself
      // reports failure — unlike a tool that returns its own error payload.
      assert.equal(observed.success, false, JSON.stringify(observed))
      return toolCall('complete', {
        status: 'blocked', summary: 'The statistics tool crashed.', blocker: 'series-stats exited non-zero.',
      })
    },
  ])

  await withHost({ complete }, async (host) => {
    const run = await waitForRun(host, await startRun(host))
    // A crashed tool is still not a crashed run: the Agent decides the outcome.
    assert.equal(run.status, 'blocked', JSON.stringify(run))
  })
})

test('an unknown node or capability fails the call, not the run', { skip }, async () => {
  const complete = scriptedCompletion([
    () => toolCall('use', { node_id: 'no-such-node', capability: 'tool.invoke', input: { values: [1] } }),
    (request) => {
      const observed = JSON.parse(request.messages.findLast((message) => message.role === 'tool').content)
      assert.equal(observed.success, false, JSON.stringify(observed))
      assert.match(observed.error, /does not declare capability/)
      return toolCall('complete', {
        status: 'blocked', summary: 'The statistics node was unreachable.', blocker: 'no-such-node is not in the graph.',
      })
    },
  ])

  await withHost({ complete }, async (host) => {
    assert.equal((await waitForRun(host, await startRun(host))).status, 'blocked')
  })
})

test('a blocked run reports its blocker instead of inventing an answer', { skip }, async () => {
  const complete = scriptedCompletion([
    () => toolCall('complete', {
      status: 'blocked', summary: 'No series was supplied.', blocker: 'The request contained no numeric series.',
    }),
  ])
  await withHost({ complete }, async (host) => {
    const run = await waitForRun(host, await startRun(host, 'Summarize something.'))
    assert.equal(run.status, 'blocked', JSON.stringify(run))
    assert.equal(run.summary, 'No series was supplied.')
  })
})

test('user input suspends the run until an answer arrives', { skip }, async () => {
  const complete = scriptedCompletion([
    () => toolCall('request_user_input', { prompt: 'Include the partial final period?', choices: ['Yes', 'No'] }),
    (request) => {
      const answer = JSON.parse(request.messages.findLast((message) => message.role === 'tool').content)
      assert.match(JSON.stringify(answer), /No/)
      return toolCall('complete', { status: 'done', summary: 'Excluded the partial period.' })
    },
  ])

  await withHost({ complete }, async (host) => {
    const started = await startRun(host)
    const waiting = await waitForRun(host, started, (run) => Boolean(run.pendingInteraction))
    assert.equal(waiting.status, 'running')
    assert.deepEqual(waiting.pendingInteraction.choices, ['Yes', 'No'])

    const answered = await host.app.inject({
      method: 'POST',
      url: `/api/public/runs/${started.run.id}/interactions/${waiting.pendingInteraction.id}/answer`,
      headers: { authorization: `Bearer ${started.accessToken}` },
      payload: { answer: 'No' },
    })
    assert.equal(answered.statusCode, 200, answered.body)

    // The same interaction cannot be answered twice.
    const replayed = await host.app.inject({
      method: 'POST',
      url: `/api/public/runs/${started.run.id}/interactions/${waiting.pendingInteraction.id}/answer`,
      headers: { authorization: `Bearer ${started.accessToken}` },
      payload: { answer: 'Yes' },
    })
    assert.equal(replayed.statusCode, 409, replayed.body)

    assert.equal((await waitForRun(host, started)).status, 'completed')
  })
})

test('cancellation ends a waiting run and discards its workspace changes', { skip }, async () => {
  const complete = scriptedCompletion([
    () => toolCall('create', { nodes: [{
      type: 'note', label: 'Draft', description: 'Written before cancellation', content: 'draft',
    }] }),
    () => toolCall('request_user_input', { prompt: 'Continue?' }),
    () => toolCall('complete', { status: 'done', summary: 'Never reached.' }),
  ])

  await withHost({ complete }, async (host) => {
    const started = await startRun(host)
    await waitForRun(host, started, (run) => Boolean(run.pendingInteraction))

    const cancelled = await host.app.inject({
      method: 'POST',
      url: `/api/public/runs/${started.run.id}/cancel`,
      headers: { authorization: `Bearer ${started.accessToken}` },
    })
    assert.equal(cancelled.statusCode, 200, cancelled.body)

    const run = await waitForRun(host, started)
    assert.equal(run.status, 'cancelled', JSON.stringify(run))
    assert.equal(run.error.code, 'run_cancelled')

    // A cancelled run commits nothing, even though `create` already succeeded
    // inside the loop. Commit happens only on completed or blocked.
    const workspace = (await host.app.inject({
      url: `/api/public/harnesses/${SLUG}/workspace`, headers: workspaceHeaders(host),
    })).json().workspace
    assert.equal(workspace.nodes.some((node) => node.label === 'Draft'), false)
    assert.equal(workspace.revision, 0)
  })
})

test('a disconnected browser resumes the event stream with Last-Event-ID', { skip }, async () => {
  let release
  const gate = new Promise((resolve) => { release = resolve })
  const complete = scriptedCompletion([
    () => toolCall('use', { node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { values: [1, 2, 3] } }),
    async () => {
      await gate
      return toolCall('complete', { status: 'done', summary: 'Finished after the client reconnected.' })
    },
  ])

  await withHost({ complete }, async (host) => {
    await host.app.listen({ host: '127.0.0.1', port: 0 })
    const origin = host.app.listeningOrigin
    const created = await fetch(`${origin}/api/public/harnesses/${SLUG}/runs`, {
      method: 'POST',
      headers: { ...workspaceHeaders(host), 'content-type': 'application/json' },
      body: JSON.stringify({ exposureId: EXPOSURE_ID, input: { request: 'Summarize.' } }),
    })
    const { run, accessToken } = await created.json()
    const auth = { authorization: `Bearer ${accessToken}` }

    // First connection: read a few events, then drop the socket mid-run, the
    // way a closed browser tab does.
    const first = new AbortController()
    const firstStream = await fetch(`${origin}/api/public/runs/${run.id}/events`, { headers: auth, signal: first.signal })
    let lastSequence = 0
    const seen = []
    try {
      for await (const chunk of firstStream.body) {
        for (const line of Buffer.from(chunk).toString('utf8').split('\n')) {
          if (!line.startsWith('data: ')) continue
          const event = JSON.parse(line.slice(6))
          lastSequence = event.sequence
          seen.push(event.type)
        }
        if (seen.includes('run.started')) break
      }
    } finally { first.abort() }
    assert.ok(lastSequence >= 2, `expected to observe events, saw ${JSON.stringify(seen)}`)

    release()

    // Reconnect with Last-Event-ID. The Host replays only what came after,
    // and ends the stream once the run is terminal.
    const resumed = await fetch(`${origin}/api/public/runs/${run.id}/events`, {
      headers: { ...auth, accept: 'text/event-stream', 'last-event-id': String(lastSequence) },
    })
    const replayed = []
    for await (const chunk of resumed.body) {
      for (const line of Buffer.from(chunk).toString('utf8').split('\n')) {
        if (!line.startsWith('data: ')) continue
        replayed.push(JSON.parse(line.slice(6)))
      }
    }
    assert.ok(replayed.length > 0, 'the resumed stream delivered no events')
    assert.ok(replayed.every((event) => event.sequence > lastSequence),
      `resumed stream repeated delivered events: ${JSON.stringify(replayed.map((event) => event.sequence))}`)
    assert.equal(replayed.at(-1).type, 'run.completed')

    // The run itself was never affected by the dropped connection.
    const finished = await (await fetch(`${origin}/api/public/runs/${run.id}`, { headers: auth })).json()
    assert.equal(finished.run.status, 'completed')
  })
})

test('Host restart cancels interrupted runs, replays no tools, and keeps the workspace', { skip }, async () => {
  const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-example-restart-'))
  try {
    const complete = scriptedCompletion([
      () => toolCall('create', { nodes: [{
        type: 'note', label: 'Committed', description: 'Survives restart', content: 'durable',
      }] }),
      () => toolCall('complete', { status: 'done', summary: 'Saved.' }),
    ])
    await withHost({ complete, projectRoot }, async (host) => {
      assert.equal((await waitForRun(host, await startRun(host))).status, 'completed')
    })

    // Simulate a Host killed mid-run: a non-terminal run left in state.json.
    const statePath = join(projectRoot, 'state.json')
    const state = JSON.parse(await readFile(statePath, 'utf8'))
    state.runs.push({ id: 'interrupted', status: 'running', sequence: 0, events: [] })
    await writeFile(statePath, JSON.stringify(state))

    await withHost({
      projectRoot,
      complete: () => { throw new Error('A restarted Host must never replay an interrupted run.') },
    }, async (host) => {
      const recovered = JSON.parse(await readFile(statePath, 'utf8'))
      const interrupted = recovered.runs.find((run) => run.id === 'interrupted')
      assert.equal(interrupted.status, 'cancelled')
      assert.equal(interrupted.error.code, 'host_restarted')

      const workspace = (await host.app.inject({
        url: `/api/public/harnesses/${SLUG}/workspace`, headers: workspaceHeaders(host),
      })).json().workspace
      assert.equal(workspace.nodes.find((node) => node.label === 'Committed').values.content, 'durable')
    })
  } finally {
    await rm(projectRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  }
})

test('one data directory admits one Host, and the workspace token is required', { skip }, async () => {
  await withHost({ complete: async () => toolCall('complete', { status: 'done', summary: 'n/a' }) }, async (host, projectRoot) => {
    assert.equal((await host.app.inject(`/api/public/harnesses/${SLUG}/workspace`)).statusCode, 401)
    await assert.rejects(
      startMinimalHost({ projectRoot, complete: async () => ({}) }),
      /already using this data directory/,
    )
    // state.json never contains the workspace token, only its hash per run.
    const state = await readFile(join(projectRoot, 'state.json'), 'utf8')
    assert.equal(state.includes(host.token), false)
  })
})

test('the local Host serializes top-level runs sharing one workspace', { skip }, async () => {
  // Issue #6 requires the serial-execution constraint to be documented. This
  // is its executable form: two runs accepted back to back never overlap,
  // because apps/local-host/src/index.mjs chains them onto one promise queue.
  let active = 0
  let maxConcurrent = 0
  const release = []
  const complete = async () => {
    active += 1
    maxConcurrent = Math.max(maxConcurrent, active)
    await new Promise((resolve) => release.push(resolve))
    active -= 1
    return toolCall('complete', { status: 'done', summary: 'Done.' })
  }

  await withHost({ complete }, async (host) => {
    const first = await startRun(host, 'first')
    const second = await startRun(host, 'second')

    // Both runs are accepted immediately; only one is ever running.
    const queued = await host.app.inject({
      url: `/api/public/runs/${second.run.id}`, headers: { authorization: `Bearer ${second.accessToken}` },
    })
    assert.equal(queued.json().run.status, 'queued')

    const deadline = Date.now() + 5000
    while (release.length === 0 && Date.now() < deadline) await delay(10)
    release.shift()()
    assert.equal((await waitForRun(host, first)).status, 'completed')

    while (release.length === 0 && Date.now() < deadline) await delay(10)
    release.shift()()
    assert.equal((await waitForRun(host, second)).status, 'completed')

    assert.equal(maxConcurrent, 1, 'two top-level runs executed concurrently')
  })
})
