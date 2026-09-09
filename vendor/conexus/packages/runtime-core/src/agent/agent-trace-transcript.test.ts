import assert from 'node:assert/strict'
import test from 'node:test'
import type { AgentMessage } from './agent-loop.js'
import { createAgentTraceTranscript } from './agent-trace-transcript.js'

const HISTORICAL_COMPLETION_REQUIRED_MESSAGE =
  'Your response has not terminated the Agent run. Use the complete tool with status and a final user-facing summary; status=blocked also requires blocker.'

test('agent trace transcript removes runtime continuation messages and keeps complete turns', () => {
  const messages: AgentMessage[] = [
    { role: 'system', content: 'system prompt' },
    { role: 'user', content: 'test' },
    { role: 'assistant', content: 'Ready.' },
    { role: 'user', content: HISTORICAL_COMPLETION_REQUIRED_MESSAGE },
    {
      role: 'assistant',
      content: '',
      tool_calls: [{
        id: 'call-1',
        type: 'function',
        function: { name: 'complete', arguments: '{"status":"done","summary":"Finished."}' }
      }]
    },
    { role: 'tool', content: '{"success":true}', tool_call_id: 'call-1', name: 'complete' }
  ]

  assert.deepEqual(createAgentTraceTranscript(messages), [
    { role: 'system', content: 'system prompt' },
    { role: 'user', content: 'test' },
    { role: 'assistant', content: 'Ready.' },
    messages[4],
    messages[5]
  ])
})
