import assert from 'node:assert/strict'
import test from 'node:test'
import { AgentRunController } from './agent-run-controller.js'
import { executeAgentNodeRuntime } from './agent-node-runtime.js'

test('executeAgentNodeRuntime normalizes a completed Agent session', async () => {
  const result = await executeAgentNodeRuntime({
    messages: [{ role: 'user', content: 'Finish the task.' }],
    controller: new AgentRunController(),
    callModel: async () => ({
      content: null,
      finish_reason: 'tool_calls',
      tool_calls: [{
        id: 'complete-1',
        type: 'function',
        function: {
          name: 'complete',
          arguments: JSON.stringify({
            status: 'done',
            summary: 'Finished.'
          })
        }
      }]
    }),
    dispatchTool: async () => JSON.stringify({
      success: true,
      status: 'done'
    })
  })

  assert.equal(result.success, true)
  assert.equal(result.status, 'done')
  assert.equal(result.sourceStatus, 'done')
  assert.equal(result.summary, 'Finished.')
  assert.deepEqual(result.completionGaps, [])
  assert.deepEqual(result.toolCounts, { complete: 1 })
})

test('executeAgentNodeRuntime maps the diagnostic iteration guard to blocked', async () => {
  const result = await executeAgentNodeRuntime({
    messages: [{ role: 'user', content: 'Start.' }],
    maxIterations: 1,
    controller: new AgentRunController(),
    callModel: async () => ({
      content: 'Still working.',
      finish_reason: 'tool_calls',
      tool_calls: [{
        id: 'lookup-1',
        type: 'function',
        function: { name: 'lookup', arguments: '{}' }
      }]
    }),
    dispatchTool: async () => ''
  })

  assert.equal(result.success, false)
  assert.equal(result.status, 'blocked')
  assert.equal(result.sourceStatus, 'max_iterations')
  assert.equal(result.summary, 'Still working.')
  assert.deepEqual(result.completionGaps, ['max_iterations'])
})
