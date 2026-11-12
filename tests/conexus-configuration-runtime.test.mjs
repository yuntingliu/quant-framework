import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises'
import { join, dirname, basename, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { inspect } from 'node:util'
import { channel } from 'node:diagnostics_channel'
import { getEventListeners } from 'node:events'
import { setTimeout as delay } from 'node:timers/promises'
import { ConfigurationStoreReader } from '../integrations/conexus/configuration-store.mjs'
import { withConfigurationAuthority } from '../integrations/conexus/configuration-authority.mjs'
import { createJsonAgentConfiguration, createSqliteAgentConfiguration, ConfigurationRuntimeError } from '../integrations/conexus/configuration-runtime.mjs'

const python = process.env.ALPHALAB_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
const bridge = fileURLToPath(new URL('./fixtures/configuration_runtime_bridge.py', import.meta.url))
const KEYS = ['runtime-model-secret-4101', 'runtime-reference-secret-4102', 'runtime-brave-secret-4103', 'runtime-rq-secret-4104',
  'runtime-environment-model-4105', 'runtime-environment-brave-4106', 'runtime-rotated-key-4107']
const environment = { CONEXUS_MODEL_BASE_URL: 'https://openrouter.ai/api/v1', CONEXUS_MODEL_ID: 'provider/env-model',
  CONEXUS_MODEL_API_KEY: KEYS[4], BRAVE_SEARCH_API_KEY: KEYS[5] }
const model = (changes = {}) => ({ id: 'model', name: 'Model', base_url: 'https://openrouter.ai/api/v1',
  model: 'provider/model-v0', api_key: KEYS[0], ...changes })
const search = (changes = {}) => ({ id: 'search', name: 'Search', provider: 'brave', model_profile_id: 'alternate', brave_api_key: KEYS[2], ...changes })
const params = signal => ({ input: { query: 'synthetic research', count: 2 }, ...(signal ? { signal } : {}) })

function noSecrets(value) {
  const visible = inspect(value, { depth: Infinity }) + JSON.stringify(value) + String(value?.stack || '')
  for (const key of KEYS) assert.ok(!visible.includes(key), 'A private synthetic credential escaped.')
}
async function rejected(promise, code) {
  let caught
  try { await promise } catch (error) { caught = error }
  assert.ok(caught instanceof ConfigurationRuntimeError)
  if (code) assert.equal(caught.code, code)
  assert.equal(caught.cause, undefined)
  noSecrets(caught)
}
function fakeDependencies() {
  const completions = [], fetches = []
  return {
    completions, fetches,
    createModelCompletion: profile => async request => {
      completions.push({ profile, model: request.model })
      return { content: 'Synthetic completion', model: request.model }
    },
    fetchImpl: async (url, options) => {
      fetches.push({ url: String(url), ...options })
      return String(url).includes('api.search.brave.com')
        ? Response.json({ web: { results: [{ title: 'Source', url: 'https://source.example/article', description: 'Evidence' }] } })
        : Response.json({ usage: { server_tool_use: { web_search_requests: 1 } }, choices: [{ message: { annotations: [
          { type: 'url_citation', url_citation: { title: 'Source', url: 'https://source.example/article', content: 'Evidence' } },
        ] } }] })
    },
  }
}
async function fixture(t, initialize = true) {
  const base = await mkdtemp(join(tmpdir(), 'alphalab-runtime-store-')), root = join(base, 'installation Ω')
  const runtimes = [], children = []
  function launch(command, value = {}) {
    const child = spawn(python, ['-B', bridge, root, command], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } })
    let stdout = '', stderr = ''
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8')
    child.stdout.on('data', part => { stdout += part })
    child.stderr.on('data', part => { stderr += part })
    child.stdin.on('error', () => {})
    const closed = new Promise(resolve => { child.once('error', () => resolve(-1)); child.once('close', resolve) })
    child.stdin.write(JSON.stringify(value) + '\n')
    if (command !== 'lock') child.stdin.end()
    const owned = { child, closed, get stdout() { return stdout }, get stderr() { return stderr },
      async ready() {
        const deadline = Date.now() + 15000
        while (!stdout.includes('"ready": true')) { assert.ok(Date.now() < deadline && child.exitCode === null); await delay(20) }
      } }
    children.push(owned)
    return owned
  }
  async function run(command, value) {
    const owned = launch(command, value)
    assert.equal(await owned.closed, 0, 'Synthetic Python fixture failed.')
    noSecrets(owned.stdout + owned.stderr)
    return JSON.parse(owned.stdout.trim().split('\n').at(-1))
  }
  function runtime(dependencies = fakeDependencies(), env = environment) {
    const result = createSqliteAgentConfiguration(root, dependencies.createModelCompletion, { env, fetchImpl: dependencies.fetchImpl })
    runtimes.push(result)
    return result
  }
  t.after(async () => {
    await Promise.all(runtimes.map(runtime => runtime.close()))
    for (const owned of children) {
      if (owned.child.exitCode === null && owned.child.signalCode === null) owned.child.kill()
      owned.child.stdin.destroy()
      await owned.closed
    }
    assert.equal(dirname(base), resolve(tmpdir()))
    assert.ok(basename(base).startsWith('alphalab-runtime-store-'))
    await rm(base, { recursive: true, force: true })
  })
  if (initialize) await run('init')
  return { base, root, run, launch, runtime, runtimes, database: join(root, 'app/secrets/configuration-store.sqlite3') }
}
function observedWorkers(t) {
  const workers = new Set(), events = channel('worker_threads'), observe = ({ worker }) => workers.add(worker)
  events.subscribe(observe)
  t.after(() => events.unsubscribe(observe))
  return workers
}

test('JSON composition remains the default and ignores unrelated SQLite/backend inputs', async t => {
  const f = await fixture(t), dependencies = fakeDependencies(), workers = observedWorkers(t)
  const path = join(f.root, 'model-providers.json')
  await writeFile(path, JSON.stringify({ version: 1, active_id: 'model', profiles: [model({ model: 'provider/json-model' })] }))
  await writeFile(join(f.root, 'search-providers.json'), JSON.stringify({ version: 1, provider: 'off' }))
  const before = await readFile(f.database)
  const runtime = createJsonAgentConfiguration(path, dependencies.createModelCompletion,
    { env: { ...environment, ALPHALAB_CONFIGURATION_BACKEND: 'sqlite' }, fetchImpl: dependencies.fetchImpl })
  assert.equal(runtime.backend, 'json')
  assert.equal(await runtime.modelConfigured(), true)
  assert.equal((await runtime.complete({ signal: new AbortController().signal })).model, 'provider/json-model')
  assert.equal((await runtime.webSearch(params())).result.code, 'search_disabled')
  await runtime.close()
  assert.deepEqual(await readFile(f.database), before)
  assert.equal(workers.size, 0)
})

test('the actual Agent entry point keeps JSON startup, readiness and shutdown wiring', async t => {
  const f = await fixture(t), secrets = join(f.root, 'conexus/secrets'), path = join(secrets, 'model-providers.json')
  await mkdir(secrets, { recursive: true })
  await writeFile(f.database, 'synthetic unavailable SQLite store')
  await writeFile(join(secrets, 'search-providers.json'), JSON.stringify({ version: 1, provider: 'off' }))
  // Substitute only the optional bundled core and research workspace. Execute
  // the real entry point and real JSON composition without a server/provider.
  const core = `
    let capability, runtimeClosed = false;
    export const createLocalRuntime = options => {
      capability = options.capabilities['web.search'];
      return { adapter: {}, close() { runtimeClosed = true } };
    };
    export const createModelCompletion = profile => async () => ({ content: profile.model });
    export const compileHarnessWorkspace = () => ({});
    export const createLocalHost = async options => {
      const hooks = {}, routes = {};
      const close = async () => { await hooks.onClose() };
      return { close, app: {
        listeningOrigin: 'http://127.0.0.1:18555',
        post(path, handler) { routes[path] = handler },
        addHook(name, handler) { hooks[name] = handler },
        async listen() {
          const health = await hooks.preSerialization({ routeOptions: { url: '/health' } }, {}, { ok: true });
          let ready = true;
          try { await hooks.preHandler({ method: 'POST', routeOptions: { url: '/api/public/harnesses/alphalab-research-agent/runs' } }) }
          catch (error) { if (error.statusCode !== 503) throw error; ready = false }
          const model = ready ? (await options.complete({ signal: new AbortController().signal })).content : null;
          const search = await capability({ input: { query: 'synthetic query' } });
          let status;
          await routes['/api/alphalab/search/test']({ headers: {}, body: { provider: 'off' } }, {
            code(value) { status = value; return this }, send() {}
          });
          await close();
          console.log(JSON.stringify({ health, ready, model, searchCode: search.result.code, status, runtimeClosed }));
        }
      } };
    };
  `
  const script = `
    import { registerHooks } from 'node:module';
    const core = ${JSON.stringify('data:text/javascript,' + encodeURIComponent(core))};
    const workspace = 'data:text/javascript,export const researchCanvas = async () => ({})';
    registerHooks({ resolve(specifier, context, next) {
      if (specifier.endsWith('/build/conexus/apps/local-host/src/index.mjs')) return { url: core, shortCircuit: true };
      if (specifier === './research-canvas.mjs') return { url: workspace, shortCircuit: true };
      return next(specifier, context);
    } });
    await import(${JSON.stringify(new URL('../integrations/conexus/local-host.mjs', import.meta.url).href)});
  `
  for (const configured of [true, false]) {
    await writeFile(path, JSON.stringify({ version: 1, active_id: configured ? 'model' : null,
      profiles: configured ? [model({ model: 'provider/json-startup' })] : [] }))
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(CONEXUS_|ALPHALAB_|BRAVE_|RQ_)/i.test(key)))
    const child = spawn(process.execPath, ['--input-type=module', '-e', script], { windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'], env: { ...env,
        ALPHALAB_MODEL_PROVIDERS_FILE: path, CONEXUS_LOCAL_ROOT: join(f.root, 'conexus/project'),
        ALPHALAB_AUTHORITY_PYTHON: python,
        CONEXUS_LOCAL_TOKEN: 'synthetic-entry-token', CONEXUS_LOCAL_PORT: '18555',
        ALPHALAB_CONFIGURATION_BACKEND: 'sqlite',
        ALPHALAB_AGENT_CONNECTION_FILE: join(f.root, 'connection.json'),
      } })
    let stdout = '', stderr = ''
    child.stdout.on('data', part => { stdout += part }); child.stderr.on('data', part => { stderr += part })
    const closed = new Promise(resolve => { child.once('error', () => resolve(-1)); child.once('close', resolve) })
    const timer = setTimeout(() => child.kill(), 20000) // Only this test-owned child.
    let exit
    try { exit = await closed } finally { clearTimeout(timer) }
    noSecrets(stdout + stderr)
    assert.equal(exit, 0, 'Synthetic Agent entry point failed.')
    const report = JSON.parse(stdout.split('\n').find(line => line.startsWith('{')))
    assert.equal(report.health.modelConfigured, configured)
    assert.equal(report.ready, configured)
    assert.equal(report.model, configured ? 'provider/json-startup' : null)
    assert.equal(report.searchCode, 'search_disabled')
    assert.equal(report.status, 401)
    assert.equal(report.runtimeClosed, true)
  }
  assert.equal(await readFile(f.database, 'utf8'), 'synthetic unavailable SQLite store')
})

test('SQLite model captures once per run; profile changes affect only subsequent runs', async t => {
  const f = await fixture(t), d = fakeDependencies(), runtime = f.runtime(d), run = new AbortController().signal
  await writeFile(join(f.root, 'model-providers.json'), JSON.stringify({ active_id: 'poison', profiles: [{ id: 'poison', model: 'wrong', api_key: 'wrong' }] }))
  assert.equal((await runtime.complete({ signal: run })).model, 'provider/model-v0')
  await f.run('edit', { profiles: { models: model({ model: 'provider/model-v1', api_key: KEYS[6] }) } })
  assert.equal((await runtime.complete({ signal: run })).model, 'provider/model-v0')
  assert.equal((await runtime.complete({ signal: new AbortController().signal })).model, 'provider/model-v1')
  assert.deepEqual(d.completions.map(call => call.profile.apiKey), [KEYS[0], KEYS[0], KEYS[6]])
  noSecrets(runtime)
})

test('parallel first model calls use one real SQLite capture', async t => {
  const f = await fixture(t), d = fakeDependencies(), runtime = f.runtime(d), workers = observedWorkers(t)
  const signal = new AbortController().signal
  const results = await Promise.all(Array.from({ length: 4 }, () => runtime.complete({ signal })))
  assert.ok(results.every(result => result.model === 'provider/model-v0'))
  assert.equal(workers.size, 1)
  await runtime.close()
  assert.ok([...workers].every(worker => worker.threadId === -1))
})

test('model and search retain their separate first-use selection timing', async t => {
  const f = await fixture(t), d = fakeDependencies(), runtime = f.runtime(d), signal = new AbortController().signal
  await runtime.complete({ signal })
  await f.run('edit', { profiles: { models: model({ model: 'provider/model-v1', api_key: KEYS[6] }),
    search: search({ provider: 'openrouter', model_profile_id: 'model' }) } })
  noSecrets(await runtime.webSearch(params(signal)))
  await f.run('edit', { profiles: { models: model({ model: 'provider/model-v2' }), search: search({ brave_api_key: KEYS[5] }) } })
  assert.equal((await runtime.complete({ signal })).model, 'provider/model-v0')
  await runtime.webSearch(params(signal))
  await runtime.webSearch(params(new AbortController().signal))
  assert.equal(JSON.parse(d.fetches[0].body).model, 'provider/model-v1')
  assert.equal(JSON.parse(d.fetches[1].body).model, 'provider/model-v1')
  assert.equal(d.fetches[2].headers['X-Subscription-Token'], KEYS[5])
})

test('search and its inactive referenced model use exactly one generation despite a publication', async t => {
  const f = await fixture(t), d = fakeDependencies(), runtime = f.runtime(d)
  await f.run('edit', { profiles: { search: search({ provider: 'openrouter' }) }, selections: { models: { kind: 'none' } } })
  const read = ConfigurationStoreReader.prototype.readSnapshot
  let reads = 0
  t.mock.method(ConfigurationStoreReader.prototype, 'readSnapshot', async function(options) {
    const snapshot = await read.call(this, options)
    if (++reads === 1) await f.run('edit', { profiles: { models: model({ id: 'alternate', model: 'provider/new-reference', api_key: KEYS[6] }) }, activate: false })
    return snapshot
  })
  const response = await runtime.webSearch(params(new AbortController().signal))
  assert.equal(response.result.success, true)
  assert.equal(reads, 1)
  assert.equal(JSON.parse(d.fetches[0].body).model, 'provider/alternate-v0')
  assert.equal(d.fetches[0].headers.Authorization, 'Bearer ' + KEYS[1])
  noSecrets(response)
})

test('concurrent Python publications produce complete SQLite search/model generations', async t => {
  const f = await fixture(t), d = fakeDependencies(), runtime = f.runtime(d)
  await f.run('edit', { profiles: { search: search({ provider: 'openrouter', model_profile_id: 'model' }) } })
  // Match the production composition's shared authority lifetime. Eight
  // independent Python-owner startups test the startup deadline, not snapshot
  // consistency. The real lease remains enforced for all eight concurrent reads.
  await withConfigurationAuthority(f.root, 'inactive_sqlite', async () => {
    const writer = f.launch('loop', { count: 30 })
    await writer.ready()
    const responses = await Promise.all(Array.from({ length: 8 }, () => runtime.webSearch(params(new AbortController().signal))))
    assert.equal(await writer.closed, 0)
    assert.ok(responses.every(response => response.result.success))
    assert.equal(d.fetches.length, 8)
    for (const call of d.fetches) {
      const generation = Number(JSON.parse(call.body).model.split('-v').at(-1))
      assert.equal(call.headers.Authorization, 'Bearer ' + (generation ? `runtime-model-key-${generation}` : KEYS[0]))
    }
    for (const response of responses) noSecrets(response)
  })
})

for (const kind of ['none', 'environment', 'cleared', 'local', 'saved-environment-id']) {
  test(`SQLite model selection ${kind} never borrows saved/environment credentials`, async t => {
    const f = await fixture(t), d = fakeDependencies()
    if (kind === 'none' || kind === 'environment') await f.run('edit', { selections: { models: { kind } } })
    if (kind === 'cleared') await f.run('edit', { profiles: { models: model({ api_key: '' }) }, clear: ['models'] })
    if (kind === 'local') await f.run('edit', { profiles: { models: model({ base_url: 'http://localhost:8000/v1', api_key: '' }) } })
    if (kind === 'saved-environment-id') await f.run('edit', { profiles: { models: model({ id: 'environment' }) } })
    const runtime = f.runtime(d)
    if (kind === 'none' || kind === 'cleared') {
      assert.equal(await runtime.modelConfigured(), false)
      await rejected(runtime.complete({ signal: new AbortController().signal }), 'MODEL_UNAVAILABLE')
      assert.equal(d.completions.length, 0)
    } else {
      assert.equal(await runtime.modelConfigured(), true)
      await runtime.complete({ signal: new AbortController().signal })
      assert.equal(d.completions[0].profile.apiKey, kind === 'environment' ? KEYS[4] : kind === 'local' ? '' : KEYS[0])
    }
  })
}

for (const kind of ['none', 'environment', 'off', 'cleared', 'incomplete-reference', 'missing-reference']) {
  test(`SQLite search selection ${kind} stays isolated`, async t => {
    const f = await fixture(t), d = fakeDependencies()
    if (kind === 'none' || kind === 'environment') await f.run('edit', { selections: { search: { kind } } })
    if (kind === 'off') await f.run('edit', { profiles: { search: search({ provider: 'off' }) } })
    if (kind === 'cleared') await f.run('edit', { profiles: { search: search({ brave_api_key: '' }) }, clear: ['search'] })
    if (kind === 'incomplete-reference') {
      await f.run('edit', { profiles: { search: search({ provider: 'openrouter' }) } })
      await f.run('edit', { profiles: { models: model({ id: 'alternate', api_key: '' }) }, clear: ['models'], activate: false })
    }
    if (kind === 'missing-reference') await f.run('patch', { search: { provider: 'openrouter', model_profile_id: 'missing' } })
    const result = await f.runtime(d).webSearch(params(new AbortController().signal))
    if (kind === 'environment') {
      assert.equal(result.result.success, true)
      assert.equal(d.fetches[0].headers['X-Subscription-Token'], KEYS[5])
    } else {
      assert.equal(result.result.code, kind === 'none' || kind === 'off' ? 'search_disabled' : 'search_not_configured')
      assert.equal(d.fetches.length, 0)
    }
    noSecrets(result)
  })
}

test('explicit search connection overrides remain private and never mutate saved selection', async t => {
  const f = await fixture(t), d = fakeDependencies(), runtime = f.runtime(d)
  await f.run('edit', { selections: { models: { kind: 'none' }, search: { kind: 'none' } } })
  const before = await readFile(f.database)
  assert.equal((await runtime.testSearch({ provider: 'brave', brave_api_key: '' }, params())).result.code, 'search_not_configured')
  assert.equal((await runtime.testSearch({ provider: 'off' }, params())).result.code, 'search_disabled')
  noSecrets(await runtime.testSearch({ provider: 'openrouter', model_profile_id: 'alternate' }, params()))
  noSecrets(await runtime.testSearch({ provider: 'brave', brave_api_key: KEYS[6] }, params()))
  assert.equal(d.fetches[0].headers.Authorization, 'Bearer ' + KEYS[1])
  assert.equal(d.fetches[1].headers['X-Subscription-Token'], KEYS[6])
  assert.deepEqual(await readFile(f.database), before)
})

test('two installations have independent captures even for the same run signal', async t => {
  const a = await fixture(t), b = await fixture(t), first = fakeDependencies(), second = fakeDependencies()
  await b.run('edit', { profiles: { models: model({ model: 'provider/other', api_key: KEYS[6] }) } })
  const signal = new AbortController().signal
  assert.equal((await a.runtime(first).complete({ signal })).model, 'provider/model-v0')
  assert.equal((await b.runtime(second).complete({ signal })).model, 'provider/other')
  assert.equal(first.completions[0].profile.apiKey, KEYS[0])
  assert.equal(second.completions[0].profile.apiKey, KEYS[6])
})

for (const missing of [true, false]) test(`unavailable SQLite (${missing ? 'missing' : 'corrupt'}) fails without JSON fallback`, async t => {
  const f = await fixture(t, !missing), d = fakeDependencies()
  await mkdir(f.root, { recursive: true })
  await writeFile(join(f.root, 'model-providers.json'), JSON.stringify({ active_id: 'model', profiles: [model()] }))
  if (!missing) await writeFile(f.database, 'corrupt synthetic database')
  const runtime = f.runtime(d)
  await rejected(runtime.modelConfigured(), 'CONFIGURATION_UNAVAILABLE')
  await rejected(runtime.complete({ signal: new AbortController().signal }), 'MODEL_UNAVAILABLE')
  assert.equal((await runtime.webSearch(params())).result.success, false)
  assert.equal(d.fetches.length + d.completions.length, 0)
  if (missing) await assert.rejects(readFile(f.database), { code: 'ENOENT' })
  else assert.equal(await readFile(f.database, 'utf8'), 'corrupt synthetic database')
})

test('cancellation suppression and repeated close release pending reader Workers', async t => {
  const f = await fixture(t), runtime = f.runtime(), workers = observedWorkers(t)
  const locker = f.launch('lock'); await locker.ready()
  const controller = new AbortController()
  controller.signal.addEventListener('abort', event => event.stopImmediatePropagation(), { once: true })
  const pending = runtime.complete({ signal: controller.signal })
  const until = Date.now() + 5000
  while (!workers.size) { assert.ok(Date.now() < until); await delay(5) }
  controller.abort(new Error(KEYS[0]))
  await rejected(pending, 'RUNTIME_CANCELLED')
  const a = runtime.close(), b = runtime.close()
  assert.equal(a, b)
  await a
  assert.ok([...workers].every(worker => worker.threadId === -1))
  assert.equal(getEventListeners(controller.signal, 'abort').length, 0) // The test's once-only listener was consumed too.
  locker.child.stdin.end(); assert.equal(await locker.closed, 0)
})

test('close settles provider work even when a test double ignores its cancellation signal', async t => {
  const f = await fixture(t)
  let started
  const ready = new Promise(resolve => { started = resolve })
  const d = fakeDependencies()
  d.createModelCompletion = () => async () => { started(); return new Promise(() => {}) }
  const runtime = f.runtime(d), pending = runtime.complete({ signal: new AbortController().signal })
  const settled = rejected(pending, 'RUNTIME_CLOSED')
  await ready
  await runtime.close()
  await settled
  await rejected(runtime.modelConfigured(), 'RUNTIME_CLOSED')
})

test('provider exceptions and reflected search credentials never become public diagnostics', async t => {
  const f = await fixture(t), d = fakeDependencies()
  d.createModelCompletion = () => { throw new Error(KEYS[0], { cause: new Error(KEYS[1]) }) }
  d.fetchImpl = async () => Response.json({ web: { results: [{ title: 'Unsafe', url: 'https://source.example', description: KEYS[2] }] } })
  const runtime = f.runtime(d)
  await rejected(runtime.complete({ signal: new AbortController().signal }), 'MODEL_UNAVAILABLE')
  const result = await runtime.webSearch(params())
  assert.equal(result.result.code, 'search_provider_error')
  noSecrets(result)
  const reflected = fakeDependencies()
  reflected.createModelCompletion = profile => async () => ({ content: '%72' + profile.apiKey.slice(1) + '%' })
  await rejected(f.runtime(reflected).complete({ signal: new AbortController().signal }), 'MODEL_UNAVAILABLE')
  reflected.createModelCompletion = profile => async () => ({ ['%72' + profile.apiKey.slice(1)]: 'Synthetic' })
  await rejected(f.runtime(reflected).complete({ signal: new AbortController().signal }), 'MODEL_UNAVAILABLE')
})

test('reader deadlines and close reject pending operations and release Workers', async t => {
  const f = await fixture(t), d = fakeDependencies(), workers = observedWorkers(t)
  const locker = f.launch('lock'); await locker.ready()
  const runtime = createSqliteAgentConfiguration(f.root, d.createModelCompletion, { env: environment, fetchImpl: d.fetchImpl,
    readerOptions: { timeoutMs: 30 } })
  f.runtimes.push(runtime)
  await rejected(runtime.complete({ signal: new AbortController().signal }), 'MODEL_UNAVAILABLE')
  await runtime.close()
  assert.equal(d.completions.length, 0)
  assert.ok(workers.size > 0 && [...workers].every(worker => worker.threadId === -1))
  const another = f.runtime(d), pending = another.complete({ signal: new AbortController().signal })
  const settled = rejected(pending, 'RUNTIME_CLOSED')
  const before = workers.size, deadline = Date.now() + 5000
  while (workers.size === before) { assert.ok(Date.now() < deadline); await delay(5) }
  await another.close()
  await settled
  assert.ok([...workers].every(worker => worker.threadId === -1))
  locker.child.stdin.end(); assert.equal(await locker.closed, 0)
})
