import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { compileHarnessWorkspace, createLocalHost, createLocalRuntime, createModelCompletion } from '../src/index.mjs'

const token = 'local-test-credential-1234567890'
const headers = { authorization: `Bearer ${token}` }
const call = (name, args) => ({ content: null, finish_reason: 'tool_calls', tool_calls: [{
  id: crypto.randomUUID(), type: 'function', function: { name, arguments: JSON.stringify(args) },
}] })
function release(model = 'test/local') {
  return compileHarnessWorkspace({ harnessNodeId: 'harness', workspace: { nodes: [
    { id: 'harness', type: 'harness', data: { label: 'Local Agent', defaultExposureId: 'agent' } },
    { id: 'agent', type: 'agent', parentId: 'harness', data: { label: 'Agent', model,
      systemPrompt: 'Complete the requested local task.', exposeInHarness: true, exposureId: 'agent' } },
  ], edges: [] } })
}
async function waitFor(host, run, predicate = row => ['completed', 'failed', 'cancelled'].includes(row.status)) {
  const until = Date.now() + 10000
  while (Date.now() < until) {
    const response = await host.app.inject({ url: `/api/public/runs/${run.run.id}`, headers: { authorization: `Bearer ${run.accessToken}` } })
    const value = response.json().run
    if (predicate(value)) return value
    await delay(10)
  }
  throw new Error('Run did not reach the expected state.')
}
async function start(host) {
  const response = await host.app.inject({ method: 'POST', url: '/api/public/harnesses/local-agent/runs', headers,
    payload: { exposureId: 'agent', input: { request: 'Perform the local task.' } } })
  assert.equal(response.statusCode, 202, response.body)
  return response.json()
}

test('local service enforces credentials and persists native graph mutations across an upgrade', async () => {
  const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-local-test-'))
  let host
  const observed = []
  try {
    host = await createLocalHost({ projectRoot, slug: 'local-agent', token, release: release(), model: 'test/local',
      complete: async request => {
        observed.push(request)
        return observed.length === 1
          ? call('create', { nodes: [{ type: 'note', label: 'Report', description: 'Research report', content: '# Saved report' }] })
          : call('complete', { status: 'done', summary: 'Saved report.' })
      } })
    assert.equal((await host.app.inject('/api/public/harnesses/local-agent/workspace')).statusCode, 401)
    await assert.rejects(createLocalHost({ projectRoot, slug: 'local-agent', token, release: release() }), /already using/)
    const run = await start(host)
    const done = await waitFor(host, run)
    assert.equal(done.status, 'completed', JSON.stringify(done))
    assert.equal(done.nodeChanges.created.length, 1, JSON.stringify(observed.at(-1).messages.filter(message => message.role === 'tool')))
    assert.equal((await host.app.inject(`/api/public/runs/${done.id}`)).statusCode, 404)
    const events = await host.app.inject({ url: `/api/public/runs/${done.id}/events`, headers: { authorization: `Bearer ${run.accessToken}` } })
    assert.match(events.body, /run.completed/)
    assert.equal(observed[1].messages.some(message => message.role === 'tool' && message.content.includes('"success":false')), false)
    await host.close()
    host = await createLocalHost({ projectRoot, slug: 'local-agent', token, release: release('test/new'), model: 'test/new' })
    const workspace = (await host.app.inject({ url: '/api/public/harnesses/local-agent/workspace', headers })).json().workspace
    assert.equal(workspace.nodes.find(node => node.label === 'Report').values.content, '# Saved report')
    const saved = JSON.parse(await readFile(join(projectRoot, 'state.json'), 'utf8'))
    assert.equal(saved.workspace.nodes.find(node => node.id === 'agent').data.model, 'test/new')
    assert.equal(JSON.stringify(saved).includes(token), false)
    assert.equal((await host.app.inject({ method: 'POST', url: '/api/public/harnesses/local-agent/runs', headers, payload: {} })).statusCode, 503)
  } finally { await host?.close(); await rm(projectRoot, { recursive: true, force: true }) }
})

test('pending native interactions accept an answer and active runs can be cancelled', async () => {
  const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-local-test-'))
  let calls = 0
  const host = await createLocalHost({ projectRoot, slug: 'local-agent', token, release: release(), model: 'test/local',
    complete: async () => ++calls % 2 === 1
      ? call('request_user_input', { prompt: 'Proceed?', choices: ['Yes', 'No'] })
      : call('complete', { status: 'done', summary: 'Confirmed.' }) })
  try {
    const run = await start(host)
    const waiting = await waitFor(host, run, row => Boolean(row.pendingInteraction))
    const response = await host.app.inject({ method: 'POST',
      url: `/api/public/runs/${run.run.id}/interactions/${waiting.pendingInteraction.id}/answer`,
      headers: { authorization: `Bearer ${run.accessToken}` }, payload: { answer: 'Yes' } })
    assert.equal(response.statusCode, 200, response.body)
    assert.equal((await waitFor(host, run)).status, 'completed')
    const cancelled = await start(host)
    await waitFor(host, cancelled, row => Boolean(row.pendingInteraction))
    await host.app.inject({ method: 'POST', url: `/api/public/runs/${cancelled.run.id}/cancel`,
      headers: { authorization: `Bearer ${cancelled.accessToken}` } })
    assert.equal((await waitFor(host, cancelled)).status, 'cancelled')
  } finally { await host.close(); await rm(projectRoot, { recursive: true, force: true }) }
})

test('restart marks interrupted work cancelled without replaying its tools', async () => {
  const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-local-test-'))
  let host = await createLocalHost({ projectRoot, slug: 'local-agent', token, release: release() })
  await host.close()
  const path = join(projectRoot, 'state.json')
  const state = JSON.parse(await readFile(path, 'utf8'))
  state.runs.push({ id: 'interrupted', status: 'running', sequence: 0, events: [] })
  await writeFile(path, JSON.stringify(state))
  try {
    host = await createLocalHost({ projectRoot, slug: 'local-agent', token, release: release(), complete: () => assert.fail('Must not replay') })
    const recovered = JSON.parse(await readFile(path, 'utf8'))
    assert.equal(recovered.runs[0].status, 'cancelled')
    assert.equal(recovered.runs[0].error.code, 'host_restarted')
  } finally { await host.close(); await rm(projectRoot, { recursive: true, force: true }) }
})

test('a local model needs no cloud credential and remote configuration fails closed', async () => {
  let called
  const complete = createModelCompletion({ baseUrl: 'http://127.0.0.1:1234/v1', model: 'local/model',
    fetchImpl: async (url, init) => {
      called = { url, init }
      return new Response(JSON.stringify({ choices: [{ message: { role: 'assistant', content: 'Ready' }, finish_reason: 'stop' }] }))
    } })
  const result = await complete({ model: 'local/model', messages: [], signal: new AbortController().signal })
  assert.equal(result.content, 'Ready')
  assert.equal(called.init.headers.Authorization, undefined)
  assert.equal(called.url.pathname, '/v1/chat/completions')
  assert.throws(() => createModelCompletion({ baseUrl: 'https://example.com/v1', model: 'model' }), /API key/)
})
