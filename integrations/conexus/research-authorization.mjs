import { randomUUID } from 'node:crypto'

const readOnly = new Set([
  'projects.list', 'projects.get', 'files.list', 'files.read', 'templates.factors',
  'workspace.context', 'workspace.outputs.prepare', 'data.catalog', 'data.status',
  'data.query', 'data.validate', 'data.market_bars', 'data.fundamentals', 'data.factor_returns',
  'sync.list', 'sync.status', 'backtest.status', 'backtest.wait', 'backtest.summary',
  'backtest.events', 'backtest.analysis', 'backtest.attribution', 'backtest.robustness',
  'backtest.signals', 'backtest.validation', 'backtest.compare', 'market_risk.evaluate',
])
const actions = new Set([
  'projects.create', 'projects.update', 'projects.migrate_default', 'projects.delete',
  'strategies.create', 'strategies.rename', 'files.write', 'files.install_factor_template',
  'recipe.plan', 'recipe.sync', 'sync.cancel', 'strategy.preview', 'factor.snapshot',
  'factor.history', 'factor.research', 'backtest.run', 'backtest.batch',
])

/** Authority belongs to the host, never to the Tool's model-supplied booleans. */
export function researchAuthorization(adapter, tools, { authorize } = {}) {
  const trusted = new Map(tools.map(tool => [tool.toolName, tool.code]))
  const pending = new Map()
  const denied = message => ({ success: false, stderr: message, exitCode: 1 })
  function ask(runId, input, signal) {
    if (signal.aborted) return Promise.resolve(false)
    return new Promise(resolve => {
      const entry = { id: randomUUID(), input, resolve: approved => {
        signal.removeEventListener('abort', abort)
        const queue = pending.get(runId) ?? []
        const rest = queue.filter(item => item !== entry)
        if (rest.length) pending.set(runId, rest)
        else pending.delete(runId)
        resolve(approved)
      } }
      const abort = () => entry.resolve(false)
      pending.set(runId, [...(pending.get(runId) ?? []), entry])
      signal.addEventListener('abort', abort, { once: true })
    })
  }
  return {
    adapter: {
      ...adapter,
      executeShell: async () => denied('Shell execution is unavailable to the research Agent.'),
      async executeTool(params) {
        const definition = params.definition
        if (!trusted.has(definition.name) || trusted.get(definition.name) !== definition.code)
          return denied('Only the published AlphaLab tools may execute; edited or new tools are not trusted.')
        // Freeze the exact operation before awaiting approval; later edits cannot widen it.
        const input = structuredClone(params.args)
        const command = input.command
        if (!readOnly.has(command)) {
          if (!actions.has(command)) return denied('Unknown research command; consult the published SDK.')
          const approved = authorize
            ? await authorize({ runId: params.runId, input: structuredClone(input), signal: params.signal })
            : await ask(params.runId, input, params.signal)
          if (approved !== true || params.signal.aborted) return denied('The user did not authorize this operation. Do not retry or invent results.')
        }
        return adapter.executeTool({ ...params, args: input })
      },
      async disposeRun(runId) {
        for (const entry of [...(pending.get(runId) ?? [])]) entry.resolve(false)
        return adapter.disposeRun?.(runId)
      },
    },
    interaction(runId) {
      const entry = pending.get(runId)?.[0]
      if (!entry) return undefined
      return { id: entry.id, kind: 'question', ownerNodeId: 'alphalab-research-agent-v1',
        question: `Approve this operation? It may execute local Python or change project data.\n${JSON.stringify(entry.input, null, 2)}`,
        choices: ['Approve', 'Deny'] }
    },
    answer(runId, interactionId, answer) {
      const entry = pending.get(runId)?.[0]
      if (!entry || entry.id !== interactionId) return false
      entry.resolve(answer === 'Approve')
      return true
    },
    close() { for (const queue of [...pending.values()]) for (const entry of [...queue]) entry.resolve(false) },
  }
}

/** Reuse authenticated Run polling and interaction UI; no credentials enter the model. */
export function installResearchAuthorization(host, policy) {
  host.app.addHook('preSerialization', async (request, _reply, payload) => {
    if (request.method === 'GET' && request.routeOptions.url === '/api/public/runs/:id' && payload?.run) {
      const interaction = policy.interaction(payload.run.id)
      if (interaction) return { ...payload, run: { ...payload.run, pendingInteraction: interaction } }
    }
    return payload
  })
  host.app.addHook('preHandler', async (request, reply) => {
    if (request.method !== 'POST' || request.routeOptions.url !== '/api/public/runs/:id/interactions/:interaction/answer') return
    const pending = policy.interaction(request.params.id)
    if (!pending || pending.id !== request.params.interaction) return
    // Authenticate using the core route before resolving anything, including a denial.
    const snapshot = await host.app.inject({ method: 'GET',
      url: `/api/public/runs/${encodeURIComponent(request.params.id)}`,
      headers: { authorization: request.headers.authorization ?? '' } })
    if (snapshot.statusCode !== 200) return reply.code(snapshot.statusCode).send(snapshot.json())
    if (!['Approve', 'Deny'].includes(request.body?.answer))
      return reply.code(422).send({ error: 'Choose Approve or Deny for this operation.' })
    if (!policy.answer(request.params.id, request.params.interaction, request.body.answer))
      return reply.code(409).send({ error: 'Approval is no longer pending.' })
    const { run } = snapshot.json()
    delete run.pendingInteraction
    return reply.send({ run })
  })
  host.app.addHook('onClose', async () => policy.close())
}
