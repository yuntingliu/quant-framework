import { access, readFile } from "node:fs/promises"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"

const alphalabRoot = resolve(process.env.ALPHALAB_SOURCE_ROOT || process.env.ALPHALAB_PROJECT_ROOT || process.cwd())
const projectRoot = resolve(process.env.CONEXUS_PROJECT_ROOT || alphalabRoot)
const conexusRoot = resolve(process.env.CONEXUS_ROOT || resolve(alphalabRoot, "..", "Conexus"))
const hostingModule = resolve(
  conexusRoot,
  "apps",
  "web",
  "server-dist",
  "harnesses",
  "web-harness-hosting.js",
)
const runtimeModule = resolve(
  conexusRoot,
  "apps",
  "web",
  "server-dist",
  "agents",
  "trusted-web-runtime.js",
)

await access(hostingModule).catch(() => {
  throw new Error(`Conexus Web Host is not built: ${hostingModule}`)
})

const [{ WebHarnessHostingStore }, { TRUSTED_WEB_RUNTIME_PROFILE }] = await Promise.all([
  import(pathToFileURL(hostingModule).href),
  import(pathToFileURL(runtimeModule).href),
])
const canvas = JSON.parse(await readFile(resolve(projectRoot, ".conexus", "canvas.json"), "utf8"))
const store = new WebHarnessHostingStore({
  projectRoot,
  runtimeProfile: TRUSTED_WEB_RUNTIME_PROFILE,
})
const result = await store.host({
  harnessNodeId: "alphalab-research-harness-v1",
  slug: process.env.CONEXUS_HOSTING_SLUG || "alphalab-research-agent",
  accessPolicy: "anonymous",
  billingPolicy: "publisher",
  canvasState: { nodes: canvas.nodes, edges: canvas.edges },
})

console.log(JSON.stringify(result, null, 2))
