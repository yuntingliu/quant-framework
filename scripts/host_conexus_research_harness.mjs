import { access, readFile } from "node:fs/promises"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"

const alphalabRoot = resolve(process.env.ALPHALAB_SOURCE_ROOT || process.env.ALPHALAB_PROJECT_ROOT || process.cwd())
const projectRoot = resolve(process.env.CONEXUS_PROJECT_ROOT || alphalabRoot)
const conexusRoot = resolve(process.env.CONEXUS_ROOT || resolve(alphalabRoot, "..", "Conexus", "source"))
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

// This adapter is specific to the legacy internal API. Newer Web Hosts manage
// credentials and activation through the administrator hosting operation.
for (const modulePath of [hostingModule, runtimeModule]) {
  await access(modulePath).catch(() => {
    throw new Error(
      "This Conexus checkout is incompatible with the legacy hosting helper. " +
      "Use the checkout's administrator Host operation to migrate and host the Harness, " +
      "then configure its enterprise service credential. See deploy/conexus-cloud/README.md. " +
      `Missing module: ${modulePath}`,
    )
  })
}
if (process.argv.includes("--check")) {
  console.log(JSON.stringify({ legacyModulesPresent: true, hosted: false, modelVerified: false }))
  process.exit(0)
}

const [{ WebHarnessHostingStore }, { TRUSTED_WEB_RUNTIME_PROFILE }] = await Promise.all([
  import(pathToFileURL(hostingModule).href),
  import(pathToFileURL(runtimeModule).href),
])
const canvas = JSON.parse(await readFile(resolve(projectRoot, process.env.CONEXUS_CANVAS_PATH || ".conexus/canvas.json"), "utf8"))
const store = new WebHarnessHostingStore({
  projectRoot,
  runtimeProfile: TRUSTED_WEB_RUNTIME_PROFILE,
})
const result = await store.host({
  harnessNodeId: "alphalab-research-harness-v1",
  slug: process.env.CONEXUS_HOSTING_SLUG || "alphalab-research-agent",
  identityPolicy: "enterprise",
  billingPolicy: "publisher",
  canvasState: { nodes: canvas.nodes, edges: canvas.edges },
})

console.log(JSON.stringify(result, null, 2))
