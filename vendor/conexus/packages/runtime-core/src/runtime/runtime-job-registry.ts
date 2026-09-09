import type {
  RuntimeHealth,
  RuntimeJobEvent,
  RuntimeJobEventName,
  RuntimeJobEventPayload,
  RuntimeJobSnapshot,
  RuntimeJobStatus,
  RuntimeJobTerminalStatus,
  RuntimeKind
} from '@conexus/runtime-protocol'

export type RuntimeJobAbortHandler = () => void | boolean
export type RuntimeJobContinueHandler = (input: unknown) => void | boolean

export interface RuntimeJob {
  id: string
  kind: RuntimeKind
  status: RuntimeJobStatus
  startedAt: string
  updatedAt: string
  sessionId?: string | null
  metadata?: Record<string, unknown>
  health?: RuntimeHealth
  abort?: RuntimeJobAbortHandler
  continue?: RuntimeJobContinueHandler
  events: RuntimeJobEvent[]
}

export interface RuntimeJobSnapshotOptions {
  eventLimit?: number
}

export interface RuntimeHealthPolicy {
  checkIntervalMs: number
  idleThresholdMs: number
  stuckToolThresholdMs: number
}

export interface RuntimeJobRetentionPolicy {
  maxEventsPerJob: number
  maxEventBytesPerJob: number
}

export interface RuntimeJobRegistryOptions {
  clock?: () => Date
  createId?: (kind: RuntimeKind, targetId: string) => string
  onEvent?: (event: RuntimeJobEvent, job: RuntimeJob) => void
  retention?: Partial<RuntimeJobRetentionPolicy>
  healthPolicy?: Partial<RuntimeHealthPolicy>
}

export interface StartRuntimeJobRecord {
  id: string
  kind: RuntimeKind
  status?: RuntimeJobStatus
  sessionId?: string | null
  metadata?: Record<string, unknown>
  abort?: RuntimeJobAbortHandler
  continue?: RuntimeJobContinueHandler
}

export interface FinalizeRuntimeJobRecord {
  status: RuntimeJobTerminalStatus
  result?: Record<string, unknown>
  statusPayload?: Record<string, unknown>
}

export interface ResumeRuntimeJobRecord {
  sessionId?: string | null
  metadata?: Record<string, unknown>
  abort?: RuntimeJobAbortHandler
  continue?: RuntimeJobContinueHandler
}

export const DEFAULT_RUNTIME_HEALTH_POLICY: RuntimeHealthPolicy = {
  checkIntervalMs: 10_000,
  idleThresholdMs: 60_000,
  stuckToolThresholdMs: 180_000
}

export const DEFAULT_RUNTIME_JOB_RETENTION_POLICY: RuntimeJobRetentionPolicy = {
  maxEventsPerJob: 300,
  maxEventBytesPerJob: 1024 * 1024
}

export type RuntimeJobListener = (job: RuntimeJob, event: RuntimeJobEvent) => void

function nonNegativeInteger(value: number, name: string): number {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${name} must be a non-negative integer.`)
  }
  return value
}

function positiveInteger(value: number, name: string): number {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new Error(`${name} must be a positive integer.`)
  }
  return value
}

function utf8ByteLength(value: string): number {
  let bytes = 0
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index)
    if (code < 0x80) {
      bytes += 1
    } else if (code < 0x800) {
      bytes += 2
    } else if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1)
      if (next >= 0xdc00 && next <= 0xdfff) index += 1
      bytes += next >= 0xdc00 && next <= 0xdfff ? 4 : 3
    } else {
      bytes += 3
    }
  }
  return bytes
}

function serializedEventBytes(event: RuntimeJobEvent): number {
  try {
    const serialized = JSON.stringify(event)
    return typeof serialized === 'string' ? utf8ByteLength(serialized) : Number.MAX_SAFE_INTEGER
  } catch {
    return Number.MAX_SAFE_INTEGER
  }
}

function cloneEvent(event: RuntimeJobEvent): RuntimeJobEvent {
  return {
    ...event,
    ...(event.payload ? { payload: { ...event.payload } } : {})
  }
}

function cloneHealth(health: RuntimeHealth | undefined): RuntimeHealth | undefined {
  return health ? { ...health } : undefined
}

/**
 * Platform-neutral in-memory registry for live Runtime jobs.
 *
 * Hosts provide their own event transport through `onEvent`. Abort requests
 * only invoke the registered cancellation hook; the executor owns the final
 * status transition after its work has actually stopped.
 */
export class RuntimeJobRegistry {
  private readonly jobs = new Map<string, RuntimeJob>()
  private readonly eventBytes = new Map<string, number>()
  private readonly eventSequences = new Map<string, number>()
  private readonly terminalListeners = new Map<string, Set<RuntimeJobListener>>()
  private readonly attentionListeners = new Map<string, Set<RuntimeJobListener>>()
  private readonly clock: () => Date
  private readonly createId?: RuntimeJobRegistryOptions['createId']
  private readonly onEvent?: RuntimeJobRegistryOptions['onEvent']
  private readonly healthPolicy: RuntimeHealthPolicy
  private readonly retention: RuntimeJobRetentionPolicy
  private readonly monitor: ReturnType<typeof setInterval>

  constructor(options: RuntimeJobRegistryOptions = {}) {
    this.clock = options.clock ?? (() => new Date())
    this.createId = options.createId
    this.onEvent = options.onEvent
    this.healthPolicy = {
      ...DEFAULT_RUNTIME_HEALTH_POLICY,
      ...options.healthPolicy
    }
    this.retention = {
      ...DEFAULT_RUNTIME_JOB_RETENTION_POLICY,
      ...options.retention
    }

    positiveInteger(this.healthPolicy.checkIntervalMs, 'healthPolicy.checkIntervalMs')
    nonNegativeInteger(this.healthPolicy.idleThresholdMs, 'healthPolicy.idleThresholdMs')
    nonNegativeInteger(this.healthPolicy.stuckToolThresholdMs, 'healthPolicy.stuckToolThresholdMs')
    nonNegativeInteger(this.retention.maxEventsPerJob, 'retention.maxEventsPerJob')
    nonNegativeInteger(this.retention.maxEventBytesPerJob, 'retention.maxEventBytesPerJob')

    this.monitor = setInterval(() => this.checkHealth(), this.healthPolicy.checkIntervalMs)
    const timer = this.monitor as ReturnType<typeof setInterval> & { unref?: () => void }
    timer.unref?.()
  }

  start(params: StartRuntimeJobRecord): RuntimeJob {
    if (this.jobs.has(params.id)) {
      throw new Error(`Runtime job already exists: ${params.id}.`)
    }
    const now = this.nowIso()
    const job: RuntimeJob = {
      id: params.id,
      kind: params.kind,
      status: params.status ?? 'starting',
      startedAt: now,
      updatedAt: now,
      sessionId: params.sessionId ?? null,
      metadata: params.metadata ? { ...params.metadata } : undefined,
      health: {
        status: 'healthy',
        reason: 'job started',
        idleMs: 0,
        checkedAt: now
      },
      abort: this.isTerminalStatus(params.status) || params.status === 'idle' ? undefined : params.abort,
      continue: this.isTerminalStatus(params.status) || params.status === 'idle' ? undefined : params.continue,
      events: []
    }
    this.jobs.set(params.id, job)
    this.eventBytes.set(params.id, 0)
    if (!this.eventSequences.has(params.id)) this.eventSequences.set(params.id, 0)
    const createdEvent = this.emit(job, 'created', params.metadata)
    if (this.isTerminalStatus(job.status)) this.notifyTerminal(job, createdEvent)
    return job
  }

  resume(id: string, params: ResumeRuntimeJobRecord = {}): RuntimeJob {
    const job = this.jobs.get(id)
    if (!job) throw new Error(`Runtime job not found: ${id}.`)
    if (job.status !== 'waiting') {
      throw new Error(`Runtime job ${id} cannot resume from status ${job.status}.`)
    }
    job.sessionId = params.sessionId ?? job.sessionId ?? null
    job.metadata = {
      ...(job.metadata ?? {}),
      ...(params.metadata ?? {})
    }
    job.abort = params.abort ?? job.abort
    job.continue = params.continue ?? job.continue
    return this.transition(job, 'running')
  }

  update(
    id: string,
    status: RuntimeJobStatus,
    payload?: Record<string, unknown>
  ): RuntimeJob | undefined {
    const job = this.jobs.get(id)
    if (!job) return undefined
    if (this.isTerminalStatus(job.status)) return job
    if (this.isTerminalStatus(status)) {
      return this.finalize(id, { status, statusPayload: payload })
    }
    return this.transition(job, status, payload)
  }

  finalize(id: string, params: FinalizeRuntimeJobRecord): RuntimeJob | undefined {
    if (!this.isTerminalStatus(params.status)) {
      throw new TypeError(`Runtime job finalization requires a terminal status, received ${String(params.status)}.`)
    }
    const job = this.jobs.get(id)
    if (!job) return undefined
    if (this.isTerminalStatus(job.status)) return job

    job.status = params.status
    job.updatedAt = this.nowIso()
    job.abort = undefined
    job.continue = undefined
    if (params.result) this.emit(job, 'result', params.result)
    const terminalEvent = this.emit(
      job,
      params.status === 'error' ? 'error' : 'status',
      params.statusPayload
    )
    this.notifyTerminal(job, terminalEvent)
    return job
  }

  event<TEvent extends RuntimeJobEventName>(
    id: string,
    event: TEvent,
    payload?: RuntimeJobEventPayload<TEvent>
  ): RuntimeJob | undefined {
    const job = this.jobs.get(id)
    if (!job) return undefined
    if (this.isTerminalStatus(job.status)) return job
    const normalizedPayload = this.normalizeEventPayload(event, payload)
    job.updatedAt = this.nowIso()
    this.emit(job, event, normalizedPayload)
    return job
  }

  get(id: string): RuntimeJob | undefined {
    return this.jobs.get(id)
  }

  list(): RuntimeJob[] {
    return [...this.jobs.values()]
  }

  isTerminalStatus(
    status: RuntimeJobStatus | string | undefined | null
  ): status is RuntimeJobTerminalStatus {
    return status === 'done' || status === 'blocked' || status === 'error' || status === 'aborted'
  }

  newJobId(kind: RuntimeKind, targetId: string): string {
    if (this.createId) return this.createId(kind, targetId)
    return `${kind}:${targetId}:${this.now().getTime()}:${Math.random().toString(36).slice(2, 8)}`
  }

  snapshot(job: RuntimeJob, options: RuntimeJobSnapshotOptions = {}): RuntimeJobSnapshot {
    const limit = Math.max(0, Math.floor(options.eventLimit ?? 20))
    const returnedEvents = limit === 0 ? [] : job.events.slice(-limit)
    const lastSequence = this.eventSequences.get(job.id) ?? 0
    const firstRetainedSequence = job.events[0]?.sequence ?? null
    return {
      id: job.id,
      kind: job.kind,
      status: job.status,
      startedAt: job.startedAt,
      updatedAt: job.updatedAt,
      sessionId: job.sessionId,
      metadata: job.metadata ? { ...job.metadata } : undefined,
      health: cloneHealth(job.health),
      events: returnedEvents.map(cloneEvent),
      eventCursor: {
        firstRetainedSequence,
        firstReturnedSequence: returnedEvents[0]?.sequence ?? null,
        lastSequence,
        nextSequence: lastSequence + 1,
        retentionTruncated: lastSequence > 0 && firstRetainedSequence !== 1
      },
      canAbort: Boolean(job.abort),
      canContinue: Boolean(job.continue)
    }
  }

  getSnapshot(id: string, options: RuntimeJobSnapshotOptions = {}): RuntimeJobSnapshot | undefined {
    const job = this.get(id)
    return job ? this.snapshot(job, options) : undefined
  }

  listSnapshots(options: RuntimeJobSnapshotOptions = {}): RuntimeJobSnapshot[] {
    return this.list().map((job) => this.snapshot(job, options))
  }

  onTerminal(id: string, listener: RuntimeJobListener): () => void {
    const job = this.jobs.get(id)
    if (job && this.isTerminalStatus(job.status)) {
      const event = job.events[job.events.length - 1]
      if (event) queueMicrotask(() => this.callListener(listener, job, event))
      return () => {}
    }
    const listeners = this.terminalListeners.get(id) ?? new Set<RuntimeJobListener>()
    listeners.add(listener)
    this.terminalListeners.set(id, listeners)
    return () => {
      listeners.delete(listener)
      if (listeners.size === 0) this.terminalListeners.delete(id)
    }
  }

  onAttention(id: string, listener: RuntimeJobListener): () => void {
    const job = this.jobs.get(id)
    if (job?.health?.status === 'stuck' || job?.health?.status === 'needs_attention') {
      const event = job.events[job.events.length - 1]
      if (event) queueMicrotask(() => this.callListener(listener, job, event))
      return () => {}
    }
    const listeners = this.attentionListeners.get(id) ?? new Set<RuntimeJobListener>()
    listeners.add(listener)
    this.attentionListeners.set(id, listeners)
    return () => {
      listeners.delete(listener)
      if (listeners.size === 0) this.attentionListeners.delete(id)
    }
  }

  abort(id: string): boolean {
    const job = this.jobs.get(id)
    if (!job?.abort) return false
    const accepted = job.abort()
    if (accepted === false) return false
    return true
  }

  continue(id: string, input: unknown): boolean {
    const job = this.jobs.get(id)
    if (!job?.continue) return false
    const accepted = job.continue(input)
    if (accepted === false) return false
    this.update(id, 'running')
    return true
  }

  remove(id: string): void {
    this.jobs.delete(id)
    this.eventBytes.delete(id)
    this.terminalListeners.delete(id)
    this.attentionListeners.delete(id)
  }

  checkHealth(): void {
    const now = this.now().getTime()
    for (const job of this.jobs.values()) {
      if (this.isTerminalStatus(job.status) || job.status === 'idle') continue
      const health = this.deriveHealth(job, now)
      if (health.status === job.health?.status && health.reason === job.health.reason) {
        job.health = health
        continue
      }
      job.health = health
      const event = this.emit(job, 'health', { health })
      if (health.status === 'stuck' || health.status === 'needs_attention') {
        this.notifyAttention(job, event)
      }
    }
  }

  dispose(): void {
    clearInterval(this.monitor)
    this.terminalListeners.clear()
    this.attentionListeners.clear()
  }

  private now(): Date {
    const value = this.clock()
    if (!(value instanceof Date) || !Number.isFinite(value.getTime())) {
      throw new Error('RuntimeJobRegistry clock must return a valid Date.')
    }
    return new Date(value.getTime())
  }

  private nowIso(): string {
    return this.now().toISOString()
  }

  private transition(
    job: RuntimeJob,
    status: Exclude<RuntimeJobStatus, RuntimeJobTerminalStatus>,
    payload?: Record<string, unknown>
  ): RuntimeJob {
    const now = this.nowIso()
    job.status = status
    job.updatedAt = now
    if (status === 'running') {
      job.health = {
        status: 'healthy',
        reason: 'job running',
        idleMs: 0,
        checkedAt: now
      }
    }
    if (status === 'idle') {
      job.abort = undefined
      job.continue = undefined
    }
    this.emit(job, 'status', payload)
    return job
  }

  private normalizeEventPayload<TEvent extends RuntimeJobEventName>(
    event: TEvent,
    payload: RuntimeJobEventPayload<TEvent> | undefined
  ): Record<string, unknown> | undefined {
    if (event !== 'tool_call' && event !== 'tool_result') return payload
    const raw = payload as Record<string, unknown> | undefined
    const callId = typeof raw?.callId === 'string' ? raw.callId.trim() : ''
    const tool = typeof raw?.tool === 'string' ? raw.tool.trim() : ''
    if (!callId || !tool) {
      throw new TypeError(`Runtime ${event} events require non-empty callId and tool values.`)
    }
    return { ...raw, callId, tool }
  }

  private deriveHealth(job: RuntimeJob, now: number): RuntimeHealth {
    const checkedAt = new Date(now).toISOString()
    const latestEvent = [...job.events].reverse().find((event) => event.event !== 'health')
    const latestAt = latestEvent ? Date.parse(latestEvent.at) : Date.parse(job.updatedAt)
    const idleMs = Number.isFinite(latestAt) ? Math.max(0, now - latestAt) : 0
    const children = [...this.jobs.values()]
      .filter((candidate) => candidate.metadata?.parentJobId === job.id)
      .sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt))
    const unhealthyChild = children.find((child) =>
      child.status === 'blocked'
      || child.status === 'error'
      || child.status === 'aborted'
      || child.health?.status === 'stuck'
      || child.health?.status === 'needs_attention')
    if (unhealthyChild) {
      const detail = unhealthyChild.status === 'blocked'
        || unhealthyChild.status === 'error'
        || unhealthyChild.status === 'aborted'
        ? `ended with ${unhealthyChild.status}`
        : `is ${unhealthyChild.health?.status}: ${unhealthyChild.health?.reason}`
      return {
        status: 'needs_attention',
        reason: `child job ${unhealthyChild.id} ${detail}`,
        idleMs,
        checkedAt
      }
    }

    const activeTool = this.activeToolCall(job, now)
    if (activeTool && activeTool.elapsedMs >= this.healthPolicy.stuckToolThresholdMs) {
      return {
        status: 'stuck',
        reason: `tool ${activeTool.tool} has not returned`,
        idleMs,
        currentTool: activeTool.tool,
        currentToolCallId: activeTool.callId,
        currentToolElapsedMs: activeTool.elapsedMs,
        checkedAt
      }
    }
    if (idleMs >= this.healthPolicy.idleThresholdMs) {
      return {
        status: 'idle',
        reason: `no runtime events for ${Math.round(idleMs / 1000)}s`,
        idleMs,
        ...(activeTool ? { currentTool: activeTool.tool, currentToolCallId: activeTool.callId, currentToolElapsedMs: activeTool.elapsedMs } : {}),
        checkedAt
      }
    }
    return {
      status: 'healthy',
      reason: 'recent runtime activity',
      idleMs,
      ...(activeTool ? { currentTool: activeTool.tool, currentToolCallId: activeTool.callId, currentToolElapsedMs: activeTool.elapsedMs } : {}),
      checkedAt
    }
  }

  private activeToolCall(job: RuntimeJob, now: number): { tool: string; callId: string; elapsedMs: number } | null {
    const open = new Map<string, { tool: string; at: string }>()
    for (const event of job.events) {
      if (event.event === 'tool_call') {
        const callId = typeof event.payload?.callId === 'string' ? event.payload.callId : ''
        const tool = typeof event.payload?.tool === 'string' ? event.payload.tool : ''
        if (callId && tool) open.set(callId, { tool, at: event.at })
      } else if (event.event === 'tool_result') {
        const callId = typeof event.payload?.callId === 'string' ? event.payload.callId : ''
        if (callId) open.delete(callId)
      }
    }
    let oldest: { tool: string; callId: string; atMs: number } | null = null
    for (const [callId, item] of open) {
      const atMs = Date.parse(item.at)
      if (!Number.isFinite(atMs)) continue
      if (!oldest || atMs < oldest.atMs) oldest = { tool: item.tool, callId, atMs }
    }
    return oldest ? { tool: oldest.tool, callId: oldest.callId, elapsedMs: Math.max(0, now - oldest.atMs) } : null
  }

  private emit(
    job: RuntimeJob,
    event: RuntimeJobEventName,
    payload?: Record<string, unknown>
  ): RuntimeJobEvent {
    const sequence = (this.eventSequences.get(job.id) ?? 0) + 1
    this.eventSequences.set(job.id, sequence)
    const item: RuntimeJobEvent = {
      eventId: `${job.id}:${sequence}`,
      jobId: job.id,
      sequence,
      kind: job.kind,
      status: job.status,
      sessionId: job.sessionId,
      metadata: job.metadata ? { ...job.metadata } : undefined,
      event,
      payload,
      at: this.nowIso()
    }
    job.events.push(item)
    let bytes = (this.eventBytes.get(job.id) ?? 0) + serializedEventBytes(item)
    while (
      job.events.length > this.retention.maxEventsPerJob
      || bytes > this.retention.maxEventBytesPerJob
    ) {
      const removed = job.events.shift()
      if (!removed) break
      bytes = Math.max(0, bytes - serializedEventBytes(removed))
    }
    this.eventBytes.set(job.id, bytes)

    try {
      this.onEvent?.(item, job)
    } catch {
      // Event transports are observational and must not corrupt Job state.
    }
    return item
  }

  private notifyTerminal(job: RuntimeJob, event: RuntimeJobEvent): void {
    const listeners = this.terminalListeners.get(job.id)
    if (!listeners?.size) return
    this.terminalListeners.delete(job.id)
    for (const listener of listeners) this.callListener(listener, job, event)
  }

  private notifyAttention(job: RuntimeJob, event: RuntimeJobEvent): void {
    const listeners = this.attentionListeners.get(job.id)
    if (!listeners?.size) return
    this.attentionListeners.delete(job.id)
    for (const listener of listeners) this.callListener(listener, job, event)
  }

  private callListener(listener: RuntimeJobListener, job: RuntimeJob, event: RuntimeJobEvent): void {
    try {
      listener(job, event)
    } catch {
      // Listeners are observational and must not interrupt state transitions.
    }
  }
}
