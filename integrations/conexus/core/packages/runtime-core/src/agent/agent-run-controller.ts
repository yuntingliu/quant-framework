export type AgentContinuationInput = string | { text: string }

export interface AgentRunControllerOptions {
  readonly signal?: AbortSignal
}

export interface AgentInteractionRequest<TResult = unknown> {
  readonly kind: string
  /** Canvas node that owns the interaction, including interactions from nested runs. */
  readonly ownerNodeId: string
  /**
   * The effective signal for the run that owns this interaction.
   *
   * Nested runs may share a root AgentRunController while still having a
   * narrower lifetime. Supplying that run's signal prevents its interaction
   * from outliving the run without aborting unrelated root interactions.
   */
  readonly signal?: AbortSignal
  readonly abortValue?: TResult
}

export interface AgentPendingInteractionSnapshot {
  readonly id: string
  readonly kind: string
  readonly ownerNodeId: string
}

interface PendingAgentInteraction {
  id: string
  kind: string
  ownerNodeId: string
  hasAbortValue: boolean
  abortValue: unknown
  signal?: AbortSignal
  abortListener?: () => void
  resolve: (value: unknown) => void
  reject: (reason: unknown) => void
}

/**
 * Owns the transport-independent controls for one Agent run.
 *
 * The controller intentionally does not execute a model or a tool. Hosts may
 * enqueue input and abort a run while the shared Agent loop decides when that
 * input can be inserted without corrupting the tool-call transcript.
 */
export class AgentRunController {
  private readonly abortController = new AbortController()
  private pendingContinuations: string[] = []
  private readonly interactions = new Map<string, PendingAgentInteraction>()
  readonly signal: AbortSignal

  constructor(options: AgentRunControllerOptions = {}) {
    this.signal = options.signal
      ? AbortSignal.any([options.signal, this.abortController.signal])
      : this.abortController.signal
    if (this.signal.aborted) this.handleAbort()
    else this.signal.addEventListener('abort', () => this.handleAbort(), { once: true })
  }

  get aborted(): boolean {
    return this.signal.aborted
  }

  get pendingContinuationCount(): number {
    return this.pendingContinuations.length
  }

  get pendingInteractions(): readonly AgentPendingInteractionSnapshot[] {
    return [...this.interactions.values()].map(({ id, kind, ownerNodeId }) => ({ id, kind, ownerNodeId }))
  }

  continue(input: unknown): boolean {
    if (this.signal.aborted) return false
    const text = normalizeAgentContinuation(input)
    if (!text) return false
    this.pendingContinuations.push(text)
    return true
  }

  drainContinuations(): string[] {
    if (this.signal.aborted || this.pendingContinuations.length === 0) return []
    const pending = this.pendingContinuations
    this.pendingContinuations = []
    return pending
  }

  requestInteraction<TResult>(id: string, request: AgentInteractionRequest<TResult>): Promise<TResult> {
    const interactionId = normalizeInteractionField(id)
    const kind = normalizeInteractionField(request?.kind)
    const ownerNodeId = normalizeInteractionField(request?.ownerNodeId)
    if (!interactionId || !kind || !ownerNodeId) {
      return Promise.reject(new TypeError('Agent interactions require non-empty id, kind, and ownerNodeId values.'))
    }
    if (this.interactions.has(interactionId)) {
      return Promise.reject(new Error(`Agent interaction already exists: ${interactionId}.`))
    }

    const hasAbortValue = Object.prototype.hasOwnProperty.call(request, 'abortValue')
    if (this.signal.aborted) {
      return abortedInteractionResult(request, this.signal.reason)
    }
    if (request.signal?.aborted) {
      return abortedInteractionResult(request, request.signal.reason)
    }

    return new Promise<TResult>((resolve, reject) => {
      const pending: PendingAgentInteraction = {
        id: interactionId,
        kind,
        ownerNodeId,
        hasAbortValue,
        abortValue: request.abortValue,
        signal: request.signal,
        resolve: (value) => resolve(value as TResult),
        reject
      }
      if (request.signal) {
        pending.abortListener = () => this.settleAbortedInteraction(pending, request.signal?.reason)
        request.signal.addEventListener('abort', pending.abortListener, { once: true })
      }
      this.interactions.set(interactionId, pending)
    })
  }

  resolveInteraction(id: string, value: unknown): boolean {
    const interactionId = normalizeInteractionField(id)
    if (!interactionId) return false
    const pending = this.interactions.get(interactionId)
    if (!pending) return false
    this.removeInteraction(pending)
    pending.resolve(value)
    return true
  }

  abort(reason?: unknown): boolean {
    if (this.signal.aborted) return false
    this.abortController.abort(reason)
    return true
  }

  private handleAbort(): void {
    this.pendingContinuations = []
    for (const pending of [...this.interactions.values()]) {
      this.settleAbortedInteraction(pending, this.signal.reason)
    }
  }

  private settleAbortedInteraction(pending: PendingAgentInteraction, reason: unknown): void {
    if (this.interactions.get(pending.id) !== pending) return
    this.removeInteraction(pending)
    if (pending.hasAbortValue) pending.resolve(pending.abortValue)
    else pending.reject(agentAbortError(reason))
  }

  private removeInteraction(pending: PendingAgentInteraction): void {
    this.interactions.delete(pending.id)
    if (pending.signal && pending.abortListener) {
      pending.signal.removeEventListener('abort', pending.abortListener)
    }
  }
}

export function normalizeAgentContinuation(input: unknown): string | null {
  const text = typeof input === 'string'
    ? input
    : input !== null
      && typeof input === 'object'
      && !Array.isArray(input)
      && typeof (input as { text?: unknown }).text === 'string'
      ? (input as { text: string }).text
      : ''
  const normalized = text.trim()
  return normalized || null
}

function normalizeInteractionField(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function agentAbortError(reason: unknown): Error {
  if (reason instanceof Error && reason.name === 'AbortError') return reason
  const error = new Error('Agent run was aborted.', { cause: reason })
  error.name = 'AbortError'
  return error
}

function abortedInteractionResult<TResult>(
  request: AgentInteractionRequest<TResult>,
  reason: unknown
): Promise<TResult> {
  return Object.prototype.hasOwnProperty.call(request, 'abortValue')
    ? Promise.resolve(request.abortValue as TResult)
    : Promise.reject(agentAbortError(reason))
}
