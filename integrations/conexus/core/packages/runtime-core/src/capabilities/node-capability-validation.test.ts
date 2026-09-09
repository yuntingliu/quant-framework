import assert from 'node:assert/strict'
import test from 'node:test'
import { getNodeCapabilityContract } from './node-capability-contracts.js'
import { validateNodeCapabilityInput } from './node-capability-validation.js'

const cliExec = getNodeCapabilityContract({
  id: 'cli-1',
  type: 'cli',
  data: {}
}, 'cli.exec')

assert.ok(cliExec)

test('cli.exec accepts an integer timeout within its declared range', () => {
  assert.deepEqual(validateNodeCapabilityInput(cliExec, {
    command: 'node -v',
    timeout_ms: 120_000
  }), { ok: true })
})

test('cli.exec rejects fractional and out-of-range timeouts through JSON Schema', () => {
  const fractional = validateNodeCapabilityInput(cliExec, {
    command: 'node -v',
    timeout_ms: 1_000.5
  })
  assert.equal(fractional.ok, false)
  if (!fractional.ok) assert.equal(fractional.details[0]?.keyword, 'type')

  const tooLarge = validateNodeCapabilityInput(cliExec, {
    command: 'node -v',
    timeout_ms: 600_001
  })
  assert.equal(tooLarge.ok, false)
  if (!tooLarge.ok) assert.equal(tooLarge.details[0]?.keyword, 'maximum')
})

test('capability input validation enforces required and additional properties', () => {
  const missingCommand = validateNodeCapabilityInput(cliExec, { timeout_ms: 120_000 })
  assert.equal(missingCommand.ok, false)
  if (!missingCommand.ok) assert.equal(missingCommand.details[0]?.keyword, 'required')

  const unexpected = validateNodeCapabilityInput(cliExec, {
    command: 'node -v',
    unexpected: true
  })
  assert.equal(unexpected.ok, false)
  if (!unexpected.ok) assert.equal(unexpected.details[0]?.keyword, 'additionalProperties')
})
