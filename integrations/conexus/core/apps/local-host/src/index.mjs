import Fastify from 'fastify'
import { createHash, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto'
import {
  AgentRunController, bindHarnessReleaseExposure, executeHostedRelease, harnessReleaseChecksum,
  inspectHostedRelease, instantiateHarnessWorkspace, redactPortableGraphData, validateHostedExposureInvocation,
} from '@conexus/runtime-core'
import { openState } from './state.mjs'

export { compileHarnessWorkspace } from '@conexus/runtime-core'
export { createLocalRuntime } from './local-runtime.mjs'
export { createModelCompletion } from './model-provider.mjs'

const terminal = status => ['completed', 'blocked', 'failed', 'cancelled'].includes(status)
const hash = token => createHash('sha256').update(token).digest('hex')
const now = () => new Date().toISOString()
const publicRun = ({ tokenHash, input, events, sequence, ...run }) => structuredClone(run)
const error = (message, statusCode = 400) => Object.assign(new Error(message), { statusCode })
const equal = (left, right) => timingSafeEqual(Buffer.from(hash(left)), Buffer.from(hash(right)))

function mergeRelease(state, release) {
  const previous = new Map(state.release.graph.nodes.map(node => [node.id, node]))
  const current = new Map(state.workspace.nodes.map(node => [node.id, node]))
  for (const node of release.graph.nodes) {
    const old = previous.get(node.id)
    const saved = current.get(node.id)
    if (!old) { current.set(node.id, structuredClone(node)); continue }
    if (!saved) continue
    for (const key of new Set([...Object.keys(old.data), ...Object.keys(node.data)])) {
      if (JSON.stringify(saved.data[key]) !== JSON.stringify(old.data[key])) continue
      if (key in node.data) saved.data[key] = structuredClone(node.data[key])
      else delete saved.data[key]
    }
  }
  const nextIds = new Set(release.graph.nodes.map(node => node.id))
  for (const [id, old] of previous) {
    if (!nextIds.has(id) && JSON.stringify(current.get(id)) === JSON.stringify(old)) current.delete(id)
  }
  const previousEdges = new Set(state.release.graph.edges.map(edge => edge.id))
  const edges = new Map(state.workspace.edges.filter(edge => !previousEdges.has(edge.id)).map(edge => [edge.id, edge]))
  for (const edge of release.graph.edges) edges.set(edge.id, structuredClone(edge))
  state.workspace = { nodes: [...current.values()], edges: [...edges.values()].filter(edge => current.has(edge.source) && current.has(edge.target)) }
  state.release = release
  state.releaseChecksum = harnessReleaseChecksum(release)
}

/** Single-user HTTP composition. This package has no account, billing, Canvas UI or enterprise imports. */
export async function createLocalHost({ projectRoot, slug, release, token, complete, model, runtimeAdapter }) {
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) throw new Error('Invalid local Harness slug.')
  if (typeof token !== 'string' || token.length < 24) throw new Error('The local Host requires a token of at least 24 characters.')
  const inspected = inspectHostedRelease(release, undefined, runtimeAdapter?.profile)
  if (!inspected.compatibility.deployable) throw new Error(JSON.stringify(inspected.compatibility.issues))
  release = inspected.release
  const exposure = bindHarnessReleaseExposure(release).exposure
  const store = await openState(projectRoot, {
    schema: 'conexus.local-host.v1', slug, release, releaseChecksum: inspected.checksum,
    workspace: instantiateHarnessWorkspace(release, exposure.id), revision: 0,
    nodes: release.graph.nodes.filter(node => ['note', 'document', 'custom', 'image', 'files'].includes(node.type)).map(node => {
      const { label, description, ...values } = redactPortableGraphData(node.data, node.type)
      return { id: node.id, type: node.type, label: label ?? node.id, description, values,
        createdAt: now(), updatedAt: now(), createdByRunId: '', updatedByRunId: '' }
    }), runs: [],
  })
  const state = store.state
  try {
    if (state.slug !== slug) throw new Error('The data directory belongs to another Harness.')
    if (state.releaseChecksum !== inspected.checksum) mergeRelease(state, release)
    instantiateHarnessWorkspace(release, exposure.id, state.workspace)
    for (const run of state.runs) {
      if (terminal(run.status)) continue
      run.status = 'cancelled'
      run.completedAt = now()
      delete run.pendingInteraction
      run.error = { code: 'host_restarted', message: 'The previous Host stopped during this run. Start a new run to continue.' }
    }
    await store.save()
  } catch (problem) { await store.close(); throw problem }

  const app = Fastify({ logger: false, bodyLimit: 1024 * 1024 })
  const controllers = new Map()
  const listeners = new Map()
  let queue = Promise.resolve()
  let closing = false
  const descriptor = {
    slug, title: release.manifest.name, summary: release.manifest.summary,
    exposures: release.manifest.exposures.map(({ nodeId, ...item }) => item), defaultExposureId: exposure.id,
  }
  const authorizeWorkspace = request => {
    const supplied = request.headers.authorization?.replace(/^Bearer /, '') ?? ''
    if (!equal(supplied, token)) throw error('Local workspace access denied.', 401)
  }
  const authorizeRun = request => {
    const run = state.runs.find(item => item.id === request.params.id)
    const supplied = request.headers.authorization?.replace(/^Bearer /, '') ?? ''
    if (!run || !equal(hash(supplied), run.tokenHash)) throw error('Run not found or access denied.', 404)
    return run
  }
  async function append(run, type, payload = {}) {
    const event = { sequence: ++run.sequence, runId: run.id, at: now(), type, status: run.status, ...payload }
    run.events.push(event)
    if (run.events.length > 1000) run.events.shift()
    await store.save()
    for (const listener of listeners.get(run.id) ?? []) listener(event)
  }
  function snapshot() {
    return { workspaceId: slug, slug, releaseChecksum: state.releaseChecksum,
      revision: state.revision, updatedAt: state.updatedAt, nodes: state.nodes }
  }
  async function execute(run) {
    if (terminal(run.status)) return
    const controller = new AgentRunController()
    controllers.set(run.id, controller)
    run.status = 'running'
    run.startedAt = now()
    try {
      await append(run, 'run.started')
      const bound = bindHarnessReleaseExposure(release, run.exposureId)
      const result = await executeHostedRelease({ runId: run.id, release, exposureId: run.exposureId,
        input: run.input, workspace: state.workspace, complete, defaultModel: model,
        runtimeAdapter, controller, signal: controller.signal,
        events: { emit: async event => {
          const payload = event.payload
          const ownerNodeId = payload.nodeId
          if (event.type === 'agent.assistant_turn' && ownerNodeId === bound.exposure.nodeId) {
            await append(run, 'run.message', { ownerNodeId, message: {
              role: 'assistant', content: payload.content ?? '', tool_calls: payload.toolCalls ?? [],
            } })
          } else if (event.type === 'agent.tool_result' && ownerNodeId === bound.exposure.nodeId) {
            let result = payload.result
            if (typeof result === 'string') { try { result = JSON.parse(result) } catch { result = {} } }
            await append(run, 'run.message', { ownerNodeId, message: { role: 'tool',
              tool_call_id: payload.toolCallId, name: payload.toolName,
              content: JSON.stringify({ success: result?.success !== false,
                ...(typeof result?.error === 'string' ? { message: result.error.slice(0, 280) } : {}) }),
            } })
          } else if (event.type === 'agent.interaction_requested') {
            const interaction = { id: payload.interactionId, kind: 'question', ownerNodeId,
              question: payload.question, ...(payload.choices ? { choices: payload.choices } : {}) }
            run.pendingInteraction = interaction
            await append(run, 'run.interaction_requested', { interaction })
          }
        } },
      })
      if (controller.aborted) throw error('Run cancelled.')
      const timestamp = now()
      const nodes = new Map(state.nodes.map(node => [node.id, node]))
      for (const id of result.workspaceChanges.deleted) nodes.delete(id)
      for (const node of [...result.workspaceChanges.created, ...result.workspaceChanges.updated]) {
        const previous = nodes.get(node.id)
        nodes.set(node.id, { ...node, createdAt: previous?.createdAt ?? timestamp, updatedAt: timestamp,
          createdByRunId: previous?.createdByRunId ?? run.id, updatedByRunId: run.id })
      }
      state.workspace = result.workspace
      state.nodes = [...nodes.values()]
      state.revision++
      state.updatedAt = timestamp
      run.workspaceRevision = state.revision
      run.nodeChanges = { created: result.workspaceChanges.created.map(node => node.id),
        updated: result.workspaceChanges.updated.map(node => node.id), deleted: result.workspaceChanges.deleted }
      run.summary = result.summary
      run.status = result.status
      if (result.result !== undefined) run.result = result.result
    } catch (problem) {
      run.status = controller.aborted ? 'cancelled' : 'failed'
      run.error = { code: controller.aborted ? 'run_cancelled' : problem.code ?? 'local_run_failed',
        message: controller.aborted ? 'Run cancelled.' : problem.message }
    } finally {
      controllers.delete(run.id)
      delete run.pendingInteraction
      run.completedAt = now()
      await append(run, `run.${run.status}`)
    }
  }

  app.setErrorHandler((problem, _request, reply) => reply.code(problem.statusCode ?? 500).send({
    error: { code: problem.code ?? 'local_host_error', message: problem.message },
  }))
  app.addHook('onRequest', async request => {
    const hostname = new URL(`http://${request.headers.host ?? ''}`).hostname
    if (!['localhost', '127.0.0.1', '[::1]'].includes(hostname)) throw error('Use a loopback Host address.', 403)
  })
  app.get('/health', async () => ({ ok: true, mode: 'local', modelConfigured: Boolean(complete) }))
  app.get(`/api/public/harnesses/${slug}/descriptor`, async () => descriptor)
  app.get(`/api/public/harnesses/${slug}`, async request => {
    authorizeWorkspace(request)
    return { ...descriptor, schema: 'conexus.local-harness.v1', version: release.manifest.version }
  })
  app.get(`/api/public/harnesses/${slug}/workspace`, async request => {
    authorizeWorkspace(request)
    return { workspace: snapshot() }
  })
  app.post(`/api/public/harnesses/${slug}/runs`, async (request, reply) => {
    authorizeWorkspace(request)
    if (closing) throw error('Local Host is stopping.', 503)
    if (!complete) throw error('Configure a model endpoint and model identifier.', 503)
    if (state.runs.filter(run => !terminal(run.status)).length >= 32) throw error('Local run queue is full.', 429)
    const bound = bindHarnessReleaseExposure(release, request.body?.exposureId)
    const input = request.body?.input
    validateHostedExposureInvocation(release, bound.exposure, input)
    const accessToken = randomBytes(32).toString('base64url')
    const run = { id: randomUUID(), slug, version: release.manifest.version, exposureId: bound.exposure.id,
      status: 'queued', createdAt: now(), input, tokenHash: hash(accessToken), sequence: 0, events: [] }
    state.runs.push(run)
    while (state.runs.length > 500 && terminal(state.runs[0].status)) state.runs.shift()
    await append(run, 'run.queued')
    queue = queue.then(() => execute(run)).catch(problem => { app.log.error(problem) })
    return reply.code(202).send({ run: publicRun(run), accessToken })
  })
  app.get('/api/public/runs/:id', async request => ({ run: publicRun(authorizeRun(request)) }))
  app.post('/api/public/runs/:id/cancel', async request => {
    const run = authorizeRun(request)
    if (!terminal(run.status)) {
      if (controllers.has(run.id)) controllers.get(run.id).abort()
      else { run.status = 'cancelled'; run.completedAt = now(); await append(run, 'run.cancelled') }
    }
    return { run: publicRun(run) }
  })
  app.post('/api/public/runs/:id/interactions/:interaction/answer', async request => {
    const run = authorizeRun(request)
    const answer = request.body?.answer
    if (typeof answer !== 'string' || !answer.trim() || answer.length > 20000) throw error('A bounded answer is required.')
    if (run.pendingInteraction?.id !== request.params.interaction ||
      !controllers.get(run.id)?.resolveInteraction(request.params.interaction, answer.trim())) throw error('Interaction is no longer pending.', 409)
    const interaction = run.pendingInteraction
    delete run.pendingInteraction
    await append(run, 'run.interaction_resolved', { interaction })
    return { run: publicRun(run) }
  })
  app.get('/api/public/runs/:id/events', async (request, reply) => {
    const run = authorizeRun(request)
    reply.hijack()
    reply.raw.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive' })
    let last = Number(request.headers['last-event-id']) || 0
    const send = event => {
      if (event.sequence <= last || reply.raw.destroyed) return
      last = event.sequence
      reply.raw.write(`id: ${event.sequence}\ndata: ${JSON.stringify(event)}\n\n`)
      if (terminal(event.status)) reply.raw.end()
    }
    const subscribers = listeners.get(run.id) ?? new Set()
    listeners.set(run.id, subscribers)
    subscribers.add(send)
    for (const event of run.events) send(event)
    if (terminal(run.status)) reply.raw.end()
    const heartbeat = setInterval(() => reply.raw.write(': heartbeat\n\n'), 15000)
    reply.raw.on('close', () => { clearInterval(heartbeat); subscribers.delete(send) })
  })
  const close = async () => {
    if (closing) return
    closing = true
    for (const run of state.runs) {
      if (run.status === 'queued') { run.status = 'cancelled'; run.completedAt = now(); await append(run, 'run.cancelled') }
    }
    for (const controller of controllers.values()) controller.abort()
    await queue
    await app.close()
    await store.close()
  }
  app.post('/api/local/shutdown', async request => {
    authorizeWorkspace(request)
    setImmediate(() => { void close() })
    return { ok: true }
  })
  return { app, close }
}
