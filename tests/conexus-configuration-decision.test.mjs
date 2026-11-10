import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtemp, realpath, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { spawn } from 'node:child_process'
import { inspect } from 'node:util'
import { parseDecisionRecord, validateDecisionForRoot, ConfigurationDecisionError } from '../integrations/conexus/configuration-decision.mjs'
import { configurationRootKey } from '../integrations/conexus/configuration-store-worker.mjs'
import { ConfigurationStoreReader } from '../integrations/conexus/configuration-store.mjs'

const ROOT_KEY = 'a'.repeat(64), CANARY = 'synthetic-decision-private-credential-9317'
function record(state = 'prepared') {
  return { protocol_version: 1, state, backend: 'sqlite', root_key: ROOT_KEY,
    installation_id: 'b'.repeat(32), decision_id: 'c'.repeat(32), revision: 'd'.repeat(32),
    evidence: { kind: 'legacy-exact-v1', verification_id: 'e'.repeat(32), migration_revision: 'd'.repeat(32),
      store_protocol_version: 1, selection_kinds: { models: 'profile', search: 'none', rqdata: 'none' } } }
}
function noSecrets(value) {
  const text = inspect(value, { depth: Infinity }) + JSON.stringify(value) + (value?.stack || '')
  assert.ok(!text.includes(CANARY))
  for (const token of ['migration-model-key-6101', 'migration-brave-key-6103', 'migration-rq-password-6104']) assert.ok(!text.includes(token))
}
function invalid(action) {
  assert.throws(action, error => {
    assert.ok(error instanceof ConfigurationDecisionError); assert.equal(error.code, 'DECISION_UNAVAILABLE')
    assert.equal(error.cause, undefined); noSecrets(error); return true
  })
}

for (const state of ['prepared', 'committed']) test(`strict ${state} metadata is detached, frozen and never constructs a backend`, () => {
  const value = record(state), parsed = parseDecisionRecord(JSON.stringify(value), { rootKey: ROOT_KEY })
  assert.deepEqual(JSON.parse(JSON.stringify(parsed)), value)
  assert.ok(Object.isFrozen(parsed) && Object.isFrozen(parsed.evidence) && Object.isFrozen(parsed.evidence.selection_kinds))
  assert.throws(() => { parsed.backend = 'json' })
  noSecrets(parsed)
})

const selectionValues = { models: 'profile', rqdata: 'environment', search: 'none' }
const selectionKeyCases = [
  ...[
    ['models', 'rqdata', 'search'], ['models', 'search', 'rqdata'],
    ['rqdata', 'models', 'search'], ['rqdata', 'search', 'models'],
    ['search', 'models', 'rqdata'], ['search', 'rqdata', 'models'],
  ].map(keys => [keys.join('/'), Object.fromEntries(keys.map(key => [key, selectionValues[key]])), true]),
  ['single-combined-key', { 'models,rqdata,search': 'none' }, false],
  ['combined-pair', { 'models,rqdata': 'environment', search: 'none' }, false],
  ['combined-plus-individuals', { 'models,rqdata,search': 'none', models: 'profile', search: 'none' }, false],
  ['combined-plus-all-required', { ...selectionValues, 'models,rqdata,search': 'none' }, false],
  ...Object.keys(selectionValues).map(missing => [
    `missing-${missing}`, Object.fromEntries(Object.entries(selectionValues).filter(([key]) => key !== missing)), false,
  ]),
  ['unexpected-field', { ...selectionValues, extra: 'none' }, false],
]

for (const state of ['prepared', 'committed']) for (const [name, selections, valid] of selectionKeyCases) {
  test(`selection keys ${state}: ${name}`, async t => {
    const base = await mkdtemp(join(tmpdir(), 'alphalab-decision-keys-')), root = await realpath(base)
    t.after(async () => { assert.equal(dirname(root), await realpath(tmpdir())); await rm(root, { recursive: true, force: true }) })
    const value = record(state); value.root_key = configurationRootKey(root)
    // Prove the root and all unrelated metadata are valid before changing keys.
    assert.equal((await validateDecisionForRoot(JSON.stringify(value), root)).state, state)
    value.evidence.selection_kinds = selections
    const wire = JSON.stringify(value)
    if (valid) {
      const parsed = parseDecisionRecord(wire, { rootKey: value.root_key })
      assert.deepEqual(JSON.parse(JSON.stringify(parsed)), value)
      assert.deepEqual(await validateDecisionForRoot(wire, root), parsed)
      noSecrets(parsed)
    } else {
      invalid(() => parseDecisionRecord(wire, { rootKey: value.root_key }))
      await assert.rejects(validateDecisionForRoot(wire, root), error => {
        assert.ok(error instanceof ConfigurationDecisionError); assert.equal(error.code, 'DECISION_UNAVAILABLE')
        assert.equal(error.cause, undefined); noSecrets(error); return true
      })
    }
  })
}

for (const [path, value] of [
  [['protocol_version'], 2], [['protocol_version'], true], [['protocol_version'], '1'],
  [['state'], 'active'], [['backend'], 'json'], [['root_key'], 'f'.repeat(64)],
  [['installation_id'], '../private'], [['decision_id'], ''], [['revision'], null], [['token'], CANARY],
  [['evidence', 'password'], CANARY], [['evidence', 'kind'], 'backup'],
  [['evidence', 'verification_id'], CANARY], [['evidence', 'store_protocol_version'], true],
  [['evidence', 'migration_revision'], 'f'.repeat(32)], [['evidence', 'selection_kinds', 'models'], 'auto'],
]) test('unknown, credential-bearing or wrongly typed metadata is rejected without echo', () => {
  const valueRecord = record(), parent = path.slice(0, -1).reduce((value, key) => value[key], valueRecord)
  parent[path.at(-1)] = value
  invalid(() => parseDecisionRecord(JSON.stringify(valueRecord), { rootKey: ROOT_KEY }))
})

for (const edit of [
  wire => '{"protocol_version":0,' + wire.slice(1),
  wire => '{"\\u0070rotocol_version":0,' + wire.slice(1),
  wire => wire.replace('"verification_id":', '"verification_id":"bad","verification_id":'),
  wire => wire.replace('"protocol_version":1', '"protocol_version":1.0'),
  wire => wire.replace('"protocol_version":1', '"protocol_version":NaN'),
  wire => wire + ' true', wire => wire.slice(0, -1), () => ' '.repeat(16385),
]) test('duplicate decoded keys, invalid tokens and oversized records fail closed', () => {
  invalid(() => parseDecisionRecord(edit(JSON.stringify(record())), { rootKey: ROOT_KEY }))
})

test('expected installation and revision are strict; hostile options never execute getters', () => {
  const input = JSON.stringify(record())
  invalid(() => parseDecisionRecord(input, { rootKey: ROOT_KEY, installationId: 'f'.repeat(32) }))
  invalid(() => parseDecisionRecord(input, { rootKey: ROOT_KEY, revision: 'f'.repeat(32) }))
  let invoked = 0
  invalid(() => parseDecisionRecord(input, { get rootKey() { invoked++; throw new Error(CANARY) } }))
  invalid(() => parseDecisionRecord(input, new Proxy({}, { ownKeys() { invoked++; throw new Error(CANARY) } })))
  assert.equal(invoked, 0)
})

test('raw bytes use the same strict UTF-8 and no-BOM policy as Python', () => {
  const wire = Buffer.from(JSON.stringify(record()))
  assert.equal(parseDecisionRecord(wire, { rootKey: ROOT_KEY }).state, 'prepared')
  invalid(() => parseDecisionRecord(Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), wire]), { rootKey: ROOT_KEY }))
  invalid(() => parseDecisionRecord(Buffer.from([0xff]), { rootKey: ROOT_KEY }))
})

test('root validation rejects noncanonical or foreign roots without any SQLite discovery', async t => {
  const base = await mkdtemp(join(tmpdir(), 'alphalab-decision-node-')), root = await realpath(base)
  t.after(async () => { assert.equal(dirname(root), await realpath(tmpdir())); await rm(root, { recursive: true, force: true }) })
  const value = record(); value.root_key = configurationRootKey(root)
  assert.equal((await validateDecisionForRoot(JSON.stringify(value), root)).state, 'prepared')
  await assert.rejects(validateDecisionForRoot(JSON.stringify(value), dirname(root)), { code: 'DECISION_UNAVAILABLE' })
  // No native database was needed or initialized by this parser.
  assert.equal((await import('node:fs')).existsSync(join(root, 'app/secrets/configuration-store.sqlite3')), false)
})

test('real Python offline preparation and Node consume identical decision/store identities', async t => {
  const base = await mkdtemp(join(tmpdir(), 'alphalab-decision-interop-')), root = join(base, 'installation Ω')
  const python = process.env.ALPHALAB_TEST_PYTHON
  assert.ok(python, 'Set the explicit test Python executable')
  const script = `
import json, os, sys
from pathlib import Path
for name in ('ALPHALAB_APP_DATA_DIR', 'ALPHALAB_CACHE_DIR', 'ALPHALAB_RUNTIME_DIR'):
    os.environ[name] = str(Path(sys.argv[1]) / name)
from tests.api.test_configuration_migration_service import native_documents, write_sources
from apps.api.services.configuration_migration_service import migrate_legacy_configuration
from apps.api.services.configuration_decision_service import prepare_sqlite_candidate
from alphalab.utils.configuration_backend import ConfigurationBackend
root = Path(sys.argv[1])
write_sources(root, native_documents())
report = migrate_legacy_configuration(root, node_executable=sys.argv[2], acknowledge_offline=True)
owner = ConfigurationBackend.json(root)
owner.close()
record = prepare_sqlite_candidate(owner, node_executable=sys.argv[2], expected_installation_id=report.installation_id,
    expected_revision=report.revision, acknowledge_offline=True)
print(json.dumps(record))
`
  const child = spawn(python, ['-B', '-c', script, root, process.execPath], { windowsHide: true,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, stdio: ['ignore', 'pipe', 'pipe'] })
  let output = '', diagnostics = ''
  child.stdout.on('data', part => { output += part }); child.stderr.on('data', part => { diagnostics += part })
  const timer = setTimeout(() => child.kill(), 20000)
  const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve) })
  clearTimeout(timer)
  t.after(async () => { assert.equal(dirname(base), tmpdir()); await rm(base, { recursive: true, force: true }) })
  noSecrets(diagnostics); assert.equal(code, 0, 'Synthetic preparation bridge failed')
  const value = await validateDecisionForRoot(output, root)
  const reader = new ConfigurationStoreReader(root)
  t.after(() => reader.close())
  const snapshot = await reader.readSnapshot()
  const validated = parseDecisionRecord(output, { rootKey: configurationRootKey(root), installationId: snapshot.installation_id, revision: snapshot.revision })
  assert.equal(validated.decision_id, value.decision_id)
  assert.equal(validated.state, 'prepared'); noSecrets(output)
  assert.equal(snapshot.selections.models.kind, 'profile')
  assert.equal(Object.hasOwn(snapshot.documents, 'rqdata'), false)
  assert.equal(Object.keys(snapshot.documents).length, 2)
  assert.equal(snapshot.protocol_version, value.evidence.store_protocol_version)
})
