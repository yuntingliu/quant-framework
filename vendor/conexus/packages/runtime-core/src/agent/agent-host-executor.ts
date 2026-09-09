import type { RuntimeJobEventName } from '@conexus/runtime-protocol'
import type {
  AgentAssistantTurn,
  AgentLLMResult,
  AgentMessage,
  AgentToolCall
} from './agent-loop.js'
import type { AgentRunController } from './agent-run-controller.js'
import {
  runAgentSession,
  type AgentSessionResult,
  type AgentSessionToolDecision
} from './agent-session-engine.js'
import type { AgentHostRuntimeSession } from './agent-host-runtime-session.js'

type MaybePromise<T> = T | Promise<T>

export interface AgentHostExecutionJournal {
  session: AgentHostRuntimeSession
  jobId: string
  nodeId: string
}

export interface AgentHostExecutionHooks<TSuspension> {
  onContinuations?: (continuations: readonly string[]) => MaybePromise<void>
  beforeIteration?: (iteration: number) => MaybePromise<void>
  onAssistantTurn?: (turn: AgentAssistantTurn) => MaybePromise<void>
  onToolCall?: (toolCall: AgentToolCall, args: Record<string, unknown>) => MaybePromise<void>
  onToolResult?: (toolCall: AgentToolCall, result: string) => MaybePromise<void>
  decideAfterTool?: (
    toolCall: AgentToolCall,
    result: string
  ) => MaybePromise<AgentSessionToolDecision<TSuspension>>
}

export interface ExecuteAgentHostSessionOptions<TSuspension = never>
  extends AgentHostExecutionHooks<TSuspension> {
  messages: AgentMessage[]
  /** Optional diagnostic/test guard. Omit it to run until completion, cancellation, or timeout. */
  maxIterations?: number
  controller: AgentRunController
  signal?: AbortSignal
  callModel: (messages: AgentMessage[]) => Promise<AgentLLMResult>
  dispatchTool: (toolCall: AgentToolCall, args: Record<string, unknown>) => Promise<string>
  journal?: AgentHostExecutionJournal
  journalToolResult?: (toolCall: AgentToolCall, result: string) => string
  onModelError?: (error: unknown) => never
}

export function agentContinuationText(continuation: string): string {
  return `Additional user input for the current objective:\n${continuation.trim()}`
}

function record(
  journal: AgentHostExecutionJournal | undefined,
  event: RuntimeJobEventName,
  payload: Record<string, unknown>
): void {
  journal?.session.recordEvent(journal.jobId, event, {
    nodeId: journal.nodeId,
    ...payload
  })
}

/**
 * Canonical host-facing Agent executor. Hosts supply model, tool, persistence,
 * and optional presentation hooks; transcript journaling and continuation
 * semantics are owned here once for Desktop, Web, and Hosted execution.
 */
export function executeAgentHostSession<TSuspension = never>(
  options: ExecuteAgentHostSessionOptions<TSuspension>
): Promise<AgentSessionResult<TSuspension>> {
  return runAgentSession({
    messages: options.messages,
    ...(options.maxIterations !== undefined ? { maxIterations: options.maxIterations } : {}),
    controller: options.controller,
    ...(options.signal ? { signal: options.signal } : {}),
    continuationMessage: (continuation) => ({
      role: 'user',
      content: agentContinuationText(continuation)
    }),
    onContinuations: async (continuations) => {
      for (const continuation of continuations) {
        record(options.journal, 'message', { role: 'user', content: continuation })
      }
      await options.onContinuations?.(continuations)
    },
    ...(options.beforeIteration ? { beforeIteration: options.beforeIteration } : {}),
    callModel: options.callModel,
    onAssistantTurn: async (turn) => {
      record(options.journal, 'message', {
        role: 'assistant',
        content: turn.content ?? '',
        ...(turn.toolCalls.length > 0 ? { toolCalls: turn.toolCalls } : {})
      })
      await options.onAssistantTurn?.(turn)
    },
    onToolCall: async (toolCall, args) => {
      record(options.journal, 'tool_call', {
        callId: toolCall.id,
        tool: toolCall.function.name,
        args
      })
      await options.onToolCall?.(toolCall, args)
    },
    dispatchTool: options.dispatchTool,
    onToolResult: async (toolCall, result) => {
      record(options.journal, 'tool_result', {
        callId: toolCall.id,
        tool: toolCall.function.name,
        result: options.journalToolResult?.(toolCall, result) ?? result
      })
      await options.onToolResult?.(toolCall, result)
    },
    ...(options.decideAfterTool ? { decideAfterTool: options.decideAfterTool } : {}),
    ...(options.onModelError ? { onModelError: options.onModelError } : {})
  })
}
