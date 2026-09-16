import { researchCanvas } from './research-canvas.mjs'
import { configuredCompletion, readActiveProvider } from './model-providers.mjs'
import { writeFile, rename } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const entry = resolve(sourceRoot, 'build/conexus/apps/local-host/src/index.mjs')
const { createLocalHost, createLocalRuntime, createModelCompletion, compileHarnessWorkspace } = await import(pathToFileURL(entry).href)

async function webSearch(params) {
  const key = process.env.BRAVE_SEARCH_API_KEY?.trim()
  if (!key) return { result: { success: false, error: 'Configure BRAVE_SEARCH_API_KEY for web search. Local research tools remain available.' } }
  const query = typeof params.input.query === 'string' ? params.input.query.trim() : ''
  if (!query) return { result: { success: false, error: 'query is required' } }
  const count = Math.max(1, Math.min(20, Math.trunc(Number(params.input.count) || 5)))
  const url = new URL('https://api.search.brave.com/res/v1/web/search')
  url.searchParams.set('q', query)
  url.searchParams.set('count', String(count))
  if (typeof params.input.freshness === 'string') url.searchParams.set('freshness', params.input.freshness)
  try {
    const response = await fetch(url, {
      headers: { Accept: 'application/json', 'X-Subscription-Token': key },
      signal: AbortSignal.any([params.signal, AbortSignal.timeout(20000)]),
    })
    if (!response.ok) return { result: { success: false, error: `Search provider returned HTTP ${response.status}` } }
    const payload = await response.json()
    return { result: { success: true, results: (payload.web?.results ?? []).slice(0, count).map(row => ({
      title: row.title, url: row.url, snippet: row.description ?? '',
    })) } }
  } catch { return { result: { success: false, error: 'Search provider request failed or timed out.' } } }
}

const model = 'alphalab/configured-provider'
const providerPath = process.env.ALPHALAB_MODEL_PROVIDERS_FILE || resolve(process.env.CONEXUS_LOCAL_ROOT, '..', 'secrets/model-providers.json')
const slug = 'alphalab-research-agent'
const projectRoot = process.env.CONEXUS_LOCAL_ROOT
const runtime = createLocalRuntime({ projectRoot,
  toolEnvironment: { ALPHALAB_API_ORIGIN: process.env.ALPHALAB_API_ORIGIN },
  capabilities: { 'web.search': webSearch },
})
const workspace = await researchCanvas(resolve(sourceRoot, 'integrations/conexus/alphalab-research-agent'), { model, slug })
const release = compileHarnessWorkspace({ workspace, harnessNodeId: 'alphalab-research-harness-v1' })
const token = process.env.CONEXUS_LOCAL_TOKEN
const host = await createLocalHost({ projectRoot, release, slug, token, model, runtimeAdapter: runtime.adapter,
  complete: configuredCompletion(providerPath, createModelCompletion),
})
host.app.addHook('preSerialization', async (request, _reply, payload) => {
  if (request.routeOptions.url !== '/health') return payload
  return { ...payload, modelConfigured: Boolean(await readActiveProvider(providerPath)) }
})
host.app.addHook('preHandler', async request => {
  if (request.method === 'POST' && request.routeOptions.url === `/api/public/harnesses/${slug}/runs`
      && !(await readActiveProvider(providerPath))) {
    const error = new Error('Open Model providers and configure a model before starting the Agent.')
    error.statusCode = 503
    throw error
  }
})
let closing
function close() {
  closing ??= host.close().finally(() => runtime.close())
  return closing
}
const parentPipe = process.env.ALPHALAB_AGENT_PARENT_PIPE === '1'
host.app.addHook('onClose', async () => {
  runtime.close()
  if (parentPipe) process.stdin.destroy()
})
try {
  await host.app.listen({ host: '127.0.0.1', port: Number(process.env.CONEXUS_LOCAL_PORT) })
  const connection = process.env.ALPHALAB_AGENT_CONNECTION_FILE
  await writeFile(`${connection}.tmp`, JSON.stringify({ origin: host.app.listeningOrigin, slug, token, pid: process.pid }), { mode: 0o600 })
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
