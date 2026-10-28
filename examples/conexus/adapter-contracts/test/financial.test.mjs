// The financial half of issue #11's first acceptance criterion: the Tool node
// AlphaLab's research Agent actually calls still satisfies the contracts, and
// the double a benchmark substitutes for it produces the same envelopes.
//
//   node --test 'examples/conexus/adapter-contracts/test/*.test.mjs'
//
// The example Tool node runs for real here — a node child process reaching the
// API stub over ALPHALAB_API_ORIGIN — so what is validated is a live envelope,
// not a fixture someone wrote to match the schema.
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { BUILD_HINT, runtimeIsBuilt } from '../../lib/runtime.mjs'
import { startResearchHost, SLUG } from '../../financial-integration/host.mjs'
import { startAlphaLabApiStub } from '../../financial-integration/doubles/api-stub.mjs'
import { EXPOSURE_ID, RESULT_NODE_ID, TOOL_NODE_ID } from '../../financial-integration/harness.mjs'
import { assertContract, checkContract } from '../../../../integrations/conexus/contracts/adapters.mjs'
import { contractCompletion, contractFinancialTool, toolTurn } from '../../../../integrations/conexus/contracts/doubles.mjs'

const skip = runtimeIsBuilt() ? false : BUILD_HINT

/** Builds one `use` turn, asserting the input against the request contract first. */
const invoke = (command, args = {}, extra = {}) => {
  const input = { command, args, ...extra }
  assertContract('financial.tool.request', input)
  return toolTurn('use', { node_id: TOOL_NODE_ID, capability: 'tool.invoke', input })
}

/** The tool's own payload, nested inside the invocation envelope. */
const lastTool = request => JSON.parse(request.messages.findLast(message => message.role === 'tool').content).output

async function waitForRun(host, started) {
  // Five real tool invocations in one run, at roughly 19 seconds per Windows
  // CI tool start. This matches the budget the financial example suite uses.
  const deadline = Date.now() + (process.platform === 'win32' ? 180000 : 25000)
  while (Date.now() < deadline) {
    const { run } = (await host.app.inject({
      url: `/api/public/runs/${started.run.id}`,
      headers: { authorization: `Bearer ${started.accessToken}` },
    })).json()
    if (['completed', 'blocked', 'failed', 'cancelled'].includes(run.status)) return run
    await delay(20)
  }
  throw new Error('The run did not reach a terminal state in time.')
}

test('every envelope the real financial Tool node returns conforms', { skip }, async () => {
  const stub = await startAlphaLabApiStub({ pollsBeforeDone: 2 })
  const envelopes = []
  const record = request => {
    const payload = lastTool(request)
    assertContract('financial.tool.response', payload)
    envelopes.push(payload)
    return payload
  }

  let jobId
  const complete = contractCompletion([
    () => invoke('data.status'),
    request => {
      assert.equal(record(request).success, true)
      // Refused: the confirmation flag is absent, so nothing executes.
      return invoke('backtest.run', { strategy_id: 'main' })
    },
    request => {
      const refused = record(request)
      assert.equal(refused.success, false)
      assert.match(refused.error, /confirm_python_execution/)
      return invoke('backtest.run', { strategy_id: 'main' }, { confirm_python_execution: true })
    },
    request => {
      const submitted = record(request)
      assert.equal(submitted.status, 'running')
      jobId = submitted.job_id
      return invoke('backtest.wait', { job_id: jobId })
    },
    request => {
      assert.equal(record(request).status, 'succeeded')
      return invoke('backtest.summary', { job_id: jobId })
    },
    request => {
      const summary = record(request).result
      return toolTurn('edit', { operations: [{
        kind: 'patch', node_id: RESULT_NODE_ID,
        set: { data: { version: 1, requestId: 'contract-1', findings: {
          runId: summary.run_id,
          totalReturn: summary.total_return,
          annualizedVolatility: summary.annualized_volatility,
          maxDrawdown: summary.max_drawdown,
          executionReliable: summary.execution_reliable,
        } } },
      }] })
    },
    () => toolTurn('complete', { status: 'done', summary: 'Backtest recorded under contract.' }),
  ])

  const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-financial-contract-'))
  let host
  try {
    host = await startResearchHost({ projectRoot, apiOrigin: stub.origin, complete })
    const started = await host.app.inject({
      method: 'POST',
      url: `/api/public/harnesses/${SLUG}/runs`,
      headers: { authorization: `Bearer ${host.token}` },
      payload: { exposureId: EXPOSURE_ID, input: { request: 'Run the main strategy backtest.' } },
    })
    assert.equal(started.statusCode, 202, started.body)
    const run = await waitForRun(host, started.json())
    assert.equal(run.status, 'completed', JSON.stringify(run))

    // Every command the harness exposes that this run touched, checked.
    assert.deepEqual(envelopes.map(envelope => envelope.command),
      ['data.status', 'backtest.run', 'backtest.run', 'backtest.wait', 'backtest.summary'])
    // A refusal is one bounded line with no backend detail in it.
    const refusal = envelopes.find(envelope => envelope.success === false)
    assert.ok(refusal.error.length <= 400 && !refusal.error.includes('\n'))
  } finally {
    await host?.close()
    await stub.close()
    await rm(projectRoot, { recursive: true, force: true })
  }
})

test('the financial double answers the real command surface identically in shape', { skip }, async () => {
  // Same commands, same contract, no backend: what a benchmark substitutes
  // when it wants the tool path without the AlphaLab API behind it.
  const double = contractFinancialTool({ steps: 2 })
  const started = await double({ command: 'backtest.run', confirm_python_execution: true })
  const envelopes = [
    await double({ command: 'data.status' }),
    await double({ command: 'backtest.run', args: { strategy_id: 'main' } }),
    started,
    await double({ command: 'backtest.wait', args: { job_id: started.job_id } }),
    await double({ command: 'backtest.summary', args: { job_id: started.job_id } }),
  ]
  for (const envelope of envelopes) assert.deepEqual(checkContract('financial.tool.response', envelope), [])
  assert.deepEqual(envelopes.map(envelope => envelope.success), [true, false, true, true, true])
})
