import test from 'node:test'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../', import.meta.url))
const pythonExecutable = [process.env.COLLABORATION_TEST_PYTHON, 'build/member1/venv/Scripts/python.exe', '.venv/Scripts/python.exe', '.venv/bin/python']
  .filter(Boolean).map(path => resolve(root, path)).find(path => existsSync(path))
const built = existsSync(new URL('../build/conexus/packages/runtime-core/dist/index.js', import.meta.url)) && Boolean(pythonExecutable)
const runner = built ? await import('../scripts/check_collaboration_foundation.mjs') : null
for (const scenario of runner?.scenarios ?? ['runtime unavailable']) {
  test(`collaboration real Host: ${scenario}`, { skip: !built, timeout: 240000 }, async () => {
    await runner.runScenario({ scenario, pythonExecutable,
      output: join(root, 'build/member1/host-tests', `${scenario}-${randomUUID()}`) })
  })
}
