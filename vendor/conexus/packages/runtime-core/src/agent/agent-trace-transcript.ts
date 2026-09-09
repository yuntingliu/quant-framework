import type { AgentMessage } from './agent-loop.js'

// Narrow persisted-history migration for transcripts written before reminder injection was removed.
const HISTORICAL_COMPLETION_REQUIRED_MESSAGE =
  'Your response has not terminated the Agent run. Use the complete tool with status and a final user-facing summary; status=blocked also requires blocker.'
const HISTORICAL_EMPTY_RESPONSE_CONTINUATION_MESSAGE =
  'Your previous response was empty. Continue the current task from the existing transcript, use the available tools as needed, and call the complete tool only after the task is actually finished.'

function isInternalContinuation(message: AgentMessage): boolean {
  return message.role === 'user'
    && typeof message.content === 'string'
    && (
      message.content === HISTORICAL_COMPLETION_REQUIRED_MESSAGE
      || message.content === HISTORICAL_EMPTY_RESPONSE_CONTINUATION_MESSAGE
    )
}

/**
 * Canonical persisted observability transcript for an Agent request.
 *
 * Provider prompts still retain exact runtime continuations in provider-attempt
 * payloads. The request transcript preserves the actual system, user, assistant,
 * and tool turns without synthesizing display-only messages.
 */
export function createAgentTraceTranscript(
  messages: readonly AgentMessage[]
): AgentMessage[] {
  return messages
    .filter((message) => !isInternalContinuation(message))
    .map((message) => structuredClone(message))
}
