import type { RuntimeJobRegistry } from '../runtime/runtime-job-registry.js'
import { AgentHostRuntimeSession } from './agent-host-runtime-session.js'

export interface CreateAgentHostSession<TContext> {
  sessionId: string
  rootNodeId: string
  deadlineAt?: number
  jobs: RuntimeJobRegistry
  now?: () => number
  createContext: (session: AgentHostRuntimeSession) => TContext
  onIdle?: (context: TContext) => void
}

export interface AgentHostSessionRecord<TContext> {
  rootNodeId: string
  session: AgentHostRuntimeSession
  context: TContext
}

/** Shared active-session and Agent-node exclusivity index for interactive hosts. */
export class AgentHostSessionRegistry<TContext> {
  private readonly records = new Map<string, AgentHostSessionRecord<TContext>>()

  create(options: CreateAgentHostSession<TContext>): AgentHostSessionRecord<TContext> {
    const sessionId = options.sessionId.trim()
    const rootNodeId = options.rootNodeId.trim()
    if (!sessionId || !rootNodeId) throw new TypeError('Agent host sessions require sessionId and rootNodeId.')
    if (this.records.has(sessionId)) throw new Error(`Agent session already exists: ${sessionId}.`)
    if (this.hasActiveOwner(rootNodeId)) throw new Error(`Agent node already has an active run: ${rootNodeId}.`)

    let context!: TContext
    const session = new AgentHostRuntimeSession({
      sessionId,
      ...(options.deadlineAt !== undefined ? { deadlineAt: options.deadlineAt } : {}),
      jobs: options.jobs,
      ...(options.now ? { now: options.now } : {}),
      onIdle: () => {
        const current = this.records.get(sessionId)
        if (current?.session !== session) return
        this.records.delete(sessionId)
        options.onIdle?.(current.context)
      }
    })
    context = options.createContext(session)
    const record = { rootNodeId, session, context }
    this.records.set(sessionId, record)
    return record
  }

  get(sessionId: string): AgentHostSessionRecord<TContext> | undefined {
    return this.records.get(sessionId.trim())
  }

  has(sessionId: string): boolean {
    return this.records.has(sessionId.trim())
  }

  hasActiveOwner(ownerNodeId: string): boolean {
    const normalized = ownerNodeId.trim()
    return [...this.records.values()].some((record) => (
      record.rootNodeId === normalized || record.session.activeOwnerNodeIds.includes(normalized)
    ))
  }

  values(): AgentHostSessionRecord<TContext>[] {
    return [...this.records.values()]
  }

  abortAll(reason?: unknown): void {
    for (const record of this.records.values()) record.session.abortAll(reason)
  }
}
