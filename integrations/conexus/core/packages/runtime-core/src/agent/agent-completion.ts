import type { RuntimeToolSchema } from '../contracts.js'

export type AgentCompletionStatus = 'done' | 'blocked'

export interface AcceptedAgentCompletion {
  status: AgentCompletionStatus
  summary: string
  blocker?: string
}

export const AGENT_COMPLETION_TOOL: RuntimeToolSchema = {
  type: 'function',
  function: {
    name: 'complete',
    description: 'End this Agent run as done or blocked. summary is the final user-facing result; blocked also requires a concrete blocker.',
    parameters: {
      type: 'object',
      properties: {
        status: { type: 'string', enum: ['done', 'blocked'] },
        summary: { type: 'string', minLength: 1, description: 'Final user-facing answer. State the actual result or conclusion, not a progress report.' },
        blocker: { type: 'string', minLength: 1, description: 'Required only when status=blocked. State the concrete blocker and needed input or external change; omit it when status=done.' }
      },
      required: ['status', 'summary'],
      additionalProperties: false
    }
  }
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

type AgentCompletionValidation =
  | { ok: true; completion: AcceptedAgentCompletion }
  | { ok: false; error: string }

function validateAgentCompletion(args: Record<string, unknown>): AgentCompletionValidation {
  const unsupported = Object.keys(args).filter((key) => key !== 'status' && key !== 'summary' && key !== 'blocker')
  if (unsupported.length > 0) {
    return { ok: false, error: `complete received unsupported fields: ${unsupported.join(', ')}.` }
  }
  const status = nonEmptyString(args.status)
  const summary = nonEmptyString(args.summary)
  const blocker = nonEmptyString(args.blocker)
  if (status !== 'done' && status !== 'blocked') {
    return {
      ok: false,
      error: 'complete.status must be done or blocked. Continue the task and call complete again with a valid status.'
    }
  }
  if (!summary) {
    return { ok: false, error: 'complete.summary is required and must contain the final user-facing answer.' }
  }
  if (status === 'done' && blocker) {
    return { ok: false, error: 'complete.blocker is only valid for status=blocked.' }
  }
  if (status === 'blocked' && !blocker) {
    return {
      ok: false,
      error: 'complete.blocker is required for status=blocked. State the concrete blocker and what is needed to proceed.'
    }
  }
  return {
    ok: true,
    completion: { status, summary, ...(blocker ? { blocker } : {}) }
  }
}

export function executeAgentCompletionTool(args: Record<string, unknown>): string {
  const validated = validateAgentCompletion(args)
  if (!validated.ok) return JSON.stringify({ success: false, error: validated.error })
  return JSON.stringify({
    success: true,
    status: validated.completion.status
  })
}

export function parseAcceptedAgentCompletion(
  args: Record<string, unknown>,
  result: string
): AcceptedAgentCompletion | null {
  const validated = validateAgentCompletion(args)
  if (!validated.ok) return null
  try {
    const parsed = JSON.parse(result) as Record<string, unknown>
    if (Object.keys(parsed).some((key) => key !== 'success' && key !== 'status')) return null
    if (parsed.success !== true || (parsed.status !== 'done' && parsed.status !== 'blocked')) return null
    if (parsed.status !== validated.completion.status) return null
    return validated.completion
  } catch {
    return null
  }
}
