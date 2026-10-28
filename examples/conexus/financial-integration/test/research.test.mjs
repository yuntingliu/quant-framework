// Reproducible checks for the financial integration shape.
//
//   node --test 'examples/conexus/financial-integration/test/*.test.mjs'
//
// These cover what the minimal example cannot: an asynchronous job at the
// Agent boundary, the execution-confirmation gate, a bounded backend failure,
// and the atomic structured-output write.
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { runtimeIsBuilt, BUILD_HINT } from '../../lib/runtime.mjs'
import { startResearchHost, SLUG } from '../host.mjs'
import { startAlphaLabApiStub } from '../doubles/api-stub.mjs'
import { scriptedCompletion, toolCall } from '../../minimal-agent/doubles/scripted-model.mjs'
import { TOOL_NODE_ID, RESULT_NODE_ID, EXPOSURE_ID } from '../harness.mjs'

const skip = runtimeIsBuilt() ? false : BUILD_HINT

const invoke = (command, args = {}, extra = {}) =>
  toolCall('use', { node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { command, args, ...extra } })

const lastTool = (request) => JSON.parse(request.messages.findLast((message) => message.role === 'tool').content).output

async function withResearchHost({ complete, apiOrigin, stub }, body) {
  const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-financial-test-'))
  const host = await startResearchHost({ projectRoot, apiOrigin: apiOrigin ?? stub.origin, complete })
  try {
    return await body(host)
  } finally {
    await host.close()
    // Allow Windows to release handles from the tool child process after close.
    await rm(projectRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  }
}

async function startRun(host, request = 'Run the main strategy backtest.') {
  const response = await host.app.inject({
    method: 'POST',
    url: `/api/public/harnesses/${SLUG}/runs`,
    headers: { authorization: `Bearer ${host.token}` },
    payload: { exposureId: EXPOSURE_ID, input: { request } },
  })
  assert.equal(response.statusCode, 202, response.body)
  return response.json()
}

async function waitForRun(host, started, done = (run) => ['completed', 'blocked', 'failed', 'cancelled'].includes(run.status)) {
  // The full scenario starts four real tool processes. Windows CI can take
  // ~19 seconds per PowerShell start even when the tool itself returns promptly.
  const deadline = Date.now() + (process.platform === 'win32' ? 180000 : 25000)
  while (Date.now() < deadline) {
    const run = (await host.app.inject({
      url: `/api/public/runs/${started.run.id}`,
      headers: { authorization: `Bearer ${started.accessToken}` },
    })).json().run
    if (done(run)) return run
    await delay(20)
  }
  throw new Error('The run did not reach the expected state in time.')
}

function workspaceNode(host, nodeId) {
  return host.app.inject({
    url: `/api/public/harnesses/${SLUG}/workspace`, headers: { authorization: `Bearer ${host.token}` },
  }).then((response) => response.json().workspace.nodes.find((node) => node.id === nodeId))
}

test('an asynchronous backtest is submitted, polled, waited on, and then read', { skip }, async () => {
  const stub = await startAlphaLabApiStub({ pollsBeforeDone: 2 })
  let jobId
  const observed = { statuses: [] }
  const complete = scriptedCompletion([
    () => invoke('backtest.run', { strategy_id: 'main' }, { confirm_python_execution: true }),
    (request) => {
      const submitted = lastTool(request)
      assert.equal(submitted.success, true, JSON.stringify(submitted))
      // Submission returns immediately with a job id and no result.
      assert.equal(submitted.status, 'running')
      assert.equal(submitted.result, undefined)
      jobId = submitted.job_id
      return invoke('backtest.status', { job_id: jobId })
    },
    (request) => {
      const status = lastTool(request)
      observed.statuses.push(status.status)
      // A single status read never blocks; it reports the current state.
      assert.equal(status.status, 'running')
      return invoke('backtest.wait', { job_id: jobId })
    },
    (request) => {
      const waited = lastTool(request)
      observed.statuses.push(waited.status)
      assert.equal(waited.status, 'succeeded', JSON.stringify(waited))
      assert.equal(waited.timed_out, undefined)
      return invoke('backtest.summary', { job_id: jobId })
    },
    (request) => {
      const summary = lastTool(request).result
      assert.equal(summary.run_id, jobId)
      assert.equal(summary.total_return, stub.expectedSummary.total_return)
      return toolCall('edit', { operations: [{
        kind: 'patch',
        node_id: RESULT_NODE_ID,
        set: { data: { version: 1, requestId: 'test-1', findings: {
          runId: summary.run_id,
          totalReturn: summary.total_return,
          annualizedVolatility: summary.annualized_volatility,
          maxDrawdown: summary.max_drawdown,
          executionReliable: summary.execution_reliable,
        } } },
      }] })
    },
    () => toolCall('complete', { status: 'done', summary: 'Backtest recorded.' }),
  ])

  try {
    await withResearchHost({ complete, stub }, async (host) => {
      const run = await waitForRun(host, await startRun(host))
      assert.equal(run.status, 'completed', JSON.stringify(run))
      assert.deepEqual(observed.statuses, ['running', 'succeeded'])
      assert.deepEqual(run.nodeChanges.updated, [RESULT_NODE_ID])

      const result = await workspaceNode(host, RESULT_NODE_ID)
      assert.equal(result.values.data.findings.runId, jobId)
      assert.equal(result.values.data.findings.executionReliable, true)
    })
    // Exactly one backtest was submitted: the run is not retried on its own.
    assert.equal(stub.requests.filter((entry) => entry.method === 'POST' && entry.path === '/api/backtests').length, 1)
  } finally {
    await stub.close()
  }
})

test('execution without the confirmation flag is refused and submits no job', { skip }, async () => {
  const stub = await startAlphaLabApiStub()
  const complete = scriptedCompletion([
    () => invoke('backtest.run', { strategy_id: 'main' }),
    (request) => {
      const refused = lastTool(request)
      assert.equal(refused.success, false, JSON.stringify(refused))
      assert.match(refused.error, /confirm_python_execution=true/)
      return toolCall('complete', {
        status: 'blocked',
        summary: 'A backtest was not authorized.',
        blocker: 'Ask the user to authorize running the strategy before executing it.',
      })
    },
  ])

  try {
    await withResearchHost({ complete, stub }, async (host) => {
      assert.equal((await waitForRun(host, await startRun(host))).status, 'blocked')
      // The result node keeps its initial value; nothing was concluded.
      const result = await workspaceNode(host, RESULT_NODE_ID)
      assert.equal(result.values.data.findings, null)
    })
    // The gate is enforced inside the tool, so the backend never saw a request.
    assert.equal(stub.requests.some((entry) => entry.path === '/api/backtests'), false)
  } finally {
    await stub.close()
  }
})

test('an unreachable backend becomes a bounded tool error, not a crashed run', { skip }, async () => {
  const stub = await startAlphaLabApiStub()
  const unreachable = stub.origin
  await stub.close()

  const complete = scriptedCompletion([
    () => invoke('data.status'),
    (request) => {
      const failure = lastTool(request)
      assert.equal(failure.success, false, JSON.stringify(failure))
      assert.ok(failure.error.length > 0 && failure.error.length <= 300)
      // One line, no stack trace, no absolute paths.
      assert.equal(failure.error.includes('\n'), false)
      return toolCall('complete', {
        status: 'blocked', summary: 'The AlphaLab API was unreachable.', blocker: 'Start the AlphaLab backend and retry.',
      })
    },
  ])

  await withResearchHost({ complete, apiOrigin: unreachable }, async (host) => {
    const run = await waitForRun(host, await startRun(host))
    assert.equal(run.status, 'blocked', JSON.stringify(run))
  })
})

test('cancelling during a wait discards the structured output', { skip }, async () => {
  const stub = await startAlphaLabApiStub({ pollsBeforeDone: 2 })
  let jobId
  const complete = scriptedCompletion([
    () => invoke('backtest.run', { strategy_id: 'main' }, { confirm_python_execution: true }),
    (request) => {
      jobId = lastTool(request).job_id
      return toolCall('edit', { operations: [{
        kind: 'patch',
        node_id: RESULT_NODE_ID,
        set: { data: { version: 1, requestId: 'cancelled', findings: {
          runId: jobId, totalReturn: 0, executionReliable: false,
        } } },
      }] })
    },
    // Suspends the run so the test can cancel it deterministically.
    () => toolCall('request_user_input', { prompt: 'Record this partial result?' }),
    () => toolCall('complete', { status: 'done', summary: 'Never reached.' }),
  ])

  try {
    await withResearchHost({ complete, stub }, async (host) => {
      const started = await startRun(host)
      await waitForRun(host, started, (run) => Boolean(run.pendingInteraction))
      await host.app.inject({
        method: 'POST',
        url: `/api/public/runs/${started.run.id}/cancel`,
        headers: { authorization: `Bearer ${started.accessToken}` },
      })
      const run = await waitForRun(host, started)
      assert.equal(run.status, 'cancelled', JSON.stringify(run))

      // The edit succeeded inside the loop but the run never committed, so the
      // workspace shows no partial conclusion.
      const result = await workspaceNode(host, RESULT_NODE_ID)
      assert.equal(result.values.data.findings, null)
    })
    // Cancelling the Agent run does not cancel the backend job: the stub still
    // holds it. Reclaiming backend work is the application's responsibility,
    // not the Host's.
    assert.ok(jobId, 'the backtest was never submitted')
  } finally {
    await stub.close()
  }
})
