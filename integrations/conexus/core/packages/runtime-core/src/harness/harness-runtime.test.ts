import assert from 'node:assert/strict'
import test from 'node:test'
import type { RuntimeEvent, RuntimeNode } from '../contracts.js'
import {
  executeHarnessInvocation,
  type HarnessExecutionHost,
  type HarnessNodeRunRecord
} from './harness-runtime.js'

const nodes: RuntimeNode[] = [
  { id: 'agent-1', type: 'agent', data: { label: 'Research Agent' } },
  { id: 'tool-1', type: 'tool', data: { label: 'Formatter' } }
]

function createHost(executeNode: HarnessExecutionHost['executeNode']): {
  host: HarnessExecutionHost
  updates: Array<Record<string, unknown>>
  events: RuntimeEvent[]
} {
  const updates: Array<Record<string, unknown>> = []
  const events: RuntimeEvent[] = []
  const times = ['2026-07-17T08:00:00.000Z', '2026-07-17T08:00:01.000Z']
  let timeIndex = 0

  return {
    updates,
    events,
    host: {
      clock: {
        now: () => times[timeIndex++] ?? '2026-07-17T08:00:02.000Z'
      },
      ids: {
        create: (prefix) => `${prefix}-fixed`
      },
      events: {
        emit: (event) => {
          events.push(structuredClone(event))
        }
      },
      updateHarness: (patch) => {
        updates.push(structuredClone(patch))
      },
      executeNode
    }
  }
}

function record(overrides: Partial<HarnessNodeRunRecord> = {}): HarnessNodeRunRecord {
  return {
    nodeId: 'agent-1',
    nodeType: 'agent',
    label: 'Research Agent',
    status: 'completed',
    success: true,
    summary: 'Complete output',
    completionGaps: [],
    result: { answer: 42 },
    ...overrides
  }
}

test('executeHarnessInvocation completes a selected Agent capability and appends bounded invocation history', async () => {
  const execution = createHost(async (params) => {
    assert.equal(params.node.id, 'agent-1')
    assert.equal(params.harnessId, 'harness-1')
    assert.equal(params.invocationId, 'inv-fixed')
    assert.equal(params.signal.aborted, false)
    assert.deepEqual(params.priorRuns, [])
    return record()
  })
  const existingInvocations = Array.from({ length: 11 }, (_, index) => ({
    id: `old-${index}`,
    status: 'completed',
    startedAt: '2026-07-16T08:00:00.000Z',
    completedAt: '2026-07-16T08:00:01.000Z',
    targetNodeId: 'agent-1',
    exposureId: 'research',
    currentNodeId: '',
    runOrder: ['agent-1'],
    runs: []
  }))

  const result = await executeHarnessInvocation({
    harnessId: 'harness-1',
    targetNodeId: 'agent-1',
    exposureId: 'research',
    nodes,
    edges: [],
    existingRuntime: {
      invocations: existingInvocations
    },
    host: execution.host,
    signal: new AbortController().signal
  })

  assert.deepEqual(result, {
    success: true,
    harnessId: 'harness-1',
    exposureId: 'research',
    nodeId: 'agent-1',
    nodeType: 'agent',
    status: 'completed',
    invocationId: 'inv-fixed',
    result: { answer: 42 },
    summary: 'Research Agent: completed - Complete output',
    completionGaps: []
  })
  assert.equal(execution.updates.length, 2)
  const runningInvocation = execution.updates[0]?.invocation as Record<string, unknown>
  const completedInvocation = execution.updates[1]?.invocation as Record<string, unknown>
  assert.equal(execution.updates[0]?.status, 'running')
  assert.equal(runningInvocation.exposureId, 'research')
  assert.equal(execution.updates[1]?.status, 'completed')
  assert.equal(completedInvocation.status, 'completed')
  assert.equal(completedInvocation.completedAt, '2026-07-17T08:00:01.000Z')
  assert.deepEqual(completedInvocation.runs, [record()])

  const history = execution.updates[1]?.invocations as Array<{ id: string }>
  assert.equal(history.length, 10)
  assert.equal(history[0]?.id, 'old-2')
  assert.equal(history[9]?.id, 'inv-fixed')
  assert.deepEqual(
    execution.events.map(({ type, at }) => ({ type, at })),
    [
      { type: 'harness.started', at: '2026-07-17T08:00:00.000Z' },
      { type: 'harness.completed', at: '2026-07-17T08:00:01.000Z' }
    ]
  )
  assert.deepEqual(execution.events[1]?.payload, {
    harnessId: 'harness-1',
    exposureId: 'research',
    nodeId: 'agent-1',
    summary: 'Research Agent: completed - Complete output',
    completionGaps: []
  })
})

test('executeHarnessInvocation can target a selected Tool capability and reports needs_repair', async () => {
  const execution = createHost(async ({ node }) => {
    assert.equal(node.id, 'tool-1')
    return record({
      nodeId: 'tool-1',
      nodeType: 'tool',
      label: 'Formatter',
      status: 'blocked',
      success: false,
      summary: 'Missing source text',
      completionGaps: ['source_text'],
      result: undefined
    })
  })

  const result = await executeHarnessInvocation({
    harnessId: 'harness-1',
    targetNodeId: 'tool-1',
    exposureId: 'formatter',
    nodes,
    edges: [],
    host: execution.host,
    signal: new AbortController().signal
  })

  assert.deepEqual(result, {
    success: false,
    harnessId: 'harness-1',
    exposureId: 'formatter',
    nodeId: 'tool-1',
    nodeType: 'tool',
    status: 'needs_repair',
    invocationId: 'inv-fixed',
    summary: 'Formatter: needs repair - Missing source text',
    completionGaps: ['source_text']
  })
  const runningInvocation = execution.updates[0]?.invocation as Record<string, unknown>
  const completedInvocation = execution.updates[1]?.invocation as Record<string, unknown>
  assert.equal(runningInvocation.targetNodeId, 'tool-1')
  assert.equal(runningInvocation.exposureId, 'formatter')
  assert.equal(completedInvocation.status, 'needs_repair')
  assert.equal(execution.updates[1]?.status, 'needs_repair')
  assert.deepEqual(execution.updates[1]?.completionGaps, ['source_text'])
  assert.deepEqual(execution.events.map((event) => event.type), [
    'harness.started',
    'harness.needs_repair'
  ])
})

test('executeHarnessInvocation converts node failures into an error result, history, and event', async () => {
  const execution = createHost(async () => {
    throw new Error('model unavailable')
  })

  const result = await executeHarnessInvocation({
    harnessId: 'harness-1',
    targetNodeId: 'agent-1',
    exposureId: 'research',
    nodes,
    edges: [],
    existingRuntime: {
      invocations: [{
        id: 'previous',
        status: 'completed',
        startedAt: '2026-07-16T08:00:00.000Z',
        completedAt: '2026-07-16T08:00:01.000Z',
        targetNodeId: 'agent-1',
        exposureId: 'research',
        currentNodeId: '',
        runOrder: ['agent-1'],
        runs: []
      }]
    },
    host: execution.host,
    signal: new AbortController().signal
  })

  assert.deepEqual(result, {
    success: false,
    harnessId: 'harness-1',
    exposureId: 'research',
    nodeId: 'agent-1',
    nodeType: 'agent',
    status: 'error',
    invocationId: 'inv-fixed',
    completionGaps: ['runtime_error'],
    error: 'model unavailable'
  })
  assert.equal(execution.updates.length, 2)
  assert.equal(execution.updates[1]?.status, 'error')
  assert.equal(execution.updates[1]?.lastError, 'model unavailable')
  const failedInvocation = execution.updates[1]?.invocation as Record<string, unknown>
  assert.equal(failedInvocation.status, 'error')
  assert.equal(failedInvocation.error, 'model unavailable')
  assert.deepEqual(failedInvocation.runs, [])
  const history = execution.updates[1]?.invocations as Array<Record<string, unknown>>
  assert.deepEqual(history.map((item) => item.id), ['previous', 'inv-fixed'])
  assert.deepEqual(execution.events.map((event) => event.type), ['harness.started', 'harness.error'])
  assert.deepEqual(execution.events[1]?.payload, {
    harnessId: 'harness-1',
    exposureId: 'research',
    nodeId: 'agent-1',
    error: 'model unavailable'
  })
})

test('executeHarnessInvocation does not execute a node for an already-aborted signal', async () => {
  let executeCalls = 0
  const execution = createHost(async () => {
    executeCalls++
    return record()
  })
  const controller = new AbortController()
  controller.abort()

  const result = await executeHarnessInvocation({
    harnessId: 'harness-1',
    targetNodeId: 'agent-1',
    exposureId: 'research',
    nodes,
    edges: [],
    host: execution.host,
    signal: controller.signal
  })

  assert.equal(executeCalls, 0)
  assert.equal(result.success, false)
  assert.equal(result.status, 'aborted')
  assert.equal(result.error, undefined)
  assert.deepEqual(result.completionGaps, ['aborted'])
  assert.equal(execution.updates[1]?.lastError, undefined)
  assert.deepEqual(execution.events.map((event) => event.type), ['harness.started', 'harness.aborted'])
})

test('executeHarnessInvocation discards a node result when cancellation arrives during execution', async () => {
  const controller = new AbortController()
  const execution = createHost(async () => {
    controller.abort()
    return record()
  })

  const result = await executeHarnessInvocation({
    harnessId: 'harness-1',
    targetNodeId: 'agent-1',
    exposureId: 'research',
    nodes,
    edges: [],
    host: execution.host,
    signal: controller.signal
  })

  assert.equal(result.status, 'aborted')
  assert.deepEqual(result.completionGaps, ['aborted'])
  const invocation = execution.updates[1]?.invocation as Record<string, unknown>
  assert.deepEqual(invocation.runs, [])
})
