// Runs a shipped AlphaLab Tool node's own code with the HTTP transport
// replaced, so a conformance test or a benchmark exercises the real
// implementation rather than a reimplementation of it.
//
// Why this exists: the earlier conformance test drove
// examples/conexus/financial-integration, whose simplified tool returns a
// `{ success, command }` envelope the shipped tool never produces. Checking a
// lookalike against a schema derived from the lookalike proves nothing about
// production, and a benchmark built that way can reward calls the application
// would reject.
//
// What is substituted is `fetch` and `ALPHALAB_API_ORIGIN` — the transport and
// the data behind it. What is not substituted is the tool body: argument
// validation, confirmation gates, the compaction that bounds payloads, and
// `publicFailure`'s error shaping all run for real. That boundary is the point:
// a change to any of them must surface as a contract failure.
//
// The tool body is a sequence of function declarations ending in
// `async function run(input)`, which is the Tool node contract Runtime Core
// executes in a child process. Here it is evaluated in-process instead, so the
// checks need no built runtime and no child process.
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const BUNDLE = 'integrations/conexus/alphalab-research-agent/tools'

export const SHIPPED_TOOLS = Object.freeze({
  projectRun: `${BUNDLE}/Project-Run.tool.json`,
  projectFiles: `${BUNDLE}/Project-Files.tool.json`,
})

export function readShippedTool(relativePath) {
  return JSON.parse(readFileSync(resolve(repositoryRoot, relativePath), 'utf8'))
}

/** Every command a shipped tool declares, from its own inputSchema. */
export function shippedCommands(relativePath) {
  return readShippedTool(relativePath).inputSchema.properties.command.enum
}

/**
 * Instantiates a shipped Tool node's `run(input)` over a stubbed transport.
 *
 * `routes` receives `{ method, path, body }` and returns either
 * `[status, payload]` or a payload for 200. Every call is recorded, so a test
 * can assert which endpoint a command reached and that a refused command
 * reached none at all — the difference between "the tool declined" and "the
 * tool asked the backend and the backend declined".
 */
export function instantiateShippedTool(relativePath, routes, { origin = 'http://stub.invalid' } = {}) {
  const calls = []
  const transport = async (url, options = {}) => {
    const path = String(url).slice(origin.length)
    const body = options.body === undefined ? undefined : JSON.parse(options.body)
    calls.push({ method: options.method ?? 'GET', path, body })
    const answer = await routes({ method: options.method ?? 'GET', path, body })
    const [status, payload] = Array.isArray(answer) ? answer : [200, answer]
    return new Response(JSON.stringify(payload ?? {}), {
      status, headers: { 'Content-Type': 'application/json' },
    })
  }
  // The tool reads configuration only from the environment the Host injects
  // through `toolEnvironment`, so a stub origin is all it takes to redirect it.
  const scope = { env: { ALPHALAB_API_ORIGIN: origin } }
  const { code } = readShippedTool(relativePath)
  const run = new Function('fetch', 'process', `${code}\nreturn run`)(transport, scope)
  return { run, calls }
}
