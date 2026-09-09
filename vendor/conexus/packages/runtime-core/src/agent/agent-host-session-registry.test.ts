import assert from 'node:assert/strict'
import test from 'node:test'
import { AgentRunController } from './agent-run-controller.js'
import { AgentHostSessionRegistry } from './agent-host-session-registry.js'
import { isAgentHostTimeout } from './agent-host-runtime-session.js'
import { RuntimeJobRegistry } from '../runtime/runtime-job-registry.js'

test('AgentHostSessionRegistry owns session and Agent-node exclusivity until idle', () => {
  const jobs = new RuntimeJobRegistry({ healthPolicy: { checkIntervalMs: 60_000 } })
  const sessions = new AgentHostSessionRegistry<{ label: string }>()
  const record = sessions.create({
    sessionId: 'session-1',
    rootNodeId: 'agent-1',
    deadlineAt: Date.now() + 60_000,
    jobs,
    createContext: () => ({ label: 'desktop' })
  })
  const controller = new AgentRunController()
  record.session.registerJob({ jobId: 'job-1', ownerNodeId: 'agent-1', controller })
  assert.equal(sessions.has('session-1'), true)
  assert.equal(sessions.hasActiveOwner('agent-1'), true)
  assert.throws(() => sessions.create({
    sessionId: 'session-2',
    rootNodeId: 'agent-1',
    deadlineAt: Date.now() + 60_000,
    jobs,
    createContext: () => ({ label: 'web' })
  }), /active run/)
  record.session.finalizeJob('job-1', 'done')
  assert.equal(sessions.has('session-1'), false)
  jobs.dispose()
})

test('AgentHostRuntimeSession enforces its deadline once for every host', async () => {
  const jobs = new RuntimeJobRegistry({ healthPolicy: { checkIntervalMs: 60_000 } })
  const sessions = new AgentHostSessionRegistry<void>()
  const record = sessions.create({
    sessionId: 'session-timeout',
    rootNodeId: 'agent-timeout',
    deadlineAt: Date.now() + 5,
    jobs,
    createContext: () => undefined
  })
  const controller = new AgentRunController()
  record.session.registerJob({ jobId: 'job-timeout', ownerNodeId: 'agent-timeout', controller })
  await new Promise((resolve) => setTimeout(resolve, 15))
  assert.equal(controller.aborted, true)
  assert.equal(isAgentHostTimeout(controller.signal.reason), true)
  jobs.dispose()
})
