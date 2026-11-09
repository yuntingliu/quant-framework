/** Private stdin launch agreement; neither health nor public identity is proof.
 * Used only by the explicit managed host, never by production local-host.mjs.
 */
import { createHmac, timingSafeEqual } from 'node:crypto'
import { inspect } from 'node:util'
import { validateBackendContract, ConfigurationBackendError } from './configuration-backend.mjs'

export const AGENT_CONTENT_TYPE = 'application/vnd.alphalab-agent+json'
const LIMIT = 65536

/** Narrow bounded JSON grammar, with decoded-key uniqueness before assignment.
 * Authentication uses safe integers only, never floating point or exponents.
 */
export function parseAgentMessage(input) {
  let result
  try {
    const text = typeof input === 'string' ? input : new TextDecoder('utf-8', { fatal: true }).decode(input)
    if (Buffer.byteLength(text) > LIMIT) throw 0
    let offset = 0, nodes = 0
    const whitespace = () => { while (/[ \t\r\n]/.test(text[offset] ?? '\0')) offset++ }
    const string = () => {
      const start = offset++
      while (offset < text.length) {
        if (text[offset] === '\\') { offset += 2; continue }
        if (text[offset++] === '"') return JSON.parse(text.slice(start, offset))
      }
      throw 0
    }
    function value(depth) {
      if (++nodes > 1024 || depth > 8) throw 0
      whitespace()
      const char = text[offset]
      if (char === '"') return string()
      if (char === '{' || char === '[') {
        const object = char === '{', result = object ? Object.create(null) : [], end = object ? '}' : ']'
        offset++; whitespace()
        if (text[offset] === end) { offset++; return result }
        while (offset < text.length) {
          let key
          if (object) {
            if (++nodes > 1024 || text[offset] !== '"') throw 0
            key = string(); whitespace()
            if (Object.hasOwn(result, key) || text[offset++] !== ':') throw 0
          }
          const child = value(depth + 1)
          if (object) result[key] = child
          else result.push(child)
          whitespace()
          const separator = text[offset++]
          if (separator === end) return result
          if (separator !== ',') throw 0
          whitespace()
        }
        throw 0
      }
      for (const [token, literal] of [['true', true], ['false', false], ['null', null]]) {
        if (text.startsWith(token, offset)) { offset += token.length; return literal }
      }
      const token = /^-?(?:0|[1-9]\d*)/.exec(text.slice(offset))?.[0]
      if (!token) throw 0
      offset += token.length
      const number = Number(token)
      if (!Number.isSafeInteger(number)) throw 0
      return number
    }
    result = value(0); whitespace()
    if (offset !== text.length) throw 0
  } catch { throw new ConfigurationBackendError() }
  return result
}

export async function readAgentHandoff(input = process.stdin) {
  // One bounded private line; keep stdin open afterwards for parent EOF.
  const line = await new Promise((resolve, reject) => {
    let data = Buffer.alloc(0)
    const stop = () => { clearTimeout(timer); input.off('data', receive); input.off('end', fail); input.off('error', fail); input.pause() }
    const fail = () => { stop(); reject(new ConfigurationBackendError()) }
    const receive = part => {
      data = Buffer.concat([data, part])
      if (data.length > 65536) return fail()
      const end = data.indexOf(10)
      if (end >= 0) {
        if (end !== data.length - 1) return fail()
        stop(); resolve(data.subarray(0, end))
      }
    }
    const timer = setTimeout(fail, 15000)
    input.on('data', receive); input.once('end', fail); input.once('error', fail)
  })
  let result
  try {
    const value = parseAgentMessage(line)
    if (Object.keys(value).sort().join(',') !== 'contract,launch,secret,token'
      || ['secret', 'token', 'launch'].some(key => typeof value[key] !== 'string' || !/^[0-9a-f]{64}$/.test(value[key]))) throw 0
    const contract = await validateBackendContract(value.contract)
    if (contract.backend !== 'sqlite') throw 0
    result = Object.freeze({ contract, secret: value.secret, token: value.token, launch: value.launch,
      toJSON() { throw new ConfigurationBackendError() }, [inspect.custom]: () => 'AgentHandoff(<private>)' })
  } catch { /* Never retain private parser/validation exceptions. */ }
  if (!result) throw new ConfigurationBackendError()
  return result
}

export function installAgentAuthentication(app, handoff, configuration, slug) {
  // Internal trusted composition. Agreement must describe the very runtime
  // serving search, not a caller-supplied public descriptor or environment.
  if (JSON.stringify(configuration.identity) !== JSON.stringify({ backend: handoff.contract.backend,
    decision_id: handoff.contract.decision_id, installation_id: handoff.contract.installation_id })) throw new ConfigurationBackendError()
  const equal = (a, b) => a.length === b.length && timingSafeEqual(a, b)
  // A monotonic high-water mark is constant-size replay state. Only an
  // authenticated request can advance it; no unauthenticated allocation.
  let lastSequence = 0, closed = false, sessions = new WeakMap()
  app.addContentTypeParser(AGENT_CONTENT_TYPE, { parseAs: 'buffer', bodyLimit: LIMIT }, (_request, raw, done) => {
    try { done(null, parseAgentMessage(raw)) } catch {
      const error = new ConfigurationBackendError()
      error.statusCode = 401
      done(error)
    }
  })
  app.post('/api/alphalab/configuration/proof', async (request, reply) => {
    const nonce = request.body?.nonce, sequence = request.body?.sequence, socket = request.raw?.socket
    if (closed || request.headers['content-type'] !== AGENT_CONTENT_TYPE || !socket || socket.destroyed
      || !Number.isSafeInteger(sequence) || sequence <= 0
      || typeof nonce !== 'string' || !/^[0-9a-f]{64}$/.test(nonce)
      || typeof request.body?.mac !== 'string' || !/^[0-9a-f]{64}$/.test(request.body.mac)
      || Object.keys(request.body).length !== 3) return reply.code(401).send({ error: 'Invalid agreement.' })
    const requestMac = createHmac('sha256', Buffer.from(handoff.secret, 'hex'))
      .update(JSON.stringify(['request', sequence, nonce, handoff.token])).digest('hex')
    if (!equal(Buffer.from(requestMac), Buffer.from(request.body.mac))) return reply.code(401).send({ error: 'Invalid agreement.' })
    if (sequence <= lastSequence) return reply.code(401).send({ error: 'Invalid agreement.' })
    // No await between authentication, sequencing and session publication.
    lastSequence = sequence
    sessions = new WeakMap() // At most one pending socket grant per launch.
    sessions.set(socket, sequence)
    const identity = { version: 1, ...handoff.contract, origin: app.listeningOrigin,
      slug, pid: process.pid, launch: handoff.launch, nonce, sequence }
    const mac = createHmac('sha256', Buffer.from(handoff.secret, 'hex'))
      .update(JSON.stringify([...Object.values(identity), handoff.token])).digest('hex')
    return { ...identity, mac }
  })
  app.post('/api/alphalab/search/test', async (request, reply) => {
    const expected = Buffer.from(`Bearer ${handoff.token}`), received = Buffer.from(request.headers.authorization || '')
    const socket = request.raw?.socket, sequence = sessions.get(socket)
    if (closed || request.headers['content-type'] !== AGENT_CONTENT_TYPE || !socket || socket.destroyed
      || sequence === undefined || request.headers['x-alphalab-proof-sequence'] !== String(sequence)
      || !equal(expected, received)) return reply.code(401).send({ error: 'Authentication required.' })
    sessions.delete(socket) // One use, consumed synchronously before provider work.
    try {
      return await configuration.testSearch(request.body, {
        input: { query: 'OpenRouter web search server tool documentation', count: 3 }, signal: AbortSignal.timeout(50000),
      })
    } catch { return reply.code(503).send({ error: 'Search test unavailable.' }) }
  })
  return Object.freeze({ close() { closed = true; sessions = new WeakMap() } })
}
