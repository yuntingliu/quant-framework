import assert from 'node:assert/strict'
import test from 'node:test'
import { AgentRunController } from './agent-run-controller.js'
import { AgentHostRuntimeSession } from './agent-host-runtime-session.js'
import { RuntimeJobRegistry } from '../runtime/runtime-job-registry.js'
import { executeAgentHostSession } from './agent-host-executor.js'

test('executeAgentHostSession applies one continuation and Runtime Job journal contract', async () => {
  const jobs = new RuntimeJobRegistry({ healthPolicy: { checkIntervalMs: 60_000 } })
  const controller = new AgentRunController()
  const hostSession = new AgentHostRuntimeSession({
    sessionId: 'session-1',
    deadlineAt: Date.now() + 60_000,
    jobs,
    onIdle: () => {}
  })
  hostSession.registerJob({ jobId: 'job-1', ownerNodeId: 'agent-1', controller })
  controller.continue('  new constraint  ')
  const messages = [{ role: 'user' as const, content: 'Start' }]
  const result = await executeAgentHostSession({
    messages,
    maxIterations: 2,
    controller,
    journal: { session: hostSession, jobId: 'job-1', nodeId: 'agent-1' },
    callModel: async () => ({
      content: 'I will finish this now.',
      finish_reason: 'tool_calls',
      tool_calls: [{
        id: 'complete-1',
        type: 'function',
        function: { name: 'complete', arguments: JSON.stringify({ status: 'done', summary: 'Done' }) }
      }]
    }),
    dispatchTool: async () => JSON.stringify({ success: true, status: 'done' })
  })

  assert.equal(result.status, 'done')
  assert.equal(messages[1]?.content, 'Additional user input for the current objective:\nnew constraint')
  assert.deepEqual(
    jobs.get('job-1')?.events.filter((event) => event.event === 'message').map((event) => event.payload),
    [
      { nodeId: 'agent-1', role: 'user', content: 'new constraint' },
      {
        nodeId: 'agent-1',
        role: 'assistant',
        content: 'I will finish this now.',
        toolCalls: [{
          id: 'complete-1',
          type: 'function',
          function: {
            name: 'complete',
            arguments: JSON.stringify({ status: 'done', summary: 'Done' })
          }
        }]
      }
    ]
  )
  jobs.dispose()
})
