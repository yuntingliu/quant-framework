import type { AgentMessage, AgentToolCall } from './agent-loop.js'

const MISSING_TOOL_RESULT_CONTENT = JSON.stringify({
  success: false,
  error: 'Tool call was present in conversation history without a corresponding result; treated as aborted.'
})

interface PendingToolCalls {
  calls: AgentToolCall[]
  answeredIds: Set<string>
}

function completionSummaryFromToolResult(content: AgentMessage['content']): string | null {
  if (typeof content !== 'string') return null
  try {
    const parsed = JSON.parse(content) as { summary?: unknown }
    return typeof parsed.summary === 'string' && parsed.summary.trim()
      ? parsed.summary.trim()
      : null
  } catch {
    return null
  }
}

function normalizedToolCalls(toolCalls: AgentToolCall[] | undefined): AgentToolCall[] {
  return (toolCalls ?? []).filter((toolCall) => Boolean(
    toolCall.id.trim()
      && toolCall.type === 'function'
      && toolCall.function?.name.trim()
  ))
}

function appendMissingToolResults(result: AgentMessage[], pending: PendingToolCalls | null): void {
  if (!pending) return
  for (const toolCall of pending.calls) {
    if (pending.answeredIds.has(toolCall.id)) continue
    result.push({
      role: 'tool',
      content: MISSING_TOOL_RESULT_CONTENT,
      tool_call_id: toolCall.id,
      name: toolCall.function.name
    })
  }
}

/**
 * Produces model-safe user/assistant/tool history.
 *
 * Client-supplied system messages are intentionally discarded. Tool results are
 * kept only when they answer the immediately preceding assistant tool calls, and
 * missing results are synthesized so OpenAI-compatible providers receive a valid
 * transcript.
 */
export function repairAgentMessageHistory(messages: readonly AgentMessage[]): AgentMessage[] {
  const result: AgentMessage[] = []
  let pending: PendingToolCalls | null = null
  let historicalCompletionSummary: string | null = null

  for (const message of messages) {
    if (message.role === 'tool') {
      if (!pending || !message.tool_call_id || pending.answeredIds.has(message.tool_call_id)) continue
      const answeredCall = pending.calls.find((toolCall) => toolCall.id === message.tool_call_id)
      if (!answeredCall) continue
      result.push({
        role: 'tool',
        content: message.content ?? '',
        tool_call_id: message.tool_call_id,
        ...(message.name ? { name: message.name } : {})
      })
      pending.answeredIds.add(message.tool_call_id)
      if (answeredCall.function.name === 'complete') {
        historicalCompletionSummary = completionSummaryFromToolResult(message.content)
      }
      continue
    }

    if (message.role !== 'user' && message.role !== 'assistant') continue
    appendMissingToolResults(result, pending)
    pending = null

    // Narrow persisted-history migration: older clients synthesized the complete
    // summary as an assistant turn after the tool result. It was UI-only content
    // and must never be sent back to a model.
    if (
      historicalCompletionSummary
      && message.role === 'assistant'
      && !message.tool_calls?.length
      && typeof message.content === 'string'
      && message.content.trim() === historicalCompletionSummary
    ) {
      historicalCompletionSummary = null
      continue
    }
    historicalCompletionSummary = null

    if (message.role === 'assistant' && message.tool_calls?.length) {
      const toolCalls = normalizedToolCalls(message.tool_calls)
      if (toolCalls.length === 0) {
        if (message.content !== null) result.push({ role: 'assistant', content: message.content })
        continue
      }
      result.push({ role: 'assistant', content: message.content, tool_calls: toolCalls })
      pending = { calls: toolCalls, answeredIds: new Set() }
      continue
    }

    if (message.content !== null) result.push({ role: message.role, content: message.content })
  }

  appendMissingToolResults(result, pending)
  return result
}
