/** Disposable real Conexus Local Host for Member 2 live publication acceptance; no model calls. */
import { createInterface } from 'node:readline'
import { resolve } from 'node:path'
import { createLocalHost, compileHarnessWorkspace } from '../build/member-2/conexus-runtime/apps/local-host/src/index.mjs'

const [projectRoot, portText] = process.argv.slice(2)
if (!projectRoot || !/^\d+$/.test(portText)) throw new Error('Expected projectRoot and port')
const slug = 'local-agent'
const token = process.env.MEMBER2_TEST_WORKSPACE_TOKEN
const publicationSecret = process.env.MEMBER2_TEST_PUBLICATION_SECRET
if (!token || !publicationSecret) throw new Error('Missing Member 2 test credentials')
const outputs = ['alphalab-decision-notebook-v1', 'alphalab-workspace-result-v1', 'alphalab-workspace-commands-v1']
const release = compileHarnessWorkspace({
  harnessNodeId: 'harness', workspace: { nodes: [
    { id: 'harness', type: 'harness', data: { label: 'Member 2 Test Harness', defaultExposureId: 'agent' } },
    { id: 'agent', type: 'agent', parentId: 'harness', data: {
      label: 'Agent', model: 'test/disabled', systemPrompt: 'No model execution.',
      exposeInHarness: true, exposureId: 'agent',
    } },
    ...outputs.map(id => ({ id, type: 'note', parentId: 'harness',
      data: { label: id, description: 'AlphaLab workspace output', content: '', data: {} } })),
  ], edges: [] },
})
const host = await createLocalHost({
  projectRoot: resolve(projectRoot), slug, release, token, publicationSecret,
  // No complete callback: tests must not call a model provider.
})
await host.app.listen({ host: '127.0.0.1', port: Number(portText) })
process.stdout.write('READY\n')
let closing = false
async function stop() {
  if (closing) return
  closing = true
  try { await host.close() } finally { process.exit(0) }
}
createInterface({ input: process.stdin }).on('line', line => {
  if (line === 'stop') void stop()
})
process.stdin.on('end', () => { void stop() })
process.on('SIGTERM', () => { void stop() })
