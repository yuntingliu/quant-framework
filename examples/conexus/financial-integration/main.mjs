// Runnable end-to-end financial integration demo.
//
//   node examples/conexus/financial-integration/main.mjs
//
// Starts a stub AlphaLab API and a scripted model on loopback, then walks the
// full research shape: bounded reads, a refused unauthorized execution, an
// authorized asynchronous backtest, and one structured output written back
// into the workspace. No real backend, no credentials, no network egress.
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { startResearchHost, SLUG } from './host.mjs'
import { startAlphaLabApiStub } from './doubles/api-stub.mjs'
import { startModelEndpoint, toolCall } from '../minimal-agent/doubles/scripted-model.mjs'
import { TOOL_NODE_ID, RESULT_NODE_ID, EXPOSURE_ID } from './harness.mjs'

const REQUEST_ID = 'example-request-1'

const invoke = (command, args = {}, extra = {}) =>
  toolCall('use', { node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { command, args, ...extra } })

const lastTool = (request) => JSON.parse(request.messages.findLast((message) => message.role === 'tool').content).output

let jobId

const script = [
  () => invoke('data.status'),
  (request) => {
    const coverage = lastTool(request).result
    console.log(`coverage: ${coverage.dataset} ${coverage.first_session}..${coverage.last_session} (${coverage.session_count} sessions)`)
    // Deliberately unauthorized: no confirm_python_execution.
    return invoke('backtest.run', { strategy_id: 'main' })
  },
  (request) => {
    const refused = lastTool(request)
    console.log(`unauthorized run refused: ${refused.error}`)
    return invoke('backtest.run', { strategy_id: 'main' }, { confirm_python_execution: true })
  },
  (request) => {
    jobId = lastTool(request).job_id
    console.log(`backtest submitted: ${jobId}`)
    return invoke('backtest.status', { job_id: jobId })
  },
  (request) => {
    console.log(`first status read: ${lastTool(request).status}`)
    return invoke('backtest.wait', { job_id: jobId })
  },
  (request) => {
    console.log(`terminal status: ${lastTool(request).status}`)
    return invoke('backtest.summary', { job_id: jobId })
  },
  (request) => {
    const summary = lastTool(request).result
    console.log(`summary: total_return=${summary.total_return} vol=${summary.annualized_volatility}`)
    return toolCall('edit', { operations: [{
      kind: 'patch',
      node_id: RESULT_NODE_ID,
      set: { data: { version: 1, requestId: REQUEST_ID, findings: {
        runId: summary.run_id,
        totalReturn: summary.total_return,
        annualizedVolatility: summary.annualized_volatility,
        maxDrawdown: summary.max_drawdown,
        executionReliable: summary.execution_reliable,
      } } },
    }] })
  },
  () => toolCall('complete', { status: 'done', summary: 'Ran one authorized backtest and recorded its pinned result.' }),
]

const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-financial-'))
const api = await startAlphaLabApiStub()
const model = await startModelEndpoint(script)
let host

try {
  host = await startResearchHost({ projectRoot, apiOrigin: api.origin, model: model.model, baseUrl: model.baseUrl })
  await host.app.listen({ host: '127.0.0.1', port: 0 })
  const origin = host.app.listeningOrigin
  const auth = { authorization: `Bearer ${host.token}` }
  console.log(`stub AlphaLab API: ${api.origin}`)
  console.log(`local Host: ${origin}\n`)

  const created = await fetch(`${origin}/api/public/harnesses/${SLUG}/runs`, {
    method: 'POST',
    headers: { ...auth, 'content-type': 'application/json' },
    body: JSON.stringify({ exposureId: EXPOSURE_ID, input: { request: 'Run the main strategy backtest and report its result.' } }),
  })
  if (created.status !== 202) throw new Error(`run rejected: ${created.status} ${await created.text()}`)
  const { run, accessToken } = await created.json()

  const events = await fetch(`${origin}/api/public/runs/${run.id}/events`, {
    headers: { authorization: `Bearer ${accessToken}`, accept: 'text/event-stream' },
  })
  for await (const chunk of events.body) void chunk

  const finished = await (await fetch(`${origin}/api/public/runs/${run.id}`, {
    headers: { authorization: `Bearer ${accessToken}` },
  })).json()
  console.log(`\nstatus: ${finished.run.status}`)
  console.log(`summary: ${finished.run.summary}`)

  const { workspace } = await (await fetch(`${origin}/api/public/harnesses/${SLUG}/workspace`, { headers: auth })).json()
  const result = workspace.nodes.find((node) => node.id === RESULT_NODE_ID)
  console.log(`\nresult node:\n${JSON.stringify(result.values.data, null, 2)}`)
  if (finished.run.status !== 'completed') process.exitCode = 1
} finally {
  await host?.close()
  await model.close()
  await api.close()
  await rm(projectRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
}
