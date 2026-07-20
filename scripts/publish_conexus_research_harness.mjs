import { access, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const projectRoot = resolve(process.env.ALPHALAB_PROJECT_ROOT || process.cwd())
const conexusRoot = resolve(process.env.CONEXUS_ROOT || resolve(projectRoot, '..', 'Conexus'))
const publicationModule = resolve(conexusRoot, 'backend', 'dist', 'web-harness-publication.js')
const runtimeModule = resolve(conexusRoot, 'backend', 'dist', 'trusted-web-runtime.js')

await access(publicationModule).catch(() => {
  throw new Error(`Conexus backend is not built: ${publicationModule}. Run npm --prefix "${conexusRoot}\\backend" run build.`)
})

const [{ WebHarnessPublicationStore }, { TRUSTED_WEB_RUNTIME_PROFILE }] = await Promise.all([
  import(pathToFileURL(publicationModule).href),
  import(pathToFileURL(runtimeModule).href),
])
const canvas = JSON.parse(await readFile(resolve(projectRoot, '.conexus', 'canvas.json'), 'utf8'))
const store = new WebHarnessPublicationStore({ projectRoot, runtimeProfile: TRUSTED_WEB_RUNTIME_PROFILE })
const result = await store.publish({
  harnessNodeId: 'alphalab-research-harness-v1',
  slug: process.env.CONEXUS_PUBLICATION_SLUG || 'alphalab-research-agent',
  accessPolicy: 'anonymous',
  billingPolicy: 'publisher',
  canvasState: { nodes: canvas.nodes, edges: canvas.edges },
})

console.log(JSON.stringify(result, null, 2))
