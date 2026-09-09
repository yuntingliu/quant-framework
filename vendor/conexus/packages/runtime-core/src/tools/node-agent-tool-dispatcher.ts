import { executeAgentCompletionTool } from '../agent/agent-completion.js'
import type { AgentRunController } from '../agent/agent-run-controller.js'
import type { NodeAgentToolName } from './node-agent-tools.js'

export interface FindNodesRequest {
  scope: 'current' | 'root' | 'harness'
  harnessNodeId?: string
  query?: string
  types: string[]
  capabilities: string[]
  relationTo?: string
  statuses: string[]
  cursor?: string
  limit: number
}

export interface ObserveNodeRequest {
  nodeId: string
  detail: 'values' | 'full'
  fields?: string[]
  includeContracts: boolean
  startLine?: number
  endLine?: number
}

export interface ObserveNodesRequest {
  requests: ObserveNodeRequest[]
}

export interface RequestUserInputRequest {
  prompt: string
  choices: string[]
  responseSchema?: Record<string, unknown>
  uiSchema?: Record<string, unknown>
}

export interface UseNodeRequest {
  nodeId: string
  capability: string
  input: Record<string, unknown>
}

export type NodeAgentToolHandlerResult = string | Record<string, unknown>

export interface NodeAgentToolHandlers<TContext = undefined> {
  findNodes: (request: FindNodesRequest, context: TContext) => NodeAgentToolHandlerResult | Promise<NodeAgentToolHandlerResult>
  observeNodes: (request: ObserveNodesRequest, context: TContext) => NodeAgentToolHandlerResult | Promise<NodeAgentToolHandlerResult>
  createNodes: (args: Record<string, unknown>, context: TContext) => NodeAgentToolHandlerResult | Promise<NodeAgentToolHandlerResult>
  editNodes: (args: Record<string, unknown>, context: TContext) => NodeAgentToolHandlerResult | Promise<NodeAgentToolHandlerResult>
  useNode: (request: UseNodeRequest, context: TContext) => NodeAgentToolHandlerResult | Promise<NodeAgentToolHandlerResult>
  requestUserInput: (request: RequestUserInputRequest, context: TContext) => NodeAgentToolHandlerResult | Promise<NodeAgentToolHandlerResult>
}

export type NodeAgentToolDispatchResult =
  | { handled: true; name: NodeAgentToolName; result: string }
  | { handled: false }

export interface DispatchNodeAgentToolOptions<TContext = undefined> {
  name: string
  args: Record<string, unknown>
  context: TContext
  handlers: NodeAgentToolHandlers<TContext>
}

function jsonResult(value: unknown): string {
  return JSON.stringify(value)
}

function normalizedResult(value: NodeAgentToolHandlerResult): string {
  return typeof value === 'string' ? value : jsonResult(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function stringArray(value: unknown, field: string, maximum: number): string[] | string {
  if (value === undefined) return []
  if (!Array.isArray(value)) return `${field} must be an array of strings.`
  if (value.length > maximum) return `${field} accepts at most ${maximum} values.`
  const output: string[] = []
  for (const item of value) {
    const text = textValue(item)
    if (!text) return `${field} must contain non-empty strings.`
    if (!output.includes(text)) output.push(text)
  }
  return output
}

function findNodesRequest(args: Record<string, unknown>): FindNodesRequest | string {
  const scope = args.scope ?? 'current'
  if (scope !== 'current' && scope !== 'root' && scope !== 'harness') {
    return 'find scope must be current, root, or harness.'
  }
  const harnessNodeId = textValue(args.harness_node_id)
  if (scope === 'harness' && !harnessNodeId) return 'find requires harness_node_id when scope=harness.'
  const types = stringArray(args.types, 'find types', 20)
  if (typeof types === 'string') return types
  const capabilities = stringArray(args.capabilities, 'find capabilities', 20)
  if (typeof capabilities === 'string') return capabilities
  const statuses = stringArray(args.statuses, 'find statuses', 20)
  if (typeof statuses === 'string') return statuses
  const limit = args.limit ?? 50
  if (!Number.isInteger(limit) || (limit as number) < 1 || (limit as number) > 100) {
    return 'find limit must be an integer from 1 through 100.'
  }
  return {
    scope,
    ...(harnessNodeId ? { harnessNodeId } : {}),
    ...(textValue(args.query) ? { query: textValue(args.query) } : {}),
    types,
    capabilities,
    ...(textValue(args.relation_to) ? { relationTo: textValue(args.relation_to) } : {}),
    statuses,
    ...(textValue(args.cursor) ? { cursor: textValue(args.cursor) } : {}),
    limit: limit as number
  }
}

function observeNodesRequest(args: Record<string, unknown>): ObserveNodesRequest | string {
  if (!Array.isArray(args.requests) || args.requests.length < 1 || args.requests.length > 20) {
    return 'observe requests must contain 1 through 20 items.'
  }
  const requests: ObserveNodeRequest[] = []
  for (let index = 0; index < args.requests.length; index += 1) {
    const item = recordValue(args.requests[index])
    const nodeId = textValue(item?.node_id)
    if (!item || !nodeId) return `observe requests[${index}].node_id is required.`
    const detail = item.detail ?? 'values'
    if (detail !== 'values' && detail !== 'full') {
      return `observe requests[${index}].detail must be values or full.`
    }
    const fields = stringArray(item.fields, `observe requests[${index}].fields`, 64)
    if (typeof fields === 'string') return fields
    const startLine = item.start_line
    const endLine = item.end_line
    if (startLine !== undefined && (!Number.isInteger(startLine) || (startLine as number) < 1)) {
      return `observe requests[${index}].start_line must be a positive integer.`
    }
    if (endLine !== undefined && (!Number.isInteger(endLine) || (endLine as number) < 1)) {
      return `observe requests[${index}].end_line must be a positive integer.`
    }
    requests.push({
      nodeId,
      detail,
      ...(fields.length > 0 ? { fields } : {}),
      includeContracts: item.include_contracts === true,
      ...(startLine !== undefined ? { startLine: startLine as number } : {}),
      ...(endLine !== undefined ? { endLine: endLine as number } : {})
    })
  }
  return { requests }
}

function requestUserInputRequest(args: Record<string, unknown>): RequestUserInputRequest | string {
  const prompt = textValue(args.prompt)
  if (!prompt) return 'request_user_input requires prompt.'
  const choices = stringArray(args.choices, 'request_user_input choices', 12)
  if (typeof choices === 'string') return choices
  const responseSchema = args.response_schema === undefined ? undefined : recordValue(args.response_schema)
  if (args.response_schema !== undefined && !responseSchema) {
    return 'request_user_input response_schema must be a JSON Schema object.'
  }
  if (responseSchema && responseSchema.type !== 'object') {
    return 'request_user_input response_schema must have type=object.'
  }
  if (responseSchema && choices.length > 0) {
    return 'request_user_input accepts either choices or response_schema, not both.'
  }
  const uiSchema = args.ui_schema === undefined ? undefined : recordValue(args.ui_schema)
  if (args.ui_schema !== undefined && !uiSchema) return 'request_user_input ui_schema must be an object.'
  if (uiSchema && !responseSchema) return 'request_user_input ui_schema requires response_schema.'
  return {
    prompt,
    choices,
    ...(responseSchema ? { responseSchema } : {}),
    ...(uiSchema ? { uiSchema } : {})
  }
}

function useNodeRequest(args: Record<string, unknown>): UseNodeRequest | string {
  const nodeId = textValue(args.node_id)
  const capability = textValue(args.capability)
  const input = recordValue(args.input)
  if (!nodeId) return 'use requires node_id.'
  if (!capability) return 'use requires capability.'
  if (!input) return 'use input must be an object.'
  return { nodeId, capability, input }
}

function arrayArgument(
  args: Record<string, unknown>,
  field: 'nodes' | 'operations',
  maximum: number
): string | undefined {
  const value = args[field]
  if (!Array.isArray(value) || value.length < 1 || value.length > maximum) {
    return `${field} must contain 1 through ${maximum} items.`
  }
  if (value.some((item) => !recordValue(item))) return `${field} must contain objects.`
  return undefined
}

function editArgument(args: Record<string, unknown>): string | undefined {
  const error = arrayArgument(args, 'operations', 100)
  if (error) return error
  for (let index = 0; index < (args.operations as unknown[]).length; index += 1) {
    const operation = recordValue((args.operations as unknown[])[index])!
    if (operation.kind === 'patch') {
      const set = recordValue(operation.set)
      if (!set || Object.keys(set).length === 0) {
        return `operations[${index}] patch requires a non-empty set object.`
      }
    }
  }
  return undefined
}

/** Closed dispatcher for the seven model-visible node Agent operations. */
export async function dispatchNodeAgentTool<TContext = undefined>(
  options: DispatchNodeAgentToolOptions<TContext>
): Promise<NodeAgentToolDispatchResult> {
  const { name, args, context, handlers } = options
  if (name === 'complete') return { handled: true, name, result: executeAgentCompletionTool(args) }
  if (name === 'find') {
    const request = findNodesRequest(args)
    if (typeof request === 'string') return { handled: true, name, result: jsonResult({ success: false, error: request }) }
    return { handled: true, name, result: normalizedResult(await handlers.findNodes(request, context)) }
  }
  if (name === 'observe') {
    const request = observeNodesRequest(args)
    if (typeof request === 'string') return { handled: true, name, result: jsonResult({ success: false, error: request }) }
    return { handled: true, name, result: normalizedResult(await handlers.observeNodes(request, context)) }
  }
  if (name === 'create') {
    const error = arrayArgument(args, 'nodes', 50)
    if (error) return { handled: true, name, result: jsonResult({ success: false, error }) }
    return { handled: true, name, result: normalizedResult(await handlers.createNodes(args, context)) }
  }
  if (name === 'edit') {
    const error = editArgument(args)
    if (error) return { handled: true, name, result: jsonResult({ success: false, error }) }
    return { handled: true, name, result: normalizedResult(await handlers.editNodes(args, context)) }
  }
  if (name === 'use') {
    const request = useNodeRequest(args)
    if (typeof request === 'string') return { handled: true, name, result: jsonResult({ success: false, error: request }) }
    return { handled: true, name, result: normalizedResult(await handlers.useNode(request, context)) }
  }
  if (name === 'request_user_input') {
    const request = requestUserInputRequest(args)
    if (typeof request === 'string') return { handled: true, name, result: jsonResult({ success: false, error: request }) }
    return { handled: true, name, result: normalizedResult(await handlers.requestUserInput(request, context)) }
  }
  return { handled: false }
}

export interface RequestAgentUserInputOptions extends RequestUserInputRequest {
  controller?: AgentRunController
  ownerNodeId: string
  interactionId: string
  signal?: AbortSignal
  emit: (request: {
    interactionId: string
    ownerNodeId: string
    kind: 'question' | 'form'
    prompt: string
    choices: string[]
    responseSchema?: Record<string, unknown>
    uiSchema?: Record<string, unknown>
  }) => void | Promise<void>
  noControllerError?: string
  abortedError?: string
}

/** Registers before emission so immediate user input cannot race the suspended Agent. */
export async function requestAgentUserInput(options: RequestAgentUserInputOptions): Promise<string> {
  const controller = options.controller
  if (!controller) {
    return jsonResult({
      success: false,
      error: options.noControllerError ?? 'This Agent execution has no interactive controller.'
    })
  }
  const ownerNodeId = textValue(options.ownerNodeId)
  const interactionId = textValue(options.interactionId)
  if (!ownerNodeId || !interactionId) {
    return jsonResult({ success: false, error: 'Agent interaction requires ownerNodeId and interactionId.' })
  }
  if (controller.pendingInteractions.some((interaction) => interaction.ownerNodeId === ownerNodeId)) {
    return jsonResult({ success: false, error: 'This Agent already has a pending user input request.' })
  }
  const abortValue = jsonResult({
    success: false,
    error: options.abortedError ?? 'Agent run was aborted while waiting for user input.'
  })
  const answer = controller.requestInteraction<unknown>(interactionId, {
    kind: options.responseSchema ? 'form' : 'question',
    ownerNodeId,
    ...(options.signal ? { signal: options.signal } : {}),
    abortValue
  })
  try {
    await options.emit({
      interactionId,
      ownerNodeId,
      kind: options.responseSchema ? 'form' : 'question',
      prompt: options.prompt,
      choices: options.choices,
      ...(options.responseSchema ? { responseSchema: options.responseSchema } : {}),
      ...(options.uiSchema ? { uiSchema: options.uiSchema } : {})
    })
  } catch (error) {
    controller.resolveInteraction(interactionId, abortValue)
    throw error
  }
  const settled = await answer
  return settled === abortValue
    ? settled
    : jsonResult({ success: true, value: settled })
}
