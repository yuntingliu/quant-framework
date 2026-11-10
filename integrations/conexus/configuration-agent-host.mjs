/** Explicit inactive managed composition. Never imported by normal startup. */
import { resolve, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { readAgentHandoff, installAgentAuthentication } from './configuration-agent-auth.mjs'
import { createCoordinatedAgentConfiguration } from './configuration-backend.mjs'
import { openConfigurationAuthority } from './configuration-authority.mjs'
import { researchCanvas } from './research-canvas.mjs'

let configuration, runtime, host, authentication, authority, closing
let retired
const retirement = new Promise(resolve => { retired = resolve })
const close = () => closing ??= (async () => {
  authentication?.close()
  try { await host?.close() } finally {
    try { runtime?.close() } finally {
      try { await configuration?.close() } finally { process.stdin.destroy(); retired() }
    }
  }
})()
try {
  const handoff = await readAgentHandoff()
  authority = await openConfigurationAuthority(handoff.contract.root, handoff.contract.backend === 'json' ? 'json' : 'inactive_sqlite')
  {
    const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
    const core = await import(pathToFileURL(resolve(sourceRoot, 'build/conexus/apps/local-host/src/index.mjs')).href)
    configuration = await authority.run(() => createCoordinatedAgentConfiguration(handoff.contract, core.createModelCompletion, { env: {} }))
    const slug = 'alphalab-research-agent', model = 'alphalab/configured-provider'
    const projectRoot = resolve(handoff.contract.root, 'conexus/project')
    runtime = core.createLocalRuntime({ projectRoot, toolEnvironment: {}, capabilities: { 'web.search': configuration.webSearch } })
    const workspace = await researchCanvas(resolve(sourceRoot, 'integrations/conexus/alphalab-research-agent'), { model, slug })
    const release = core.compileHarnessWorkspace({ workspace, harnessNodeId: 'alphalab-research-harness-v1' })
    host = await core.createLocalHost({ projectRoot, release, slug, token: handoff.token, model,
      runtimeAdapter: runtime.adapter, complete: configuration.complete })
    authentication = installAgentAuthentication(host.app, handoff, configuration, slug)
    await host.app.listen({ host: '127.0.0.1', port: 0 })
    // Private stdout pipe to our parent, never a credential-bearing file.
    process.stdout.write(JSON.stringify({ origin: host.app.listeningOrigin }) + '\n')
    const shutdown = () => { void close().catch(() => { process.stderr.write('Managed Agent shutdown failed.\n'); process.exitCode = 1 }) }
    process.stdin.once('end', shutdown)
    process.stdin.once('error', shutdown)
    process.stdin.resume()
    process.on('SIGINT', shutdown)
    process.on('SIGTERM', shutdown)
    await retirement
  }
} catch {
  try { await close?.() } catch { /* Static diagnostics only. */ }
  process.stdin.destroy()
  process.stderr.write('Managed Agent unavailable.\n')
  process.exitCode = 1
} finally { try { await authority?.close() } catch { process.exitCode = 1 } }
