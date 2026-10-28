// Boots the financial example, passing the business API origin into tool
// processes. `toolEnvironment` is the only channel an application has for
// giving Tool nodes configuration: the Host copies a small allowlist of OS
// variables plus these entries into each tool child process, and nothing else.
import { mkdir } from 'node:fs/promises'
import { randomBytes } from 'node:crypto'
import { loadConexus } from '../lib/runtime.mjs'
import { researchWorkspace, HARNESS_NODE_ID } from './harness.mjs'

export const SLUG = 'research-agent'

export async function startResearchHost({
  projectRoot,
  apiOrigin,
  complete,
  model = 'example/local',
  baseUrl,
  apiKey = '',
  token = randomBytes(24).toString('base64url'),
}) {
  const { compileHarnessWorkspace, createLocalHost, createLocalRuntime, createModelCompletion } = await loadConexus()
  await mkdir(projectRoot, { recursive: true, mode: 0o700 })

  const runtime = createLocalRuntime({
    projectRoot,
    toolEnvironment: { ALPHALAB_API_ORIGIN: apiOrigin },
  })
  const workspace = researchWorkspace({ model, slug: SLUG })
  const release = compileHarnessWorkspace({ workspace, harnessNodeId: HARNESS_NODE_ID })

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
