import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { boundedFetch, contractSchema, protocolMessages, requireLive, liveScenarios, root, runLiveScenario, python } from '../scripts/lib/collaboration-live.mjs'

test('live execution requires explicit opt-in', () => {
  for (const value of [undefined, false, '1', 'true']) assert.throws(() => requireLive(value))
  requireLive(true)
})

test('live entry point rejects before accessing any provider or output path', async () => {
  await assert.rejects(runLiveScenario({ scenario: 'clean', settingsPath: null, output: null, enabled: false }), /explicit/)
})

test('baseline subprocess failures stay failed and cannot become a pass', async () => {
  const args = ['-e', 'console.log(JSON.stringify({status:"failed",assertions_passed:false}));process.exit(1)']
  await assert.rejects(python(process.execPath, args))
  assert.deepEqual(await python(process.execPath, args, 10000, true), { status: 'failed', assertions_passed: false })
  await assert.rejects(python(process.execPath, ['-e', 'console.log(JSON.stringify({status:"passed"}));process.exit(1)'], 10000, true))
})

test('live transport caps calls and retains usage without secrets or reasoning', async () => {
  const calls = []
  let upstream = 0
  const fetchImpl = async () => {
    upstream++
    return new Response(JSON.stringify({ usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7, prompt_cache_hit_tokens: 3 },
      choices: [{ message: { content: 'private response', reasoning_content: 'private reasoning', tool_calls: [{ function: { name: 'use' } }] } }] }), { status: 200 })
  }
  const call = boundedFetch({ endpoint: 'https://model.example/v1', calls, maxCalls: 1, fetchImpl, context: () => ({ role: 'reviewer' }) })
  const init = { body: JSON.stringify({ max_tokens: 20, messages: [{ content: 'private prompt' }] }), headers: { Authorization: 'Bearer private-key' } }
  await call('https://model.example/v1/chat/completions', init)
  await assert.rejects(call('https://model.example/v1/chat/completions', init))
  assert.equal(upstream, 1)
  assert.equal(calls[0].usage.total_tokens, 7)
  assert.equal(calls[0].role, 'reviewer')
  assert.deepEqual(calls[0].tool_names, ['use'])
  assert(!JSON.stringify(calls).includes('private'))
})

test('live transport rejects changed endpoint and excessive output before dispatch', async () => {
  let upstream = 0
  const call = boundedFetch({ endpoint: 'https://model.example', calls: [], fetchImpl: async () => { upstream++; return new Response('{}') } })
  await assert.rejects(call('https://another.example/chat/completions', { body: '{}' }))
  await assert.rejects(call('https://model.example/chat/completions', { body: '{"max_tokens":4097}' }))
  assert.equal(upstream, 0)
})

test('review instructions expose schema and bindings without scripted answers', async () => {
  const schema = contractSchema(JSON.parse(await readFile(join(root, 'alphalab/collaboration/schemas/protocol.v1.json'))), 'review_result')
  assert(schema.definitions.review_result && schema.definitions.finding)
  assert(!schema.definitions.final_output_request)
  const fixture = { delegation: { child_task_id: 'child' }, evidence_ids: ['result'], candidate_context: { report: 'A supplied claim' },
    expected_status: 'findings', review: { status: 'findings', explanation: 'DO_NOT_LEAK' } }
  for (const role of ['researcher', 'reviewer']) {
    const encoded = JSON.stringify(protocolMessages([{ role: 'system', content: 'role' }, { role: 'user', content: '{}' }], { role }, fixture, schema))
    assert(!encoded.includes('DO_NOT_LEAK') && !encoded.includes('expected_status'))
  }
})

// Ordinary npm test never reads the saved key or sends paid requests.
test('opt-in real-provider foundation acceptance matrix', { skip: process.env.ALPHALAB_LIVE_TESTS !== '1', timeout: 3000000 }, async () => {
  const output = join(root, 'build/member1/live-tests', randomUUID())
  const results = []
  for (const scenario of liveScenarios) {
    results.push(await runLiveScenario({ scenario, output: join(output, scenario), enabled: true,
      pythonExecutable: process.env.COLLABORATION_TEST_PYTHON ?? join(root, 'build/member1/venv/Scripts/python.exe'),
      settingsPath: process.env.ALPHALAB_LIVE_PROVIDER_SETTINGS ?? join(root, 'data/runtime/conexus/secrets/model-providers.json') }))
  }
  assert.deepEqual(results.filter(row => row.status !== 'passed').map(row => row.scenario), [])
})
