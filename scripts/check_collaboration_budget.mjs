// Offline Phase 7 acceptance through the real Host and completion/fetch seam.
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { randomUUID } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createCollaborationHost, COLLABORATION_NODES as nodes } from '../integrations/conexus/collaboration/host.mjs'
import { pinConfiguredModelProvider } from '../integrations/conexus/collaboration/model-provider.mjs'
import { prepareFixture } from './lib/collaboration-host-scenarios.mjs'

export const budgetScenarios = ['shared', 'exhausted', 'lowered', 'retry', 'repair', 'unverifiable', 'tool-limit', 'time-limit', 'events']
const tool = (name, args) => ({ id: randomUUID(), type: 'function', function: { name, arguments: JSON.stringify(args) } })
const use = (node_id, input) => tool('use', { node_id, capability: 'tool.invoke', input })
const done = () => tool('complete', { status: 'done', summary: 'Scripted budget acceptance.' })

export async function runBudgetScenario({ scenario, output, pythonExecutable }) {
  assert(budgetScenarios.includes(scenario))
  const fixture = await prepareFixture(output, pythonExecutable)
  const requests = [], turns = { researcher: 0, reviewer: 0 }
  const settingsPath = join(output, 'model-providers.json')
  let switched = false
  const server = createServer(async (request, response) => {
    try {
      let raw = ''
      for await (const chunk of request) raw += chunk
      const body = JSON.parse(raw)
      const role = body.messages[0].content.includes('the reviewer') ? 'reviewer' : 'researcher'
      requests.push({ role, model: body.model, max_tokens: body.max_tokens })
      assert.equal(body.model, 'scripted/original')
      assert(body.max_tokens >= 10 && body.max_tokens <= 10)
      if (!switched) {
        switched = true
        await writeFile(settingsPath, JSON.stringify({ active_id: 'new', profiles: [{ id: 'new', model: 'scripted/changed', base_url: baseUrl }] }))
      }
      if (scenario === 'retry' && requests.length === 1) {
        response.writeHead(503, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ error: { message: 'FAKE_PROVIDER_SECRET_MUST_NOT_LEAK' },
          usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 } }))
        return
      }
      const step = ++turns[role]
      const calls = role === 'reviewer'
        ? step === 1 ? [...(scenario === 'events' ? Array.from({ length: 340 }, () => tool('find', { scope: 'current' })) : []),
          use(nodes.submit, { review: fixture.review })] : [done()]
        : step === 1 ? [use(nodes.control, { operation: 'start', delegation: fixture.delegation })]
          : step === 2 ? [use(nodes.control, { operation: 'wait', child_task_id: fixture.delegation.child_task_id })]
            : scenario === 'exhausted' ? [tool('find', { scope: 'current' })] : [done()]
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: null, tool_calls: calls }, finish_reason: 'tool_calls' }],
        usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20,
          prompt_tokens_details: { cached_tokens: 4 }, completion_tokens_details: { reasoning_tokens: 3 } } }))
    } catch {
      response.writeHead(500)
      response.end('{}')
    }
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const baseUrl = `http://127.0.0.1:${server.address().port}/v1`
  await writeFile(settingsPath, JSON.stringify({ active_id: 'original', profiles: [{ id: 'original', model: 'scripted/original', base_url: baseUrl }] }))
  try {
    const provider = await pinConfiguredModelProvider({ settingsPath, providerId: 'local-scripted',
      boundsId: scenario === 'unverifiable' ? null : 'synthetic-fixed-input-10-v1',
      inputTokenBound: () => 10, supportsOutputCap: true,
      pricing: { id: 'synthetic-price-v1', currency: 'USD', input_micros_per_million: 1_000_000, output_micros_per_million: 2_000_000 } })
    let repaired = false
    const options = { pythonExecutable, evidenceRoot: join(output, 'evidence'), storeRoot: join(output, 'control'),
      taskId: fixture.task_id, candidate: fixture.candidate,
      scopes: ['model.invoke', 'evidence.read', 'review.submit', 'review.delegate'],
      modelRetries: scenario === 'retry' ? 1 : 0, schemaRepairRetries: scenario === 'repair' ? 1 : 0,
      modelProvider: { configuration: provider.configuration, boundInput: provider.boundInput,
        async complete(request) {
          if (scenario === 'repair' && !repaired) {
            repaired = true
            return { response: { content: null }, usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 } }
          }
          return provider.complete(request)
        } },
      budget: { max_output_tokens: 10, max_total_tokens: scenario === 'lowered' ? 90 : ['retry', 'repair'].includes(scenario) ? 200 : 100,
        max_cost_micros: 1000, currency: 'USD', max_tool_calls: scenario === 'tool-limit' ? 1 : scenario === 'events' ? 400 : 20,
        wall_time_ms: scenario === 'time-limit' ? 1 : 120000 },
    }
    const host = await createCollaborationHost(options)
    const result = await host.run()
    await host.flushAccounting()
    const state = await host.snapshot(), accounting = await host.accounting(), events = await host.events(), calls = await host.requests()
    assert.equal(accounting.totals.pending, 0)
    if (['shared', 'retry', 'repair', 'events'].includes(scenario)) assert.equal(state.status, 'completed')
    else assert(['blocked', 'timed_out'].includes(state.status))
    if (['shared', 'exhausted'].includes(scenario)) {
      assert.equal(accounting.totals.total_tokens, 100)
      assert.equal(accounting.roles.researcher.total_tokens, 60)
      assert.equal(accounting.roles.reviewer.total_tokens, 40)
      assert.equal(accounting.totals.cost_micros, 150)
      assert.equal(requests.length, 5)
    }
    if (scenario === 'lowered') {
      assert.equal(requests.length, 4)
      assert.equal(accounting.totals.total_tokens, 80)
    }
    if (scenario === 'unverifiable' || scenario === 'time-limit') assert.equal(requests.length, 0)
    if (scenario === 'unverifiable') assert.equal(state.reason, 'BUDGET_UNVERIFIABLE')
    if (['exhausted', 'lowered', 'tool-limit'].includes(scenario)) assert.equal(state.reason, 'BUDGET_EXHAUSTED')
    if (scenario === 'retry') {
      assert.equal(requests.length, 6)
      assert.equal(accounting.totals.total_tokens, 103)
      assert.equal(accounting.totals.cost_micros, 154)
      assert(calls.some(call => call.purpose === 'retry'))
      assert(calls.some(call => call.settlement.outcome === 'failed' && call.settlement.usage.total_tokens === 3))
    }
    if (scenario === 'repair') {
      assert.equal(requests.length, 5)
      assert.equal(accounting.totals.model_calls, 6)
      assert.equal(accounting.totals.total_tokens, 120)
      assert(calls.some(call => call.purpose === 'schema_repair'))
      assert(calls.some(call => call.settlement.outcome === 'invalid'))
    }
    if (scenario === 'events') {
      assert(events.filter(event => event.role === 'reviewer').length > 1000)
      assert.equal(calls.filter(call => call.kind === 'tool').length, 345)
      assert.equal(accounting.totals.tool_calls, 345)
      assert.equal(accounting.totals.total_tokens, 100)
    }
    if (requests.length) {
      const newProvider = await pinConfiguredModelProvider({ settingsPath })
      assert.equal(newProvider.configuration.model_id, 'scripted/changed')
      assert.equal(accounting.model.model_id, 'scripted/original')
    }
    const reopened = await createCollaborationHost(options)
    await reopened.recover()
    assert.deepEqual(await reopened.accounting(), accounting)
    assert.equal((await reopened.snapshot()).status, state.status)
    const log = JSON.stringify({ accounting, events, calls })
    assert(!log.includes('FAKE_PROVIDER_SECRET') && !log.includes('127.0.0.1') && !log.includes('Authorization'))
    assert.equal(accounting.totals.model_calls, requests.length + (repaired ? 1 : 0))
    const verification = { scenario, status: 'passed', task_state: state.status, reason: state.reason,
      model_calls: accounting.totals.model_calls, tool_calls: accounting.totals.tool_calls,
      tokens: accounting.totals.total_tokens, synthetic_cost_micros: accounting.totals.cost_micros,
      downstream_http_calls: requests.length, external_model_calls: 0, currency: 'USD', pricing: 'synthetic',
      model_profile_pinned: true, restart_preserved: true, final_publication: false }
    for (const [name, value] of Object.entries({ verification, accounting, events, calls, state, requests, result })) {
      await writeFile(join(output, `${name}.json`), JSON.stringify(value, null, 2) + '\n')
    }
    return verification
  } finally { server.closeAllConnections(); await new Promise(resolvePromise => server.close(resolvePromise)) }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2)
  const get = (flag, fallback) => args.includes(flag) ? args[args.indexOf(flag) + 1] : fallback
  if (args.includes('--help')) console.log('Usage: node scripts/check_collaboration_budget.mjs --scenario all|' + budgetScenarios.join('|') + ' --output build/member1/budget [--python executable]')
  else {
    const selected = get('--scenario', 'all'), trial = join(resolve(get('--output', 'build/member1/budget')), `trial-${randomUUID().slice(0, 12)}`)
    for (const scenario of selected === 'all' ? budgetScenarios : [selected]) {
      const output = join(trial, scenario)
      const result = await runBudgetScenario({ scenario, output, pythonExecutable: resolve(get('--python', 'build/member1/venv/Scripts/python.exe')) })
      console.log(JSON.stringify({ ...result, artifacts: output }))
    }
  }
}
