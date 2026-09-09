import assert from 'node:assert/strict'
import test from 'node:test'
import { AgentRunController } from './agent-run-controller.js'
import { AgentHostRuntimeSession } from './agent-host-runtime-session.js'
import { RuntimeJobRegistry } from '../runtime/runtime-job-registry.js'

test('AgentHostRuntimeSession owns registration, controls, events, and terminal cleanup', () => {
  const jobs = new RuntimeJobRegistry({ healthPolicy: { checkIntervalMs: 60_000 } })
  let idle = 0
  const session = new AgentHostRuntimeSession({
    sessionId: 'session-1',
    deadlineAt: Date.now() + 10_000,
    jobs,
    onIdle: () => { idle += 1 }
  })
  const controller = new AgentRunController()
  const job = session.registerJob({
    jobId: 'job-1',
    ownerNodeId: 'writer',
    controller,
    status: 'starting',
    metadata: { rootJobId: 'job-1' }
  })
  assert.equal(job.status, 'starting')
  assert.equal(job.metadata?.agentNodeId, 'writer')
  assert.equal(jobs.continue(job.id, 'more'), true)
  assert.deepEqual(controller.drainContinuations(), ['more'])
  session.updateJob(job.id, 'thinking', { nodeId: 'writer' })
  session.recordEvent(job.id, 'message', { role: 'assistant', content: 'working' })
  session.finalizeJob(job.id, 'done', { status: 'done' })
  assert.equal(jobs.get(job.id)?.status, 'done')
  assert.equal(idle, 1)
  jobs.dispose()
})

test('AgentHostRuntimeSession accepts interactions with host persistence before resume', async () => {
  const jobs = new RuntimeJobRegistry({ healthPolicy: { checkIntervalMs: 60_000 } })
  const session = new AgentHostRuntimeSession({
    sessionId: 'session-1',
    deadlineAt: Date.now() + 10_000,
    jobs,
    onIdle: () => {}
  })
  const controller = new AgentRunController()
  session.registerJob({ jobId: 'job-1', ownerNodeId: 'writer', controller })
  const answer = controller.requestInteraction('ask-1', { ownerNodeId: 'writer', kind: 'ask_user' })
  session.updateJob('job-1', 'waiting')
  const order: string[] = []
  const accepted = await session.acceptInteraction('ask-1', 'yes', {
    beforeResolve: () => { order.push('persist') }
  })
  order.push(`status:${jobs.get('job-1')?.status}`)
  assert.equal(accepted?.ownerNodeId, 'writer')
  assert.equal(await answer, 'yes')
  assert.deepEqual(order, ['persist', 'status:running'])
  jobs.dispose()
})
