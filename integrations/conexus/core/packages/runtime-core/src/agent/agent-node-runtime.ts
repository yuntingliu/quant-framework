import type { AcceptedAgentCompletion } from './agent-completion.js'
import {
  agentContinuationText,
  executeAgentHostSession,
  type ExecuteAgentHostSessionOptions
} from './agent-host-executor.js'
import { resolveAgentSessionOutcomePolicy } from './agent-policy.js'
import type {
  AgentSessionResult,
  AgentSessionStatus
} from './agent-session-engine.js'

export { agentContinuationText }

export type AgentNodeRuntimeStatus = 'done' | 'blocked' | 'aborted'
export type ExecuteAgentNodeRuntimeOptions<TSuspension = never> =
  ExecuteAgentHostSessionOptions<TSuspension>

export interface AgentNodeRuntimeOutcome<TSuspension = never> {
  success: boolean
  status: AgentNodeRuntimeStatus
  sourceStatus: AgentSessionStatus
  summary: string | null
  completionGaps: string[]
  completion?: AcceptedAgentCompletion
  lastAssistantContent: string
  toolCounts: Record<string, number>
  session: AgentSessionResult<TSuspension>
}

export class UnsupportedAgentSessionSuspensionError extends Error {
  readonly suspension: unknown

  constructor(suspension: unknown) {
    super('Agent node execution does not support out-of-band session suspension.')
    this.name = 'UnsupportedAgentSessionSuspensionError'
    this.suspension = suspension
  }
}

/**
 * Canonical Agent-node execution boundary shared by every host.
 *
 * Hosts provide model, tool, persistence, and presentation adapters through
 * ExecuteAgentNodeRuntimeOptions. The runtime owns the loop and translates its
 * internal statuses into the public Agent-node terminal contract exactly once.
 */
export async function executeAgentNodeRuntime<TSuspension = never>(
  options: ExecuteAgentNodeRuntimeOptions<TSuspension>
): Promise<AgentNodeRuntimeOutcome<TSuspension>> {
  const session = await executeAgentHostSession(options)
  const policy = resolveAgentSessionOutcomePolicy(session.status)
  if (policy.status === 'suspended') {
    throw new UnsupportedAgentSessionSuspensionError(session.suspension)
  }

  const summary = session.completion?.summary
    ?? session.summary
    ?? (session.lastAssistantContent.trim() || null)
  const completionGaps = policy.completionGap
    ? [policy.completionGap]
    : policy.status === 'aborted'
      ? ['aborted']
      : policy.status === 'blocked'
        ? [session.completion?.blocker ?? 'blocked']
        : []

  return {
    success: policy.status === 'done',
    status: policy.status,
    sourceStatus: session.status,
    summary,
    completionGaps,
    ...(session.completion ? { completion: session.completion } : {}),
    lastAssistantContent: session.lastAssistantContent,
    toolCounts: { ...session.toolCounts },
    session
  }
}
