const CLEARED_AGENT_HISTORY_FIELDS = [
  'objective',
  'summary',
  'completionGaps',
  'toolCounts',
  'lastError',
  'pendingAsk',
  'activeAgentSessionId',
  'activeAgentJobId',
  '_connectedNodeRevisions'
] as const

export function agentHistoryClearPatch(): Record<string, unknown> {
  return {
    messages: [],
    ...Object.fromEntries(CLEARED_AGENT_HISTORY_FIELDS.map((field) => [field, undefined])),
    cliSessionId: null,
    status: 'idle'
  }
}

export function clearAgentHistoryData(data: Record<string, unknown> | undefined): Record<string, unknown> {
  const next = { ...(data ?? {}) }
  for (const [key, value] of Object.entries(agentHistoryClearPatch())) {
    if (value === undefined) delete next[key]
    else next[key] = value
  }
  return next
}
