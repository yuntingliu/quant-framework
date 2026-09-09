import assert from 'node:assert/strict'
import test from 'node:test'
import { AgentRunController } from './agent-run-controller.js'
import { AgentControlSession, agentInteractionId } from './agent-control-session.js'

test('Agent control sessions retain descendants until the final job finishes', () => {
  let idleCalls = 0
  const session = new AgentControlSession({
    sessionId: 'session-1',
    deadlineAt: 10_000,
    now: () => 1_000,
    onIdle: () => { idleCalls += 1 }
  })
  session.registerJob({ jobId: 'root', ownerNodeId: 'writer', controller: new AgentRunController() })
  session.registerJob({ jobId: 'child', ownerNodeId: 'researcher', controller: new AgentRunController() })
  assert.equal(session.finishJob('root'), true)
  assert.equal(idleCalls, 0)
  assert.equal(session.finishJob('child'), true)
  assert.equal(idleCalls, 1)
  assert.equal(session.remainingTimeoutMs(), 9_000)
})

test('Agent control sessions isolate controls and provider interaction ids by job', async () => {
  const root = new AgentRunController()
  const child = new AgentRunController()
  const session = new AgentControlSession({ sessionId: 'session-1', deadlineAt: 1_000, onIdle: () => {} })
  session.registerJob({ jobId: 'root', ownerNodeId: 'writer', controller: root })
  session.registerJob({ jobId: 'child', ownerNodeId: 'researcher', controller: child })
  const rootId = agentInteractionId('root', 'provider-ask')
  const childId = agentInteractionId('child', 'provider-ask')
  const rootAnswer = root.requestInteraction(rootId, { ownerNodeId: 'writer', kind: 'ask_user' })
  const childAnswer = child.requestInteraction(childId, { ownerNodeId: 'researcher', kind: 'ask_user' })
  assert.notEqual(rootId, childId)
  assert.equal(session.pendingInteraction(rootId)?.jobId, 'root')
  assert.equal(session.pendingInteraction(childId)?.jobId, 'child')
  assert.deepEqual(new Set(session.pendingOwnerNodeIds()), new Set(['writer', 'researcher']))
  assert.equal(child.resolveInteraction(childId, 'child answer'), true)
  assert.equal(await childAnswer, 'child answer')
  root.abort()
  await assert.rejects(rootAnswer, /aborted/i)
})
