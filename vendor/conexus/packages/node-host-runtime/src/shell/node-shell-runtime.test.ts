import assert from 'node:assert/strict'
import { dirname } from 'node:path'
import test from 'node:test'
import { createNodeShellRuntime } from './node-shell-runtime.js'

test('keeps one CLI session and its working directory across commands', async () => {
  const workingDirectory = process.cwd()
  const parentDirectory = dirname(workingDirectory)
  const runtime = createNodeShellRuntime({ defaultCwd: () => workingDirectory })
  const shell = process.platform === 'win32' ? 'powershell' : 'bash'
  try {
    const first = await runtime.execute({
      cliNodeId: 'persistent-cli',
      shell,
      command: process.platform === 'win32'
        ? 'Set-Location ..; Write-Output first'
        : "cd .. && printf 'first\\n'"
    })
    assert.equal(first.exit_code, 0)
    assert.equal(first.stdout, 'first')
    assert.equal(first.cwd, parentDirectory)

    const second = await runtime.execute({
      cliNodeId: 'persistent-cli',
      shell,
      command: process.platform === 'win32' ? 'Write-Output second' : "printf 'second\\n'"
    })
    assert.equal(second.exit_code, 0)
    assert.equal(second.stdout, 'second')
    assert.equal(second.cwd, parentDirectory)
  } finally {
    runtime.disposeAll()
  }
})
