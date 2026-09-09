import type {
  RuntimeJobStatus,
  RuntimeJobTerminalStatus,
  RuntimeKind
} from '@conexus/runtime-protocol'
import { AgentRunController } from './agent-run-controller.js'
import type { AgentHostRuntimeSession } from './agent-host-runtime-session.js'

type MaybePromise<T> = T | Promise<T>

export interface AgentHostNodeRunFinalization {
  status: RuntimeJobTerminalStatus
  result?: Record<string, unknown>
  statusPayload?: Record<string, unknown>
}

export interface AgentHostNodeRunContext {
  jobId: string
  controller: AgentRunController
  signal: AbortSignal
}

export interface StartAgentHostNodeRunOptions<TResult> {
  session: AgentHostRuntimeSession
  kind: Extract<RuntimeKind, 'agent' | 'harness'>
  ownerNodeId: string
  parentSignal?: AbortSignal
  controller?: AgentRunController
  jobId?: string
  status?: Extract<RuntimeJobStatus, 'starting' | 'running'>
  metadata?: Record<string, unknown>
  run: (context: AgentHostNodeRunContext) => Promise<TResult>
  finalize: (result: TResult, context: AgentHostNodeRunContext) => MaybePromise<AgentHostNodeRunFinalization>
  onError?: (
    error: unknown,
    context: AgentHostNodeRunContext
  ) => MaybePromise<AgentHostNodeRunFinalization | undefined>
  onExecution?: (execution: Promise<void>) => void
}

export interface StartedAgentHostNodeRun {
  jobId: string
  controller: AgentRunController
  execution: Promise<void>
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/**
 * Shared asynchronous node-capability Job lifecycle. Registration, cancellation,
 * status transitions, terminal events, and control-session cleanup are kept
 * out of Electron and Web adapters.
 */
export function startAgentHostNodeRun<TResult>(
  options: StartAgentHostNodeRunOptions<TResult>
): StartedAgentHostNodeRun {
  const ownerNodeId = options.ownerNodeId.trim()
  if (!ownerNodeId) throw new TypeError('Agent host node runs require ownerNodeId.')
  const controller = options.controller ?? new AgentRunController(
    options.parentSignal ? { signal: options.parentSignal } : {}
  )
  const jobId = options.jobId?.trim() || options.session.newJobId(options.kind, ownerNodeId)
  const context: AgentHostNodeRunContext = {
    jobId,
    controller,
    signal: controller.signal
  }

  options.session.registerJob({
    jobId,
    kind: options.kind,
    ownerNodeId,
    controller,
    status: options.status ?? 'starting',
    metadata: options.metadata
  })

  const execution = (async (): Promise<void> => {
    try {
      options.session.updateJob(jobId, 'running', { nodeId: ownerNodeId })
      const result = await options.run(context)
      const finalization = await options.finalize(result, context)
      options.session.finalizeJob(
        jobId,
        finalization.status,
        finalization.result,
        finalization.statusPayload
      )
    } catch (error) {
      let finalization: AgentHostNodeRunFinalization | undefined
      try {
        finalization = await options.onError?.(error, context)
      } catch (adapterError) {
        error = adapterError
      }
      const aborted = controller.signal.aborted
      const message = errorMessage(error)
      const fallback: AgentHostNodeRunFinalization = {
        status: aborted ? 'aborted' : 'error',
        result: {
          nodeId: ownerNodeId,
          status: aborted ? 'aborted' : 'error',
          error: message
        },
        statusPayload: {
          nodeId: ownerNodeId,
          status: aborted ? 'aborted' : 'error',
          error: message
        }
      }
      const terminal = finalization ?? fallback
      options.session.finalizeJob(
        jobId,
        terminal.status,
        terminal.result,
        terminal.statusPayload
      )
    }
  })()

  options.onExecution?.(execution)
  void execution.catch(() => undefined)
  return { jobId, controller, execution }
}

export interface ActiveAgentHostNodeRunOptions {
  session: AgentHostRuntimeSession
  ownerNodeId: string
  kind: 'agent' | 'harness'
}

/** Returns the canonical node-run busy response, or undefined when runnable. */
export function activeAgentHostNodeRunResult(
  options: ActiveAgentHostNodeRunOptions
): string | undefined {
  const current = options.session.currentJobForOwner(options.ownerNodeId)
  const job = current ? options.session.job(current.jobId) : undefined
  if (!job || options.session.isTerminalStatus(job.status) || job.status === 'idle') return undefined
  const label = options.kind === 'harness' ? 'Harness' : 'Agent'
  return JSON.stringify({
    success: false,
    node_id: options.ownerNodeId,
    status: job.status,
    error: `${label} node already has a current run. Use run.wait or run.cancel through use, or create another ${label} node for parallel work.`
  })
}

export interface ValidateAgentNodeDelegationOptions {
  targetNodeId: string
  requesterNodeId?: string
  callStack?: readonly string[]
  callDepth: number
  maximumCallDepth?: number
  task?: string
}

/** Returns the canonical agent.run validation failure, or undefined. */
export function validateAgentNodeDelegation(
  options: ValidateAgentNodeDelegationOptions
): string | undefined {
  const targetNodeId = options.targetNodeId.trim()
  if (options.callStack?.includes(targetNodeId)) {
    return JSON.stringify({
      success: false,
      error: `Recursive Agent cycle rejected: ${[...options.callStack, targetNodeId].join(' -> ')}.`
    })
  }
  if (options.requesterNodeId?.trim() === targetNodeId) {
    return JSON.stringify({ success: false, error: 'Agent cannot run itself' })
  }
  if (options.callDepth >= (options.maximumCallDepth ?? 3)) {
    return JSON.stringify({ success: false, error: 'agent.run recursion limit reached' })
  }
  if (!options.task?.trim()) {
    return JSON.stringify({ success: false, error: 'task is required for Agent nodes' })
  }
  return undefined
}

export interface AgentHostNodeRunTarget {
  id: string
  type?: string
}

export interface DispatchAgentHostNodeRunOptions<TNode extends AgentHostNodeRunTarget> {
  session: AgentHostRuntimeSession
  args: Record<string, unknown>
  requesterNodeId?: string
  callDepth: number
  callStack?: readonly string[]
  findNode: (nodeId: string) => TNode | undefined
  runAgent: (node: TNode, task: string) => MaybePromise<string>
  runHarness: (node: TNode) => MaybePromise<string>
  runOther?: (node: TNode, args: Record<string, unknown>) => MaybePromise<string | undefined>
}

function optionalText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

/**
 * Canonical node capability router. Hosts only adapt how an accepted target is
 * executed; lookup, validation, active-run exclusion, and result errors are
 * identical.
 */
export async function dispatchAgentHostNodeRun<TNode extends AgentHostNodeRunTarget>(
  options: DispatchAgentHostNodeRunOptions<TNode>
): Promise<string> {
  const nodeId = optionalText(options.args.node_id)
  if (!nodeId) return JSON.stringify({ success: false, error: 'node_id is required' })
  const target = options.findNode(nodeId)
  if (!target) return JSON.stringify({ success: false, error: `node not found: ${nodeId}` })

  if (target.type === 'agent') {
    const task = optionalText(options.args.task)
    const invalid = validateAgentNodeDelegation({
      targetNodeId: target.id,
      requesterNodeId: options.requesterNodeId,
      callStack: options.callStack,
      callDepth: options.callDepth,
      task
    })
    if (invalid) return invalid
    const busy = activeAgentHostNodeRunResult({
      session: options.session,
      ownerNodeId: target.id,
      kind: 'agent'
    })
    if (busy) return busy
    return options.runAgent(target, task!)
  }

  if (target.type === 'harness') {
    const busy = activeAgentHostNodeRunResult({
      session: options.session,
      ownerNodeId: target.id,
      kind: 'harness'
    })
    if (busy) return busy
    return options.runHarness(target)
  }

  const other = await options.runOther?.(target, options.args)
  if (other !== undefined) return other
  return JSON.stringify({
    success: false,
    node_id: target.id,
    error: `This runtime cannot execute ${target.type ?? 'unknown'} node ${target.id}.`
  })
}
