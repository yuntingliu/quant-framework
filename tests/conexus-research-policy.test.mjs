import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFile, mkdtemp, rm, access } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { researchAuthorization, installResearchAuthorization } from '../integrations/conexus/research-authorization.mjs'
import { researchCompletion } from '../integrations/conexus/research-completion.mjs'
import { researchCanvas } from '../integrations/conexus/research-canvas.mjs'

const tool = { toolName: 'alphalab_project_run', code: 'published code' }
const params = (command, runId = 'r1', signal = new AbortController().signal) => ({
  runId, signal, definition: { name: tool.toolName, code: tool.code },
  args: { command, args: { project_id: 'p1' }, confirm_python_execution: true },
})
const calls = () => {
  const executed = []
  return { executed, adapter: { async executeTool(p) { executed.push(p); return { success: true, result: {} } } } }
}
test('model confirmation flags cannot grant execution; approval covers one frozen action only', async () => {
  const { adapter, executed } = calls()
  const policy = researchAuthorization(adapter, [tool])
  const input = params('backtest.run')
  const work = policy.adapter.executeTool(input)
  assert.equal(executed.length, 0)
  const pending = policy.interaction('r1')
  input.args.args.project_id = 'changed'
  assert.equal(policy.answer('r2', pending.id, 'Approve'), false)
  assert.equal(policy.answer('r1', 'forged', 'Approve'), false)
  assert.equal(policy.answer('r1', pending.id, 'Approve'), true)
  assert.equal((await work).success, true)
  assert.equal(executed[0].args.args.project_id, 'p1')
  assert.equal(policy.answer('r1', pending.id, 'Approve'), false)
  const again = policy.adapter.executeTool(params('backtest.run'))
  policy.answer('r1', policy.interaction('r1').id, 'Deny')
  assert.equal((await again).success, false)
  assert.equal(executed.length, 1)
})
test('read-only tools need no confirmation; unknown or altered tools and shell cannot bypass policy', async () => {
  const { adapter, executed } = calls()
  const policy = researchAuthorization(adapter, [tool])
  assert.equal((await policy.adapter.executeTool(params('data.validate'))).success, true)
  assert.equal((await policy.adapter.executeTool(params('invented.command'))).success, false)
  const altered = params('data.query'); altered.definition.code += '; backtest()'
  assert.equal((await policy.adapter.executeTool(altered)).success, false)
  assert.equal((await policy.adapter.executeShell({})).success, false)
  assert.equal(executed.length, 1)
})
test('abort and close reject outstanding actions without dispatch', async () => {
  const { adapter, executed } = calls()
  const policy = researchAuthorization(adapter, [tool])
  const controller = new AbortController()
  const work = policy.adapter.executeTool(params('recipe.sync', 'r1', controller.signal))
  controller.abort()
  assert.equal((await work).success, false)
  const next = policy.adapter.executeTool(params('backtest.run'))
  policy.close()
  assert.equal((await next).success, false)
  assert.equal(policy.interaction('r1'), undefined)
  assert.equal(executed.length, 0)
})
test('budget feedback counts every batch member, rejects the whole overrun, and isolates runs', async () => {
  const seen = []
  const complete = researchCompletion(async r => {
    seen.push(r.messages.at(-1).content)
    return { content: null, tool_calls: [{}, {}], finish_reason: 'tool_calls' }
  }, { maxToolCalls: 3 })
  const signal = new AbortController().signal
  await complete({ signal, messages: [] })
  await assert.rejects(complete({ signal, messages: [] }), /entire tool batch rejected/)
  assert.match(seen[1], /2\/3.*1 remain/)
  await complete({ signal: new AbortController().signal, messages: [] })
  assert.match(seen[2], /0\/3/)
})
test('compiled research instructions expose actual result schema and distinguish commands from nodes', async () => {
  const canvas = await researchCanvas(fileURLToPath(new URL('../integrations/conexus/alphalab-research-agent', import.meta.url)), { model: 'test' })
  const prompt = canvas.nodes.find(n => n.type === 'agent').data.systemPrompt
  assert.ok(!prompt.includes('{{ALPHALAB_'))
  const schema = JSON.parse(await readFile(new URL('../integrations/conexus/alphalab-research-agent/workspace-output.schema.json', import.meta.url)))
  assert.ok(prompt.includes(JSON.stringify(schema.properties.workspaceResult)))
})

test('real host authenticates pending approval, rejects replay, and resumes the same execution', async t => {
  const coreURL = new URL('../build/conexus/apps/local-host/src/index.mjs', import.meta.url)
  try { await access(coreURL) } catch { t.skip('Build the pinned Conexus runtime first'); return }
  const { createLocalHost, compileHarnessWorkspace } = await import(coreURL)
  const canvas = await researchCanvas(fileURLToPath(new URL('../integrations/conexus/alphalab-research-agent', import.meta.url)), { model: 'test' })
  const trusted = canvas.nodes.filter(n => n.type === 'tool').map(n => n.data)
  const { adapter, executed } = calls()
  adapter.profile = { name: 'test', nodeTypes: ['tool'], runNodeTypes: ['tool'], toolRuntimes: ['node'], permissions: ['network', 'shell', 'filesystem'], isolation: ['shell'], capabilityIds: [] }
  const policy = researchAuthorization(adapter, trusted)
  let turn = 0
  const complete = async () => ({ content: null, finish_reason: 'tool_calls', tool_calls: [{ id: `call-${++turn}`, type: 'function', function: {
    name: turn === 1 ? 'use' : 'complete', arguments: JSON.stringify(turn === 1
      ? { node_id: 'alphalab-project-run', capability: 'tool.invoke', input: { command: 'backtest.run', confirm_python_execution: true, args: { project_id: 'p1' } } }
      : { status: 'done', summary: 'Approved operation completed.' }),
  } }] })
  const root = await mkdtemp(join(tmpdir(), 'research-policy-'))
  const host = await createLocalHost({ projectRoot: root, slug: 'test', token: 'test-workspace-token-at-least-24-characters', model: 'test',
    release: compileHarnessWorkspace({ workspace: canvas, harnessNodeId: 'alphalab-research-harness-v1' }), runtimeAdapter: policy.adapter, complete })
  installResearchAuthorization(host, policy)
  try {
    const created = await host.app.inject({ method: 'POST', url: '/api/public/harnesses/test/runs', headers: { authorization: 'Bearer test-workspace-token-at-least-24-characters' }, payload: { exposureId: 'alphalab-research-agent', input: { request: 'Review only.' } } })
    assert.equal(created.statusCode, 202)
    const { run, accessToken } = created.json(), headers = { authorization: `Bearer ${accessToken}` }
    let snapshot
    for (let i = 0; i < 100; i++) {
      snapshot = (await host.app.inject({ url: `/api/public/runs/${run.id}`, headers })).json().run
      if (snapshot.pendingInteraction) break
      await new Promise(r => setTimeout(r, 10))
    }
    assert.ok(snapshot.pendingInteraction)
    assert.equal(executed.length, 0)
    const url = `/api/public/runs/${run.id}/interactions/${snapshot.pendingInteraction.id}/answer`
    assert.notEqual((await host.app.inject({ method: 'POST', url, payload: { answer: 'Approve' } })).statusCode, 200)
    assert.equal(executed.length, 0)
    assert.equal((await host.app.inject({ method: 'POST', url, headers, payload: { answer: 'Approve' } })).statusCode, 200)
    assert.notEqual((await host.app.inject({ method: 'POST', url, headers, payload: { answer: 'Approve' } })).statusCode, 200)
    for (let i = 0; i < 100; i++) {
      snapshot = (await host.app.inject({ url: `/api/public/runs/${run.id}`, headers })).json().run
      if (snapshot.status === 'completed') break
      await new Promise(r => setTimeout(r, 10))
    }
    assert.equal(snapshot.status, 'completed')
    assert.equal(executed.length, 1)
  } finally { await host.close(); await rm(root, { recursive: true, force: true }) }
})
