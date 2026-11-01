// Conformance tests for the financial Tool nodes AlphaLab actually ships.
//
//   npm --prefix apps/desktop test
//
// These replaced an earlier suite that drove the simplified tool in
// examples/conexus/financial-integration. That tool returns a
// `{ success, command }` envelope the shipped tool never produces, so checking
// it against a schema derived from it demonstrated nothing about production —
// and a benchmark built on that premise could reward calls the application
// would reject.
//
// Here the shipped `code` runs for real with only its HTTP transport replaced,
// and every declared requirement in `financialTool.reducedCommandSurface` is
// verified twice: once against the production tool and once against the double
// a benchmark substitutes for it. The two must refuse the same things, in the
// same words, keyed by the same identifiers.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { CONTRACT_DOCUMENT, assertContract, checkContract } from '../integrations/conexus/contracts/adapters.mjs'
import { contractFinancialTool } from '../integrations/conexus/contracts/doubles.mjs'
import { SHIPPED_TOOLS, instantiateShippedTool, readShippedTool, shippedCommands } from '../integrations/conexus/contracts/shipped-tool.mjs'

const FINANCIAL = CONTRACT_DOCUMENT.financialTool
const SURFACE = FINANCIAL.reducedCommandSurface.commands
const PASSTHROUGH = FINANCIAL.transportPassthroughCommands

// One representative backend response per endpoint the reduced surface uses.
// Deliberately minimal: what is under test is the tool's own behaviour, not the
// backend's numbers.
const BACKEND = {
  job: { id: 'job-1', status: 'queued', request: { strategy_id: 'main' } },
  finished: { id: 'job-1', status: 'succeeded', backtest_id: 'bt-1', metrics: { sharpe: 0.77 } },
  summary: { backtest_id: 'bt-1', status: 'succeeded', metrics: { sharpe: 0.77 } },
  catalog: { result: { status: 'ready', datasets: [{ id: 'rq.bars', rows: 690 }] } },
}

function routes({ method, path }) {
  if (path === '/api/backtests/jobs' && method === 'POST') return [200, BACKEND.job]
  if (path.includes('/api/backtests/jobs/') && path.includes('/wait')) return [200, BACKEND.finished]
  if (/^\/api\/backtests\/jobs\/[^/]+$/.test(path)) return [200, BACKEND.finished]
  if (path.endsWith('/summary')) return [200, BACKEND.summary]
  if (path.startsWith('/api/agent/data-tools/')) return [200, BACKEND.catalog]
  return [404, { detail: { code: 'NOT_FOUND', message: 'Unknown route.' } }]
}

/** A fully-specified call for each command in the reduced surface. */
const COMPLETE_ARGS = {
  'data.status': {},
  'backtest.run': { project_id: 'p1', start_date: '2024-01-01', end_date: '2024-12-31' },
  'backtest.status': { job_id: 'job-1' },
  'backtest.wait': { job_id: 'job-1' },
  'backtest.summary': { backtest_id: 'bt-1' },
}

function production() {
  return instantiateShippedTool(SHIPPED_TOOLS.projectRun, routes)
}

function invocation(command, args, { confirm = SURFACE[command].confirm } = {}) {
  return { command, ...(Object.keys(args).length ? { args } : {}), ...(confirm ? { [confirm]: true } : {}) }
}

test('the declared reduced surface is a real subset of the shipped commands', () => {
  const declared = shippedCommands(SHIPPED_TOOLS.projectRun)
  for (const command of Object.keys(SURFACE)) {
    assert.ok(declared.includes(command), `${command} is not a shipped project-run command`)
  }
  for (const command of PASSTHROUGH) {
    assert.ok(declared.includes(command), `${command} is not a shipped project-run command`)
  }
  // The document calls the surface five of forty-four; keep that honest.
  const total = declared.length + shippedCommands(SHIPPED_TOOLS.projectFiles).length
  assert.equal(total, 44)
  assert.equal(Object.keys(SURFACE).length, 5)
})

test('the shipped tool answers every complete call in its declared contract', async () => {
  const { run, calls } = production()
  for (const [command, args] of Object.entries(COMPLETE_ARGS)) {
    const output = await run(invocation(command, args))
    const expected = SURFACE[command].contract
    if (expected === 'transport-passthrough') {
      // The tool returns the backend payload unchanged, so nothing structural
      // may be assumed. That the document says so is the contract.
      assert.ok(PASSTHROUGH.includes(command), command)
      assert.equal(typeof output, 'object')
      assert.equal(output.status, 'succeeded')
    } else {
      assert.deepEqual(checkContract(expected, output), [], `${command} → ${JSON.stringify(output)}`)
    }
    // A complete call reaches the backend; the refusal tests below assert the
    // opposite, which is what distinguishes a gate from a failed request.
    assert.ok(calls.length > 0)
  }
})

test('the shipped tool refuses a missing required argument without calling the backend', async () => {
  for (const [command, declared] of Object.entries(SURFACE)) {
    for (const missing of declared.requiredArgs) {
      const { run, calls } = production()
      const args = { ...COMPLETE_ARGS[command] }
      delete args[missing]
      const output = await run(invocation(command, args))
      assertContract('financial.tool.failure', output)
      assert.equal(output.error_summary, `${command} requires args.${missing}`)
      assert.deepEqual(calls, [], `${command} without ${missing} reached the backend`)
    }
  }
})

test('the shipped tool refuses an unconfirmed execution before reading its arguments', async () => {
  for (const [command, declared] of Object.entries(SURFACE)) {
    if (!declared.confirm) continue
    const { run, calls } = production()
    const output = await run({ command, args: COMPLETE_ARGS[command] })
    assertContract('financial.tool.failure', output)
    assert.equal(output.error_summary, `${command} requires ${declared.confirm}=true`)
    assert.deepEqual(calls, [], `${command} executed without ${declared.confirm}`)
  }
})

test('a confirmation inside args authorizes nothing in the shipped tool', async () => {
  const { run, calls } = production()
  const output = await run({
    command: 'backtest.run',
    args: { ...COMPLETE_ARGS['backtest.run'], confirm_python_execution: true },
  })
  assertContract('financial.tool.failure', output)
  assert.match(output.error_summary, /requires confirm_python_execution=true/)
  assert.deepEqual(calls, [])
  // And the request contract rejects the same input outright, so a benchmark
  // harness never even issues it.
  assert.ok(checkContract('financial.tool.request', {
    command: 'backtest.run', args: { confirm_python_execution: true },
  }).some(detail => detail.path === '/args' && detail.keyword === 'not'))
})

test('the shipped tool uses job_id to poll and backtest_id to read a result', async () => {
  const { run } = production()
  // Polling with the result identifier, and reading with the job identifier,
  // are both refusals. A double that accepted either would teach an Agent a
  // call the application rejects.
  const polledWrong = await run({ command: 'backtest.status', args: { backtest_id: 'bt-1' } })
  assert.equal(polledWrong.error_summary, 'backtest.status requires args.job_id')
  const readWrong = await run({ command: 'backtest.summary', args: { job_id: 'job-1' } })
  assert.equal(readWrong.error_summary, 'backtest.summary requires args.backtest_id')

  const submitted = await run(invocation('backtest.run', COMPLETE_ARGS['backtest.run']))
  assertContract('financial.tool.job', submitted)
  assert.equal(submitted.job_id, 'job-1')
  assert.equal(submitted.status, 'queued')
  // Submission answers immediately and carries no result; the result arrives
  // only once a status read reports a terminal state.
  assert.equal(submitted.backtest_id, undefined)
  const polled = await run({ command: 'backtest.status', args: { job_id: submitted.job_id } })
  assertContract('financial.tool.job', polled)
  assert.equal(polled.backtest_id, 'bt-1')
})

test('a backend error becomes one bounded line with no path or digest in it', async () => {
  const { run } = production()
  const leaky = instantiateShippedTool(SHIPPED_TOOLS.projectRun, () => [500, { detail: {
    code: 'BACKTEST_FAILED',
    message: 'Traceback at /Users/operator/secrets/run.py line 3\nsecond line',
    details: { coverage: 0.5, traceback: 'should not survive' },
    log_reference: 'log-1',
  } }])
  const output = await leaky.run(invocation('backtest.run', COMPLETE_ARGS['backtest.run']))
  assertContract('financial.tool.failure', output)
  assert.equal(output.error_code, 'BACKTEST_FAILED')
  // One line, absolute path replaced, and the allowlist drops `traceback`.
  assert.ok(!output.error_summary.includes('\n'))
  assert.ok(!output.error_summary.includes('/Users/operator'))
  assert.match(output.error_summary, /<internal-path>/)
  assert.deepEqual(output.error_details, { coverage: 0.5 })
  assert.equal(output.log_reference, 'log-1')

  // A 40-64 character hex token is reduced too, so a digest cannot round-trip.
  const digest = instantiateShippedTool(SHIPPED_TOOLS.projectRun, () => [500, { detail: {
    code: 'BACKTEST_FAILED', message: `revision ${'ab'.repeat(24)} rejected`,
  } }])
  const hexed = await digest.run(invocation('backtest.run', COMPLETE_ARGS['backtest.run']))
  assert.match(hexed.error_summary, /<internal-id>/)

  // An unsupported command is a failure in the same shape, not a thrown error.
  assertContract('financial.tool.failure', await run({ command: 'nope.command' }))
})

test('an unreachable backend is a bounded failure, not a crashed invocation', async () => {
  const offline = instantiateShippedTool(SHIPPED_TOOLS.projectRun, () => {
    throw Object.assign(new Error('connect ECONNREFUSED'), { name: 'TypeError' })
  })
  const output = await offline.run(invocation('backtest.run', COMPLETE_ARGS['backtest.run']))
  assertContract('financial.tool.failure', output)
  assert.equal(output.error_code, 'ALPHALAB_API_UNAVAILABLE')
})

test('the other shipped tool fails in the same shape', async () => {
  const { run, calls } = instantiateShippedTool(SHIPPED_TOOLS.projectFiles, routes)
  const output = await run({ command: 'files.write', args: { project_id: 'p1' } })
  assertContract('financial.tool.failure', output)
  // Its default code differs; the shape does not.
  assert.ok(FINANCIAL.failureCodes.includes(output.error_code), output.error_code)
  assert.deepEqual(calls, [])
})

test('the double refuses exactly what the shipped tool refuses', async () => {
  for (const [command, declared] of Object.entries(SURFACE)) {
    for (const missing of declared.requiredArgs) {
      const args = { ...COMPLETE_ARGS[command] }
      delete args[missing]
      const input = invocation(command, args)
      const expected = await production().run(input)
      const observed = await contractFinancialTool()(input)
      assert.deepEqual(
        { status: observed.status, summary: observed.error_summary },
        { status: expected.status, summary: expected.error_summary },
        `${command} without ${missing}`,
      )
    }
    if (declared.confirm) {
      const input = { command, args: COMPLETE_ARGS[command] }
      const expected = await production().run(input)
      const observed = await contractFinancialTool()(input)
      assert.equal(observed.error_summary, expected.error_summary, command)
    }
  }
  // And an unsupported command reads the same from either backend.
  const unsupported = { command: 'nope.command' }
  assert.equal(
    (await contractFinancialTool()(unsupported)).error_summary,
    (await production().run(unsupported)).error_summary,
  )
})

test('the double answers the reduced surface in the same contracts', async () => {
  const double = contractFinancialTool({ steps: 2 })
  const submitted = await double(invocation('backtest.run', COMPLETE_ARGS['backtest.run']))
  assertContract('financial.tool.job', submitted)
  assert.equal(submitted.status, 'queued')

  const polled = await double({ command: 'backtest.status', args: { job_id: submitted.job_id } })
  assertContract('financial.tool.job', polled)
  assert.equal(polled.status, 'running')
  // Unfinished work is a job record with a non-terminal status, never a failure.
  assert.notEqual(polled.status, 'failed')
  assert.equal(polled.backtest_id, null)

  const waited = await double({ command: 'backtest.wait', args: { job_id: submitted.job_id } })
  assertContract('financial.tool.job', waited)
  assert.equal(waited.status, 'succeeded')

  const summary = await double({ command: 'backtest.summary', args: { backtest_id: waited.backtest_id } })
  assert.equal(summary.status, 'succeeded')
  assert.equal(summary.backtest_id, waited.backtest_id)

  assertContract('financial.tool.result', await double({ command: 'data.status' }))

  // Reading a result before the job finished is a refusal in both backends.
  const early = contractFinancialTool({ steps: 5 })
  const pending = await early(invocation('backtest.run', COMPLETE_ARGS['backtest.run']))
  await early({ command: 'backtest.status', args: { job_id: pending.job_id } })
  assertContract('financial.tool.failure', await early({ command: 'backtest.summary', args: { backtest_id: 'bt-1' } }))
})

test('the shipped tool declares only the confirmation flags the contract allows', () => {
  const allowed = Object.keys(CONTRACT_DOCUMENT.contracts['financial.tool.request'].schema.properties)
  for (const path of Object.values(SHIPPED_TOOLS)) {
    const schema = readShippedTool(path).inputSchema
    for (const name of Object.keys(schema.properties)) {
      assert.ok(allowed.includes(name), `${path} accepts an undeclared top-level field: ${name}`)
    }
  }
})
