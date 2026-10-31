// Boots the example Harness on the bundled local Host.
//
// This mirrors what `integrations/conexus/local-host.mjs` does for AlphaLab,
// with the AlphaLab-specific pieces removed: compile the graph, create an OS
// adapter, hand both to `createLocalHost`. The Host owns HTTP, the run queue
// and durable state; Runtime Core owns the Agent loop. Neither is replaced by
// an example.
import { mkdir } from 'node:fs/promises'
import { randomBytes } from 'node:crypto'
import { loadConexus } from '../lib/runtime.mjs'
import { minimalWorkspace } from './harness.mjs'

export const SLUG = 'minimal-agent'

/**
 * @param projectRoot  Durable state directory. Receives state.json, host.lock.
 * @param complete     In-process model callback, or omit and pass `model`+`baseUrl`.
 * @param capabilities Extra host capabilities keyed by capability id.
 */
export async function startMinimalHost({
  projectRoot,
  complete,
  model = 'example/local',
  baseUrl,
  apiKey = '',
  capabilities = {},
  token = randomBytes(24).toString('base64url'),
}) {
  const { compileHarnessWorkspace, createLocalHost, createLocalRuntime, createModelCompletion } = await loadConexus()
  await mkdir(projectRoot, { recursive: true, mode: 0o700 })

  const runtime = createLocalRuntime({ projectRoot, capabilities })
  const workspace = minimalWorkspace({ model, slug: SLUG })
  const release = compileHarnessWorkspace({ workspace, harnessNodeId: 'minimal-harness-v1' })

  const host = await createLocalHost({
    projectRoot,
    slug: SLUG,
    release,
    token,
    model,
    runtimeAdapter: runtime.adapter,
    complete: complete ?? (baseUrl ? createModelCompletion({ model, baseUrl, apiKey }) : undefined),
  })

  return {
    app: host.app,
    token,
    release,
    slug: SLUG,
    async close() {
      try { await host.close() } finally { runtime.close() }
    },
  }
}
