/** Test-owned HTTP Agent, real coordinated SQLite runtime, fake providers only. */
import { createServer } from 'node:http'
import { readFileSync, appendFileSync } from 'node:fs'
import { createHmac } from 'node:crypto'
import { readAgentHandoff, installAgentAuthentication } from '../../integrations/conexus/configuration-agent-auth.mjs'
import { createCoordinatedAgentConfiguration } from '../../integrations/conexus/configuration-backend.mjs'

const [control, trace] = process.argv.slice(2)
const mode = () => { try { return readFileSync(control, 'utf8') } catch { return 'normal' } }
if (mode() === 'startup-timeout') await new Promise(resolve => setTimeout(resolve, 30000))
if (mode() === 'exit-startup') process.exit(0)
const handoff = await readAgentHandoff()
const record = value => appendFileSync(trace, JSON.stringify(value) + '\n')
const runtime = await createCoordinatedAgentConfiguration(handoff.contract, () => async () => ({ content: 'Synthetic' }), {
  env: {}, fetchImpl: async url => Response.json(String(url).includes('openrouter.ai')
    ? { choices: [{ message: { annotations: [{ type: 'url_citation', url_citation: { title: 'Synthetic', url: 'https://source.example/article' } }] } }] }
    : { web: { results: [{ title: 'Synthetic', url: 'https://source.example/article' }] } }),
})
const routes = new Map(), configuration = { identity: runtime.identity, async testSearch(body, params) {
  record({ operation: 'test', hasKey: body.brave_api_key === 'synthetic-backend-brave-8102', provider: body.provider })
  if (mode() === 'response-secret') return { result: { success: true, error: body.brave_api_key, results: [{ title: body.brave_api_key }] } }
  return runtime.testSearch(body, params)
} }
let previous, parser
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
    const parsed = await new Promise((resolve, reject) => parser(request, Buffer.from(body), (error, value) => error ? reject(error) : resolve(value)))
    let result = await handler({ body: parsed, headers: request.headers, raw: request }, reply)
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
  addContentTypeParser(_type, _options, callback) { parser = callback } }
const authentication = installAgentAuthentication(app, handoff, configuration, 'alphalab-research-agent')
let descriptor = JSON.stringify({ origin: mode().startsWith('descriptor:') ? mode().slice(11) : app.listeningOrigin })
if (mode() === 'duplicate-origin') descriptor = '{"origin":"http://127.0.0.1:1",' + descriptor.slice(1)
process.stdout.write(descriptor + '\n')
process.stdin.once('end', async () => { authentication.close(); server.closeAllConnections(); server.close(); await runtime.close(); process.stdin.destroy() })
process.stdin.resume()
