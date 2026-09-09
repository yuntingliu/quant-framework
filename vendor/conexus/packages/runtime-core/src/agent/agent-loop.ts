import { parseToolArguments } from '../tools/tool-arguments.js'
import type { AgentRunController } from './agent-run-controller.js'

export type AgentToolCall = {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string
  }
}

export type AgentMessageContent =
  | string
  | Array<
      | { type: 'text'; text: string }
      | { type: 'image_url'; image_url: { url: string; detail?: 'auto' | 'low' | 'high' } }
    >
  | null

export type AgentMessage = {
  role: string
  content: AgentMessageContent
  tool_calls?: AgentToolCall[]
  tool_call_id?: string
  name?: string
}

export type AgentLLMResult = {
  content: string | null
  tool_calls?: AgentToolCall[]
  finish_reason: string
}

export type AgentAssistantTurn = {
  content: string | null
  toolCalls: AgentToolCall[]
}

export type LoopDecision<TResult> =
  | { kind: 'continue' }
  | { kind: 'return'; value: TResult }

export interface AgentLoopOptions<TResult> {
  messages: AgentMessage[]
  /** Optional diagnostic/test guard. Production hosts omit it for an unbounded session. */
  maxIterations?: number
  isAborted?: () => boolean
  controller?: AgentRunController
  continuationMessage?: (continuation: string) => AgentMessage
  onContinuations?: (continuations: readonly string[]) => void | Promise<void>
  beforeIteration?: (iter: number) => void | Promise<void>
  callLLM: (messages: AgentMessage[]) => Promise<AgentLLMResult>
  onAssistantTurn?: (turn: AgentAssistantTurn) => void | Promise<void>
  onNoToolCalls: (result: AgentLLMResult) => LoopDecision<TResult> | Promise<LoopDecision<TResult>>
  onToolCall?: (toolCall: AgentToolCall, args: Record<string, unknown>) => void | Promise<void>
  executeTool: (toolCall: AgentToolCall, args: Record<string, unknown>) => Promise<string>
  onToolResult?: (toolCall: AgentToolCall, result: string) => void | Promise<void>
  afterTool?: (
    toolCall: AgentToolCall,
    result: string,
    args: Record<string, unknown>
  ) => void | LoopDecision<TResult> | Promise<void | LoopDecision<TResult>>
  onAbort: () => TResult | Promise<TResult>
  onLLMError: (err: unknown) => TResult | Promise<TResult>
  onMaxIterations: () => TResult | Promise<TResult>
}

const SKIPPED_TOOL_REASON = 'New user input arrived before this tool call started. Reconsider it on the next turn.'
const ABORTED_TOOL_REASON = 'The Agent run was aborted before this tool call started.'
const COMPLETED_TOOL_REASON = 'The Agent run completed before this tool call started.'

function isAgentLoopAborted<TResult>(options: AgentLoopOptions<TResult>): boolean {
  return options.controller?.signal.aborted === true || options.isAborted?.() === true
}

function takeContinuations<TResult>(options: AgentLoopOptions<TResult>): string[] {
  return options.controller?.drainContinuations() ?? []
}

async function appendContinuations<TResult>(
  options: AgentLoopOptions<TResult>,
  continuations: readonly string[]
): Promise<void> {
  if (continuations.length === 0) return
  const toMessage = options.continuationMessage ?? ((continuation: string): AgentMessage => ({
    role: 'user',
    content: continuation
  }))
  for (const continuation of continuations) options.messages.push(toMessage(continuation))
  await options.onContinuations?.(continuations)
}

async function appendUnexecutedToolResults<TResult>(
  options: AgentLoopOptions<TResult>,
  toolCalls: readonly AgentToolCall[],
  reason: string
): Promise<void> {
  for (const toolCall of toolCalls) {
    const skippedResult = JSON.stringify({
      success: false,
      skipped: true,
      reason
    })
    options.messages.push({
      role: 'tool',
      content: skippedResult,
      tool_call_id: toolCall.id,
      name: toolCall.function.name
    })
    await options.onToolResult?.(toolCall, skippedResult)
  }
}

async function abortWithClosedToolTranscript<TResult>(
  options: AgentLoopOptions<TResult>,
  unexecutedToolCalls: readonly AgentToolCall[]
): Promise<TResult> {
  await appendUnexecutedToolResults(options, unexecutedToolCalls, ABORTED_TOOL_REASON)
  return options.onAbort()
}

export async function runAgentLoop<TResult>(options: AgentLoopOptions<TResult>): Promise<TResult> {
  for (let iter = 0; options.maxIterations === undefined || iter < options.maxIterations; iter++) {
    if (isAgentLoopAborted(options)) return options.onAbort()
    await appendContinuations(options, takeContinuations(options))
    await options.beforeIteration?.(iter)
    if (isAgentLoopAborted(options)) return options.onAbort()
    await appendContinuations(options, takeContinuations(options))
    if (isAgentLoopAborted(options)) return options.onAbort()

    let result: AgentLLMResult
    try {
      result = await options.callLLM(options.messages)
    } catch (err) {
      if (isAgentLoopAborted(options)) return options.onAbort()
      return options.onLLMError(err)
    }

    if (isAgentLoopAborted(options)) return options.onAbort()
    const toolCalls = result.tool_calls ?? []
    if (result.content || toolCalls.length > 0) {
      await options.onAssistantTurn?.({ content: result.content, toolCalls })
    }
    if (isAgentLoopAborted(options)) return options.onAbort()

    if (toolCalls.length === 0) {
      if (result.content) {
        options.messages.push({ role: 'assistant', content: result.content })
      }
      if (isAgentLoopAborted(options)) return options.onAbort()
      const continuations = takeContinuations(options)
      if (continuations.length > 0) {
        await appendContinuations(options, continuations)
        continue
      }
      const decision = await options.onNoToolCalls(result)
      if (isAgentLoopAborted(options)) return options.onAbort()
      const lateContinuations = takeContinuations(options)
      if (lateContinuations.length > 0) {
        await appendContinuations(options, lateContinuations)
        continue
      }
      if (decision.kind === 'return') return decision.value
      continue
    }

    options.messages.push({
      role: 'assistant',
      content: result.content,
      tool_calls: toolCalls
    })

    const responseContinuations = takeContinuations(options)
    if (responseContinuations.length > 0) {
      await appendUnexecutedToolResults(options, toolCalls, SKIPPED_TOOL_REASON)
      await appendContinuations(options, responseContinuations)
      continue
    }
    if (isAgentLoopAborted(options)) {
      return abortWithClosedToolTranscript(options, toolCalls)
    }

    let continueAfterTool = false
    for (let toolIndex = 0; toolIndex < toolCalls.length; toolIndex++) {
      const toolCall = toolCalls[toolIndex]
      if (!toolCall) continue
      if (isAgentLoopAborted(options)) {
        return abortWithClosedToolTranscript(options, toolCalls.slice(toolIndex))
      }
      const parsedArguments = parseToolArguments(toolCall.function.arguments)
      const args = parsedArguments.args
      const argumentParseError = parsedArguments.recovered ? null : parsedArguments.error ?? null

      await options.onToolCall?.(toolCall, args)
      const preExecutionContinuations = takeContinuations(options)
      if (preExecutionContinuations.length > 0) {
        await appendUnexecutedToolResults(
          options,
          toolCalls.slice(toolIndex),
          SKIPPED_TOOL_REASON
        )
        await appendContinuations(options, preExecutionContinuations)
        continueAfterTool = true
        break
      }
      if (isAgentLoopAborted(options)) {
        return abortWithClosedToolTranscript(options, toolCalls.slice(toolIndex))
      }
      const toolResult = argumentParseError
        ? JSON.stringify({
            success: false,
            error: 'Invalid JSON tool arguments.',
            details: argumentParseError
          })
        : await options.executeTool(toolCall, args)

      options.messages.push({
        role: 'tool',
        content: toolResult,
        tool_call_id: toolCall.id,
        name: toolCall.function.name
      })

      await options.onToolResult?.(toolCall, toolResult)
      if (isAgentLoopAborted(options)) {
        return abortWithClosedToolTranscript(options, toolCalls.slice(toolIndex + 1))
      }
      const continuations = takeContinuations(options)
      if (continuations.length > 0) {
        await appendUnexecutedToolResults(options, toolCalls.slice(toolIndex + 1), SKIPPED_TOOL_REASON)
        await appendContinuations(options, continuations)
        continueAfterTool = true
        break
      }
      const decision = await options.afterTool?.(toolCall, toolResult, args)
      if (isAgentLoopAborted(options)) {
        return abortWithClosedToolTranscript(options, toolCalls.slice(toolIndex + 1))
      }
      const lateContinuations = takeContinuations(options)
      if (lateContinuations.length > 0) {
        await appendUnexecutedToolResults(options, toolCalls.slice(toolIndex + 1), SKIPPED_TOOL_REASON)
        await appendContinuations(options, lateContinuations)
        continueAfterTool = true
        break
      }
      if (decision?.kind === 'return') {
        await appendUnexecutedToolResults(
          options,
          toolCalls.slice(toolIndex + 1),
          COMPLETED_TOOL_REASON
        )
        return decision.value
      }
      if (decision?.kind === 'continue') {
        await appendUnexecutedToolResults(options, toolCalls.slice(toolIndex + 1), SKIPPED_TOOL_REASON)
        continueAfterTool = true
        break
      }
    }
    if (continueAfterTool) continue
  }

  return options.onMaxIterations()
}
