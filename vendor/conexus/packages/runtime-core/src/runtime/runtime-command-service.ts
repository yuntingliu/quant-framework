import {
  PlatformCommandError,
  createCommandReceipt,
  parseAgentUserInputResponseCommand,
  parseRuntimeAbortCommand,
  parseRuntimeContinueCommand,
  type AgentUserInputResponseCommand,
  type CommandReceipt
} from '@conexus/runtime-protocol'
import type { AgentHostRuntimeSession } from '../agent/agent-host-runtime-session.js'

export interface RuntimeCommandTarget {
  exists(id: string): boolean
  abort(id: string): boolean
  continue(id: string, input: unknown): boolean
}

export function executeRuntimeAbortCommand(
  value: unknown,
  target: RuntimeCommandTarget
): CommandReceipt {
  let command
  try {
    command = parseRuntimeAbortCommand(value)
  } catch (error) {
    const message = error instanceof PlatformCommandError ? error.message : 'Invalid runtime abort command.'
    return createCommandReceipt(false, 'invalid_request', message)
  }
  if (!target.exists(command.id)) {
    return createCommandReceipt(false, 'not_found', `Runtime job was not found: ${command.id}.`)
  }
  return target.abort(command.id)
    ? createCommandReceipt(true, 'aborted', 'Runtime abort was accepted.')
    : createCommandReceipt(false, 'control_rejected', 'This runtime job can no longer be aborted.')
}

export function executeRuntimeContinueCommand(
  value: unknown,
  target: RuntimeCommandTarget
): CommandReceipt {
  let command
  try {
    command = parseRuntimeContinueCommand(value)
  } catch (error) {
    const message = error instanceof PlatformCommandError ? error.message : 'Invalid runtime continuation command.'
    return createCommandReceipt(false, 'invalid_request', message)
  }
  if (!target.exists(command.id)) {
    return createCommandReceipt(false, 'not_found', `Runtime job was not found: ${command.id}.`)
  }
  return target.continue(command.id, command.input)
    ? createCommandReceipt(true, 'continued', 'Runtime continuation was accepted.')
    : createCommandReceipt(false, 'control_rejected', 'This runtime job cannot accept a continuation.')
}

export interface AgentUserInputCommandTarget {
  session(sessionId: string): AgentHostRuntimeSession | undefined
  accept?: (
    session: AgentHostRuntimeSession,
    command: AgentUserInputResponseCommand
  ) => Promise<boolean>
}

export async function executeAgentUserInputResponseCommand(
  value: unknown,
  target: AgentUserInputCommandTarget
): Promise<CommandReceipt> {
  let command
  try {
    command = parseAgentUserInputResponseCommand(value)
  } catch (error) {
    const message = error instanceof PlatformCommandError ? error.message : 'Invalid Agent answer.'
    return createCommandReceipt(false, 'invalid_request', message)
  }
  const session = target.session(command.sessionId)
  if (!session) {
    return createCommandReceipt(false, 'not_found', `Agent session was not found: ${command.sessionId}.`)
  }
  const accepted = target.accept
    ? await target.accept(session, command)
    : Boolean(await session.acceptInteraction(command.interactionId, command.value))
  return accepted
    ? createCommandReceipt(true, 'answered', 'The Agent user input response was accepted.')
    : createCommandReceipt(false, 'interaction_rejected', 'This Agent is not waiting for that interaction.')
}
