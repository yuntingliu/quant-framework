import type { RuntimeEdge, RuntimeEventSink, RuntimeIdGenerator, RuntimeNode, RuntimeClock } from '../contracts.js'

export type HarnessExecutionStatus = 'running' | 'completed' | 'needs_repair' | 'error' | 'aborted'

export interface HarnessNodeRunRecord {
  nodeId: string
  nodeType: string
  label: string
  status: string
  success: boolean
  summary: string | null
  completionGaps: string[]
  result?: unknown
}

export interface HarnessInvocation {
  id: string
  status: HarnessExecutionStatus
  startedAt: string
  completedAt: string
  targetNodeId: string
  exposureId: string
  currentNodeId: string
  runOrder: string[]
  runs: HarnessNodeRunRecord[]
  error?: string
}

export interface HarnessExecutionResult {
  success: boolean
  harnessId: string
  exposureId: string
  nodeId: string
  nodeType: string
  status: Exclude<HarnessExecutionStatus, 'running'>
  invocationId: string
  result?: unknown
  summary?: string
  completionGaps: string[]
  error?: string
}

export interface HarnessExecutionHost {
  clock: RuntimeClock
  ids: RuntimeIdGenerator
  events?: RuntimeEventSink
  updateHarness(patch: Record<string, unknown>): void | Promise<void>
  executeNode(params: {
    harnessId: string
    invocationId: string
    node: RuntimeNode
    nodes: RuntimeNode[]
    edges: RuntimeEdge[]
    priorRuns: HarnessNodeRunRecord[]
    signal: AbortSignal
  }): Promise<HarnessNodeRunRecord>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function invocationStatus(value: unknown): HarnessExecutionStatus | undefined {
  return value === 'running'
    || value === 'completed'
    || value === 'needs_repair'
    || value === 'error'
    || value === 'aborted'
    ? value
    : undefined
}

function invocationRecord(value: unknown): HarnessInvocation | undefined {
  if (!isRecord(value)) return undefined
  const status = invocationStatus(value.status)
  if (
    typeof value.id !== 'string'
    || !status
    || typeof value.startedAt !== 'string'
    || typeof value.completedAt !== 'string'
    || typeof value.targetNodeId !== 'string'
    || typeof value.exposureId !== 'string'
    || typeof value.currentNodeId !== 'string'
  ) {
    return undefined
  }
  return {
    id: value.id,
    status,
    startedAt: value.startedAt,
    completedAt: value.completedAt,
    targetNodeId: value.targetNodeId,
    exposureId: value.exposureId,
    currentNodeId: value.currentNodeId,
    runOrder: Array.isArray(value.runOrder)
      ? value.runOrder.filter((item): item is string => typeof item === 'string')
      : [],
    runs: Array.isArray(value.runs)
      ? structuredClone(value.runs) as HarnessNodeRunRecord[]
      : [],
    ...(typeof value.error === 'string' ? { error: value.error } : {})
  }
}

export function appendInvocationHistory(current: unknown, invocation: HarnessInvocation): HarnessInvocation[] {
  const existing = Array.isArray(current)
    ? current.flatMap((item) => {
        const normalized = invocationRecord(item)
        return normalized ? [normalized] : []
      })
    : []
  return [...existing.slice(-9), invocation]
}

function nodeLabel(node: RuntimeNode): string {
  return typeof node.data.label === 'string' && node.data.label.trim() ? node.data.label.trim() : node.id
}

async function emit(
  host: HarnessExecutionHost,
  runId: string,
  at: string,
  type: string,
  payload: Record<string, unknown>
): Promise<void> {
  await host.events?.emit({ runId, at, type, payload })
}

export async function executeHarnessInvocation(params: {
  harnessId: string
  targetNodeId: string
  exposureId: string
  nodes: RuntimeNode[]
  edges: RuntimeEdge[]
  existingRuntime?: Record<string, unknown>
  host: HarnessExecutionHost
  signal: AbortSignal
}): Promise<HarnessExecutionResult> {
  const target = params.nodes.find((node) => node.id === params.targetNodeId)
  if (!target) {
    throw new Error(`Harness exposure target was not found: ${params.targetNodeId}.`)
  }
  if (target.type !== 'agent' && target.type !== 'tool') {
    throw new Error(`Harness exposure must target an executable Agent or Tool: ${target.id}.`)
  }

  const runtime = params.existingRuntime ?? {}
  const startedAt = params.host.clock.now()
  const invocationId = params.host.ids.create('inv')
  const invocation: HarnessInvocation = {
    id: invocationId,
    status: 'running',
    startedAt,
    completedAt: '',
    targetNodeId: target.id,
    exposureId: params.exposureId,
    currentNodeId: target.id,
    runOrder: [target.id],
    runs: []
  }

  await params.host.updateHarness({
    status: 'running',
    invocation
  })
  await emit(params.host, invocationId, startedAt, 'harness.started', {
    harnessId: params.harnessId,
    exposureId: params.exposureId,
    nodeId: target.id,
    nodeType: target.type
  })

  const runs: HarnessNodeRunRecord[] = []
  try {
    if (params.signal.aborted) throw new Error('run aborted')
    const record = await params.host.executeNode({
      harnessId: params.harnessId,
      invocationId,
      node: target,
      nodes: params.nodes,
      edges: params.edges,
      priorRuns: runs,
      signal: params.signal
    })
    if (params.signal.aborted) throw new Error('run aborted')
    runs.push(record)
    const status: HarnessExecutionResult['status'] = record.success ? 'completed' : 'needs_repair'
    const completedAt = params.host.clock.now()
    const completedInvocation: HarnessInvocation = {
      ...invocation,
      status,
      completedAt,
      currentNodeId: '',
      runs
    }
    const summary = `${nodeLabel(target)}: ${record.success ? 'completed' : 'needs repair'}${record.summary ? ` - ${record.summary}` : ''}`
    await params.host.updateHarness({
      summary,
      status,
      invocation: completedInvocation,
      invocations: appendInvocationHistory(runtime.invocations, completedInvocation),
      completionGaps: record.completionGaps
    })
    await emit(params.host, invocationId, completedAt, status === 'completed' ? 'harness.completed' : 'harness.needs_repair', {
      harnessId: params.harnessId,
      exposureId: params.exposureId,
      nodeId: target.id,
      summary,
      completionGaps: record.completionGaps
    })
    return {
      success: status === 'completed',
      harnessId: params.harnessId,
      exposureId: params.exposureId,
      nodeId: target.id,
      nodeType: target.type,
      status,
      invocationId,
      ...(record.result === undefined ? {} : { result: record.result }),
      summary,
      completionGaps: record.completionGaps
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    const completedAt = params.host.clock.now()
    const aborted = params.signal.aborted
    const failedInvocation: HarnessInvocation = {
      ...invocation,
      status: aborted ? 'aborted' : 'error',
      completedAt,
      currentNodeId: '',
      runs,
      ...(aborted ? {} : { error: message })
    }
    await params.host.updateHarness({
      status: aborted ? 'aborted' : 'error',
      invocation: failedInvocation,
      invocations: appendInvocationHistory(runtime.invocations, failedInvocation),
      ...(aborted ? {} : { lastError: message }),
      completionGaps: [aborted ? 'aborted' : 'runtime_error']
    })
    await emit(params.host, invocationId, completedAt, aborted ? 'harness.aborted' : 'harness.error', {
      harnessId: params.harnessId,
      exposureId: params.exposureId,
      nodeId: target.id,
      ...(aborted ? {} : { error: message })
    })
    return {
      success: false,
      harnessId: params.harnessId,
      exposureId: params.exposureId,
      nodeId: target.id,
      nodeType: target.type,
      status: aborted ? 'aborted' : 'error',
      invocationId,
      completionGaps: [aborted ? 'aborted' : 'runtime_error'],
      ...(aborted ? {} : { error: message })
    }
  }
}
