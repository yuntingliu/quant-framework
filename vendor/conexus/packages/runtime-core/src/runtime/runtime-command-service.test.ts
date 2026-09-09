import assert from 'node:assert/strict'
import test from 'node:test'
import { executeRuntimeAbortCommand, executeRuntimeContinueCommand } from './runtime-command-service.js'

test('runtime command service owns canonical parsing and receipts', () => {
  const calls: string[] = []
  const target = {
    exists: (id: string) => id === 'job-1',
    abort: (id: string) => { calls.push(`abort:${id}`); return true },
    continue: (id: string, input: unknown) => { calls.push(`continue:${id}:${String(input)}`); return true }
  }
  assert.equal(executeRuntimeAbortCommand({ id: 'job-1' }, target).code, 'aborted')
  assert.equal(executeRuntimeContinueCommand({ id: 'job-1', input: 'next' }, target).code, 'continued')
  assert.deepEqual(calls, ['abort:job-1', 'continue:job-1:next'])
  assert.equal(executeRuntimeAbortCommand({}, target).code, 'invalid_request')
  assert.equal(executeRuntimeAbortCommand({ id: 'missing' }, target).code, 'not_found')
})
