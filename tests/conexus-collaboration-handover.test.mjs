import test from 'node:test'
import { existsSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'

const root = fileURLToPath(new URL('../', import.meta.url))
const pythonExecutable = [process.env.COLLABORATION_TEST_PYTHON, '.venv/Scripts/python.exe', '.venv/bin/python', 'build/member1/venv/Scripts/python.exe']
  .filter(Boolean).map(value => resolve(root, value)).find(existsSync)
const built = existsSync(join(root, 'build/conexus/packages/runtime-core/dist/index.js')) && Boolean(pythonExecutable)
test('Member 2 walkthrough uses documented task/evidence/Host/result/budget interfaces', { skip: !built, timeout: 180000 }, async () => {
  const { runHandover } = await import('../scripts/check_collaboration_handover.mjs')
  await runHandover({ pythonExecutable, output: join(root, 'build/handover-tests', randomUUID()) })
})
