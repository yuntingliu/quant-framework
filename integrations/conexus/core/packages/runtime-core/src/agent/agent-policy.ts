import type { AgentSessionStatus } from './agent-session-engine.js'

export type AgentSessionEffectiveStatus = Exclude<AgentSessionStatus, 'max_iterations'>

export interface AgentSessionOutcomePolicy {
  status: AgentSessionEffectiveStatus
  completionGap?: 'max_iterations'
}

/** Host-neutral terminal policy: exhausting the iteration budget is blocked, not an execution error. */
export function resolveAgentSessionOutcomePolicy(status: AgentSessionStatus): AgentSessionOutcomePolicy {
  return status === 'max_iterations'
    ? { status: 'blocked', completionGap: 'max_iterations' }
    : { status }
}
