import assert from 'node:assert/strict'
import { test } from 'node:test'
import { PassThrough } from 'node:stream'
import { mkdtemp, realpath, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createHmac } from 'node:crypto'
import { inspect } from 'node:util'
import { createServer, request as httpRequest, Agent } from 'node:http'
import { configurationRootKey } from '../integrations/conexus/configuration-store-worker.mjs'
import { readAgentHandoff, installAgentAuthentication, parseAgentMessage, AGENT_CONTENT_TYPE } from '../integrations/conexus/configuration-agent-auth.mjs'

const SECRET = 'a1'.repeat(32), TOKEN = 'b2'.repeat(32), LAUNCH = 'c3'.repeat(32)
async function fixture(t, change = {}, edit = value => value, read = readAgentHandoff) {
  const directory = await mkdtemp(join(tmpdir(), 'alphalab-auth-')), root = await realpath(directory)
  t.after(() => rm(directory, { recursive: true, force: true }))
  const contract = { version: 1, root, root_key: configurationRootKey(root), backend: 'sqlite', decision_id: 'd'.repeat(32), installation_id: 'e'.repeat(32) }
  const value = { contract, secret: SECRET, token: TOKEN, launch: LAUNCH, ...change }
  const pipe = new PassThrough(), pending = read(pipe)
  pipe.end(edit(JSON.stringify(value)) + '\n')
  return { contract, pending }
}
function signed(sequence, nonce = 'f'.repeat(64)) {
  return { sequence, nonce, mac: createHmac('sha256', Buffer.from(SECRET, 'hex'))
    .update(JSON.stringify(['request', sequence, nonce, TOKEN])).digest('hex') }
}
async function endpoint(t, install = installAgentAuthentication) {
  const { pending, contract } = await fixture(t), handoff = await pending, routes = new Map()
  let parser, calls = 0
  const server = createServer(async (request, response) => {
    try {
      const parts = []; let size = 0
      for await (const part of request) { size += part.length; if (size > 65536) throw 0; parts.push(part) }
      const body = await new Promise((resolve, reject) => parser(request, Buffer.concat(parts), (error, value) => error ? reject(error) : resolve(value)))
      let status = 200
      const reply = { code(value) { status = value; return this }, send(value) { return value } }
      const result = await routes.get(request.url)({ body, headers: request.headers, raw: request }, reply)
      response.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(result))
    } catch { response.writeHead(401).end('{}') }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const app = { listeningOrigin: `http://127.0.0.1:${server.address().port}`,
    post(path, handler) { routes.set(path, handler) },
    addContentTypeParser(type, options, handler) {
      assert.equal(type, AGENT_CONTENT_TYPE); assert.equal(options.bodyLimit, 65536); assert.equal(options.parseAs, 'buffer'); parser = handler
    } }
  const configuration = { identity: { backend: 'sqlite', decision_id: contract.decision_id, installation_id: contract.installation_id },
    async testSearch() { calls++; return { result: { success: true } } } }
  const authentication = install(app, handoff, configuration, 'alphalab-research-agent')
  t.after(async () => { authentication.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) })
  function client() {
    const agent = new Agent({ keepAlive: true, maxSockets: 1 })
    t.after(() => agent.destroy())
    async function send(path, body, headers = {}) {
      const wire = typeof body === 'string' ? body : JSON.stringify(body)
      return new Promise((resolve, reject) => {
        const request = httpRequest(app.listeningOrigin + path, { method: 'POST', agent, timeout: 2000,
          headers: { 'Content-Type': AGENT_CONTENT_TYPE, 'Content-Length': Buffer.byteLength(wire), ...headers } }, response => {
          let text = ''; response.on('data', part => { text += part }); response.on('end', () => resolve({ status: response.statusCode, body: JSON.parse(text) }))
        })
        request.on('error', reject); request.on('timeout', () => request.destroy()); request.end(wire)
      })
    }
    return { proof: body => send('/api/alphalab/configuration/proof', body), send,
      search: sequence => send('/api/alphalab/search/test', { provider: 'off' }, {
        Authorization: `Bearer ${TOKEN}`, 'X-AlphaLab-Proof-Sequence': String(sequence),
      }) }
  }
  return { contract, client, close: authentication.close, calls: () => calls }
}
function noSecrets(value) {
  const text = JSON.stringify(value) + inspect(value) + (value?.stack || '')
  for (const key of [SECRET, TOKEN, LAUNCH]) assert.ok(!text.includes(key))
}

test('private handoff validates root/identity and cannot serialize secrets', async t => {
  const { pending } = await fixture(t), value = await pending
  assert.equal(value.contract.backend, 'sqlite')
  assert.ok(Object.isFrozen(value)); noSecrets(inspect(value))
  assert.throws(() => JSON.stringify(value), error => { noSecrets(error); return true })
})

for (const change of [{ token: 'synthetic-private-canary' }, { contract: {} }, { extra: TOKEN }, { secret: null }]) {
  test('invalid private handoff fails with static unchained error', async t => {
    const { pending } = await fixture(t, change)
    await assert.rejects(pending, error => { noSecrets(error); assert.equal(error.code, 'BACKEND_UNAVAILABLE'); assert.equal(error.cause, undefined); return true })
  })
}

test('proof requires private authentication and signs full actual endpoint/process identity', async t => {
  const f = await endpoint(t), client = f.client(), challenge = signed(1)
  const denied = await client.proof({ nonce: challenge.nonce })
  assert.equal(denied.status, 401); noSecrets(denied.body)
  const result = await client.proof(challenge)
  assert.equal(result.status, 200)
  const expected = { version: 1, ...f.contract, origin: result.body.origin, slug: 'alphalab-research-agent', pid: process.pid, launch: LAUNCH,
    nonce: challenge.nonce, sequence: 1 }
  assert.deepEqual(result.body, { ...expected, mac: createHmac('sha256', Buffer.from(SECRET, 'hex')).update(JSON.stringify([...Object.values(expected), TOKEN])).digest('hex') })
  assert.equal((await client.search(1)).status, 200); assert.equal(f.calls(), 1)
})

test('proof is one-use across sockets and private dispatch is one-use on its own socket', async t => {
  const f = await endpoint(t), a = f.client(), b = f.client(), proof = signed(1)
  assert.equal((await a.proof(proof)).status, 200)
  assert.equal((await b.proof(proof)).status, 401)
  assert.equal((await b.search(1)).status, 401)
  assert.equal((await a.search(1)).status, 200)
  assert.equal((await a.search(1)).status, 401)
  assert.equal((await a.proof(signed(2))).status, 200)
  assert.equal((await a.search(2)).status, 200)
  assert.equal(f.calls(), 2)
})

test('concurrent identical proofs have exactly one winner', async t => {
  const f = await endpoint(t), a = f.client(), b = f.client()
  const results = await Promise.all([a.proof(signed(1)), b.proof(signed(1))])
  assert.deepEqual(results.map(result => result.status).sort(), [200, 401])
})

test('a fresh proof replaces the sole pending grant, including on a different socket', async t => {
  const f = await endpoint(t), a = f.client(), b = f.client()
  assert.equal((await a.proof(signed(1))).status, 200)
  assert.equal((await b.proof(signed(2))).status, 200)
  assert.equal((await a.search(1)).status, 401)
  assert.equal((await b.search(2)).status, 200)
  assert.equal(f.calls(), 1)
})

test('wrong MAC cannot advance replay state; close invalidates existing socket authorization', async t => {
  const f = await endpoint(t), client = f.client()
  assert.equal((await client.proof({ ...signed(9999), mac: '0'.repeat(64) })).status, 401)
  assert.equal((await client.proof(signed(1))).status, 200)
  f.close(); f.close()
  assert.equal((await client.search(1)).status, 401)
  assert.equal((await client.proof(signed(2))).status, 401)
  assert.equal(f.calls(), 0)
})

test('replay high-water mark survives many sessions without a nonce cache', async t => {
  const f = await endpoint(t), client = f.client()
  for (let sequence = 1; sequence <= 32; sequence++) assert.equal((await client.proof(signed(sequence))).status, 200)
  assert.equal((await f.client().proof(signed(1))).status, 401)
  assert.equal((await client.search(1)).status, 401)
  assert.equal((await client.search(32)).status, 200)
})

for (const wire of ['{"pid":1,"pid":2}', '{"pid":1,"\\u0070id":2}', '{"nested":{"nonce":1,"nonce":2}}',
  '{"sequence":1.0}', '{"sequence":1e0}', '{"n":NaN}', '{"n":Infinity}', '{"n":9007199254740992}', '{', '[[]', '[] true']) {
  test('strict authentication grammar rejects ambiguous or malformed input', () => {
    assert.throws(() => parseAgentMessage(wire), error => { noSecrets(error); assert.equal(error.code, 'BACKEND_UNAVAILABLE'); return true })
  })
}

for (const key of ['token', '\\u0074oken']) test('private handoff rejects duplicate decoded token keys', async t => {
  const { pending } = await fixture(t, {}, value => value.replace('"token":', `"${key}":"bad","token":`))
  await assert.rejects(pending, { code: 'BACKEND_UNAVAILABLE' })
})

for (const value of ['true', '1.0', '"1"']) test('handoff protocol version requires an integer', async t => {
  const { pending } = await fixture(t, {}, wire => wire.replace('"version":1', `"version":${value}`))
  await assert.rejects(pending, { code: 'BACKEND_UNAVAILABLE' })
})

for (const edit of [value => ({ ...value, sequence: true }), value => ({ ...value, sequence: '1' }),
  value => ({ ...value, sequence: {} }), value => ({ ...value, extra: true }), value => ({ nonce: value.nonce, mac: value.mac })]) {
  test('proof fields require exact identities and types without consuming sequence', async t => {
    const f = await endpoint(t), client = f.client()
    assert.equal((await client.proof(edit(signed(1)))).status, 401)
    assert.equal((await client.proof(signed(1))).status, 200)
  })
}

for (const key of ['nonce', '\\u006eonce', 'mac', 'sequence']) test('raw proof duplicates are rejected before authentication', async t => {
  const f = await endpoint(t), client = f.client(), body = JSON.stringify(signed(1))
  assert.equal((await client.proof(`{"${key}":null,` + body.slice(1))).status, 401)
  assert.equal((await client.proof(signed(1))).status, 200)
})

test('valid escaping and nested values parse; bounds and UTF-8 are enforced', () => {
  assert.equal(parseAgentMessage('{"text":"literal \\"nonce\\":{}","nested":[1,true,null]}').nested[0], 1)
  for (const raw of [' '.repeat(65537), '['.repeat(10) + '0' + ']'.repeat(10), Buffer.from([0xff])]) {
    assert.throws(() => parseAgentMessage(raw), { code: 'BACKEND_UNAVAILABLE' })
  }
})

for (const number of ['true', '1.0', '1e0', '"1"']) test('raw integer proof fields cannot be coerced', async t => {
  const f = await endpoint(t), client = f.client()
  assert.equal((await client.proof(JSON.stringify(signed(1)).replace('"sequence":1', `"sequence":${number}`))).status, 401)
  assert.equal((await client.proof(signed(1))).status, 200)
})

test('ordinary JSON content type cannot bypass raw authentication parsing', async t => {
  const f = await endpoint(t), client = f.client()
  assert.equal((await client.send('/api/alphalab/configuration/proof', signed(1), { 'Content-Type': 'application/json' })).status, 401)
  assert.equal((await client.proof(signed(1))).status, 200)
})

async function alteredModule(before, after) {
  const url = new URL('../integrations/conexus/configuration-agent-auth.mjs', import.meta.url)
  const source = await readFile(url, 'utf8')
  assert.ok(source.includes(before))
  const changed = source.replace(before, after).replace("'./configuration-backend.mjs'", JSON.stringify(new URL('./configuration-backend.mjs', url).href))
  return import('data:text/javascript,' + encodeURIComponent(changed))
}

test('negative control: ordinary JSON parser permits the duplicate-token regression', async t => {
  const altered = await alteredModule('const value = parseAgentMessage(line)', 'const value = JSON.parse(line.toString())')
  const { pending } = await fixture(t, {}, wire => wire.replace('"token":', '"token":"bad","token":'), altered.readAgentHandoff)
  const result = await pending
  assert.throws(() => assert.equal(result.token === TOKEN, false), { code: 'ERR_ASSERTION' })
  const corrected = await fixture(t, {}, wire => wire.replace('"token":', '"token":"bad","token":'))
  await assert.rejects(corrected.pending, { code: 'BACKEND_UNAVAILABLE' })
})

test('negative control: bypassing replay consumption admits the identical second-socket proof', async t => {
  const altered = await alteredModule('if (sequence <= lastSequence)', 'if (false)')
  const f = await endpoint(t, altered.installAgentAuthentication), a = f.client(), b = f.client()
  assert.equal((await a.proof(signed(1))).status, 200)
  const replay = await b.proof(signed(1))
  assert.throws(() => assert.equal(replay.status, 401), { code: 'ERR_ASSERTION' })
  assert.equal(replay.status, 200)
  const corrected = await endpoint(t), first = corrected.client(), second = corrected.client()
  assert.equal((await first.proof(signed(1))).status, 200)
  assert.equal((await second.proof(signed(1))).status, 401)
})

test('auth cannot describe a different runtime composition', async t => {
  const { pending } = await fixture(t), value = await pending
  assert.throws(() => installAgentAuthentication({}, value, { identity: { backend: 'json' } }, 'alphalab-research-agent'), error => {
    noSecrets(error); assert.equal(error.code, 'BACKEND_UNAVAILABLE'); return true
  })
})
