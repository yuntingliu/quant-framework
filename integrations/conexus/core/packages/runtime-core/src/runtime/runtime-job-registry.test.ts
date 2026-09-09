import assert from 'node:assert/strict'
import test from 'node:test'
import {
  RuntimeJobRegistry,
  type RuntimeJobRegistryOptions
} from './runtime-job-registry.js'
import {
  asyncNodeRunToolResult,
  controlRuntimeNodeRun
} from './runtime-node-run-control.js'
import type { RuntimeJobEvent } from '@conexus/runtime-protocol'

function testRegistry(
  options: RuntimeJobRegistryOptions = {}
): { registry: RuntimeJobRegistry; setNow(value: number): void } {
  let now = Date.parse('2026-07-17T08:00:00.000Z')
  const registry = new RuntimeJobRegistry({
    clock: () => new Date(now),
    healthPolicy: { checkIntervalMs: 60_000 },
    ...options
  })
  return {
    registry,
    setNow(value: number) {
      now = value
    }
  }
}

test('RuntimeJobRegistry creates deterministic jobs and returns isolated snapshots', (t) => {
  const emitted: RuntimeJobEvent[] = []
  const { registry } = testRegistry({
    createId: (kind, targetId) => `${kind}:${targetId}:fixed`,
    onEvent: (event) => emitted.push(event)
  })
  t.after(() => registry.dispose())

  assert.equal(registry.newJobId('agent', 'agent-1'), 'agent:agent-1:fixed')
  const job = registry.start({
    id: 'job-1',
    kind: 'agent',
    status: 'running',
    sessionId: 'session-1',
    metadata: { agentNodeId: 'agent-1' },
    abort: () => {},
    continue: () => true
  })
  registry.event(job.id, 'message', { text: 'working' })

  assert.equal(emitted.length, 2)
  assert.equal(emitted[0]?.event, 'created')
  assert.deepEqual(
    emitted.map(({ eventId, jobId, sequence }) => ({ eventId, jobId, sequence })),
    [
      { eventId: 'job-1:1', jobId: 'job-1', sequence: 1 },
      { eventId: 'job-1:2', jobId: 'job-1', sequence: 2 }
    ]
  )
  assert.deepEqual(registry.list(), [job])

  const snapshot = registry.getSnapshot(job.id, { eventLimit: 1 })
  assert.ok(snapshot)
  assert.equal(snapshot.canAbort, true)
  assert.equal(snapshot.canContinue, true)
  assert.equal(snapshot.events.length, 1)
  assert.equal(snapshot.events[0]?.event, 'message')
  assert.deepEqual(snapshot.eventCursor, {
    firstRetainedSequence: 1,
    firstReturnedSequence: 2,
    lastSequence: 2,
    nextSequence: 3,
    retentionTruncated: false
  })

  snapshot.metadata!.agentNodeId = 'changed'
  snapshot.events[0]!.payload!.text = 'changed'
  assert.equal(job.metadata?.agentNodeId, 'agent-1')
  assert.equal(job.events.at(-1)?.payload?.text, 'working')
})

test('abort requests do not invent a terminal state and rejected controls do not update status', (t) => {
  const { registry, setNow } = testRegistry()
  t.after(() => registry.dispose())
  const controller = new AbortController()
  let abortAccepted = true
  let continueAccepted = false
  const job = registry.start({
    id: 'job-controls',
    kind: 'agent',
    status: 'waiting',
    metadata: { purpose: 'test' },
    abort: () => {
      if (!abortAccepted) return false
      controller.abort()
      return true
    },
    continue: () => continueAccepted
  })
  const initialEvents = job.events.length

  assert.equal(registry.abort(job.id), true)
  assert.equal(controller.signal.aborted, true)
  assert.equal(job.status, 'waiting')
  assert.equal(job.events.length, initialEvents)

  abortAccepted = false
  assert.equal(registry.abort(job.id), false)
  assert.equal(registry.continue(job.id, { text: 'not accepted' }), false)
  assert.equal(job.status, 'waiting')
  assert.equal(job.events.length, initialEvents)

  continueAccepted = true
  setNow(Date.parse('2026-07-17T08:00:01.000Z'))
  assert.equal(registry.continue(job.id, { text: 'continue' }), true)
  assert.equal(job.status, 'running')
  assert.deepEqual(job.metadata, { purpose: 'test' })
  assert.equal(job.health?.status, 'healthy')
  assert.equal(job.events.at(-1)?.event, 'status')

  registry.update(job.id, 'aborted')
  assert.equal(registry.abort(job.id), false)
  assert.equal(registry.continue(job.id, 'late input'), false)
})

test('event retention enforces both the count and serialized byte limits', (t) => {
  const { registry } = testRegistry({
    retention: { maxEventsPerJob: 3, maxEventBytesPerJob: 10_000 }
  })
  t.after(() => registry.dispose())
  const job = registry.start({ id: 'job-count', kind: 'harness', status: 'running' })
  registry.event(job.id, 'message', { index: 1 })
  registry.event(job.id, 'output', { index: 2 })
  registry.event(job.id, 'result', { index: 3 })
  registry.event(job.id, 'message', { index: 4 })

  assert.equal(job.events.length, 3)
  assert.deepEqual(job.events.map((event) => event.payload?.index), [2, 3, 4])
  assert.deepEqual(job.events.map((event) => event.sequence), [3, 4, 5])
  assert.deepEqual(registry.getSnapshot(job.id)?.eventCursor, {
    firstRetainedSequence: 3,
    firstReturnedSequence: 3,
    lastSequence: 5,
    nextSequence: 6,
    retentionTruncated: true
  })

  const delivered: RuntimeJobEvent[] = []
  const byteLimited = new RuntimeJobRegistry({
    clock: () => new Date('2026-07-17T08:00:00.000Z'),
    healthPolicy: { checkIntervalMs: 60_000 },
    retention: { maxEventsPerJob: 20, maxEventBytesPerJob: 250 },
    onEvent: (event) => delivered.push(event)
  })
  t.after(() => byteLimited.dispose())
  const byteJob = byteLimited.start({ id: 'job-bytes', kind: 'agent', status: 'running' })
  byteLimited.event(byteJob.id, 'message', { text: 'x'.repeat(2_000) })
  byteLimited.event(byteJob.id, 'output', { ok: true })

  const retainedBytes = byteJob.events.reduce(
    (total, event) => total + Buffer.byteLength(JSON.stringify(event), 'utf8'),
    0
  )
  assert.ok(retainedBytes <= 250)
  assert.equal(byteJob.events.at(-1)?.event, 'output')
  assert.equal(delivered.length, 3)
})

test('health checks report stuck tools and unhealthy child jobs through attention listeners', (t) => {
  const start = Date.parse('2026-07-17T08:00:00.000Z')
  const { registry, setNow } = testRegistry({
    healthPolicy: {
      checkIntervalMs: 60_000,
      idleThresholdMs: 50,
      stuckToolThresholdMs: 100
    }
  })
  t.after(() => registry.dispose())

  const toolJob = registry.start({ id: 'tool-job', kind: 'agent', status: 'running' })
  registry.event(toolJob.id, 'tool_call', { callId: 'call-1', tool: 'shell_exec' })
  let toolAttention = 0
  registry.onAttention(toolJob.id, (_job, event) => {
    toolAttention += 1
    assert.equal(event.event, 'health')
  })

  setNow(start + 150)
  registry.checkHealth()
  assert.equal(toolJob.health?.status, 'stuck')
  assert.equal(toolJob.health?.currentTool, 'shell_exec')
  assert.equal(toolAttention, 1)

  const parent = registry.start({
    id: 'parent-job',
    kind: 'harness',
    status: 'running'
  })
  const child = registry.start({
    id: 'child-job',
    kind: 'agent',
    status: 'running',
    metadata: { parentJobId: parent.id }
  })
  let parentAttention = 0
  registry.onAttention(parent.id, () => {
    parentAttention += 1
  })
  registry.update(child.id, 'error', { error: 'failed' })
  registry.checkHealth()

  assert.equal(parent.health?.status, 'needs_attention')
  assert.match(parent.health?.reason ?? '', /child job child-job ended with error/)
  assert.equal(parentAttention, 1)
})

test('terminal listeners fire once and late subscribers receive the terminal event', async (t) => {
  const { registry } = testRegistry()
  t.after(() => registry.dispose())
  const job = registry.start({ id: 'job-terminal', kind: 'agent', status: 'running' })
  let early = 0
  registry.onTerminal(job.id, (_job, event) => {
    early += 1
    assert.equal(event.status, 'done')
  })

  registry.update(job.id, 'done', { summary: 'complete' })
  const terminalUpdatedAt = job.updatedAt
  const terminalEvents = job.events.length
  registry.update(job.id, 'done', { summary: 'duplicate terminal update' })
  registry.update(job.id, 'running', { summary: 'illegal restart' })
  registry.event(job.id, 'message', { text: 'late event' })
  assert.equal(early, 1)
  assert.equal(job.status, 'done')
  assert.equal(job.updatedAt, terminalUpdatedAt)
  assert.equal(job.events.length, terminalEvents)

  let late = 0
  registry.onTerminal(job.id, (_job, event) => {
    late += 1
    assert.equal(event.status, 'done')
  })
  await Promise.resolve()
  assert.equal(late, 1)

  registry.remove(job.id)
  assert.equal(registry.get(job.id), undefined)
})

test('duplicate starts are rejected and resume explicitly reactivates only waiting jobs', (t) => {
  const { registry } = testRegistry()
  t.after(() => registry.dispose())
  const job = registry.start({
    id: 'job-resume',
    kind: 'agent',
    status: 'waiting',
    metadata: { retained: true }
  })

  assert.throws(
    () => registry.start({ id: job.id, kind: 'agent', status: 'running' }),
    /already exists/
  )
  const resumed = registry.resume(job.id, {
    sessionId: 'session-2',
    metadata: { resumed: true },
    abort: () => true,
    continue: () => true
  })
  assert.equal(resumed, job)
  assert.equal(job.status, 'running')
  assert.equal(job.sessionId, 'session-2')
  assert.deepEqual(job.metadata, { retained: true, resumed: true })
  assert.equal(job.events.at(-1)?.event, 'status')
  assert.equal(registry.getSnapshot(job.id)?.canAbort, true)

  assert.throws(() => registry.resume(job.id), /cannot resume from status running/)
  assert.throws(() => registry.resume('missing-job'), /not found/)

  registry.finalize(job.id, { status: 'done' })
  const previousEventId = job.events.at(-1)?.eventId
  const previousSequence = job.events.at(-1)?.sequence ?? 0
  registry.remove(job.id)
  const replacement = registry.start({ id: job.id, kind: 'agent', status: 'running' })
  assert.equal(replacement.events[0]?.sequence, previousSequence + 1)
  assert.notEqual(replacement.events[0]?.eventId, previousEventId)
  assert.equal(registry.getSnapshot(replacement.id)?.eventCursor.retentionTruncated, true)
})

test('finalize publishes a terminal result before notifying terminal listeners and then freezes the job', (t) => {
  const observed: Array<{ event: string; status: string; resultVisible: boolean }> = []
  const { registry } = testRegistry({
    onEvent: (event, currentJob) => {
      observed.push({
        event: event.event,
        status: currentJob.status,
        resultVisible: currentJob.events.some((item) => item.event === 'result')
      })
    }
  })
  t.after(() => registry.dispose())
  const job = registry.start({ id: 'job-finalize', kind: 'agent', status: 'running' })
  let listenerCalls = 0
  registry.onTerminal(job.id, (terminalJob, event) => {
    listenerCalls++
    assert.equal(event.event, 'status')
    assert.equal(terminalJob.events.at(-2)?.event, 'result')
    assert.equal(terminalJob.events.at(-1)?.event, 'status')
  })

  registry.finalize(job.id, {
    status: 'done',
    result: { answer: 42 },
    statusPayload: { summary: 'complete' }
  })

  assert.equal(listenerCalls, 1)
  assert.deepEqual(observed.slice(-2), [
    { event: 'result', status: 'done', resultVisible: true },
    { event: 'status', status: 'done', resultVisible: true }
  ])
  const frozenEventCount = job.events.length
  registry.finalize(job.id, { status: 'error', result: { answer: 0 } })
  assert.equal(job.status, 'done')
  assert.equal(job.events.length, frozenEventCount)
})

test('tool events require and normalize the canonical callId and tool fields', (t) => {
  const { registry } = testRegistry()
  t.after(() => registry.dispose())
  const job = registry.start({ id: 'job-tools', kind: 'agent', status: 'running' })

  registry.event(job.id, 'tool_call', {
    callId: ' call-1 ',
    tool: ' shell_exec ',
    args: { command: 'pwd' }
  })
  registry.event(job.id, 'tool_result', {
    callId: ' call-1 ',
    tool: ' shell_exec ',
    result: { success: true }
  })

  assert.deepEqual(job.events.slice(-2).map((event) => event.payload), [
    { callId: 'call-1', tool: 'shell_exec', args: { command: 'pwd' } },
    { callId: 'call-1', tool: 'shell_exec', result: { success: true } }
  ])
  assert.throws(
    () => registry.event(job.id, 'tool_call', { callId: '', tool: 'shell_exec' }),
    /require non-empty callId and tool/
  )
  assert.throws(
    () => registry.event(job.id, 'tool_result', { callId: 'call-2', tool: '   ' }),
    /require non-empty callId and tool/
  )
})

test('node run tools share one asynchronous start, wait, and cancel contract', async (t) => {
  const { registry } = testRegistry()
  t.after(() => registry.dispose())
  const running = registry.start({
    id: 'agent:researcher:fixed',
    kind: 'agent',
    status: 'running',
    metadata: { agentNodeId: 'researcher' },
    abort: () => true
  })

  assert.deepEqual(JSON.parse(asyncNodeRunToolResult({
    targetId: 'researcher'
  })), {
    success: true,
    node_id: 'researcher',
    status: 'running'
  })

  const waiting = controlRuntimeNodeRun(registry, {
    action: 'wait',
    node_id: 'researcher'
  })
  registry.finalize(running.id, {
    status: 'done',
    result: { summary: 'Research complete.' }
  })
  const waited = JSON.parse(await waiting) as Record<string, unknown>
  assert.equal(waited.success, true)
  assert.equal(waited.status, 'done')
  assert.deepEqual(waited.result, { summary: 'Research complete.' })

  registry.start({
    id: 'harness:review:fixed',
    kind: 'harness',
    status: 'running',
    metadata: { harnessNodeId: 'review' },
    abort: () => true
  })
  assert.deepEqual(JSON.parse(await controlRuntimeNodeRun(registry, {
    action: 'cancel',
    node_id: 'review'
  })), {
    success: true,
    node_id: 'review',
    status: 'cancellation_requested'
  })
  assert.deepEqual(JSON.parse(await controlRuntimeNodeRun(
    registry,
    { action: 'wait', node_id: 'review' },
    undefined,
    'review'
  )), {
    success: false,
    node_id: 'review',
    error: 'An Agent cannot use run.wait or run.cancel on its own run.'
  })
})
