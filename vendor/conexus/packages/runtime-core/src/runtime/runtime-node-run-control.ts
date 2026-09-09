import type { RuntimeJobEvent } from '@conexus/runtime-protocol'
import {
  RuntimeJobRegistry,
  type RuntimeJob
} from './runtime-job-registry.js'

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function jobMatchesNode(job: RuntimeJob, nodeId: string): boolean {
  return job.metadata?.agentNodeId === nodeId
    || job.metadata?.harnessNodeId === nodeId
    || job.id.startsWith(`agent:${nodeId}:`)
    || job.id.startsWith(`harness:${nodeId}:`)
}

function newestFirst(left: RuntimeJob, right: RuntimeJob): number {
  return Date.parse(right.startedAt) - Date.parse(left.startedAt)
    || Date.parse(right.updatedAt) - Date.parse(left.updatedAt)
}

export function runtimeJobsForNode(
  jobs: RuntimeJobRegistry,
  nodeId: string
): RuntimeJob[] {
  return jobs.list()
    .filter((job) => jobMatchesNode(job, nodeId))
    .sort(newestFirst)
}

export function currentRuntimeJobForNode(
  jobs: RuntimeJobRegistry,
  nodeId: string
): RuntimeJob | undefined {
  return runtimeJobsForNode(jobs, nodeId)
    .find((job) => !jobs.isTerminalStatus(job.status) && job.status !== 'idle')
}

function waitForRuntimeJobSignal(
  jobs: RuntimeJobRegistry,
  jobId: string,
  signal?: AbortSignal
): Promise<{ job: RuntimeJob; event: RuntimeJobEvent; kind: 'terminal' | 'attention' } | undefined> {
  if (signal?.aborted) return Promise.resolve(undefined)
  return new Promise((resolve) => {
    let settled = false
    let offTerminal = (): void => {}
    let offAttention = (): void => {}
    const finish = (
      job: RuntimeJob,
      event: RuntimeJobEvent,
      kind: 'terminal' | 'attention'
    ): void => {
      if (settled) return
      settled = true
      offTerminal()
      offAttention()
      signal?.removeEventListener('abort', onAbort)
      resolve({ job, event, kind })
    }
    const onAbort = (): void => {
      if (settled) return
      settled = true
      offTerminal()
      offAttention()
      resolve(undefined)
    }
    offTerminal = jobs.onTerminal(jobId, (job, event) => finish(job, event, 'terminal'))
    offAttention = jobs.onAttention(jobId, (job, event) => finish(job, event, 'attention'))
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

export function asyncNodeRunToolResult(params: {
  targetId: string
}): string {
  return JSON.stringify({
    success: true,
    node_id: params.targetId,
    status: 'running'
  })
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function parsedJson(value: string): unknown {
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

function publicRuntimeJobResult(job: RuntimeJob): { result?: unknown; error?: string } {
  const payload = [...job.events].reverse().find((event) => event.event === 'result')?.payload
  let value: unknown = payload
  if (record(value) && Object.keys(value).length === 1 && 'result' in value) value = value.result
  if (typeof value === 'string') value = parsedJson(value)
  let error: string | undefined
  if (record(value)) {
    const compact = { ...value }
    for (const key of ['success', 'nodeId', 'node_id', 'status', 'messages', 'toolCounts', 'toolNames', 'canvasObservation']) {
      delete compact[key]
    }
    if (typeof compact.lastError === 'string' && compact.lastError.trim()) error = compact.lastError.trim()
    delete compact.lastError
    if (typeof compact.error === 'string' && compact.error.trim()) error = compact.error.trim()
    delete compact.error
    if (Array.isArray(compact.completionGaps) && compact.completionGaps.length > 0) {
      compact.completion_gaps = compact.completionGaps
    }
    delete compact.completionGaps
    if (Array.isArray(compact.completion_gaps) && compact.completion_gaps.length === 0) delete compact.completion_gaps
    if (compact.summary === null || compact.summary === '') delete compact.summary
    if (compact.output === undefined) delete compact.output
    value = Object.keys(compact).length > 0 ? compact : undefined
  }
  if (!error) {
    const statusPayload = [...job.events].reverse().find((event) =>
      event.event === 'error' || event.event === 'status')?.payload
    if (typeof statusPayload?.error === 'string' && statusPayload.error.trim()) {
      error = statusPayload.error.trim()
    }
  }
  return {
    ...(value === undefined ? {} : { result: value }),
    ...(error ? { error } : {})
  }
}

function publicRuntimeJobAttention(job: RuntimeJob): Record<string, unknown> | undefined {
  const health = job.health
  if (!health || (health.status !== 'stuck' && health.status !== 'needs_attention')) return undefined
  return {
    status: health.status,
    reason: health.reason,
    ...(health.currentTool ? { current_tool: health.currentTool } : {})
  }
}

export async function controlRuntimeNodeRun(
  jobs: RuntimeJobRegistry,
  args: Record<string, unknown>,
  signal?: AbortSignal,
  requesterNodeId?: string
): Promise<string> {
  const action = text(args.action)
  const nodeId = text(args.node_id)
  if (!nodeId) return JSON.stringify({ success: false, error: 'node_id is required' })
  if (action !== 'wait' && action !== 'cancel') {
    return JSON.stringify({ success: false, error: 'action must be wait or cancel' })
  }
  if (requesterNodeId === nodeId) {
    return JSON.stringify({
      success: false,
      node_id: nodeId,
      error: 'An Agent cannot use run.wait or run.cancel on its own run.'
    })
  }

  const current = currentRuntimeJobForNode(jobs, nodeId)
  if (!current) {
    const latest = runtimeJobsForNode(jobs, nodeId)[0]
    return JSON.stringify({
      success: action === 'wait',
      node_id: nodeId,
      status: latest?.status ?? 'idle',
      ...(latest ? publicRuntimeJobResult(latest) : {}),
      ...(action === 'cancel' ? { error: `node has no current run: ${nodeId}` } : {})
    })
  }

  if (action === 'cancel') {
    const accepted = jobs.abort(current.id)
    return JSON.stringify({
      success: accepted,
      node_id: nodeId,
      status: accepted ? 'cancellation_requested' : 'not_cancelled'
    })
  }

  const waited = await waitForRuntimeJobSignal(jobs, current.id, signal)
  if (!waited) {
    return JSON.stringify({
      success: false,
      node_id: nodeId,
      status: 'aborted',
      error: 'Waiting for the node run was aborted.'
    })
  }
  const attention = waited.kind === 'attention'
    ? publicRuntimeJobAttention(waited.job)
    : undefined
  return JSON.stringify({
    success: true,
    node_id: nodeId,
    status: waited.job.status,
    ...(waited.kind === 'terminal' ? publicRuntimeJobResult(waited.job) : {}),
    ...(attention ? { attention } : {})
  })
}
