import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { randomBytes } from 'node:crypto'
import { resolve } from 'node:path'
import { createLocalHost, createLocalRuntime, createModelCompletion } from './index.mjs'

const projectRoot = resolve(process.env.CONEXUS_LOCAL_ROOT || '.conexus/local')
await mkdir(projectRoot, { recursive: true, mode: 0o700 })
const tokenPath = resolve(projectRoot, 'host-token')
try { await writeFile(tokenPath, randomBytes(32).toString('base64url'), { flag: 'wx', mode: 0o600 }) }
catch (error) { if (error.code !== 'EEXIST') throw error }
if (!process.env.CONEXUS_RELEASE_FILE) throw new Error('Set CONEXUS_RELEASE_FILE to a compiled Harness Release JSON file.')
const release = JSON.parse(await readFile(resolve(process.env.CONEXUS_RELEASE_FILE), 'utf8'))
const model = process.env.CONEXUS_MODEL_ID || ''
const baseUrl = process.env.CONEXUS_MODEL_BASE_URL || ''
const runtime = createLocalRuntime({ projectRoot })
const host = await createLocalHost({ projectRoot, release, slug: process.env.CONEXUS_LOCAL_SLUG || 'local-agent',
  token: (await readFile(tokenPath, 'utf8')).trim(), model,
  complete: model && baseUrl ? createModelCompletion({ model, baseUrl, apiKey: process.env.CONEXUS_MODEL_API_KEY || '' }) : undefined,
  runtimeAdapter: runtime.adapter,
})
await host.app.listen({ host: '127.0.0.1', port: Number(process.env.CONEXUS_LOCAL_PORT || 8787) })
console.log(`Conexus local service: ${host.app.listeningOrigin}`)
console.log(`Local access credential: ${tokenPath}`)
async function close() { try { await host.close() } finally { runtime.close() } }
process.on('SIGINT', () => { void close() })
process.on('SIGTERM', () => { void close() })
