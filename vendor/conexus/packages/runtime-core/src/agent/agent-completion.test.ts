import assert from 'node:assert/strict'
import test from 'node:test'
import {
  executeAgentCompletionTool,
  parseAcceptedAgentCompletion
} from './agent-completion.js'

test('completion accepts done with a user-facing summary', () => {
  const args = { status: 'done', summary: 'Finished.' }
  const result = executeAgentCompletionTool(args)
  assert.deepEqual(JSON.parse(result), { success: true, status: 'done' })
  assert.deepEqual(parseAcceptedAgentCompletion(args, result), {
    status: 'done',
    summary: 'Finished.'
  })
})

test('completion requires a concrete blocker for blocked', () => {
  const result = executeAgentCompletionTool({ status: 'blocked', summary: 'Cannot continue.' })
  assert.equal(parseAcceptedAgentCompletion({ status: 'blocked', summary: 'Cannot continue.' }, result), null)
  assert.match(result, /blocker is required/)
})

test('completion accepts blocked without echoing summary or blocker in the tool result', () => {
  const args = {
    status: 'blocked',
    summary: 'Cannot continue.',
    blocker: 'Missing access.'
  }
  const result = executeAgentCompletionTool(args)
  assert.deepEqual(JSON.parse(result), { success: true, status: 'blocked' })
  assert.deepEqual(parseAcceptedAgentCompletion(args, result), {
    status: 'blocked',
    summary: 'Cannot continue.',
    blocker: 'Missing access.'
  })
})

test('completion rejects blocker for done', () => {
  const args = {
    status: 'done',
    summary: 'Finished.',
    blocker: 'Not actually blocked.'
  }
  const result = executeAgentCompletionTool(args)
  assert.equal(parseAcceptedAgentCompletion(args, result), null)
  assert.match(result, /only valid for status=blocked/)
})

test('completion rejects obsolete or unknown fields', () => {
  const args = {
    status: 'done',
    summary: 'Finished.',
    verification: 'Obsolete.'
  }
  const result = executeAgentCompletionTool(args)
  assert.equal(parseAcceptedAgentCompletion(args, result), null)
  assert.match(result, /unsupported fields: verification/)
})

test('completion preserves literal JSON summary text', () => {
  const summary = JSON.stringify({ summary: 'The document node was created.' })
  const args = {
    status: 'done',
    summary
  }
  const result = executeAgentCompletionTool(args)
  assert.deepEqual(parseAcceptedAgentCompletion(args, result), {
    status: 'done',
    summary
  })
})

test('completion preserves user-facing JSON that is not only a summary envelope', () => {
  const summary = JSON.stringify({ summary: 'Finished.', documentId: 'document-1' })
  const args = {
    status: 'done',
    summary
  }
  const result = executeAgentCompletionTool(args)
  assert.equal(parseAcceptedAgentCompletion(args, result)?.summary, summary)
})
