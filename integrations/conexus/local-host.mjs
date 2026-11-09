import { researchCanvas } from './research-canvas.mjs'
import { createJsonAgentConfiguration } from './configuration-runtime.mjs'
import { createCoordinatedAgentConfiguration, validateBackendContract, ConfigurationBackendError } from './configuration-backend.mjs'
import { writeFile, rename } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { timingSafeEqual } from 'node:crypto'

const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
// A launcher handoff can bind JSON to an installation, never activate SQLite.
// Reject inconsistent state before loading the core or starting any runtime.
let choice
if (process.env.ALPHALAB_AGENT_CONFIGURATION_DECISION !== undefined) {
  try {
    choice = await validateBackendContract(JSON.parse(process.env.ALPHALAB_AGENT_CONFIGURATION_DECISION), { production: true })
    if (resolve(process.env.ALPHALAB_MODEL_PROVIDERS_FILE || '') !== resolve(choice.root, 'conexus/secrets/model-providers.json')) throw 0
  } catch { throw new ConfigurationBackendError() }
}
const entry = resolve(sourceRoot, 'build/conexus/apps/local-host/src/index.mjs')
const { createLocalHost, createLocalRuntime, createModelCompletion, compileHarnessWorkspace } = await import(pathToFileURL(entry).href)

const model = 'alphalab/configured-provider'
const providerPath = process.env.ALPHALAB_MODEL_PROVIDERS_FILE || resolve(process.env.CONEXUS_LOCAL_ROOT, '..', 'secrets/model-providers.json')
// Fixed JSON startup. SQLite composition is deliberately not selectable here.
const configuration = choice
  ? await createCoordinatedAgentConfiguration(choice, createModelCompletion, { production: true })
  : createJsonAgentConfiguration(providerPath, createModelCompletion)
const slug = 'alphalab-research-agent'
const projectRoot = process.env.CONEXUS_LOCAL_ROOT
const runtime = createLocalRuntime({ projectRoot,
  toolEnvironment: { ALPHALAB_API_ORIGIN: process.env.ALPHALAB_API_ORIGIN },
  capabilities: { 'web.search': configuration.webSearch },
})
const workspace = await researchCanvas(resolve(sourceRoot, 'integrations/conexus/alphalab-research-agent'), { model, slug })
const release = compileHarnessWorkspace({ workspace, harnessNodeId: 'alphalab-research-harness-v1' })
const token = process.env.CONEXUS_LOCAL_TOKEN
const host = await createLocalHost({ projectRoot, release, slug, token, model, runtimeAdapter: runtime.adapter,
  complete: configuration.complete,
})
host.app.post('/api/alphalab/search/test', async (request, reply) => {
  const expected = Buffer.from(`Bearer ${token}`)
  const received = Buffer.from(request.headers.authorization || '')
  if (!token || received.length !== expected.length || !timingSafeEqual(received, expected)) {
    return reply.code(401).send({ error: 'Authentication required.' })
  }
  return configuration.testSearch(request.body, { input: { query: 'OpenRouter web search server tool documentation', count: 3 }, signal: AbortSignal.timeout(50000) })
})
host.app.addHook('preSerialization', async (request, _reply, payload) => {
  if (request.routeOptions.url !== '/health') return payload
  return { ...payload, modelConfigured: await configuration.modelConfigured(),
    ...(configuration.identity ? { configuration: configuration.identity } : {}) }
})
host.app.addHook('preHandler', async request => {
  if (request.method === 'POST' && request.routeOptions.url === `/api/public/harnesses/${slug}/runs`
      && !(await configuration.modelConfigured())) {
    const error = new Error('Open Model providers and configure a model before starting the Agent.')
    error.statusCode = 503
    throw error
  }
})
let closing
function close() {
  closing ??= host.close().finally(async () => { runtime.close(); await configuration.close() })
  return closing
}
const parentPipe = process.env.ALPHALAB_AGENT_PARENT_PIPE === '1'
host.app.addHook('onClose', async () => {
  runtime.close()
  await configuration.close()
  if (parentPipe) process.stdin.destroy()
})
try {
  await host.app.listen({ host: '127.0.0.1', port: Number(process.env.CONEXUS_LOCAL_PORT) })
  const connection = process.env.ALPHALAB_AGENT_CONNECTION_FILE
  await writeFile(`${connection}.tmp`, JSON.stringify({ origin: host.app.listeningOrigin, slug, token, pid: process.pid,
    ...(configuration.identity ? { configuration: configuration.identity } : {}) }), { mode: 0o600 })
  await rename(`${connection}.tmp`, connection)
  console.log(`Conexus local Agent listening at ${host.app.listeningOrigin}`)
  process.on('SIGINT', () => { void close() })
  process.on('SIGTERM', () => { void close() })
  if (parentPipe) {
    // A private pipe follows the owning launcher even after a forced Windows exit.
    process.stdin.once('end', () => { void close() })
    process.stdin.once('error', () => { void close() })
    process.stdin.resume()
  }
} catch (error) {
  await close()
  throw error
}
