import type { AgentPendingInteractionSnapshot, AgentRunController } from './agent-run-controller.js'

export interface AgentControlJob {
  jobId: string
  ownerNodeId: string
  controller: AgentRunController
}

export interface AgentControlPendingInteraction extends AgentControlJob {
  interaction: AgentPendingInteractionSnapshot
}

export interface AgentControlSessionOptions {
  sessionId: string
  deadlineAt?: number
  now?: () => number
  onIdle: () => void
}

/**
 * Provider interaction ids are only unique within one model run. Scope them to
 * the concrete RuntimeJob before exposing them to a client.
 */
export function agentInteractionId(jobId: string, interactionId: string): string {
  const normalizedJobId = jobId.trim()
  const normalizedInteractionId = interactionId.trim()
  if (!normalizedJobId || !normalizedInteractionId) {
    throw new TypeError('Agent interactions require jobId and interactionId.')
  }
  return `agent-job:${encodeURIComponent(normalizedJobId)}:interaction:${encodeURIComponent(normalizedInteractionId)}`
}

/**
 * Host-neutral ownership and control routing for every independently
 * controllable Agent job launched from one client session.
 */
export class AgentControlSession {
  readonly sessionId: string
  readonly deadlineAt?: number
  private readonly jobs = new Map<string, AgentControlJob>()
  private readonly now: () => number
  private readonly onIdle: () => void
  private registeredAnyJob = false
  private idleNotified = false

  constructor(options: AgentControlSessionOptions) {
    if (!options.sessionId.trim()) throw new TypeError('Agent control session requires a session id.')
    if (options.deadlineAt !== undefined && !Number.isFinite(options.deadlineAt)) {
      throw new TypeError('Agent control session deadline must be finite when provided.')
    }
    this.sessionId = options.sessionId
    this.deadlineAt = options.deadlineAt
    this.now = options.now ?? Date.now
    this.onIdle = options.onIdle
  }

  get activeJobCount(): number {
    return this.jobs.size
  }

  get activeJobs(): AgentControlJob[] {
    return [...this.jobs.values()]
  }

  get activeOwnerNodeIds(): string[] {
    return [...new Set([...this.jobs.values()].map((job) => job.ownerNodeId))]
  }

  registerJob(job: AgentControlJob): void {
    const jobId = job.jobId.trim()
    const ownerNodeId = job.ownerNodeId.trim()
    if (!jobId || !ownerNodeId) throw new TypeError('Agent control jobs require jobId and ownerNodeId.')
    const existing = this.jobs.get(jobId)
    if (existing) {
      if (existing.ownerNodeId !== ownerNodeId || existing.controller !== job.controller) {
        throw new Error(`Agent control job is already registered with different controls: ${jobId}.`)
      }
      return
    }
    if (this.idleNotified) throw new Error(`Agent control session is already closed: ${this.sessionId}.`)
    this.registeredAnyJob = true
    this.jobs.set(jobId, { jobId, ownerNodeId, controller: job.controller })
  }

  finishJob(jobId: string): boolean {
    const removed = this.jobs.delete(jobId.trim())
    if (removed) this.notifyIdleIfNeeded()
    return removed
  }

  job(jobId: string): AgentControlJob | undefined {
    return this.jobs.get(jobId.trim())
  }

  currentJobForOwner(ownerNodeId: string): AgentControlJob | undefined {
    const normalizedOwner = ownerNodeId.trim()
    return [...this.jobs.values()].filter((job) => job.ownerNodeId === normalizedOwner).at(-1)
  }

  pendingInteraction(interactionId: string): AgentControlPendingInteraction | undefined {
    const normalizedId = interactionId.trim()
    if (!normalizedId) return undefined
    for (const job of this.jobs.values()) {
      const interaction = job.controller.pendingInteractions.find((candidate) => candidate.id === normalizedId)
      if (interaction) return { ...job, interaction }
    }
    return undefined
  }

  pendingOwnerNodeIds(): string[] {
    return [...new Set(this.activeJobs.flatMap((job) =>
      job.controller.pendingInteractions.map((interaction) => interaction.ownerNodeId)
    ))]
  }

  abortAll(reason?: unknown): void {
    for (const job of this.jobs.values()) job.controller.abort(reason)
  }

  remainingTimeoutMs(): number | undefined {
    return this.deadlineAt === undefined
      ? undefined
      : Math.max(1, Math.ceil(this.deadlineAt - this.now()))
  }

  private notifyIdleIfNeeded(): void {
    if (!this.registeredAnyJob || this.jobs.size > 0 || this.idleNotified) return
    this.idleNotified = true
    this.onIdle()
  }
}
