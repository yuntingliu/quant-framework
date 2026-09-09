import { researchCanvas } from './research-canvas.mjs'
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

const model = process.env.CONEXUS_MODEL_ID?.trim() || 'unconfigured'
const baseUrl = process.env.CONEXUS_MODEL_BASE_URL?.trim()
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
  complete: baseUrl && model !== 'unconfigured'
    ? createModelCompletion({ baseUrl, model, apiKey: process.env.CONEXUS_MODEL_API_KEY?.trim() || '' }) : undefined,
})
await host.app.listen({ host: '127.0.0.1', port: Number(process.env.CONEXUS_LOCAL_PORT) })
const connection = process.env.ALPHALAB_AGENT_CONNECTION_FILE
await writeFile(`${connection}.tmp`, JSON.stringify({ origin: host.app.listeningOrigin, slug, token }), { mode: 0o600 })
await rename(`${connection}.tmp`, connection)
console.log(`Conexus local Agent listening at ${host.app.listeningOrigin}`)
async function close() { try { await host.close() } finally { runtime.close() } }
process.on('SIGINT', () => { void close() })
process.on('SIGTERM', () => { void close() })
