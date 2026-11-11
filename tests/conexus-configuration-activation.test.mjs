import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { mkdtemp, realpath, rm, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { inspect } from 'node:util'
import { ConfigurationStoreReader } from '../integrations/conexus/configuration-store.mjs'
import { createCoordinatedAgentConfiguration } from '../integrations/conexus/configuration-backend.mjs'

const python = process.env.ALPHALAB_TEST_PYTHON
assert.ok(python)
const bridge = fileURLToPath(new URL('./fixtures/configuration_activation_bridge.py', import.meta.url))
const canaries = ['migration-model-key-6101', 'migration-model-key-6102', 'migration-brave-key-6103', 'migration-rq-password-6104']
function privateErrors(value) {
  const text = inspect(value, { depth: Infinity }) + String(value?.stack) + JSON.stringify(value)
  for (const canary of canaries) assert.ok(!text.includes(canary))
}
async function execute(program, args, input) {
  const child = spawn(program, args, { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] })
  let stdout = '', stderr = ''
  child.stdout.on('data', part => { stdout += part }); child.stderr.on('data', part => { stderr += part })
  child.stdin.on('error', () => {})
  const timer = setTimeout(() => child.kill(), 45000)
  try {
    child.stdin.end(input)
    const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve) })
    privateErrors(stderr)
    assert.equal(code, 0, 'Test-owned subprocess failed')
    return stdout
  } finally { clearTimeout(timer) }
}
async function fixture(t, fault) {
  const base = await realpath(await mkdtemp(join(tmpdir(), 'alphalab-activation-node-')))
  t.after(async () => {
    assert.equal(dirname(base), await realpath(tmpdir()))
    await rm(base, { recursive: true, force: true })
  })
  const result = await execute(python, ['-B', bridge, base, process.execPath, fault])
  return { ...JSON.parse(result), base }
}
for (const fault of ['anchor', 'descendant', 'fork', 'missing', 'cycle', 'rollback']) test(`Python/Node committed ancestry: ${fault}`, async t => {
  const f = await fixture(t, fault)
  const reader = new ConfigurationStoreReader(f.root, { activation: JSON.stringify(f.record) })
  try {
    if (['anchor', 'descendant'].includes(fault)) {
      const snapshot = await reader.readSnapshot()
      assert.equal(snapshot.revision, f.revision)
      assert.equal(snapshot.installation_id, f.record.installation_id)
      assert.equal(Object.hasOwn(snapshot.documents, 'rqdata'), false)
      assert.equal(snapshot.documents.models.counter, snapshot.documents.search.counter)
      privateErrors(snapshot)
    } else await assert.rejects(reader.readSnapshot(), error => {
      assert.equal(error.code, 'STORE_CORRUPT'); privateErrors(error); return true
    })
  } finally { await reader.close() }
})

test('committed Node production rejects a matching handoff dictionary without a real owner proof', async t => {
  const f = await fixture(t, 'anchor')
  let constructed = false
  const contract = { version: 1, root: f.root, root_key: f.record.root_key, backend: 'sqlite',
    installation_id: f.record.installation_id, decision_id: f.record.decision_id }
  for (const ownerProof of [undefined, '0'.repeat(64)]) {
    await assert.rejects(createCoordinatedAgentConfiguration(contract, () => {
      constructed = true; return async () => ({ content: 'must not execute' })
    }, { production: true, ownerProof }), error => {
      assert.equal(error.code, 'BACKEND_UNAVAILABLE'); privateErrors(error); return true
    })
    assert.equal(constructed, false)
  }
})

test('negative control detects disabling the Node ancestry audit in memory', async t => {
  const f = await fixture(t, 'fork')
  const reader = new ConfigurationStoreReader(f.root, { activation: JSON.stringify(f.record) })
  try { await assert.rejects(reader.readSnapshot(), { code: 'STORE_CORRUPT' }) }
  finally { await reader.close() }
  const worker = fileURLToPath(new URL('../integrations/conexus/configuration-store-worker.mjs', import.meta.url))
  const needle = 'if (activation !== undefined) auditAncestry(execute, activation, meta, location.key)'
  assert.ok((await readFile(worker, 'utf8')).includes(needle))
  const preload = join(f.base, 'negative-control.mjs')
  await writeFile(preload, `import { registerHooks } from 'node:module'; import { readFileSync } from 'node:fs';
    registerHooks({ load(url, context, next) {
      if (url === ${JSON.stringify(new URL('../integrations/conexus/configuration-store-worker.mjs', import.meta.url).href)}) {
        return { format: 'module', shortCircuit: true, source: readFileSync(${JSON.stringify(worker)}, 'utf8').replace(${JSON.stringify(needle)}, '') }
      }
      return next(url, context)
    } });`)
  const module = new URL('../integrations/conexus/configuration-store.mjs', import.meta.url).href
  const script = `import { ConfigurationStoreReader } from ${JSON.stringify(module)};
    let input='';for await (const part of process.stdin) input+=part;
    const value=JSON.parse(input), reader=new ConfigurationStoreReader(value.root,{activation:JSON.stringify(value.record)});
    try {await reader.readSnapshot();process.stdout.write('accepted-fork')}finally{await reader.close()}`
  const outcome = await execute(process.execPath, ['--import', pathToFileURL(preload).href, '--input-type=module', '--eval', script], JSON.stringify(f))
  assert.throws(() => assert.notEqual(outcome, 'accepted-fork', 'Disabled audit accepted a fork'), /Disabled audit accepted a fork/)
  const restored = new ConfigurationStoreReader(f.root, { activation: JSON.stringify(f.record) })
  try { await assert.rejects(restored.readSnapshot(), { code: 'STORE_CORRUPT' }) }
  finally { await restored.close() }
})
