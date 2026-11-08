import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile, writeFile, readdir, mkdir, cp, link, symlink, rm } from 'node:fs/promises'
import { basename, dirname, join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'
import { inspect } from 'node:util'
import { serialize } from 'node:v8'
import { Worker } from 'node:worker_threads'
import { getEventListeners } from 'node:events'
import { channel } from 'node:diagnostics_channel'
import { ConfigurationStoreReader, ConfigurationStoreError } from '../integrations/conexus/configuration-store.mjs'
import { configurationRootKey } from '../integrations/conexus/configuration-store-worker.mjs'

const python = process.env.ALPHALAB_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
const bridge = fileURLToPath(new URL('./fixtures/configuration_store_bridge.py', import.meta.url))
const bootstrap = new URL('./fixtures/configuration-store-worker-bootstrap.mjs', import.meta.url).href
const readerURL = new URL('../integrations/conexus/configuration-store.mjs', import.meta.url).href
const CANARIES = ['fixture-node-model-secret-1102', 'fixture-node-inactive-secret-2203',
  'fixture-node-brave-secret-3304', 'fixture-node-rqdata-secret-4405', 'fixture-node-worker-diagnostic-secret-5506',
  'fixture-node-constructor-secret-7701', 'fixture-node-proxy-secret-7702',
  'fixture-node-registration-secret-7703', 'fixture-node-removal-secret-7704',
  'fixture-node-recheck-secret-7705', 'fixture-node-cancellation-secret-7706',
  'fixture-node-initialization-secret-7707', 'fixture-node-security-flag-secret-7708',
  'fixture-node-suppression-secret-8801', 'fixture-node-disposal-secret-8802',
  'fixture-node-completion-race-secret-8803', 'fixture-node-final-state-secret-8804']
const databasePath = root => join(root, 'app', 'secrets', 'configuration-store.sqlite3')

function ownedChild(executable, args, options = {}) {
  const child = spawn(executable, args, { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], ...options })
  const records = []
  let buffer = '', stdout = '', stderr = '', ended = false
  child.stdout.setEncoding('utf8')
  child.stderr.setEncoding('utf8')
  child.stdout.on('data', chunk => {
    stdout += chunk; buffer += chunk
    for (;;) {
      const index = buffer.indexOf('\n')
      if (index < 0) break
      const line = buffer.slice(0, index); buffer = buffer.slice(index + 1)
      try { records.push(JSON.parse(line)) } catch { /* Diagnostic text is retained privately. */ }
    }
  })
  child.stderr.on('data', chunk => { stderr += chunk })
  child.stdin.on('error', () => {})
  const closed = new Promise(resolveClose => {
    child.once('error', () => { ended = true; resolveClose(-1) })
    child.once('close', code => { ended = true; resolveClose(code) })
  })
  return {
    child, records, closed,
    get ended() { return ended }, get stdout() { return stdout }, get stderr() { return stderr },
    async ready() {
      const until = Date.now() + 15000
      while (!records.some(row => row.ready === true)) {
        assert.ok(!ended && Date.now() < until, 'Test-owned fixture did not become ready.')
        await delay(25)
      }
    },
    async stop() {
      // This handle was created by this test helper; never enumerate or kill other processes.
      if (!ended) child.kill()
      child.stdin.destroy()
      const result = await Promise.race([closed, delay(10000, 'timeout', { ref: false })])
      assert.notEqual(result, 'timeout', 'Test-owned subprocess did not stop.')
    },
  }
}

async function fixture(t, options = {}, initialize = true, label = 'installation \u03a9') {
  const base = await mkdtemp(join(tmpdir(), 'alphalab-node-store-'))
  const root = join(base, label)
  const readers = [], children = []
  function launch(command, settings = {}) {
    const result = ownedChild(python, ['-B', bridge, root, command], {
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
    })
    children.push(result)
    result.child.stdin.write(JSON.stringify(settings) + '\n')
    if (!['lock', 'crash'].includes(command)) result.child.stdin.end()
    return result
  }
  async function run(command, settings = {}) {
    const child = launch(command, settings)
    const code = await Promise.race([child.closed, delay(20000, 'timeout', { ref: false })])
    assert.equal(code, 0, 'Python fixture operation failed.')
    return child.records.at(-1)
  }
  function reader(settings = {}, readerRoot = root) {
    const result = new ConfigurationStoreReader(readerRoot, settings)
    readers.push(result)
    return result
  }
  t.after(async () => {
    await Promise.all(readers.map(item => item.close()))
    await Promise.all(children.map(item => item.stop()))
    assert.equal(dirname(base), resolve(tmpdir()))
    assert.ok(basename(base).startsWith('alphalab-node-store-'))
    await rm(base, { recursive: true, force: true })
  })
  const first = initialize ? await run('init', options) : null
  return { base, root, first, reader, run, launch, children }
}

async function tree(root) {
  const result = Object.create(null)
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const filename = join(directory, entry.name)
      if (entry.isDirectory()) await visit(filename)
      else if (entry.isFile()) result[filename.slice(root.length)] = await readFile(filename)
    }
  }
  await visit(root)
  return result
}

async function failure(promise, expected, extra = []) {
  let caught
  try { await promise } catch (error) { caught = error }
  assert.ok(caught instanceof ConfigurationStoreError, 'Expected a sanitized reader error.')
  assert.equal(caught.code, expected)
  assert.equal(caught.cause, undefined)
  assert.equal(caught.context, undefined)
  assert.equal(caught.notes, undefined)
  const visible = [String(caught), inspect(caught, { depth: Infinity }), JSON.stringify(caught),
    String(caught.stack), serialize(caught).toString('utf8')].join('\n')
  for (const value of [...CANARIES, ...extra]) assert.ok(!visible.includes(value), 'Private fixture value escaped in an error.')
  return caught
}

async function bounded(promise, timeoutMs = 3000) {
  let timer
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Reader operation did not settle within its test budget.')), timeoutMs)
    })])
  } finally { clearTimeout(timer) }
}

function sensitiveError(secret) {
  const error = new Error(secret, { cause: new Error(secret) })
  error.context = { secret }
  error.notes = [secret]
  return error
}

function signalSymbol(signal, name) {
  for (let current = signal; current; current = Object.getPrototypeOf(current)) {
    const key = Object.getOwnPropertySymbols(current).find(symbol => symbol.description === name)
    if (key) return key
  }
  assert.fail('Expected Node 22 signal hook is unavailable.')
}

function observedWorkers(t) {
  const workers = new Set(), events = channel('worker_threads')
  const observe = ({ worker }) => workers.add(worker)
  events.subscribe(observe)
  t.after(() => events.unsubscribe(observe))
  return workers
}

function validModel(overrides = {}) {
  return JSON.stringify({ version: 1, active_id: 'model', profiles: [{ id: 'model', api_key: CANARIES[0] }], ...overrides })
}

test('Python store and Node snapshot agree; credentials require explicit private access', async t => {
  const f = await fixture(t), reader = f.reader()
  const before = await tree(f.root)
  const snapshot = await reader.readSnapshot()
  assert.equal(snapshot.protocol_version, 1)
  assert.equal(snapshot.revision, f.first.revision)
  assert.equal(snapshot.installation_id, f.first.installation_id)
  assert.equal(snapshot.documents.models.profiles[0].api_key, CANARIES[0])
  assert.equal(snapshot.documents.models.profiles[1].api_key, CANARIES[1])
  assert.equal(snapshot.documents.search.profiles[0].brave_api_key, CANARIES[2])
  assert.equal(snapshot.documents.models.large_integer, 18446744073709551615n)
  assert.equal(snapshot.documents.models.fraction, 1.5)
  assert.equal(snapshot.documents.models.profiles[0].name, 'Model \u03a9 \u{1f680}')
  assert.equal(snapshot.documents.models.__proto__.polluted, 'fixture-only')
  assert.equal({}.polluted, undefined)
  assert.deepEqual(Object.keys(snapshot.documents).sort(), ['models', 'search'])
  assert.deepEqual(Object.keys(snapshot.selections).sort(), ['models', 'search'])
  assert.ok(!inspect(snapshot.documents, { depth: Infinity }).includes(CANARIES[3]))
  const visible = [String(snapshot), inspect(snapshot), inspect(snapshot, { showHidden: true, customInspect: false }),
    JSON.stringify(snapshot), serialize(snapshot).toString('utf8'), JSON.stringify(structuredClone(snapshot)),
    inspect(reader), JSON.stringify(reader)].join('\n')
  assert.ok(CANARIES.every(value => !visible.includes(value)))
  snapshot.documents.models.profiles[0].api_key = 'caller mutation'
  snapshot.selections.models.kind = 'none'
  assert.equal(snapshot.documents.models.profiles[0].api_key, CANARIES[0])
  assert.equal(snapshot.selections.models.kind, 'profile')
  assert.deepEqual(await tree(f.root), before)
  assert.equal((await reader.readSnapshot()).revision, snapshot.revision)
})

test('Python publication changes the Node revision without changing old detached snapshots', async t => {
  const f = await fixture(t), reader = f.reader()
  const previous = await reader.readSnapshot()
  const committed = await f.run('update')
  const current = await reader.readSnapshot()
  assert.equal(current.revision, committed.revision)
  assert.notEqual(current.revision, previous.revision)
  assert.equal(previous.documents.models.counter, 0)
  assert.equal(current.documents.models.counter, 1)
  assert.equal(current.documents.search.counter, 1)
})

test('Python and Node agree on invariant Windows casing for Unicode installation names', async t => {
  const f = await fixture(t, {}, true, 'installation-\u0130\u212a\u1e9e\u01c5-\u039f\u03a3')
  assert.equal((await f.reader().readSnapshot()).revision, f.first.revision)
})

test('root-binding normalization matches Python for every Node case-changing Unicode scalar', async t => {
  const f = await fixture(t, {}, false)
  const values = ['Greek-\u039f\u03a3', 'Latin-\u0130\u212a\u212b\u1e9e', 'paths/with/SLASHES']
  for (let code = 0; code <= 0x10ffff; code++) {
    if (code >= 0xd800 && code <= 0xdfff) continue
    const character = String.fromCodePoint(code)
    if (character.toLowerCase() !== character) values.push('root/' + character)
  }
  const result = await f.run('root_keys', { values })
  assert.deepEqual(values.map(configurationRootKey), result.keys)
  assert.deepEqual(await readdir(f.base), [])
})

for (const intent of ['profile', 'environment', 'none']) {
  test(`selection intent ${intent} survives without resolving environment credentials`, async t => {
    const f = await fixture(t, { intent })
    const snapshot = await f.reader().readSnapshot()
    assert.equal(snapshot.selections.models.kind, intent)
    assert.equal(snapshot.selections.search.kind, intent)
    assert.equal(snapshot.documents.models.active_id, intent === 'profile' ? 'model' : null)
    assert.equal(snapshot.documents.search.active_id, intent === 'profile' ? 'search' : null)
  })
}

test('legacy search v1 remains v1 with its explicit default selection', async t => {
  const f = await fixture(t, { search_version: 1 })
  const snapshot = await f.reader().readSnapshot()
  assert.equal(snapshot.documents.search.version, 1)
  assert.equal(snapshot.documents.search.model_profile_id, 'model')
  assert.deepEqual(snapshot.selections.search, { kind: 'profile', id: 'default' })
})

test('explicitly cleared secrets stay empty even when environment credentials exist', async t => {
  const f = await fixture(t, { intent: 'environment', empty_secrets: true })
  const keys = ['CONEXUS_MODEL_API_KEY', 'BRAVE_API_KEY', 'RQ_PASSWORD']
  const previous = keys.map(key => process.env[key])
  try {
    for (const key of keys) process.env[key] = 'fixture-env-credential-must-not-resolve-6607'
    const snapshot = await f.reader().readSnapshot()
    assert.equal(snapshot.documents.models.profiles[0].api_key, '')
    assert.equal(snapshot.documents.search.profiles[0].brave_api_key, '')
    assert.equal(snapshot.selections.models.kind, 'environment')
    assert.equal(snapshot.selections.search.kind, 'environment')
  } finally {
    keys.forEach((key, index) => { if (previous[index] === undefined) delete process.env[key]; else process.env[key] = previous[index] })
  }
})

test('concurrent Python publications and Node requests never mix model/search generations', async t => {
  const f = await fixture(t), reader = f.reader({ maxConcurrency: 3, maxPending: 24, timeoutMs: 10000 })
  const writer = f.launch('loop', { count: 25 })
  await writer.ready()
  const snapshots = await Promise.all(Array.from({ length: 18 }, async () => {
    for (let attempt = 0; attempt < 12; attempt++) {
      try { return await reader.readSnapshot() } catch (error) {
        if (error.code !== 'STORE_BUSY') throw error
        await delay(10)
      }
    }
    assert.fail('Reader did not obtain a snapshot within the retry budget.')
  }))
  assert.equal(await writer.closed, 0)
  const published = new Map([[f.first.revision, 0], ...writer.records.filter(row => row.revision).map(row => [row.revision, row.counter])])
  for (const snapshot of snapshots) {
    assert.equal(snapshot.documents.models.counter, snapshot.documents.search.counter)
    assert.equal(snapshot.documents.models.counter, published.get(snapshot.revision))
  }
  assert.equal((await reader.readSnapshot()).documents.models.counter, 25)
})

test('missing installations and missing database files fail without creation or legacy adoption', async t => {
  const f = await fixture(t, {}, false)
  await failure(f.reader().readSnapshot(), 'STORE_MISSING')
  assert.deepEqual(await readdir(f.base), [])
  await mkdir(join(f.root, 'app', 'secrets'), { recursive: true, mode: 0o700 })
  await writeFile(join(f.root, 'app', 'secrets', 'rqdata-profiles.json'), CANARIES[3])
  const before = await tree(f.root)
  await failure(f.reader().readSnapshot(), 'STORE_MISSING')
  assert.deepEqual(await tree(f.root), before)
})

test('filesystem failures do not expose credential-bearing root names', async t => {
  const f = await fixture(t, {}, false)
  await failure(f.reader({}, join(f.base, CANARIES[0])).readSnapshot(), 'STORE_MISSING')
  assert.deepEqual(await readdir(f.base), [])
})

for (const [scenario, code] of [
  ['protocol', 'UNSUPPORTED_PROTOCOL'], ['metadata_protocol', 'UNSUPPORTED_PROTOCOL'],
  ['singleton', 'STORE_CORRUPT'], ['missing_current', 'STORE_CORRUPT'],
  ['missing_table', 'STORE_CORRUPT'], ['wal', 'UNSUPPORTED_STORAGE'],
]) {
  test(`${scenario} is rejected with no settings modifications`, async t => {
    const f = await fixture(t)
    await f.run('tamper', { case: scenario })
    const before = await tree(f.root)
    await failure(f.reader().readSnapshot(), code)
    assert.deepEqual(await tree(f.root), before)
  })
}

test('a pointer into history is corruption, never a credential rollback', async t => {
  const f = await fixture(t)
  await f.run('update')
  await f.run('tamper', { case: 'history' })
  const before = await tree(f.root)
  await failure(f.reader().readSnapshot(), 'STORE_CORRUPT')
  assert.deepEqual(await tree(f.root), before)
})

test('a genuinely interrupted Python transaction requires explicit Python recovery', async t => {
  const f = await fixture(t), writer = f.launch('crash')
  await writer.ready()
  await writer.stop()
  assert.ok((await readFile(databasePath(f.root) + '-journal')).byteLength > 512)
  const before = await tree(f.root)
  await failure(f.reader().readSnapshot(), 'RECOVERY_REQUIRED')
  assert.deepEqual(await tree(f.root), before)
})

test('independent installations isolate documents, identities and revisions', async t => {
  const one = await fixture(t), two = await fixture(t)
  await one.run('update')
  const [a, b] = await Promise.all([one.reader().readSnapshot(), two.reader().readSnapshot()])
  assert.notEqual(a.installation_id, b.installation_id)
  assert.notEqual(a.revision, b.revision)
  assert.equal(a.documents.models.counter, 1)
  assert.equal(b.documents.models.counter, 0)
  await cp(databasePath(one.root), databasePath(two.root))
  await failure(two.reader().readSnapshot(), 'INSTALLATION_MISMATCH')
})

test('an existing reader pins installation identity across database replacement', async t => {
  const f = await fixture(t), reader = f.reader()
  await reader.readSnapshot()
  await rm(databasePath(f.root))
  await f.run('init')
  await failure(reader.readSnapshot(), 'INSTALLATION_MISMATCH')
})

const malformedModels = [
  ['invalid JSON', '{"secret":"' + CANARIES[0] + '"'],
  ['duplicate version', '{"version":1,"version":1,"active_id":"model","profiles":[{"id":"model"}]}'],
  ['escaped duplicate', '{"version":1,"ver\\u0073ion":1,"active_id":"model","profiles":[{"id":"model"}]}'],
  ['NaN', validModel().replace('"version":1', '"version":NaN')],
  ['Infinity', validModel().replace('"version":1', '"version":Infinity')],
  ['float version', validModel().replace('"version":1', '"version":1.0')],
  ['unsupported native version', validModel({ version: 3 })],
  ['missing active reference', validModel({ active_id: 'missing' })],
  ['duplicate profile IDs', validModel({ profiles: [{ id: 'model' }, { id: 'model' }] })],
  ['newline identity', validModel({ active_id: 'model\n', profiles: [{ id: 'model\n' }] })],
  ['lone surrogate', validModel({ name: '\ud800' })],
  ['large integer', validModel().replace('"version":1', '"extra":18446744073709551616,"version":1')],
  ['excessive depth', validModel({ extra: Array.from({ length: 36 }).reduce(value => [value], 0) })],
  ['node limit', validModel({ extra: Array(33000).fill(null) })],
  ['size limit', validModel({ extra: 'x'.repeat(4 * 1024 * 1024) })],
]
for (const [name, text] of malformedModels) {
  test(`corrupt model JSON: ${name}`, async t => {
    const f = await fixture(t)
    await f.run('tamper', { case: 'model_text', text })
    const before = await tree(f.root)
    await failure(f.reader().readSnapshot(), 'STORE_CORRUPT')
    assert.deepEqual(await tree(f.root), before)
  })
}

for (const kind of ['none', 'environment']) {
  test(`selection ${kind} with a saved active profile is rejected`, async t => {
    const f = await fixture(t)
    await f.run('tamper', { case: 'selection_text', text: JSON.stringify({ models: { kind }, search: { kind: 'profile', id: 'search' }, rqdata: { kind: 'profile', id: 'rq' } }) })
    await failure(f.reader().readSnapshot(), 'STORE_CORRUPT')
  })
}

for (const kind of ['database-hardlink', 'database-symlink', 'parent-symlink', 'journal-symlink']) {
  test(`unsafe path ${kind} is rejected without following it`, async t => {
    const f = await fixture(t), alias = join(f.base, 'alias')
    if (kind === 'database-hardlink') {
      try { await link(databasePath(f.root), alias) } catch { t.skip('Hard links unavailable on this filesystem'); return }
    } else {
      let target, location, type
      if (kind === 'database-symlink') {
        target = databasePath(f.root); location = alias; type = 'file'
        await cp(target, alias + '-copy')
        await rm(target)
        location = target; target = alias + '-copy'
      } else if (kind === 'journal-symlink') {
        target = databasePath(f.root); location = target + '-journal'; type = 'file'
      } else {
        target = join(f.base, 'private-parent')
        await cp(join(f.root, 'app', 'secrets'), target, { recursive: true })
        await rm(join(f.root, 'app', 'secrets'), { recursive: true })
        location = join(f.root, 'app', 'secrets'); type = 'junction'
      }
      try { await symlink(target, location, type) } catch { t.skip('Symlink creation unavailable without permission changes'); return }
    }
    await failure(f.reader().readSnapshot(), 'UNSAFE_STORAGE')
  })
}

test('bounded lock waits and concurrent requests leave the main event loop responsive', async t => {
  const f = await fixture(t), locker = f.launch('lock')
  await locker.ready()
  const reader = f.reader({ maxConcurrency: 2, maxPending: 12, timeoutMs: 4000 })
  let ticks = 0
  const timer = setInterval(() => { ticks++ }, 5)
  try {
    const results = await Promise.all(Array.from({ length: 8 }, () => failure(reader.readSnapshot(), 'STORE_BUSY')))
    assert.equal(results.length, 8)
    assert.ok(ticks >= 10, 'Main event loop stalled during SQLite lock waits.')
  } finally { clearInterval(timer) }
  locker.child.stdin.end('\n')
  assert.equal(await locker.closed, 0)
  assert.equal((await reader.readSnapshot()).revision, f.first.revision)
})

test('abort reasons remain private; close rejects work and awaits worker termination', async t => {
  const f = await fixture(t), locker = f.launch('lock')
  await locker.ready()
  const reader = f.reader({ maxConcurrency: 1, maxPending: 4 })
  const controller = new AbortController()
  const cancelled = failure(reader.readSnapshot({ signal: controller.signal }), 'STORE_CANCELLED')
  controller.abort(new Error(CANARIES[0]))
  await cancelled
  const pending = failure(reader.readSnapshot(), 'STORE_CLOSED')
  await reader.close()
  await pending
  await reader.close()
  await failure(reader.readSnapshot(), 'STORE_CLOSED')
})

test('a deadline bounds queued requests and queue capacity', async t => {
  const f = await fixture(t), locker = f.launch('lock')
  await locker.ready()
  const reader = f.reader({ maxConcurrency: 1, maxPending: 2 })
  const first = failure(reader.readSnapshot(), 'STORE_BUSY')
  const second = failure(reader.readSnapshot({ timeoutMs: 10 }), 'STORE_TIMEOUT')
  await failure(reader.readSnapshot(), 'STORE_BUSY')
  await Promise.all([first, second])
})

test('already aborted signals and hostile option getters fail without inspecting secret values', async t => {
  const f = await fixture(t), reader = f.reader()
  const controller = new AbortController()
  controller.abort(CANARIES[0])
  await failure(reader.readSnapshot({ signal: controller.signal }), 'STORE_CANCELLED')
  let accessed = false
  await failure(reader.readSnapshot({ get timeoutMs() { accessed = true; throw new Error(CANARIES[0]) } }), 'INVALID_ARGUMENT')
  assert.equal(accessed, false)
  for (const duration of [0, -1, 30001, NaN, Infinity, '1']) await failure(reader.readSnapshot({ timeoutMs: duration }), 'INVALID_ARGUMENT')
})

test('hostile signal constructor and registration hooks produce only sanitized errors', async t => {
  const f = await fixture(t), reader = f.reader(), workers = observedWorkers(t)
  for (const [name, secret] of [['constructor', CANARIES[5]], ['kNewListener', CANARIES[7]]]) {
    await t.test(name, async () => {
      const signal = new AbortController().signal
      const key = name === 'constructor' ? name : signalSymbol(signal, name)
      let accessed = 0
      Object.defineProperty(signal, key, { configurable: true, get() { accessed++; throw sensitiveError(secret) } })
      try {
        await bounded(failure(Promise.resolve().then(() => reader.readSnapshot({ signal })), 'INVALID_ARGUMENT'))
        assert.ok(accessed > 0, 'The intended native signal operation was not reached.')
      } finally { delete signal[key] }
      assert.equal(getEventListeners(signal, 'abort').length, 0)
    })
  }
  assert.equal(workers.size, 0, 'An invalid signal started a Worker.')
  assert.equal((await reader.readSnapshot()).revision, f.first.revision)
})

test('Proxy signals are rejected without executing traps or inspecting private properties', async t => {
  const f = await fixture(t, {}, false), reader = f.reader()
  let traps = 0
  const hostile = () => { traps++; throw sensitiveError(CANARIES[6]) }
  const proxy = new Proxy(new AbortController().signal, {
    get: hostile, getPrototypeOf: hostile, getOwnPropertyDescriptor: hostile,
  })
  await failure(Promise.resolve().then(() => reader.readSnapshot({ signal: proxy })), 'INVALID_ARGUMENT')
  assert.equal(traps, 0)
  const revoked = Proxy.revocable(new AbortController().signal, {})
  revoked.revoke()
  await failure(reader.readSnapshot({ signal: revoked.proxy }), 'INVALID_ARGUMENT')
  for (const signal of [null, {}, false, 'signal']) await failure(reader.readSnapshot({ signal }), 'INVALID_ARGUMENT')
})

test('initial and post-registration native aborted checks are both guarded', async t => {
  const f = await fixture(t), reader = f.reader(), workers = observedWorkers(t)
  for (const phase of ['initial', 'post-registration']) {
    await t.test(phase, async () => {
      const signal = new AbortController().signal, key = signalSymbol(signal, 'kAborted')
      const descriptor = Object.getOwnPropertyDescriptor(signal, key)
      let registered = false, sawAttachedListener = false
      Object.defineProperty(signal, 'constructor', { configurable: true, get() { registered = true; return AbortSignal } })
      Object.defineProperty(signal, key, { configurable: true, get() {
        if (phase === 'initial' || registered) {
          if (phase === 'post-registration') sawAttachedListener = getEventListeners(signal, 'abort').length === 1
          throw sensitiveError(CANARIES[9])
        }
        return false
      } })
      try {
        await bounded(failure(Promise.resolve().then(() => reader.readSnapshot({ signal })), 'INVALID_ARGUMENT'))
        assert.equal(registered, phase === 'post-registration')
        assert.equal(sawAttachedListener, phase === 'post-registration')
      } finally {
        Object.defineProperty(signal, key, descriptor)
        delete signal.constructor
      }
      assert.equal(getEventListeners(signal, 'abort').length, 0)
    })
  }
  assert.equal(workers.size, 0)
})

test('public signal overrides are rejected without access and registration aborts are not missed', async t => {
  const f = await fixture(t), reader = f.reader()
  for (const name of ['aborted', 'addEventListener', 'removeEventListener']) {
    const signal = new AbortController().signal
    let accessed = 0
    Object.defineProperty(signal, name, { get() { accessed++; throw sensitiveError(CANARIES[9]) } })
    await failure(reader.readSnapshot({ signal }), 'INVALID_ARGUMENT')
    assert.equal(accessed, 0)
  }
  const overridden = new AbortController().signal
  Object.defineProperty(overridden, 'addEventListener', { value() {} })
  await failure(reader.readSnapshot({ signal: overridden }), 'INVALID_ARGUMENT')
  assert.equal((await reader.readSnapshot({ signal: new AbortController().signal })).revision, f.first.revision)
  for (const phase of ['already aborted', 'during registration']) {
    await t.test(phase, async () => {
      const controller = new AbortController(), signal = controller.signal
      let registrations = 0
      if (phase === 'already aborted') controller.abort(sensitiveError(CANARIES[10]))
      Object.defineProperty(signal, 'constructor', { configurable: true, get() {
        registrations++
        controller.abort(sensitiveError(CANARIES[10]))
        return AbortSignal
      } })
      try { await bounded(failure(reader.readSnapshot({ signal }), 'STORE_CANCELLED')) }
      finally { delete signal.constructor }
      assert.equal(registrations > 0, phase === 'during registration')
      assert.equal(getEventListeners(signal, 'abort').length, 0)
    })
  }
})

test('listener removal failures cannot prevent settlement, termination or reader reuse', async t => {
  const f = await fixture(t), workers = observedWorkers(t)
  for (const [ending, hook] of ['kRemoveListener', 'constructor'].flatMap(hook =>
    ['success', 'timeout', 'close', 'worker failure'].map(ending => [ending, hook]))) {
    await t.test(`${ending}: ${hook}`, async st => {
      const reader = f.reader(), controller = new AbortController(), signal = controller.signal
      const key = hook === 'constructor' ? hook : signalSymbol(signal, hook)
      let removals = 0, settlements = 0
      if (ending === 'worker failure') {
        const emit = Worker.prototype.emit
        st.mock.method(Worker.prototype, 'emit', function(event, ...args) {
          if (event === 'online') this.terminate()
          return emit.call(this, event, ...args)
        })
      }
      const expected = { success: 'INVALID_ARGUMENT', timeout: 'STORE_TIMEOUT', close: 'STORE_CLOSED', 'worker failure': 'WORKER_FAILED' }[ending]
      if (ending === 'timeout') {
        const start = Date.now()
        // Keep a 1ms budget from expiring synchronously in #drain before the
        // removal fault is installed. Real timers and Worker clocks stay live.
        st.mock.method(Date, 'now', () => start)
      }
      const result = reader.readSnapshot({ signal, timeoutMs: ending === 'timeout' ? 1 : 3000 })
      // Install after registration: constructor fails before removal, whereas
      // kRemoveListener fails after Node has already detached the listener.
      Object.defineProperty(signal, key, { configurable: true, get() { removals++; throw sensitiveError(CANARIES[8]) } })
      if (ending === 'timeout') st.mock.restoreAll()
      result.then(() => { settlements++ }, () => { settlements++ })
      const checked = bounded(failure(result, expected))
      if (ending === 'close') await bounded(reader.close())
      await checked
      assert.equal(removals, 1, 'The listener-removal fault was not reached exactly once.')
      assert.equal(settlements, 1)
      delete signal[key]
      const retained = getEventListeners(signal, 'abort')
      assert.equal(retained.length, hook === 'constructor' ? 1 : 0)
      for (const listener of retained) listener() // An undetachable listener is inert.
      controller.abort(sensitiveError(CANARIES[10]))
      assert.equal(settlements, 1)
      st.mock.restoreAll()
      if (ending !== 'close') assert.equal((await reader.readSnapshot()).revision, f.first.revision)
      await bounded(reader.close())
      await bounded(reader.close())
      assert.equal(getEventListeners(signal, 'abort').length, 0)
    })
  }
  assert.ok([...workers].every(worker => worker.threadId === -1))
})

test('ordinary cancellation during Worker initialization terminates the Worker', async t => {
  const f = await fixture(t), reader = f.reader(), controller = new AbortController(), workers = observedWorkers(t)
  const emit = Worker.prototype.emit
  t.mock.method(Worker.prototype, 'emit', function(event, ...args) {
    if (event === 'online') controller.abort(sensitiveError(CANARIES[11]))
    return emit.call(this, event, ...args)
  })
  await bounded(failure(reader.readSnapshot({ signal: controller.signal }), 'STORE_CANCELLED'))
  t.mock.restoreAll()
  assert.equal((await reader.readSnapshot()).revision, f.first.revision)
  await bounded(reader.close())
  assert.ok(workers.size >= 2)
  assert.ok([...workers].every(worker => worker.threadId === -1))
})

test('suppression-resistant cancellation rejects before Worker completion and cleans up', async t => {
  const f = await fixture(t), baseline = f.reader()
  assert.equal((await baseline.readSnapshot()).revision, f.first.revision)
  await baseline.close()
  const reader = f.reader(), controller = new AbortController(), workers = observedWorkers(t)
  let stopped = 0, settlements = 0, outcome
  controller.signal.addEventListener('abort', event => { stopped++; event.stopImmediatePropagation() }, { once: true })
  const pending = reader.readSnapshot({ signal: controller.signal })
  pending.then(() => { settlements++; outcome = 'success' }, error => { settlements++; outcome = error.code })
  assert.equal(workers.size, 1)
  controller.abort(sensitiveError(CANARIES[13]))
  await Promise.resolve()
  // A final-state check after a successful Worker response is insufficient:
  // cancellation must be delivered before any Worker message can run.
  assert.equal(outcome, 'STORE_CANCELLED', 'Suppressed abort did not cancel before Worker completion.')
  await bounded(failure(pending, 'STORE_CANCELLED'))
  assert.equal(stopped, 1)
  assert.equal(settlements, 1)
  await bounded(reader.close())
  await bounded(reader.close())
  assert.ok([...workers].every(worker => worker.threadId === -1))
  assert.equal(getEventListeners(controller.signal, 'abort').length, 0)
})

test('returned abort disposable is removed after synchronous close during registration', async t => {
  const f = await fixture(t), reader = f.reader(), workers = observedWorkers(t)
  const signal = new AbortController().signal
  let closing, entered = false
  Object.defineProperty(signal, 'constructor', { configurable: true, get() {
    if (!entered) { entered = true; closing = reader.close() }
    return AbortSignal
  } })
  try {
    await bounded(failure(reader.readSnapshot({ signal }), 'STORE_CLOSED'))
    await bounded(closing)
    assert.equal(entered, true)
    assert.equal(workers.size, 0)
  } finally { delete signal.constructor }
  assert.equal(getEventListeners(signal, 'abort').length, 0)
})

test('returned disposable failures cannot expose credentials or prevent termination', async t => {
  const f = await fixture(t), workers = observedWorkers(t)
  for (const kind of ['getter', 'method']) {
    await t.test(kind, async () => {
      const reader = f.reader(), controller = new AbortController(), signal = controller.signal
      let failures = 0
      const fault = () => { failures++; throw sensitiveError(CANARIES[14]) }
      const pending = reader.readSnapshot({ signal })
      Object.defineProperty(signal, 'removeEventListener', { configurable: true,
        ...(kind === 'getter' ? { get: fault } : { value: fault }) })
      try {
        await bounded(failure(pending, 'INVALID_ARGUMENT'))
        assert.equal(failures, 1)
        await bounded(reader.close())
      } finally { delete signal.removeEventListener }
      const retained = getEventListeners(signal, 'abort')
      assert.equal(retained.length, 1)
      for (const listener of retained) listener()
      controller.abort(sensitiveError(CANARIES[13]))
      assert.equal(getEventListeners(signal, 'abort').length, 0)
    })
  }
  assert.ok([...workers].every(worker => worker.threadId === -1))
})

test('abort during disposal cannot turn into a successful snapshot', async t => {
  const f = await fixture(t), workers = observedWorkers(t)
  for (const throws of [false, true]) {
    await t.test(throws ? 'disposal throws' : 'disposal completes', async () => {
      const reader = f.reader(), controller = new AbortController(), signal = controller.signal
      const remove = signal.removeEventListener
      let removals = 0
      const pending = reader.readSnapshot({ signal })
      Object.defineProperty(signal, 'removeEventListener', { configurable: true, get() {
        removals++
        controller.abort(sensitiveError(CANARIES[13]))
        if (throws) throw sensitiveError(CANARIES[14])
        return remove
      } })
      try {
        await bounded(failure(pending, 'STORE_CANCELLED'))
        assert.equal(removals, 1)
        assert.equal(controller.signal.aborted, true)
      } finally { delete signal.removeEventListener }
      await bounded(reader.close())
      assert.equal(getEventListeners(signal, 'abort').length, 0)
    })
  }
  assert.ok([...workers].every(worker => worker.threadId === -1))
})

test('abort versus Worker exit preserves the first settlement', async t => {
  const f = await fixture(t), workers = observedWorkers(t)
  for (const phase of ['before completion', 'after exit']) {
    await t.test(phase, async st => {
      const reader = f.reader(), controller = new AbortController()
      controller.signal.addEventListener('abort', event => event.stopImmediatePropagation(), { once: true })
      const emit = Worker.prototype.emit
      let messages = 0, settlements = 0
      st.mock.method(Worker.prototype, 'emit', function(event, ...args) {
        if (event === 'message') {
          messages++
          assert.equal(args[0].ok, true, 'The completion race needs a valid SQLite result.')
          if (phase === 'before completion') controller.abort(sensitiveError(CANARIES[15]))
        }
        const result = emit.call(this, event, ...args)
        if (event === 'exit' && phase === 'after exit') controller.abort(sensitiveError(CANARIES[15]))
        return result
      })
      const pending = reader.readSnapshot({ signal: controller.signal })
      pending.then(() => { settlements++ }, () => { settlements++ })
      if (phase === 'before completion') await bounded(failure(pending, 'STORE_CANCELLED'))
      else assert.equal((await bounded(pending)).revision, f.first.revision)
      assert.equal(controller.signal.aborted, true)
      assert.equal(messages, 1)
      assert.equal(settlements, 1)
      await bounded(reader.close())
      await bounded(reader.close())
      assert.equal(getEventListeners(controller.signal, 'abort').length, 0)
    })
  }
  assert.ok([...workers].every(worker => worker.threadId === -1))
})

test('abort during exit bookkeeping still precedes successful settlement', async t => {
  const f = await fixture(t), reader = f.reader(), controller = new AbortController(), workers = observedWorkers(t)
  const emit = Worker.prototype.emit
  let retirement = 0
  t.mock.method(Worker.prototype, 'emit', function(event, ...args) {
    if (event === 'message') this.on('removeListener', name => {
      if (name === 'error') { retirement++; controller.abort(sensitiveError(CANARIES[15])) }
    })
    return emit.call(this, event, ...args)
  })
  await bounded(failure(reader.readSnapshot({ signal: controller.signal }), 'STORE_CANCELLED'))
  assert.equal(retirement, 1, 'The abort must occur inside exit bookkeeping.')
  await bounded(reader.close())
  assert.ok([...workers].every(worker => worker.threadId === -1))
})

test('credential-bearing failures in the final native aborted check stay private', async t => {
  const f = await fixture(t), reader = f.reader(), signal = new AbortController().signal
  const remove = signal.removeEventListener, key = signalSymbol(signal, 'kAborted')
  const descriptor = Object.getOwnPropertyDescriptor(signal, key)
  let checks = 0, disposed = false
  const pending = reader.readSnapshot({ signal })
  Object.defineProperty(signal, 'removeEventListener', { configurable: true, value(...args) {
    remove.apply(this, args)
    disposed = true
    Object.defineProperty(signal, key, { configurable: true, get() { checks++; throw sensitiveError(CANARIES[16]) } })
  } })
  try {
    await bounded(failure(pending, 'INVALID_ARGUMENT'))
    assert.equal(disposed, true)
    assert.ok(checks > 0, 'The failure must come from the final native check.')
  } finally {
    Object.defineProperty(signal, key, descriptor)
    delete signal.removeEventListener
  }
  await bounded(reader.close())
  assert.equal(getEventListeners(signal, 'abort').length, 0)
})

test('synchronous abort and close inside Worker construction await termination', async t => {
  const f = await fixture(t), reader = f.reader(), controller = new AbortController()
  const events = channel('worker_threads')
  let worker, closing
  const interrupt = record => {
    worker = record.worker
    controller.abort(sensitiveError(CANARIES[11]))
    closing = reader.close()
  }
  events.subscribe(interrupt)
  try {
    await bounded(failure(reader.readSnapshot({ signal: controller.signal }), 'STORE_CANCELLED'))
    assert.ok(worker, 'The synchronous construction race was not reached.')
    await bounded(closing)
    assert.equal(worker.threadId, -1)
    assert.equal(getEventListeners(controller.signal, 'abort').length, 0)
  } finally { events.unsubscribe(interrupt) }
})

test('concurrent cancellation, timeouts and repeated close settle each request once', async t => {
  const f = await fixture(t), locker = f.launch('lock'), workers = observedWorkers(t)
  await locker.ready()
  const reader = f.reader({ maxConcurrency: 2, maxPending: 16 }), controllers = [], settlements = Array(12).fill(0)
  const checks = Array.from({ length: 12 }, (_, index) => {
    const controller = new AbortController()
    controllers.push(controller)
    if (index < 4) controller.signal.addEventListener('abort', event => event.stopImmediatePropagation(), { once: true })
    const result = reader.readSnapshot({ signal: controller.signal, timeoutMs: index >= 4 && index < 8 ? 10 : 3000 })
    result.then(() => { settlements[index]++ }, () => { settlements[index]++ })
    return bounded(failure(result, index < 4 ? 'STORE_CANCELLED' : index < 8 ? 'STORE_TIMEOUT' : 'STORE_CLOSED'))
  })
  for (const controller of controllers.slice(0, 4)) controller.abort(sensitiveError(CANARIES[10]))
  await Promise.all(checks.slice(0, 8))
  await bounded(reader.close())
  await Promise.all(checks)
  await Promise.all([reader.close(), reader.close()])
  for (const controller of controllers) controller.abort(sensitiveError(CANARIES[11]))
  assert.deepEqual(settlements, Array(12).fill(1))
  assert.ok(controllers.every(controller => getEventListeners(controller.signal, 'abort').length === 0))
  assert.ok([...workers].every(worker => worker.threadId === -1))
})

test('unexpected worker exit is sanitized and a subsequent request can succeed', async t => {
  const f = await fixture(t), reader = f.reader({ maxConcurrency: 1 })
  const original = Worker.prototype.emit
  let interrupted = false
  t.mock.method(Worker.prototype, 'emit', function(event, ...args) {
    if (event === 'online' && !interrupted) { interrupted = true; this.terminate() }
    return original.call(this, event, ...args)
  })
  await failure(reader.readSnapshot(), 'WORKER_FAILED')
  t.mock.restoreAll()
  assert.equal((await reader.readSnapshot()).revision, f.first.revision)
})

function scenario(f, behavior, timeoutMs = 3000, extraArgs = [], close = true, operation = 'read') {
  const script = `
    import { ConfigurationStoreReader } from ${JSON.stringify(readerURL)};
    import { inspect } from 'node:util';
    import { Worker } from 'node:worker_threads';
    const controller = new AbortController();
    const reader = new ConfigurationStoreReader(process.argv[1], { timeoutMs: ${timeoutMs} });
    const lifecycle = { messages:0, validResults:0, forced:0, exits:0, settledAtResult:false, listeners:[] };
    const workers = new Set(), emit = Worker.prototype.emit, terminate = Worker.prototype.terminate;
    let settled=false, closing, triggerTimeout;
    if (${JSON.stringify(operation)} === 'timeout-retirement') {
      const schedule = globalThis.setTimeout;
      globalThis.setTimeout = (fn,ms,...args) => {
        if(ms===${timeoutMs} && !triggerTimeout) triggerTimeout=()=>fn(...args);
        return schedule(fn,ms,...args);
      };
    }
    Worker.prototype.terminate=function(...args){ lifecycle.forced++; return terminate.apply(this,args); };
    Worker.prototype.emit=function(event,...args){
      workers.add(this);
      if(event==='message'){
        lifecycle.messages++; lifecycle.validResults+=Number(args[0]?.ok===true);
        lifecycle.sentRevision=args[0]?.snapshot?.revision;
        lifecycle.settledAtResult=settled;
      }
      if(event==='exit'){ lifecycle.exits++; lifecycle.exitCode=args[0]; }
      const result=emit.call(this,event,...args);
      if(event==='message'){
        if(${JSON.stringify(operation)}==='close-retirement')closing=reader.close();
        if(${JSON.stringify(operation)}==='abort-retirement')controller.abort(new Error(${JSON.stringify(CANARIES[15])}));
        if(${JSON.stringify(operation)}==='timeout-retirement')triggerTimeout();
      }
      return result;
    };
    let response;
    try {
      const pending=reader.readSnapshot({signal:controller.signal});
      pending.then(()=>{settled=true},()=>{settled=true});
      const snapshot = await pending;
      response={ok:true, revision:snapshot.revision, counter:snapshot.documents.models.counter,
        searchCounter:snapshot.documents.search.counter, visible:inspect(snapshot)+JSON.stringify(snapshot)};
    } catch(error) {
      response={ok:false, code:error.code, visible:inspect(error)+JSON.stringify(error)};
    } ${close ? 'finally { await reader.close(); }' : ''}
    if(closing)await closing;
    lifecycle.listeners=[...workers].map(worker=>({threadId:worker.threadId,
      message:worker.listenerCount('message'),error:worker.listenerCount('error'),exit:worker.listenerCount('exit')}));
    console.log(JSON.stringify({...response,lifecycle}));
  `
  const child = ownedChild(process.execPath, [...extraArgs, '--import', bootstrap, '--input-type=module', '-e', script, f.root], {
    env: { ...process.env, ALPHALAB_TEST_WORKER_BEHAVIOR: behavior },
  })
  f.children.push(child)
  child.child.stdin.end()
  return child
}

test('normal snapshots settle only after natural Worker exit and release listeners', async t => {
  const f = await fixture(t), reader = f.reader({ maxConcurrency: 1 }), workers = observedWorkers(t)
  const terminate = Worker.prototype.terminate
  let forced = 0, exits = 0
  t.mock.method(Worker.prototype, 'terminate', function(...args) { forced++; return terminate.apply(this, args) })
  const emit = Worker.prototype.emit
  t.mock.method(Worker.prototype, 'emit', function(event, ...args) {
    if (event === 'exit') { assert.equal(args[0], 0); exits++ }
    return emit.call(this, event, ...args)
  })
  for (let index = 0; index < 3; index++) {
    const snapshot = await reader.readSnapshot()
    assert.equal(snapshot.revision, f.first.revision)
    assert.equal(exits, index + 1, 'A message alone must not settle a snapshot.')
  }
  await bounded(reader.close())
  assert.equal(forced, 0)
  assert.equal(workers.size, 3)
  for (const worker of workers) {
    assert.equal(worker.threadId, -1)
    for (const event of ['message', 'error', 'exit']) assert.equal(worker.listenerCount(event), 0)
  }
})

test('concurrent natural completion returns complete Python generations without forced teardown', async t => {
  const f = await fixture(t), reader = f.reader({ maxConcurrency: 2, maxPending: 8 }), workers = observedWorkers(t)
  const terminate = Worker.prototype.terminate
  let forced = 0
  t.mock.method(Worker.prototype, 'terminate', function(...args) { forced++; return terminate.apply(this, args) })
  const snapshots = await Promise.all(Array.from({ length: 6 }, () => reader.readSnapshot()))
  for (const snapshot of snapshots) {
    assert.equal(snapshot.revision, f.first.revision)
    assert.equal(snapshot.documents.models.counter, snapshot.documents.search.counter)
  }
  await bounded(reader.close())
  assert.equal(forced, 0)
  assert.equal(workers.size, 6)
  assert.ok([...workers].every(worker => worker.threadId === -1 && worker.listenerCount('error') === 0))
})

function cleanScenario(child, response) {
  for (const secret of CANARIES) assert.ok(!(child.stdout + child.stderr).includes(secret))
  assert.equal(response.lifecycle.exits, 1)
  for (const worker of response.lifecycle.listeners) {
    assert.equal(worker.threadId, -1)
    assert.equal(worker.message + worker.error + worker.exit, 0)
  }
}

test('terminal results require clean Worker exit and a bounded retirement window', async t => {
  const f = await fixture(t)
  for (const [behavior, ok, forced, exitCode] of [
    ['result-delay-exit', true, 0, 0], ['result-crash', false, 0, 1],
    ['result-nonzero', false, 0, 7], ['result-hang', false, 1, 1],
  ]) {
    await t.test(behavior, async () => {
      const start = performance.now(), child = scenario(f, behavior)
      assert.equal(await bounded(child.closed, 5000), 0)
      const response = child.records.at(-1)
      assert.equal(response.lifecycle.validResults, 1, 'The failure must follow a valid result.')
      assert.equal(response.lifecycle.sentRevision, f.first.revision)
      assert.equal(response.lifecycle.settledAtResult, false)
      assert.equal(response.ok, ok)
      if (ok) assert.equal(response.revision, f.first.revision)
      else assert.equal(response.code, 'WORKER_FAILED')
      assert.equal(response.lifecycle.forced, forced)
      assert.equal(response.lifecycle.exitCode, exitCode)
      assert.equal((await readFile(join(f.root, 'worker-' + behavior + '-sent'))).length, 0)
      if (behavior === 'result-hang') assert.ok(performance.now() - start >= 400, 'No grace period was allowed.')
      cleanScenario(child, response)
    })
  }
})

test('abort, timeout and close during retirement settle once and await actual exit', async t => {
  const f = await fixture(t)
  for (const behavior of ['result-delay-exit', 'result-hang']) {
    for (const [operation, code] of [['abort-retirement', 'STORE_CANCELLED'], ['timeout-retirement', 'STORE_TIMEOUT'], ['close-retirement', 'STORE_CLOSED']]) {
      await t.test(`${behavior}: ${operation}`, async () => {
        const child = scenario(f, behavior, 3000, [], true, operation)
        assert.equal(await bounded(child.closed, 5000), 0)
        const response = child.records.at(-1)
        assert.equal(response.ok, false)
        assert.equal(response.code, code)
        assert.equal(response.lifecycle.validResults, 1)
        assert.equal(response.lifecycle.sentRevision, f.first.revision)
        assert.equal(response.lifecycle.settledAtResult, false)
        assert.equal(response.lifecycle.forced, behavior === 'result-hang' ? 1 : 0)
        cleanScenario(child, response)
      })
    }
  }
})

for (const [behavior, code] of [['stdio', null], ['crash', 'WORKER_FAILED'], ['hang', 'STORE_TIMEOUT'], ['malformed-message', 'STORE_CORRUPT']]) {
  test(`worker ${behavior} is contained and the subprocess exits without leaked credentials or idle workers`, async t => {
    const f = await fixture(t), child = scenario(f, behavior, behavior === 'hang' ? 2000 : 3000)
    const result = await Promise.race([child.closed, delay(10000, 'timeout', { ref: false })])
    assert.equal(result, 0, 'Worker lifecycle did not finish deterministically.')
    const response = child.records.at(-1)
    assert.equal(response.ok, code === null)
    if (code) assert.equal(response.code, code)
    assert.equal((await readFile(join(f.root, 'worker-' + behavior + '-entered'))).length, 0)
    for (const secret of CANARIES) assert.ok(!(child.stdout + child.stderr).includes(secret))
  })
}

test('completed requests leave no idle Worker even without an explicit close', async t => {
  const f = await fixture(t), child = scenario(f, '', 3000, [], false)
  assert.equal(await Promise.race([child.closed, delay(10000, 'timeout', { ref: false })]), 0)
  assert.equal(child.records.at(-1).ok, true)
})

test('disabled experimental SQLite fails closed instead of using a writer or legacy fallback', async t => {
  const f = await fixture(t), before = await tree(f.root)
  const child = scenario(f, '', 3000, ['--no-experimental-sqlite'])
  assert.equal(await child.closed, 0)
  assert.equal(child.records.at(-1).code, 'UNSUPPORTED_RUNTIME')
  assert.deepEqual(await tree(f.root), before)
})

test('process-only startup arguments do not prevent valid Python-store reads', async t => {
  const f = await fixture(t)
  for (const args of [['--max-old-space-size=256'], ['--title', 'configuration-reader-test'],
    ['--max_old_space_size=256'], ['--title=configuration-reader-test']]) {
    await t.test(args[0], async () => {
      const child = scenario(f, '', 3000, args)
      assert.equal(await bounded(child.closed, 10000), 0)
      assert.equal(child.records.at(-1).ok, true)
      assert.equal(child.records.at(-1).revision, f.first.revision)
      for (const secret of CANARIES) assert.ok(!(child.stdout + child.stderr).includes(secret))
    })
  }
})

test('Worker inheritance preserves compatible preloads, SQLite and security flags', async t => {
  const f = await fixture(t)
  const verify = 'data:text/javascript,' + encodeURIComponent(`
    import {isMainThread,workerData} from 'node:worker_threads';
    import {writeFileSync} from 'node:fs'; import {join} from 'node:path';
    if(!isMainThread){
      let blocked=false; try{eval('1')}catch{blocked=true}
      if(!blocked || !process.execArgv.includes('--experimental-sqlite')) throw new Error(${JSON.stringify(CANARIES[12])});
      writeFileSync(join(workerData.root,'supported-flags-entered'),'');
    }
  `)
  const child = scenario(f, '', 3000, ['--max-old-space-size=256', '--experimental-sqlite',
    '--disallow-code-generation-from-strings', '--enable-source-maps', '--import', verify])
  assert.equal(await bounded(child.closed, 10000), 0)
  assert.equal(child.records.at(-1).ok, true)
  assert.equal(child.records.at(-1).revision, f.first.revision)
  assert.equal((await readFile(join(f.root, 'supported-flags-entered'))).length, 0)
  for (const secret of CANARIES) assert.ok(!(child.stdout + child.stderr).includes(secret))
})

test('incompatible SQLite or preload requirements are never stripped to force a read', async t => {
  const f = await fixture(t)
  for (const behavior of ['disabled SQLite', 'crash']) {
    await t.test(behavior, async () => {
      const child = scenario(f, behavior === 'crash' ? behavior : '', 3000,
        ['--max-old-space-size=256', ...(behavior === 'crash' ? [] : ['--no-experimental-sqlite'])])
      assert.equal(await bounded(child.closed, 10000), 0)
      assert.equal(child.records.at(-1).ok, false)
      assert.equal(child.records.at(-1).code, behavior === 'crash' ? 'WORKER_FAILED' : 'UNSUPPORTED_RUNTIME')
      if (behavior === 'crash') assert.equal((await readFile(join(f.root, 'worker-crash-entered'))).length, 0)
      for (const secret of CANARIES) assert.ok(!(child.stdout + child.stderr).includes(secret))
    })
  }
})

for (const disableBegin of [false, true]) {
  test(`read transaction ${disableBegin ? 'negative control' : 'holds metadata and document snapshot across publication'}`, async t => {
    const f = await fixture(t), child = scenario(f, disableBegin ? 'pause-no-begin' : 'pause', 30000)
    const ready = join(f.root, 'reader-ready')
    const until = Date.now() + 10000
    while (true) {
      try { await readFile(ready); break } catch { assert.ok(Date.now() < until && !child.ended); await delay(10) }
    }
    const writer = f.launch('update_gate')
    await writer.ready()
    await delay(100)
    if (disableBegin) {
      assert.equal(await Promise.race([writer.closed, delay(5000, 'timeout', { ref: false })]), 0)
    } else assert.equal(writer.ended, false, 'Writer committed while the reader transaction should hold its snapshot.')
    await writeFile(join(f.root, 'release-reader'), '')
    assert.equal(await child.closed, 0)
    assert.equal(await writer.closed, 0)
    const result = child.records.at(-1)
    if (disableBegin) {
      assert.equal(result.ok, false)
      assert.equal(result.code, 'STORE_CORRUPT')
    } else {
      assert.equal(result.ok, true)
      assert.equal(result.revision, f.first.revision)
      assert.equal(result.counter, 0)
      assert.equal(result.searchCounter, 0)
      assert.equal((await f.reader().readSnapshot()).documents.models.counter, 1)
    }
  })
}
