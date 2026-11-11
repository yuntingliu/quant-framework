import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { mkdtemp, realpath, rm, readFile, writeFile, mkdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, relative, basename } from 'node:path'
import { createInterface } from 'node:readline'
import childProcesses from 'node:child_process'
import { syncBuiltinESMExports } from 'node:module'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'
import { inspect } from 'node:util'
import { withConfigurationAuthority, openConfigurationAuthority, ConfigurationAuthorityError, nativeProfileRoot } from '../integrations/conexus/configuration-authority.mjs'
import { readActiveProvider } from '../integrations/conexus/model-providers.mjs'
import { configuredWebSearch } from '../integrations/conexus/web-search.mjs'
import { createCoordinatedAgentConfiguration } from '../integrations/conexus/configuration-backend.mjs'
import { configurationRootKey } from '../integrations/conexus/configuration-store-worker.mjs'

const python = process.env.ALPHALAB_AUTHORITY_PYTHON
assert.ok(python, 'Set the explicit test Python interpreter for authority leases')
const canaries = ['migration-model-key-6101', 'migration-model-key-6102', 'migration-brave-key-6103', 'migration-rq-password-6104']
function noSecrets(value) {
  const text = inspect(value, { depth: Infinity }) + JSON.stringify(value) + String(value?.stack || '')
  for (const secret of canaries) assert.ok(!text.includes(secret))
}
async function run(program, args, options = {}) {
  const child = spawn(program, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], ...options })
  let stdout = '', stderr = ''
  child.stdout.on('data', part => { stdout += part }); child.stderr.on('data', part => { stderr += part })
  const timer = setTimeout(() => child.kill(), 15000)
  try {
    const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve) })
    return { code, stdout, stderr }
  } finally { clearTimeout(timer) }
}
async function fixture(t, state = 'absent') {
  const base = await realpath(await mkdtemp(join(tmpdir(), 'alphalab-authority-node-'))), root = join(base, 'installation \u03a9')
  const resources = []
  t.after(async () => {
    for (const resource of resources.reverse()) await resource.close()
    assert.equal(dirname(base), await realpath(tmpdir()))
    await rm(base, { recursive: true, force: true })
  })
  const script = `
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
root, state = Path(sys.argv[1]), sys.argv[2]
for key in ('ALPHALAB_APP_DATA_DIR', 'ALPHALAB_CACHE_DIR', 'ALPHALAB_RUNTIME_DIR'):
    os.environ[key] = str(root / key)
from tests.api.test_configuration_migration_service import native_documents, write_sources
from tests.api.test_configuration_decision import value, seed
from alphalab.utils.configuration_store import ConfigurationStore
write_sources(root, native_documents())
documents = native_documents()
choices = {key: {'kind':'profile','id':doc['active_id']} for key,doc in documents.items()}
store = ConfigurationStore.initialize(root, documents=documents, selections=choices)
snapshot = store.read_snapshot()
report = SimpleNamespace(installation_id=snapshot.installation_id, revision=snapshot.revision, selection_kinds=('profile',)*3)
if state in ('prepared', 'committed', 'unsupported'):
    record = value(root, report, state='prepared' if state == 'unsupported' else state)
    if state == 'unsupported': record['protocol_version'] = 2
    seed(root, record, root.parent)
elif state in ('partial', 'corrupt'):
    directory = root / 'app/secrets/configuration-decision'
    directory.mkdir(mode=0o777 if os.name == 'nt' else 0o700)
    (directory / ('decision.pending' if state == 'partial' else 'decision.json')).write_text('invalid metadata')
print(json.dumps({'installation_id': snapshot.installation_id, 'revision': snapshot.revision}))
`
  const result = await run(python, ['-B', '-c', script, root, state])
  noSecrets(result.stderr); assert.equal(result.code, 0, 'Synthetic installation fixture failed')
  const metadata = JSON.parse(result.stdout)
  return { root, path: join(root, 'conexus/secrets/model-providers.json'), own: resource => resources.push(resource), ...metadata }
}

for (const state of ['absent', 'prepared']) test(`${state}: native JSON reads retain JSON authority and tolerate candidate drift`, async t => {
  const f = await fixture(t, state)
  const profile = await readActiveProvider(f.path, {})
  assert.equal(profile.api_key, canaries[0]) // Intentionally private internal result.
  const document = JSON.parse(await readFile(f.path, 'utf8'))
  document.profiles[0].name = 'Legitimate JSON edit'
  await writeFile(f.path, JSON.stringify(document))
  assert.equal((await readActiveProvider(f.path, {})).name, 'Legitimate JSON edit')
  const runtime = await createCoordinatedAgentConfiguration({ version: 1, root: f.root,
    root_key: configurationRootKey(f.root), backend: 'json', decision_id: 'f'.repeat(32), installation_id: null }, () => async () => ({ content: 'Synthetic' }))
  f.own(runtime)
  assert.equal(runtime.backend, 'json'); assert.equal(await runtime.modelConfigured(), true)
})

for (const state of ['committed', 'partial', 'corrupt', 'unsupported']) test(`${state}: authority blocks native JSON credentials and inconsistent Agent admission`, async t => {
  const f = await fixture(t, state), before = await readFile(f.path)
  await assert.rejects(readActiveProvider(f.path, {}), error => {
    assert.ok(error instanceof ConfigurationAuthorityError); noSecrets(error); assert.equal(error.cause, undefined); return true
  })
  for (const backend of ['json', 'sqlite']) {
    let constructed = false
    await assert.rejects(createCoordinatedAgentConfiguration({ version: 1, root: f.root,
      root_key: configurationRootKey(f.root), backend, decision_id: 'f'.repeat(32),
      installation_id: backend === 'json' ? null : f.installation_id }, () => { constructed = true; return async () => {} }), error => {
      assert.equal(error.code, 'BACKEND_UNAVAILABLE'); assert.equal(error.cause, undefined); noSecrets(error); return true
    })
    assert.equal(constructed, false)
  }
  assert.deepEqual(await readFile(f.path), before)
})

test('the real JSON Node entry rejects committed authority before loading the Conexus bundle', async t => {
  const f = await fixture(t, 'committed')
  const result = await run(process.execPath, [fileURLToPath(new URL('../integrations/conexus/local-host.mjs', import.meta.url))], {
    env: { ...process.env, ALPHALAB_MODEL_PROVIDERS_FILE: f.path, CONEXUS_LOCAL_ROOT: join(f.root, 'conexus/project') },
  })
  assert.notEqual(result.code, 0); assert.match(result.stderr, /AUTHORITY_UNAVAILABLE/)
  assert.ok(!result.stderr.includes('ERR_MODULE_NOT_FOUND')); noSecrets(result)
})

test('the Python helper holds real exclusion until the protected Node operation completes', async t => {
  const f = await fixture(t)
  await withConfigurationAuthority(f.root, 'json', async () => {
    const result = await run(python, ['-B', '-c', `
import sys
from alphalab.utils.configuration_maintenance import configuration_access
from alphalab.utils.configuration_store import ConfigurationStoreError
try:
    with configuration_access(sys.argv[1], exclusive=True): pass
except ConfigurationStoreError as error:
    print(error.code)
`, f.root])
    assert.equal(result.code, 0); assert.equal(result.stdout.trim(), 'STORE_BUSY'); noSecrets(result.stderr)
  })
  await withConfigurationAuthority(f.root, 'json', async () => {})
})

test('negative control detects Node accepting JSON despite a committed SQLite decision', async t => {
  const f = await fixture(t, 'committed')
  const source = await readFile(new URL('../integrations/conexus/configuration-authority.mjs', import.meta.url), 'utf8')
  const start = source.indexOf('export async function withConfigurationAuthority('), end = source.indexOf('export function nativeProfileRoot', start)
  assert.ok(start >= 0 && end > start)
  const weakened = source.slice(0, start) + 'export async function withConfigurationAuthority(root, backend, action) { return action() }\n' + source.slice(end)
  const unsafe = await import('data:text/javascript;base64,' + Buffer.from(weakened).toString('base64'))
  await assert.rejects(withConfigurationAuthority(f.root, 'json', async () => 'admitted'), { code: 'AUTHORITY_UNAVAILABLE' })
  await assert.rejects(assert.rejects(unsafe.withConfigurationAuthority(f.root, 'json', async () => 'admitted')),
    error => error.code === 'ERR_ASSERTION' && error.message.includes('Missing expected rejection'))
  await assert.rejects(withConfigurationAuthority(f.root, 'json', async () => 'admitted'), { code: 'AUTHORITY_UNAVAILABLE' })
})

test('callback errors are redacted and cleanup releases the real lease', async t => {
  const f = await fixture(t)
  await assert.rejects(withConfigurationAuthority(f.root, 'json', async () => { throw new Error(canaries[0]) }), error => {
    assert.equal(error.code, 'AUTHORITY_UNAVAILABLE'); assert.equal(error.cause, undefined); noSecrets(error); return true
  })
  await withConfigurationAuthority(f.root, 'json', async () => {})
})

test('abort suppression, hostile signals and close during acquisition leave no retained lease', async t => {
  const f = await fixture(t), controller = new AbortController()
  controller.signal.addEventListener('abort', event => event.stopImmediatePropagation())
  const pending = withConfigurationAuthority(f.root, 'json', async () => { assert.fail('Cancelled admission executed') }, controller.signal)
  controller.abort()
  await assert.rejects(pending, { code: 'AUTHORITY_UNAVAILABLE' })
  await assert.rejects(withConfigurationAuthority(f.root, 'json', async () => {}, controller.signal), { code: 'AUTHORITY_UNAVAILABLE' })
  let traps = 0
  const proxy = new Proxy({}, { get() { traps++; throw new Error(canaries[1]) } })
  await assert.rejects(withConfigurationAuthority(f.root, 'json', async () => {}, proxy), error => {
    assert.equal(error.code, 'AUTHORITY_UNAVAILABLE'); noSecrets(error); return true
  })
  assert.equal(traps, 0)
  for (const key of ['constructor', 'aborted', 'addEventListener', 'removeEventListener']) {
    const signal = new AbortController().signal
    Object.defineProperty(signal, key, { get() { traps++; throw new Error(canaries[0]) } })
    await assert.rejects(withConfigurationAuthority(f.root, 'json', async () => {}, signal), error => {
      assert.equal(error.code, 'AUTHORITY_UNAVAILABLE'); noSecrets(error); return true
    })
  }
  assert.equal(traps, 0)
  await withConfigurationAuthority(f.root, 'json', async () => {})
})

test('a borrowed live lease still rechecks durable authority before each native read', async t => {
  const f = await fixture(t, 'prepared'), path = join(f.root, 'app/secrets/configuration-decision/decision.json')
  const original = await readFile(path, 'utf8')
  await withConfigurationAuthority(f.root, 'json', async () => {
    // Deliberate test-owned violation of the cooperative transition contract.
    // Reusing an actual lease must not become an unvalidated decision cache.
    try {
      const changed = JSON.parse(original); changed.state = 'committed'
      await writeFile(path, JSON.stringify(changed))
      await assert.rejects(readActiveProvider(f.path, {}), { code: 'AUTHORITY_UNAVAILABLE' })
    } finally { await writeFile(path, original) }
    assert.equal((await readActiveProvider(f.path, {})).api_key, canaries[0])
  })
})

test('Windows Unicode case aliases preserve the canonical authority binding', { skip: process.platform !== 'win32' }, async t => {
  const f = await fixture(t, 'prepared')
  assert.equal((await readActiveProvider(f.path.toLowerCase(), {})).api_key, canaries[0])
})

test('an owned composition lease closes once and never reacquires implicitly after close', async t => {
  const f = await fixture(t, 'prepared'), authority = await openConfigurationAuthority(f.root, 'json')
  try {
    const profile = await authority.run(() => readActiveProvider(f.path, {}))
    assert.equal(profile.api_key, canaries[0])
  } finally {
    const first = authority.close(), second = authority.close()
    assert.equal(first, second)
    await first
  }
  await assert.rejects(authority.run(async () => 'must not run'), { code: 'AUTHORITY_UNAVAILABLE' })
  await withConfigurationAuthority(f.root, 'json', async () => {})
})

function aliases(path) {
  const values = [path, join(dirname(path), '..', 'secrets', basename(path)), relative(process.cwd(), path)]
  if (process.platform === 'win32') values.push(
    join(dirname(path), basename(path).toUpperCase()),
    path.replace(/conexus|app|secrets/g, name => name.toUpperCase()),
    path.toUpperCase(), path.replaceAll('\\', '/'), path + '.',
  )
  return values
}
for (const state of ['absent', 'prepared', 'committed', 'corrupt']) test(`native path aliases: ${state} preserves credential authority`, async t => {
  const f = await fixture(t, state), allowed = ['absent', 'prepared'].includes(state)
  const searchPath = join(f.root, 'conexus/secrets/search-providers.json')
  const searchDocument = JSON.parse(await readFile(searchPath, 'utf8'))
  Object.assign(searchDocument.profiles[0], { provider: 'brave', model_profile_id: null })
  await writeFile(searchPath, JSON.stringify(searchDocument))
  for (const file of [f.path, searchPath, join(f.root, 'app/secrets/rqdata-profiles.json')]) {
    for (const alias of aliases(file)) {
      const root = nativeProfileRoot(alias)
      assert.ok(root, `Native alias misclassified: ${basename(alias)}`)
      assert.equal(configurationRootKey(root), configurationRootKey(f.root))
    }
  }
  const exercise = async () => { for (const alias of aliases(f.path)) {
    if (allowed) assert.equal((await readActiveProvider(alias, {})).api_key, canaries[0])
    else await assert.rejects(readActiveProvider(alias, {}), { code: 'AUTHORITY_UNAVAILABLE' })
    let dispatched = false
    const search = configuredWebSearch(alias, { env: {}, fetchImpl: async (_url, request) => {
      dispatched = true
      assert.equal(request.headers['X-Subscription-Token'], canaries[2])
      return Response.json({ web: { results: [] } })
    } })
    const result = await search({ input: { query: 'synthetic query' } })
    assert.equal(dispatched, allowed)
    assert.equal(result.result.success, allowed)
    noSecrets(result)
  } }
  // Reuse one real helper in allowed states; every nested adapter call still
  // enters/leaves its own OS-backed execution credit. Denied states stay direct.
  if (allowed) await withConfigurationAuthority(f.root, 'json', exercise)
  else await exercise()
})

test('negative control detects the original Windows alias credential bypass', { skip: process.platform !== 'win32' }, async t => {
  const f = await fixture(t, 'committed'), alias = f.path.toUpperCase()
  const source = await readFile(new URL('../integrations/conexus/configuration-authority.mjs', import.meta.url), 'utf8')
  const start = source.indexOf('export async function withJsonProfileAuthority(')
  assert.ok(start > 0)
  const weakened = source.slice(0, start) + `export async function withJsonProfileAuthority(path, action) {
    return path.endsWith('model-providers.json') ? withConfigurationAuthority(rootForCanonical(path), 'json', () => action(path)) : action(path)
  }`
  const unsafe = await import('data:text/javascript;base64,' + Buffer.from(weakened).toString('base64'))
  const leaked = await unsafe.withJsonProfileAuthority(alias, path => readFile(path, 'utf8'))
  assert.ok(leaked.includes(canaries[0]))
  await assert.rejects(assert.rejects(Promise.resolve(leaked)),
    error => error.code === 'ERR_ASSERTION' && error.message.includes('Missing expected rejection'))
  await assert.rejects(readActiveProvider(alias, {}), { code: 'AUTHORITY_UNAVAILABLE' })
})

async function offlineProbe(root, timeout = 0.1) {
  const result = await run(python, ['-B', '-c', `
import json, sys
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_deployment import coordinated_offline, DeploymentPreparationError
owner=ConfigurationBackend.json(sys.argv[1]); owner.close()
stopped=[]
try:
    with coordinated_offline(owner, stop_services=lambda: stopped.append(True), timeout=float(sys.argv[2])):
        print(json.dumps({'offline':True, 'stopped':bool(stopped)}))
except DeploymentPreparationError as error:
    print(json.dumps({'offline':False, 'stopped':bool(stopped), 'code':error.code}))
`, root, String(timeout)])
  assert.equal(result.code, 0); noSecrets(result)
  return JSON.parse(result.stdout)
}
test('idle Agent reaches coordinated stop; new work is fenced until owned helper exit', async t => {
  const f = await fixture(t), scope = await openConfigurationAuthority(f.root, 'json')
  f.own(scope)
  const child = spawn(python, ['-B', '-c', `
import sys
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_deployment import coordinated_offline
owner=ConfigurationBackend.json(sys.argv[1]); owner.close()
def stop():
    print('stop',flush=True)
    assert sys.stdin.readline() == 'stopped\\n'
with coordinated_offline(owner,stop_services=stop,timeout=5):
    print('offline',flush=True)
`, f.root], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] })
  const lines = createInterface({ input: child.stdout }), iterator = lines[Symbol.asyncIterator]()
  const exited = new Promise(resolve => child.once('close', resolve))
  let stderr = ''
  child.stderr.on('data', part => { stderr += part })
  const timer = setTimeout(() => child.kill(), 12000)
  try {
    assert.equal((await iterator.next()).value, 'stop', 'Idle Agent blocked its stop callback')
    await assert.rejects(scope.run(async () => assert.fail('Shutdown admitted new work')), { code: 'AUTHORITY_UNAVAILABLE' })
    await scope.close()
    child.stdin.end('stopped\n')
    assert.equal((await iterator.next()).value, 'offline')
    assert.equal(await exited, 0); noSecrets(stderr)
  } finally { clearTimeout(timer); child.stdin.end(); lines.close(); if (child.exitCode === null) { child.kill(); await exited } }
})
test('unconfirmed idle Agent shutdown cannot mint offline mutation authority', async t => {
  const f = await fixture(t), scope = await openConfigurationAuthority(f.root, 'json'); f.own(scope)
  assert.deepEqual(await offlineProbe(f.root), { offline: false, stopped: true, code: 'BUSY' })
  assert.equal(await scope.run(async () => 'still-owned'), 'still-owned')
  await scope.close()
  assert.deepEqual(await offlineProbe(f.root), { offline: true, stopped: true })
})
test('negative control detects an idle helper retaining execution-shared admission', async t => {
  const f = await fixture(t)
  const original = await readFile(new URL('../alphalab/utils/configuration_authority.py', import.meta.url), 'utf8')
  const needle = 'with ExitStack() as lifetime, ExitStack() as work:'
  assert.ok(original.includes(needle))
  const vulnerable = original.replace(needle, 'with ExitStack() as lifetime, ExitStack() as work, execution_access(root):')
  const child = spawn(python, ['-B', '-c', vulnerable, f.root, 'json'], { windowsHide: true, stdio: ['pipe', 'pipe', 'ignore'] })
  const lines = createInterface({ input: child.stdout }), iterator = lines[Symbol.asyncIterator]()
  const exited = new Promise(resolve => child.once('close', resolve)), timer = setTimeout(() => child.kill(), 12000)
  try {
    assert.equal((await iterator.next()).value, 'leased')
    const result = await offlineProbe(f.root)
    assert.equal(result.code, 'BUSY')
    assert.throws(() => assert.equal(result.stopped, true, 'Idle helper must allow coordinated stop'), /Idle helper must allow coordinated stop/)
  } finally { child.stdin.end(); await exited; clearTimeout(timer); lines.close() }
  assert.deepEqual(await offlineProbe(f.root), { offline: true, stopped: true })
})

async function cancelledBorrow(t, { lateReject = false, fresh = false } = {}) {
  const f = await fixture(t), scope = await openConfigurationAuthority(f.root, 'json'); f.own(scope)
  const controller = new AbortController()
  controller.signal.addEventListener('abort', event => event.stopImmediatePropagation())
  let begin, release, ended
  const started = new Promise(resolve => { begin = resolve }), hold = new Promise(resolve => { release = resolve })
  const retired = new Promise(resolve => { ended = resolve })
  const action = async signal => {
    begin()
    try { await hold; assert.equal(signal.aborted, true); if (lateReject) throw new Error(canaries[0]); return canaries[1] }
    finally { ended() }
  }
  const pending = fresh ? withConfigurationAuthority(f.root, 'json', action, controller.signal) : scope.run(action, controller.signal)
  const rejected = assert.rejects(pending, error => { assert.equal(error.code, 'AUTHORITY_UNAVAILABLE'); noSecrets(error); return true })
  try {
    await started; controller.abort()
    await Promise.race([rejected, delay(700).then(() => assert.fail('Cancelled borrowed call stayed pending'))])
    // The callback is still alive: its actual execution credit must remain held.
    assert.deepEqual(await offlineProbe(f.root), { offline: false, stopped: false, code: 'BUSY' })
    assert.equal(await scope.run(async () => 'shared owner survived'), 'shared owner survived')
  } finally { release(); await retired; await rejected }
  // Flush the helper's release acknowledgement before testing reuse/retirement.
  assert.equal(await scope.run(async () => 'retired'), 'retired')
}
test('borrowed abort settles promptly but retains callback lease through late success', async t => cancelledBorrow(t))
test('borrowed abort safely observes late credential-bearing rejection', async t => cancelledBorrow(t, { lateReject: true }))
test('fresh abort also retains execution protection until callback retirement', async t => cancelledBorrow(t, { fresh: true }))

test('concurrent borrowed calls cancel independently and leave the shared owner usable', async t => {
  const f = await fixture(t), scope = await openConfigurationAuthority(f.root, 'json'); f.own(scope)
  let release, allStarted, count = 0
  const hold = new Promise(resolve => { release = resolve }), started = new Promise(resolve => { allStarted = resolve })
  const controllers = Array.from({ length: 6 }, () => new AbortController())
  const pending = controllers.map((controller, index) => scope.run(async () => {
    if (++count === controllers.length) allStarted()
    await hold
    return index
  }, controller.signal))
  const outcomes = pending.map(promise => promise.then(value => ({ value }), error => { noSecrets(error); return { code: error.code } }))
  try {
    await started
    controllers.forEach((controller, index) => { if (index % 2 === 0) controller.abort() })
    for (const index of [0, 2, 4]) assert.deepEqual(await outcomes[index], { code: 'AUTHORITY_UNAVAILABLE' })
  } finally { release() }
  for (const index of [1, 3, 5]) assert.deepEqual(await outcomes[index], { value: index })
  assert.equal(await scope.run(async () => 'healthy'), 'healthy')
})

test('close is bounded while cancelled work retains its actual lease until completion', async t => {
  const f = await fixture(t), scope = await openConfigurationAuthority(f.root, 'json')
  let release, begin
  const hold = new Promise(resolve => { release = resolve }), started = new Promise(resolve => { begin = resolve })
  const request = scope.run(async () => { begin(); await hold; return canaries[0] })
  const rejected = assert.rejects(request, error => { noSecrets(error); return error.code === 'AUTHORITY_UNAVAILABLE' })
  try {
    await started
    const first = scope.close(); assert.equal(scope.close(), first)
    await assert.rejects(first, { code: 'AUTHORITY_UNAVAILABLE' }); await rejected
    assert.deepEqual(await offlineProbe(f.root), { offline: false, stopped: false, code: 'BUSY' })
  } finally { release(); await rejected }
  // Retry only the exclusive probe: never force a shared helper down early.
  for (let attempt = 0; ; attempt++) {
    const result = await offlineProbe(f.root)
    if (result.offline) break
    assert.ok(attempt < 3); await delay(50)
  }
})

test('borrowed helper exit rejects promptly without leaking late callback exceptions', async t => {
  const f = await fixture(t), original = childProcesses.spawn
  let helper, scope, release, begin
  try {
    childProcesses.spawn = (...args) => { const child = original(...args); if (args[1]?.includes('alphalab.utils.configuration_authority')) helper = child; return child }
    syncBuiltinESMExports()
    scope = await openConfigurationAuthority(f.root, 'json')
  } finally { childProcesses.spawn = original; syncBuiltinESMExports() }
  assert.ok(helper)
  const hold = new Promise(resolve => { release = resolve }), started = new Promise(resolve => { begin = resolve })
  const pending = scope.run(async () => { begin(); await hold; throw new Error(canaries[2]) })
  const rejected = assert.rejects(pending, error => { noSecrets(error); return error.code === 'AUTHORITY_UNAVAILABLE' })
  try {
    await started; helper.kill()
    await Promise.race([rejected, delay(700).then(() => assert.fail('Borrowed helper exit did not settle request'))])
  } finally { release(); await rejected; await scope.close().catch(error => noSecrets(error)) }
})

test('negative control detects direct awaiting of a cancelled borrowed callback', async t => {
  const f = await fixture(t)
  const source = (await readFile(new URL('../integrations/conexus/configuration-authority.mjs', import.meta.url), 'utf8'))
    .replace("fileURLToPath(new URL('../..', import.meta.url))", 'process.cwd()')
  const needle = 'value = await runCall(owner, action, signal)'
  assert.ok(source.includes(needle))
  const unsafe = await import('data:text/javascript;base64,' + Buffer.from(source.replace(needle,
    'value = owned ? await runCall(owner, action, signal) : await action(signal)')).toString('base64'))
  const scope = await unsafe.openConfigurationAuthority(f.root, 'json'), controller = new AbortController()
  let release, begin
  const hold = new Promise(resolve => { release = resolve }), started = new Promise(resolve => { begin = resolve })
  const pending = scope.run(async () => { begin(); await hold }, controller.signal)
  let settled = false
  const observed = pending.then(() => { settled = true }, () => { settled = true })
  try {
    await started; controller.abort(); await delay(100)
    assert.throws(() => assert.equal(settled, true, 'Borrowed abort must settle promptly'), /Borrowed abort must settle promptly/)
  } finally { release(); await observed; await scope.close() }
  await withConfigurationAuthority(f.root, 'json', async () => 'correction remains active')
})

test('closing a borrowed handle cancels only its outward calls and retains the shared lease', async t => {
  const f = await fixture(t), parent = await openConfigurationAuthority(f.root, 'json'); f.own(parent)
  const borrowed = await parent.run(() => openConfigurationAuthority(f.root, 'json'))
  let begin, release
  const started = new Promise(resolve => { begin = resolve }), hold = new Promise(resolve => { release = resolve })
  const pending = borrowed.run(async () => { begin(); await hold; return canaries[0] })
  const rejected = assert.rejects(pending, error => { noSecrets(error); return error.code === 'AUTHORITY_UNAVAILABLE' })
  try {
    await started; await borrowed.close(); await rejected
    assert.deepEqual(await offlineProbe(f.root), { offline: false, stopped: false, code: 'BUSY' })
    assert.equal(await parent.run(async () => 'shared owner survived'), 'shared owner survived')
  } finally { release(); await rejected }
  await assert.rejects(borrowed.run(async () => 'closed'), { code: 'AUTHORITY_UNAVAILABLE' })
  assert.equal(await parent.run(async () => 'healthy'), 'healthy')
})

// New deny-only witnesses must agree with Python before ANY native credential
// read. File presence blocks admission; it never manufactures SQLite authority.
for (const fence of ['configuration-recovery', 'configuration-backup-only.json', 'configuration-owner.json']) {
  test(`recovery/backup authority fence agrees across Python and Node: ${fence}`, async t => {
    const f = await fixture(t, 'absent')
    const filename = join(f.root, 'app/secrets', fence)
    if (fence === 'configuration-recovery') await mkdir(filename, { mode: process.platform === 'win32' ? 0o777 : 0o700 })
    else await writeFile(filename, 'deny-only test witness', { mode: 0o600 })
    const result = await run(python, ['-B', '-c', `
import sys
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_store import ConfigurationStoreError
try:
    owner=ConfigurationBackend.production(sys.argv[1])
except ConfigurationStoreError:
    print('rejected')
else:
    owner.close()
    raise AssertionError('Witness bypassed authority')
`, f.root])
    assert.equal(result.code, 0); assert.equal(result.stdout.trim(), 'rejected'); noSecrets(result)
    await assert.rejects(readActiveProvider(f.path, {}), error => { noSecrets(error); return true })
    await assert.rejects(openConfigurationAuthority(f.root, 'json'), error => { noSecrets(error); return true })
  })
}
