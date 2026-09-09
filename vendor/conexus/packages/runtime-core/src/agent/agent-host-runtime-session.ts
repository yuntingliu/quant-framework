import {
  AgentControlSession,
  type AgentControlJob,
  type AgentControlPendingInteraction,
  type AgentControlSessionOptions
} from './agent-control-session.js'
import type { AgentRunController } from './agent-run-controller.js'
import {
  RuntimeJobRegistry,
  type RuntimeJob
} from '../runtime/runtime-job-registry.js'
import { controlRuntimeNodeRun } from '../runtime/runtime-node-run-control.js'
import type {
  RuntimeJobEventName,
  RuntimeJobStatus,
  RuntimeJobTerminalStatus,
  RuntimeKind
} from '@conexus/runtime-protocol'

export interface AgentHostRuntimeSessionOptions extends AgentControlSessionOptions {
  jobs: RuntimeJobRegistry
}

export interface RegisterAgentHostJob {
  jobId: string
  ownerNodeId: string
  kind?: RuntimeKind
  controller: AgentRunController
  status?: Extract<RuntimeJobStatus, 'starting' | 'running' | 'thinking' | 'waiting'>
  metadata?: Record<string, unknown>
}

export interface AcceptAgentInteractionOptions {
  statusPayload?: Record<string, unknown>
  beforeResolve?: (pending: AgentControlPendingInteraction) => void | Promise<void>
}

export function isAgentHostTimeout(reason: unknown): boolean {
  return reason instanceof DOMException
    ? reason.name === 'TimeoutError'
    : Boolean(reason && typeof reason === 'object' && 'name' in reason && reason.name === 'TimeoutError')
}

/**
 * Shared host-level lifecycle for interactive Agent RuntimeJobs. Hosts retain
 * only transport, persistence, model, and tool adapters.
 */
export class AgentHostRuntimeSession {
  readonly sessionId: string
  private readonly jobs: RuntimeJobRegistry
  private readonly controls: AgentControlSession
  private deadlineTimer: ReturnType<typeof setTimeout> | undefined

  constructor(options: AgentHostRuntimeSessionOptions) {
    this.sessionId = options.sessionId
    this.jobs = options.jobs
    this.controls = new AgentControlSession({
      ...options,
      onIdle: () => {
        this.clearDeadline()
        options.onIdle()
      }
    })
    if (options.deadlineAt !== undefined) {
      const delay = Math.max(0, Math.ceil(options.deadlineAt - (options.now?.() ?? Date.now())))
      this.deadlineTimer = setTimeout(() => {
        this.deadlineTimer = undefined
        this.abortAll(new DOMException('Agent control session reached its deadline.', 'TimeoutError'))
      }, Math.min(delay, 2_147_483_647))
      const timer = this.deadlineTimer as ReturnType<typeof setTimeout> & { unref?: () => void }
      timer.unref?.()
    }
  }

  get activeJobCount(): number {
    return this.controls.activeJobCount
  }

  get activeOwnerNodeIds(): string[] {
    return this.controls.activeOwnerNodeIds
  }

  registerJob(record: RegisterAgentHostJob): RuntimeJob {
    const kind = record.kind ?? 'agent'
    const metadata = {
      ...(record.metadata ?? {}),
      ...(kind === 'harness'
        ? { harnessNodeId: record.ownerNodeId }
        : { agentNodeId: record.ownerNodeId })
    }
    const controls = {
      sessionId: this.sessionId,
      metadata,
      abort: () => record.controller.abort(new DOMException('Run aborted by user.', 'AbortError')),
      continue: (input: unknown) => record.controller.continue(input)
    }
    const existing = this.jobs.get(record.jobId)
    let job: RuntimeJob
    if (!existing) {
      job = this.jobs.start({
        id: record.jobId,
        kind,
        status: record.status ?? 'running',
        ...controls
      })
    } else if (existing.status === 'waiting') {
      job = this.jobs.resume(record.jobId, controls)
    } else {
      if (this.jobs.isTerminalStatus(existing.status)) {
        throw new Error(`Agent RuntimeJob ${record.jobId} cannot start from status ${existing.status}.`)
      }
      job = existing
    }
    this.controls.registerJob({
      jobId: record.jobId,
      ownerNodeId: record.ownerNodeId,
      controller: record.controller
    })
    return job
  }

  job(jobId: string): RuntimeJob | undefined {
    return this.jobs.get(jobId)
  }

  newJobId(kind: RuntimeKind, targetId: string): string {
    return this.jobs.newJobId(kind, targetId)
  }

  isTerminalStatus(status: RuntimeJobStatus | string | undefined | null): status is RuntimeJobTerminalStatus {
    return this.jobs.isTerminalStatus(status)
  }

  currentJobForOwner(ownerNodeId: string): AgentControlJob | undefined {
    return this.controls.currentJobForOwner(ownerNodeId)
  }

  pendingInteraction(interactionId: string): AgentControlPendingInteraction | undefined {
    return this.controls.pendingInteraction(interactionId)
  }

  pendingOwnerNodeIds(): string[] {
    return this.controls.pendingOwnerNodeIds()
  }

  controlNodeRun(
    args: Record<string, unknown>,
    signal?: AbortSignal,
    requesterNodeId?: string
  ): Promise<string> {
    return controlRuntimeNodeRun(this.jobs, args, signal, requesterNodeId)
  }

  async acceptInteraction(
    interactionId: string,
    value: unknown,
    options: AcceptAgentInteractionOptions = {}
  ): Promise<AgentControlPendingInteraction | undefined> {
    const pending = this.controls.pendingInteraction(interactionId)
    if (!pending) return undefined
    await options.beforeResolve?.(pending)
    if (!pending.controller.resolveInteraction(interactionId, value)) return undefined
    this.jobs.update(
      pending.jobId,
      'running',
      options.statusPayload ?? { nodeId: pending.ownerNodeId }
    )
    return pending
  }

  updateJob(jobId: string, status: RuntimeJobStatus, payload?: Record<string, unknown>): RuntimeJob | undefined {
    return this.jobs.update(jobId, status, payload)
  }

  recordEvent(jobId: string, event: RuntimeJobEventName, payload?: Record<string, unknown>): RuntimeJob | undefined {
    return this.jobs.event(jobId, event, payload)
  }

  finalizeJob(
    jobId: string,
    status: RuntimeJobTerminalStatus,
    result?: Record<string, unknown>,
    statusPayload?: Record<string, unknown>
  ): RuntimeJob | undefined {
    const existing = this.jobs.get(jobId)
    const job = existing && this.jobs.isTerminalStatus(existing.status)
      ? existing
      : this.jobs.finalize(jobId, { status, result, statusPayload })
    this.controls.finishJob(jobId)
    return job
  }

  finalizeOpenDescendantJobs(
    rootJobId: string,
    status: Extract<RuntimeJobTerminalStatus, 'error' | 'aborted'>,
    error?: string
  ): RuntimeJob[] {
    const finalized: RuntimeJob[] = []
    for (const job of this.jobs.list()) {
      if (
        job.id === rootJobId
        || job.metadata?.rootJobId !== rootJobId
        || this.jobs.isTerminalStatus(job.status)
        || job.status === 'idle'
      ) {
        continue
      }
      const nodeId = typeof job.metadata?.agentNodeId === 'string'
        ? job.metadata.agentNodeId
        : typeof job.metadata?.harnessNodeId === 'string'
          ? job.metadata.harnessNodeId
          : undefined
      const payload = {
        ...(nodeId ? { nodeId } : {}),
        status,
        ...(error ? { error } : {})
      }
      const terminal = this.finalizeJob(job.id, status, payload, payload)
      if (terminal) finalized.push(terminal)
    }
    return finalized
  }

  finishJob(jobId: string): boolean {
    return this.controls.finishJob(jobId)
  }

  abortAll(reason?: unknown): void {
    this.controls.abortAll(reason)
  }

  remainingTimeoutMs(): number | undefined {
    return this.controls.remainingTimeoutMs()
  }

  private clearDeadline(): void {
    if (!this.deadlineTimer) return
    clearTimeout(this.deadlineTimer)
    this.deadlineTimer = undefined
  }
}
