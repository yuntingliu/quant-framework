import assert from 'node:assert/strict'
import test from 'node:test'
import { AgentHostRuntimeSession } from './agent-host-runtime-session.js'
import {
  activeAgentHostNodeRunResult,
  dispatchAgentHostNodeRun,
  startAgentHostNodeRun,
  validateAgentNodeDelegation
} from './agent-host-node-run.js'
import { RuntimeJobRegistry } from '../runtime/runtime-job-registry.js'

test('startAgentHostNodeRun owns registration and terminal cleanup', async () => {
  const jobs = new RuntimeJobRegistry({
    createId: (kind, targetId) => `${kind}:${targetId}:1`,
    healthPolicy: { checkIntervalMs: 60_000 }
  })
  const session = new AgentHostRuntimeSession({
    sessionId: 'session-1',
    jobs,
    onIdle: () => {}
  })
  let release!: (value: string) => void
  const deferred = new Promise<string>((resolve) => {
    release = resolve
  })
  const started = startAgentHostNodeRun({
    session,
    kind: 'agent',
    ownerNodeId: 'agent-2',
    run: () => deferred,
    finalize: (summary) => ({
      status: 'done',
      result: { nodeId: 'agent-2', summary },
      statusPayload: { nodeId: 'agent-2', summary }
    })
  })

  assert.equal(started.jobId, 'agent:agent-2:1')
  assert.equal(session.currentJobForOwner('agent-2')?.jobId, started.jobId)
  assert.deepEqual(JSON.parse(activeAgentHostNodeRunResult({
    session,
    ownerNodeId: 'agent-2',
    kind: 'agent'
  }) ?? '{}'), {
    success: false,
    node_id: 'agent-2',
    status: 'running',
    error: 'Agent node already has a current run. Use run.wait or run.cancel through use, or create another Agent node for parallel work.'
  })

  release('Finished.')
  await started.execution

  assert.equal(jobs.get(started.jobId)?.status, 'done')
  assert.equal(session.currentJobForOwner('agent-2'), undefined)
  assert.equal(activeAgentHostNodeRunResult({
    session,
    ownerNodeId: 'agent-2',
    kind: 'agent'
  }), undefined)
  jobs.dispose()
})

test('validateAgentNodeDelegation shares self, cycle, depth, and task checks', () => {
  assert.match(validateAgentNodeDelegation({
    targetNodeId: 'agent-1',
    requesterNodeId: 'agent-1',
    callDepth: 0,
    task: 'work'
  }) ?? '', /cannot run itself/)
  assert.match(validateAgentNodeDelegation({
    targetNodeId: 'agent-1',
    callStack: ['agent-1', 'agent-2'],
    callDepth: 1,
    task: 'work'
  }) ?? '', /cycle rejected/)
  assert.match(validateAgentNodeDelegation({
    targetNodeId: 'agent-3',
    callDepth: 3,
    task: 'work'
  }) ?? '', /recursion limit/)
  assert.match(validateAgentNodeDelegation({
    targetNodeId: 'agent-3',
    callDepth: 0
  }) ?? '', /task is required/)
})

test('dispatchAgentHostNodeRun owns target routing and canonical errors', async () => {
  const jobs = new RuntimeJobRegistry({ healthPolicy: { checkIntervalMs: 60_000 } })
  const session = new AgentHostRuntimeSession({
    sessionId: 'session-router',
    jobs,
    onIdle: () => {}
  })
  const nodes = [
    { id: 'agent-2', type: 'agent' },
    { id: 'note-1', type: 'note' }
  ]
  let delegatedTask = ''
  const dispatch = (args: Record<string, unknown>) => dispatchAgentHostNodeRun({
    session,
    args,
    requesterNodeId: 'agent-1',
    callDepth: 0,
    callStack: ['agent-1'],
    findNode: (nodeId) => nodes.find((node) => node.id === nodeId),
    runAgent: (node, task) => {
      delegatedTask = `${node.id}:${task}`
      return JSON.stringify({ success: true })
    },
    runHarness: () => JSON.stringify({ success: true })
  })

  assert.deepEqual(JSON.parse(await dispatch({})), {
    success: false,
    error: 'node_id is required'
  })
  assert.deepEqual(JSON.parse(await dispatch({ node_id: 'missing' })), {
    success: false,
    error: 'node not found: missing'
  })
  assert.deepEqual(JSON.parse(await dispatch({ node_id: 'note-1' })), {
    success: false,
    node_id: 'note-1',
    error: 'This runtime cannot execute note node note-1.'
  })
  assert.deepEqual(JSON.parse(await dispatch({ node_id: 'agent-2', task: ' research ' })), {
    success: true
  })
  assert.equal(delegatedTask, 'agent-2:research')
  jobs.dispose()
})

test('AgentHostRuntimeSession terminalizes open descendants in shared core', async () => {
  const jobs = new RuntimeJobRegistry({ healthPolicy: { checkIntervalMs: 60_000 } })
  const session = new AgentHostRuntimeSession({
    sessionId: 'session-descendants',
    jobs,
    onIdle: () => {}
  })
  let release!: () => void
  const deferred = new Promise<void>((resolve) => {
    release = resolve
  })
  const child = startAgentHostNodeRun({
    session,
    kind: 'harness',
    ownerNodeId: 'harness-1',
    metadata: { rootJobId: 'root-1' },
    run: () => deferred,
    finalize: () => ({ status: 'done' })
  })

  const finalized = session.finalizeOpenDescendantJobs('root-1', 'error', 'Root failed.')
  assert.equal(finalized.length, 1)
  assert.equal(jobs.get(child.jobId)?.status, 'error')
  assert.equal(
    jobs.get(child.jobId)?.events.find((event) => event.event === 'result')?.payload?.error,
    'Root failed.'
  )
  release()
  await child.execution
  assert.equal(jobs.get(child.jobId)?.status, 'error')
  jobs.dispose()
})
