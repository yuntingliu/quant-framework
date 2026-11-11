/** Test-owned HTTP Agent, real coordinated SQLite runtime, fake providers only. */
import { createServer } from 'node:http'
import { readFileSync, appendFileSync } from 'node:fs'
import { createHmac } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { readAgentHandoff, installAgentAuthentication } from '../../integrations/conexus/configuration-agent-auth.mjs'
import { createCoordinatedAgentConfiguration } from '../../integrations/conexus/configuration-backend.mjs'

async function runFixture() {
  const [control, trace] = process.argv.slice(2)
  const mode = () => { try { return readFileSync(control, 'utf8') } catch { return 'normal' } }
  if (mode() === 'startup-timeout') await new Promise(resolve => setTimeout(resolve, 30000))
  if (mode() === 'exit-startup') process.exit(0)
  const handoff = await readAgentHandoff()
  const record = value => appendFileSync(trace, JSON.stringify(value) + '\n')
  const runtime = await createCoordinatedAgentConfiguration(handoff.contract, () => async () => ({ content: 'Synthetic' }), {
    env: {}, ownerProof: handoff.secret, production: handoff.apiOrigin !== undefined,
    fetchImpl: async url => Response.json(String(url).includes('openrouter.ai')
      ? { choices: [{ message: { annotations: [{ type: 'url_citation', url_citation: { title: 'Synthetic', url: 'https://source.example/article' } }] } }] }
      : { web: { results: [{ title: 'Synthetic', url: 'https://source.example/article' }] } }),
  })
  const routes = new Map(), configuration = { identity: runtime.identity, async testSearch(body, params) {
    record({ operation: 'test', hasKey: body.brave_api_key === 'synthetic-backend-brave-8102', provider: body.provider })
    if (mode() === 'response-secret') return { result: { success: true, error: body.brave_api_key, results: [{ title: body.brave_api_key }] } }
    return runtime.testSearch(body, params)
  } }
  let previous, parser
  const hooks = []
  routes.set('/api/public/fixture', async request => ({ ok: true, method: request.raw.method }))
  const server = createServer(async (request, response) => {
    let body = ''
    for await (const part of request) body += part
    const handler = routes.get(request.url)
    if (!handler) { response.writeHead(404).end(); return }
    const current = mode()
    if (request.url.endsWith('/proof')) {
      record({ operation: 'proof' })
      if (current === 'unavailable') { request.socket.destroy(); return }
      if (current === 'timeout') { return }
      if (current === 'slow-close') {
        response.writeHead(200, { Connection: 'close', 'Content-Type': 'application/json' })
        response.write('{')
        const trickle = setInterval(() => response.write(' '), 25)
        response.once('close', () => clearInterval(trickle))
        return
      }
      if (current === 'redirect') { response.writeHead(307, { Location: 'http://127.0.0.1:1/' }).end(); return }
    }
    let status = 200, sent
    const reply = { code(value) { status = value; return this }, send(value) { sent = value; return value } }
    try {
      const parsed = request.url.startsWith('/api/public/') ? (body ? JSON.parse(body) : undefined)
        : await new Promise((resolve, reject) => parser(request, Buffer.from(body), (error, value) => error ? reject(error) : resolve(value)))
      const incoming = { body: parsed, headers: request.headers, raw: request, routeOptions: { url: request.url } }
      let result
      for (const hook of hooks) { result = await hook(incoming, reply); if (result !== undefined) break }
      if (result === undefined) result = await handler(incoming, reply)
      result ??= sent
      if (request.url.endsWith('/proof')) {
        if (current === 'unauthenticated') result = { ...runtime.identity, ok: true }
        else if (current === 'replay' && previous) result = previous
        else if (current.startsWith('wrong-')) {
          const field = current.slice(6)
          result[field] = field === 'pid' ? process.pid + 1 : field === 'origin' ? 'http://127.0.0.1:1' : 'f'.repeat(field === 'root_key' || field === 'launch' ? 64 : 32)
          const { mac, ...identity } = result
          result.mac = createHmac('sha256', Buffer.from(handoff.secret, 'hex'))
            .update(JSON.stringify([...Object.values(identity), handoff.token])).digest('hex')
        } else if (current === 'stale-token') result.mac = '0'.repeat(64)
        else previous = result
        if (current === 'close-proof') response.setHeader('Connection', 'close')
      } else if (current === 'test-redirect') { response.writeHead(307, { Location: 'http://127.0.0.1:1/' }).end(); return }
      let wire = JSON.stringify(result)
      if (request.url.endsWith('/proof')) {
        if (current === 'duplicate-pid') wire = '{"pid":0,' + wire.slice(1)
        if (current === 'escaped-pid') wire = '{"\\u0070id":0,' + wire.slice(1)
        if (current === 'duplicate-mac') wire = '{"mac":"bad",' + wire.slice(1)
        if (current === 'boolean-version') wire = wire.replace('"version":1', '"version":true')
        if (current === 'float-version') wire = wire.replace('"version":1', '"version":1.0')
        if (current === 'string-version') wire = wire.replace('"version":1', '"version":"1"')
      }
      response.writeHead(status, { 'Content-Type': 'application/json' }).end(wire)
    } catch { response.writeHead(503).end('{}') }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const app = { listeningOrigin: `http://127.0.0.1:${server.address().port}`, post(path, handler) { routes.set(path, handler) },
    addHook(name, callback) { if (name === 'preHandler') hooks.push(callback) },
    addContentTypeParser(_type, _options, callback) { parser = callback } }
  const authentication = installAgentAuthentication(app, handoff, configuration, 'alphalab-research-agent')
  let descriptor = JSON.stringify({ origin: mode().startsWith('descriptor:') ? mode().slice(11) : app.listeningOrigin })
  if (mode() === 'duplicate-origin') descriptor = '{"origin":"http://127.0.0.1:1",' + descriptor.slice(1)
  process.stdout.write(descriptor + '\n')
  process.stdin.once('end', async () => { authentication.close(); server.closeAllConnections(); server.close(); await runtime.close(); process.stdin.destroy() })
  process.stdin.resume()

}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) await runFixture()

// Synthetic core exports for the ACTUAL managed entry. The hook registry is
// functional: auth/admission run before a handler and projection before JSON.
// This models the pinned core's Boolean(complete) default without certifying it.
export const createLocalRuntime = () => ({ adapter: {}, close() {} })
export const createModelCompletion = () => async () => ({ content: 'Synthetic' })
export const compileHarnessWorkspace = () => ({})
export async function createLocalHost({ complete, slug }) {
  const routes = new Map(), hooks = new Map()
  const controls = globalThis.__configurationFixture ?? {}
  const currentMode = () => { try { return readFileSync(controls.control, 'utf8') } catch { return 'normal' } }
  let parser, created = 0
  const manifest = { schema: 'conexus.local-harness.v1', defaultExposureId: 'research',
    exposures: [{ id: 'research', nodeType: 'agent', surfaces: ['api'] }] }
  routes.set('/health', async () => ({ ok: true, modelConfigured: Boolean(complete) }))
  routes.set(`/api/public/harnesses/${slug}`, async () => manifest)
  routes.set('/api/public/fixture', async request => ({ ok: true, method: request.method, created }))
  routes.set(`/api/public/harnesses/${slug}/runs`, async (_request, reply) => {
    // Deliberately retain the core's callback-presence test: a missing managed
    // hook must be observable as an incorrectly created Run in the regression.
    if (!complete) return reply.code(503).send({ error: 'Model unavailable.' })
    created++
    return reply.code(202).send({ run: { id: `synthetic-${created}`, status: 'queued' }, accessToken: 'synthetic-run-token' })
  })
  const server = createServer(async (raw, response) => {
    let status = 200, sent
    const reply = { get statusCode() { return status }, code(value) { status = value; return this },
      send(value) { sent = value; return value } }
    const route = raw.url.split('?')[0]
    const request = { method: raw.method, raw, headers: raw.headers, routeOptions: { url: route } }
    try {
      let body = ''
      for await (const part of raw) body += part
      request.body = raw.headers['content-type'] === 'application/vnd.alphalab-agent+json'
        ? await new Promise((resolve, reject) => parser(request, Buffer.from(body), (error, value) => error ? reject(error) : resolve(value)))
        : body ? JSON.parse(body) : undefined
      let value
      for (const hook of hooks.get('preHandler') ?? []) {
        value = await hook(request, reply)
        if (value !== undefined || sent !== undefined) break
      }
      if (value === undefined && sent === undefined) {
        if (!routes.has(route)) { status = 404; value = {} }
        else value = await routes.get(route)(request, reply)
      }
      value ??= sent
      if (route.endsWith('/proof') && currentMode() === 'wrong-proof') value.mac = '0'.repeat(64)
      if (route.endsWith('/proof') && currentMode() === 'close-proof') response.setHeader('Connection', 'close')
      if (route === '/health' && currentMode() === 'redirect') {
        response.writeHead(307, { Location: 'http://127.0.0.1:1/' }).end(); return
      }
      if (route === '/health' && currentMode() === 'unhealthy') { status = 503; value = { error: 'Unavailable.' } }
      for (const hook of hooks.get('preSerialization') ?? []) value = await hook(request, reply, value)
      if (controls.trace) appendFileSync(controls.trace, JSON.stringify({ route, method: raw.method, created, status }) + '\n')
      response.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(value))
    } catch (error) {
      // The real core serializes thrown hook failures. Never echo arbitrary
      // synthetic/runtime errors, private paths or credentials in this fixture.
      response.writeHead(error.statusCode === 503 ? 503 : 500, { 'Content-Type': 'application/json' }).end('{"error":"Unavailable."}')
    }
  })
  const app = {
    post(path, handler) { routes.set(path, handler) },
    addHook(name, handler) { hooks.set(name, [...(hooks.get(name) ?? []), handler]) },
    addContentTypeParser(_type, _options, handler) { parser = handler },
    async listen(options) {
      await new Promise(resolve => server.listen(options.port, options.host, resolve))
      this.listeningOrigin = `http://127.0.0.1:${server.address().port}`
    },
  }
  return { app, close: async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) } }
}
