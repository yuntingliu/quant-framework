import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../', import.meta.url))
const pythonExecutable = [process.env.COLLABORATION_TEST_PYTHON, 'build/member1/venv/Scripts/python.exe', '.venv/Scripts/python.exe', '.venv/bin/python']
  .filter(Boolean).map(path => resolve(root, path)).find(existsSync)
const built = existsSync(new URL('../build/conexus/packages/runtime-core/dist/index.js', import.meta.url)) && Boolean(pythonExecutable)
const runner = built ? await import('../scripts/lib/collaboration-acceptance.mjs') : null
const cases = ['clean', 'seeded-formula', 'seeded-pit', 'seeded-report', 'seeded-claim', 'missing-evidence', 'disputed',
  'correction', 'stale-candidate', 'hash-mismatch', 'duplicate-delivery', 'staging-interruption', 'accepted-recovery']
for (const scenario of cases) {
  test(`foundation integrated acceptance: ${scenario}`, { skip: !built, timeout: 240000 }, async () => {
    await runner.runAcceptanceScenario({ scenario, pythonExecutable,
      output: join(root, 'build/member1/acceptance-tests', `${scenario}-${randomUUID()}`) })
  })
}
test('foundation artifacts reject directories outside ignored build', { skip: !built }, async () => {
  await assert.rejects(runner.privateOutput(join(root, 'tests/fixtures/unsafe-output')))
})
