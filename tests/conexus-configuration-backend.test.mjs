import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile, writeFile, rename, rm } from 'node:fs/promises'
import { join, dirname, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { createInterface } from 'node:readline'
import { inspect } from 'node:util'
import { channel } from 'node:diagnostics_channel'
import { setTimeout as delay } from 'node:timers/promises'
import { createCoordinatedAgentConfiguration, validateBackendContract, ConfigurationBackendError } from '../integrations/conexus/configuration-backend.mjs'

const python = process.env.ALPHALAB_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
const bridge = fileURLToPath(new URL('./fixtures/configuration_backend_bridge.py', import.meta.url))
const KEYS = ['runtime-model-secret-4101', 'runtime-reference-secret-4102', 'runtime-brave-secret-4103', 'runtime-rq-secret-4104']
function noSecrets(value) {
  const text = inspect(value, { depth: Infinity }) + JSON.stringify(value) + (value?.stack || '')
  for (const key of KEYS) assert.ok(!text.includes(key))
}
async function unavailable(promise) {
  await assert.rejects(promise, error => {
    assert.ok(error instanceof ConfigurationBackendError)
    assert.equal(error.code, 'BACKEND_UNAVAILABLE')
    assert.equal(error.cause, undefined)
    noSecrets(error)
    return true
  })
}
async function fixture(t, mode = 'sqlite') {
  const base = await mkdtemp(join(tmpdir(), 'alphalab-backend-')), root = join(base, 'installation Ω')
  const child = spawn(python, ['-B', bridge, root, mode], { windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } })
  const lines = createInterface({ input: child.stdout }), iterator = lines[Symbol.asyncIterator]()
  let stderr = '', timer
  child.stderr.on('data', part => { stderr += part })
  child.stdin.on('error', () => {})
  const exited = new Promise(resolve => { child.once('error', () => resolve(-1)); child.once('close', resolve) })
  const next = async () => {
    try {
      return await Promise.race([iterator.next().then(row => { assert.ok(!row.done, 'Fixture exited'); return JSON.parse(row.value) }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Synthetic fixture timeout')), 15000) })])
    } finally { clearTimeout(timer) }
  }
  const runtimes = []
  t.after(async () => {
    await Promise.all(runtimes.map(runtime => runtime.close()))
    child.stdin.end()
    const forced = setTimeout(() => child.kill(), 5000)
    assert.equal(await exited, 0, 'Test-owned Python owner failed')
    clearTimeout(forced); lines.close(); noSecrets(stderr)
    assert.equal(dirname(base), resolve(tmpdir()))
    await rm(base, { recursive: true, force: true })
  })
  const { contract } = await next()
  const ask = async value => { child.stdin.write(JSON.stringify(value) + '\n'); return next() }
  const captures = [], fetches = []
  const options = { env: {}, fetchImpl: async (url, request) => {
    fetches.push({ url, request })
    return Response.json({ web: { results: [{ title: 'Source', url: 'https://source.example/article' }] } })
  } }
  const complete = profile => async () => { captures.push(profile); return { content: 'Synthetic completion' } }
  async function runtime(overrides = {}, choice = contract) {
    const value = await createCoordinatedAgentConfiguration(choice, complete, { ...options, ...overrides })
    runtimes.push(value)
    return value
  }
  return { root, contract, ask, runtime, captures, fetches, complete, options }
}

for (const mode of ['json', 'sqlite']) test(`${mode}: Python and Node agree on one choice, readiness and private capture`, async t => {
  const f = await fixture(t, mode), runtime = await f.runtime()
  assert.equal(runtime.backend, mode)
  assert.deepEqual(await f.ask({ op: 'verify', identity: runtime.identity }), { agreed: true })
  assert.equal(await runtime.modelConfigured(), true)
  const signal = new AbortController().signal
  const result = await runtime.complete({ model: 'ignored', messages: [], signal })
  assert.equal(result.content, 'Synthetic completion')
  assert.equal(f.captures[0].apiKey, KEYS[0])
  noSecrets(runtime.identity); noSecrets(result)
  const search = await runtime.webSearch({ input: { query: 'synthetic', count: 1 }, signal })
  assert.equal(search.result.success, true)
  assert.equal(f.fetches[0].request.headers['X-Subscription-Token'], KEYS[2])
})

test('SQLite has no JSON dependency; captures update only at independent first uses', async t => {
  const f = await fixture(t), runtime = await f.runtime(), run = new AbortController()
  // JSON files do not exist. A selected SQLite composition must never read them.
  await assert.rejects(readFile(join(f.root, 'conexus/secrets/model-providers.json')), { code: 'ENOENT' })
  await runtime.complete({ messages: [], signal: run.signal })
  await f.ask({ op: 'edit', profile: { id: 'model', name: 'Updated', base_url: 'https://openrouter.ai/api/v1', model: 'provider/updated' } })
  await runtime.complete({ messages: [], signal: run.signal })
  await runtime.complete({ messages: [], signal: new AbortController().signal })
  assert.equal(f.captures[0].model, f.captures[1].model)
  assert.notEqual(f.captures[1].model, f.captures[2].model)
  assert.equal(f.captures[2].apiKey, KEYS[0])
  await f.ask({ op: 'select', service: 'search', selection: { kind: 'none' } })
  const search = await runtime.webSearch({ input: { query: 'synthetic' }, signal: run.signal })
  assert.equal(search.result.code, 'search_disabled')
  assert.equal(f.fetches.length, 0)
})

test('none and cleared saved credentials cannot borrow supplied environment credentials', async t => {
  const f = await fixture(t)
  await f.ask({ op: 'select', selection: { kind: 'none' } })
  const runtime = await f.runtime({ env: { CONEXUS_MODEL_BASE_URL: 'https://openrouter.ai/api/v1',
    CONEXUS_MODEL_ID: 'env/model', CONEXUS_MODEL_API_KEY: 'synthetic-env-key' } })
  assert.equal(await runtime.modelConfigured(), false)
  await assert.rejects(runtime.complete({ messages: [] }))
  assert.equal(f.captures.length, 0)
  await f.ask({ op: 'edit', clear: true, profile: { id: 'model', name: 'Cleared',
    base_url: 'https://openrouter.ai/api/v1', model: 'provider/model-v0' } })
  assert.equal(await runtime.modelConfigured(), false)
  await assert.rejects(runtime.complete({ messages: [], signal: new AbortController().signal }))
  assert.equal(f.captures.length, 0)
})

test('missing, corrupt and mismatched SQLite fail before partial composition; no JSON fallback', async t => {
  const f = await fixture(t)
  await unavailable(f.runtime({}, { ...f.contract, installation_id: 'a'.repeat(32) }))
  const database = join(f.root, 'app/secrets/configuration-store.sqlite3')
  await rename(database, database + '.test-held')
  await unavailable(f.runtime())
  await assert.rejects(readFile(database), { code: 'ENOENT' })
  await rename(database + '.test-held', database)
  await writeFile(database, 'not SQLite')
  await unavailable(f.runtime())
  assert.equal(f.captures.length, 0)
  assert.deepEqual(await readFile(database, 'utf8'), 'not SQLite')
})

test('contract identities, root binding, unknown state and production SQLite activation fail closed', async t => {
  const f = await fixture(t), other = await fixture(t)
  const choices = [null, {}, { ...f.contract, backend: 'auto' }, { ...f.contract, version: 2 },
    { ...f.contract, extra: KEYS[0] }, { ...f.contract, decision_id: KEYS[0] },
    { ...f.contract, root: other.root }, { ...f.contract, root_key: 'a'.repeat(64) },
    new Proxy({}, { ownKeys() { throw new Error(KEYS[1]) } })]
  for (const value of choices) await unavailable(validateBackendContract(value))
  await unavailable(f.runtime({ production: true }))
  await unavailable(f.runtime({}, { ...f.contract, installation_id: other.contract.installation_id }))
})

test('cancellation resists suppression, concurrent requests and close release all owned Workers', async t => {
  const f = await fixture(t), workers = new Set()
  const observed = ({ worker }) => workers.add(worker), events = channel('worker_threads')
  events.subscribe(observed); t.after(() => events.unsubscribe(observed))
  const runtime = await f.runtime(), controller = new AbortController()
  controller.signal.addEventListener('abort', event => event.stopImmediatePropagation())
  const pending = runtime.complete({ messages: [], signal: controller.signal })
  controller.abort(new Error(KEYS[0]))
  await assert.rejects(pending, error => { noSecrets(error); assert.equal(error.code, 'RUNTIME_CANCELLED'); return true })
  await Promise.all(Array.from({ length: 3 }, () => runtime.modelConfigured()))
  const pendingReads = Array.from({ length: 3 }, () => runtime.modelConfigured())
  const settled = Promise.allSettled(pendingReads)
  await Promise.all([runtime.close(), runtime.close()]); await settled
  await delay(50)
  assert.ok(workers.size > 0)
  for (const worker of workers) assert.equal(worker.threadId, -1)
})

test('production entry refuses a SQLite handoff before attempting unavailable core startup', async t => {
  const f = await fixture(t)
  const entry = fileURLToPath(new URL('../integrations/conexus/local-host.mjs', import.meta.url))
  const child = spawn(process.execPath, [entry], { windowsHide: true, env: { ...process.env,
    ALPHALAB_AGENT_CONFIGURATION_DECISION: JSON.stringify(f.contract) }, stdio: ['ignore', 'pipe', 'pipe'] })
  let output = ''
  child.stdout.on('data', data => { output += data }); child.stderr.on('data', data => { output += data })
  const code = await new Promise(resolve => child.once('close', resolve))
  assert.notEqual(code, 0)
  assert.ok(output.includes('BACKEND_UNAVAILABLE'))
  assert.ok(!output.includes('ERR_MODULE_NOT_FOUND'))
  noSecrets(output)
})

test('production JSON entry advertises the exact Python decision using substituted core only', async t => {
  const f = await fixture(t, 'json')
  const core = `
    export const createLocalRuntime = () => ({ adapter: {}, close() {} });
    export const createModelCompletion = () => async () => ({ content: 'Synthetic' });
    export const compileHarnessWorkspace = () => ({});
    export const createLocalHost = async () => {
      const hooks = {};
      return { close: async () => hooks.onClose(), app: {
        listeningOrigin: 'http://127.0.0.1:18555', post() {},
        addHook(name, handler) { hooks[name] = handler },
        async listen() {
          console.log(JSON.stringify(await hooks.preSerialization({ routeOptions: { url: '/health' } }, {}, { ok: true })));
        }
      } };
    };
  `
  const script = `
    import { registerHooks } from 'node:module';
    registerHooks({ resolve(specifier, context, next) {
      if (specifier.endsWith('/build/conexus/apps/local-host/src/index.mjs')) return {
        url: ${JSON.stringify('data:text/javascript,' + encodeURIComponent(core))}, shortCircuit: true };
      if (specifier === './research-canvas.mjs') return {
        url: 'data:text/javascript,export const researchCanvas = async () => ({})', shortCircuit: true };
      return next(specifier, context);
    } });
    await import(${JSON.stringify(new URL('../integrations/conexus/local-host.mjs', import.meta.url).href)});
  `
  const connection = join(f.root, 'connection.json')
  const child = spawn(process.execPath, ['--input-type=module', '-e', script], { windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env,
      ALPHALAB_AGENT_CONFIGURATION_DECISION: JSON.stringify(f.contract),
      ALPHALAB_MODEL_PROVIDERS_FILE: join(f.root, 'conexus/secrets/model-providers.json'),
      CONEXUS_LOCAL_ROOT: join(f.root, 'conexus/project'), CONEXUS_LOCAL_TOKEN: 'synthetic-token',
      ALPHALAB_AGENT_CONNECTION_FILE: connection, CONEXUS_LOCAL_PORT: '18555' } })
  let output = '', diagnostic = ''
  child.stdout.on('data', data => { output += data }); child.stderr.on('data', data => { diagnostic += data })
  const stop = setTimeout(() => child.kill(), 15000)
  const code = await new Promise(resolve => child.once('close', resolve)); clearTimeout(stop)
  assert.equal(code, 0, diagnostic)
  const health = JSON.parse(output.split('\n').find(line => line.startsWith('{')))
  assert.equal(health.modelConfigured, true)
  assert.deepEqual(await f.ask({ op: 'verify', identity: health.configuration }), { agreed: true })
  const privateConnection = JSON.parse(await readFile(connection, 'utf8'))
  assert.deepEqual(privateConnection.configuration, health.configuration)
  noSecrets(health); noSecrets(privateConnection); noSecrets(output + diagnostic)
})
