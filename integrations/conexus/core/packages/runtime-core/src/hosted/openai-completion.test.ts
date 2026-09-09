import assert from 'node:assert/strict'
import test from 'node:test'
import {
  OpenAiCompletionParseError,
  parseOpenAiCompletion,
  type OpenAiCompletionParseErrorCode
} from './openai-completion.js'

test('parses OpenAI-compatible text and tool-call completions', () => {
  assert.deepEqual(parseOpenAiCompletion({
    choices: [{ message: { content: 'Finished.' }, finish_reason: 'stop' }]
  }), {
    content: 'Finished.',
    finish_reason: 'stop'
  })

  assert.deepEqual(parseOpenAiCompletion({
    choices: [{
      message: {
        content: null,
        tool_calls: [{
          id: 'call-1',
          type: 'function',
          function: { name: 'complete', arguments: '{"status":"done"}' }
        }]
      },
      finish_reason: 'tool_calls'
    }]
  }), {
    content: null,
    tool_calls: [{
      id: 'call-1',
      type: 'function',
      function: { name: 'complete', arguments: '{"status":"done"}' }
    }],
    finish_reason: 'tool_calls'
  })
})

test('uses stable parse error codes and never drops malformed tool calls', () => {
  const cases: Array<{ payload: unknown; code: OpenAiCompletionParseErrorCode }> = [
    { payload: null, code: 'invalid_payload' },
    { payload: {}, code: 'missing_choices' },
    { payload: { choices: [{}] }, code: 'missing_message' },
    { payload: { choices: [{ message: { content: 7 } }] }, code: 'invalid_content' },
    { payload: { choices: [{ message: { tool_calls: {} } }] }, code: 'invalid_tool_calls' },
    {
      payload: {
        choices: [{
          message: {
            tool_calls: [{ id: 'call-1', type: 'function', function: { name: 'complete' } }]
          }
        }]
      },
      code: 'malformed_tool_call'
    }
  ]

  for (const { payload, code } of cases) {
    assert.throws(
      () => parseOpenAiCompletion(payload),
      (error: unknown) => error instanceof OpenAiCompletionParseError && error.code === code
    )
  }
})
