import type { AgentLLMResult, AgentToolCall } from '../agent/agent-loop.js'

export type OpenAiCompletionParseErrorCode =
  | 'invalid_payload'
  | 'missing_choices'
  | 'missing_message'
  | 'invalid_content'
  | 'invalid_tool_calls'
  | 'malformed_tool_call'

export class OpenAiCompletionParseError extends Error {
  constructor(
    message: string,
    readonly code: OpenAiCompletionParseErrorCode
  ) {
    super(message)
    this.name = 'OpenAiCompletionParseError'
  }
}

/** Parses the strict non-streaming response shared by OpenAI-compatible model providers. */
export function parseOpenAiCompletion(payload: unknown): AgentLLMResult {
  if (!payload || typeof payload !== 'object') {
    throw new OpenAiCompletionParseError('Model provider returned an invalid response.', 'invalid_payload')
  }

  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || !choices[0] || typeof choices[0] !== 'object') {
    throw new OpenAiCompletionParseError('Model provider response is missing choices.', 'missing_choices')
  }

  const choice = choices[0] as { message?: unknown; finish_reason?: unknown }
  const message = choice.message
  if (!message || typeof message !== 'object') {
    throw new OpenAiCompletionParseError(
      'Model provider response is missing the first message.',
      'missing_message'
    )
  }

  const content = (message as { content?: unknown }).content
  if (content !== null && typeof content !== 'string' && content !== undefined) {
    throw new OpenAiCompletionParseError(
      'Model provider response has invalid message content.',
      'invalid_content'
    )
  }

  const rawToolCalls = (message as { tool_calls?: unknown }).tool_calls
  if (rawToolCalls !== undefined && !Array.isArray(rawToolCalls)) {
    throw new OpenAiCompletionParseError(
      'Model provider response has invalid tool calls.',
      'invalid_tool_calls'
    )
  }

  const toolCalls = Array.isArray(rawToolCalls)
    ? rawToolCalls.flatMap<AgentToolCall>((toolCall) => {
        if (!toolCall || typeof toolCall !== 'object') return []
        const value = toolCall as { id?: unknown; type?: unknown; function?: unknown }
        if (value.type !== undefined && value.type !== 'function') return []
        if (typeof value.id !== 'string' || !value.function || typeof value.function !== 'object') return []
        const fn = value.function as { name?: unknown; arguments?: unknown }
        if (typeof fn.name !== 'string' || typeof fn.arguments !== 'string') return []
        return [{ id: value.id, type: 'function', function: { name: fn.name, arguments: fn.arguments } }]
      })
    : undefined

  if (Array.isArray(rawToolCalls) && toolCalls?.length !== rawToolCalls.length) {
    throw new OpenAiCompletionParseError(
      'Model provider response has malformed tool calls.',
      'malformed_tool_call'
    )
  }

  return {
    content: typeof content === 'string' ? content : null,
    ...(toolCalls?.length ? { tool_calls: toolCalls } : {}),
    finish_reason: typeof choice.finish_reason === 'string' ? choice.finish_reason : 'stop'
  }
}
