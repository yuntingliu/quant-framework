import {
  NODE_AGENT_TOOL_NAMES,
  type NodeAgentToolName
} from '../tools/node-agent-tools.js'
import { migrateLegacyAutomaticAgentToolNames } from './agent-tool-selection.js'

export type AgentRuntimeConfigurationErrorCode =
  | 'invalid_agent_runtime_config'
  | 'model_not_allowed'
  | 'unavailable_agent_tool'
  | 'agent_timeout_exceeded'

export const DEFAULT_AGENT_RUN_TIMEOUT_MS = 10 * 60_000
export const MIN_AGENT_RUN_TIMEOUT_MS = 1
/** Deliberately below Node's 2^31 - 1 millisecond timer ceiling. */
export const MAX_AGENT_RUN_TIMEOUT_MS = 24 * 60 * 60_000

export class AgentRuntimeConfigurationError extends Error {
  readonly code: AgentRuntimeConfigurationErrorCode
  readonly details?: Record<string, unknown>

  constructor(
    message: string,
    code: AgentRuntimeConfigurationErrorCode,
    details?: Record<string, unknown>
  ) {
    super(message)
    this.name = 'AgentRuntimeConfigurationError'
    this.code = code
    this.details = details
  }
}

export interface ResolveAgentRuntimeConfigurationOptions<TToolName extends string = string> {
  requestedModel?: unknown
  requestedToolNames?: unknown
  requestedMaxTokens?: unknown
  defaultModel: string
  defaultToolNames: readonly TToolName[]
  availableToolNames: ReadonlySet<string>
  requiredToolNames?: readonly TToolName[]
  allowedModels?: ReadonlySet<string>
  allowUnconfiguredDefaultModel?: boolean
  hostLabel?: string
}

export interface AgentRuntimeConfiguration<TToolName extends string = string> {
  model: string
  toolNames: TToolName[]
  maxTokens?: number
  /** Normalized user-authored selection; omitted when host defaults were used. */
  explicitToolNames?: TToolName[]
}

export interface ResolveNodeAgentRuntimeConfigurationOptions {
  requestedModel?: unknown
  requestedToolNames?: unknown
  requestedMaxTokens?: unknown
  defaultModel: string
  allowedModels: ReadonlySet<string>
  hostLabel?: string
}

export type ResolveAgentNodeRuntimeConfigurationOptions<TToolName extends string = string> = Omit<
  ResolveAgentRuntimeConfigurationOptions<TToolName>,
  'requestedModel' | 'requestedToolNames' | 'requestedMaxTokens'
> & {
  /** Known host-injected names accepted only when recognizing a materialized automatic selection. */
  legacyAutomaticToolExtensionNames?: ReadonlySet<string>
}

export type ResolveNodeAgentNodeRuntimeConfigurationOptions = Omit<
  ResolveNodeAgentRuntimeConfigurationOptions,
  'requestedModel' | 'requestedToolNames' | 'requestedMaxTokens'
>

export interface NodeAgentRuntimeConfiguration {
  model: string
  toolNames: NodeAgentToolName[]
  maxTokens?: number
}

export interface ResolveAgentRunTimeoutOptions {
  requestedTimeoutMs?: unknown
  /** Zero is allowed only as the omitted-value policy and means no host timeout. */
  defaultTimeoutMs: number
  minimumTimeoutMs?: number
  maximumTimeoutMs?: number
  hostLabel?: string
}

function configurationError(message: string): never {
  throw new AgentRuntimeConfigurationError(message, 'invalid_agent_runtime_config')
}

export function agentNodeUsesLegacyTools(data: Readonly<Record<string, unknown>>): boolean {
  return Object.prototype.hasOwnProperty.call(data, 'tools')
}

/** Strict timeout negotiation: explicit values are either honored exactly or rejected. */
export function resolveAgentRunTimeoutMs(options: ResolveAgentRunTimeoutOptions): number {
  const hostLabel = options.hostLabel?.trim() || 'This host'
  const minimumTimeoutMs = options.minimumTimeoutMs ?? MIN_AGENT_RUN_TIMEOUT_MS
  const maximumTimeoutMs = options.maximumTimeoutMs ?? MAX_AGENT_RUN_TIMEOUT_MS
  if (!Number.isSafeInteger(minimumTimeoutMs) || minimumTimeoutMs < 1) {
    return configurationError('minimumTimeoutMs must be a positive safe integer.')
  }
  if (
    !Number.isSafeInteger(maximumTimeoutMs)
    || maximumTimeoutMs < minimumTimeoutMs
    || maximumTimeoutMs > MAX_AGENT_RUN_TIMEOUT_MS
  ) {
    return configurationError(
      `maximumTimeoutMs must be between ${minimumTimeoutMs} and ${MAX_AGENT_RUN_TIMEOUT_MS}.`
    )
  }
  if (
    !Number.isSafeInteger(options.defaultTimeoutMs)
    || (
      options.defaultTimeoutMs !== 0
      && (
        options.defaultTimeoutMs < minimumTimeoutMs
        || options.defaultTimeoutMs > maximumTimeoutMs
      )
    )
  ) {
    return configurationError('defaultTimeoutMs must be zero or within the host timeout range.')
  }

  const requestedTimeoutMs = options.requestedTimeoutMs
  if (requestedTimeoutMs === undefined) return options.defaultTimeoutMs
  if (!Number.isSafeInteger(requestedTimeoutMs) || (requestedTimeoutMs as number) < minimumTimeoutMs) {
    throw new AgentRuntimeConfigurationError(
      `Agent timeoutMs must be a safe integer of at least ${minimumTimeoutMs}.`,
      'invalid_agent_runtime_config',
      { requestedTimeoutMs, minimumTimeoutMs }
    )
  }
  if ((requestedTimeoutMs as number) > maximumTimeoutMs) {
    throw new AgentRuntimeConfigurationError(
      `${hostLabel} allows at most ${maximumTimeoutMs}ms per Agent run; ${requestedTimeoutMs}ms was requested.`,
      'agent_timeout_exceeded',
      { requestedTimeoutMs, maximumTimeoutMs }
    )
  }
  return requestedTimeoutMs as number
}

/** Omitted selection exposes all dynamic capabilities; explicit selection exposes exact matches only. */
export function selectAgentRuntimeDynamicTools<
  TTool extends { function: { name: string } }
>(
  dynamicTools: readonly TTool[],
  explicitToolNames: readonly string[] | undefined
): TTool[] {
  if (explicitToolNames === undefined) return [...dynamicTools]
  const selected = new Set(explicitToolNames)
  return dynamicTools.filter((tool) => selected.has(tool.function.name))
}

function resolveModel(
  requested: unknown,
  options: ResolveAgentRuntimeConfigurationOptions,
  hostLabel: string
): string {
  const explicit = requested !== undefined
  if (explicit && (typeof requested !== 'string' || !requested.trim() || requested.trim().length > 240)) {
    return configurationError('Agent model must be a non-empty string of at most 240 characters.')
  }
  const model = explicit ? (requested as string).trim() : options.defaultModel.trim()
  if (!model && !options.allowUnconfiguredDefaultModel) {
    return configurationError('The default Agent model must be a non-empty string.')
  }
  if (model.length > 240) {
    return configurationError('The default Agent model must not exceed 240 characters.')
  }
  if (options.allowedModels && !options.allowedModels.has(model)) {
    throw new AgentRuntimeConfigurationError(
      `${hostLabel} does not support Agent model "${model}".`,
      'model_not_allowed',
      { model, allowedModels: [...options.allowedModels] }
    )
  }
  return model
}

function normalizeToolNames(
  value: unknown,
  source: 'requested' | 'default' | 'required'
): string[] {
  if (!Array.isArray(value)) {
    if (source !== 'requested') return configurationError(`Agent ${source} tool names must be an array.`)
    return configurationError('Agent toolNames must be an array of tool names.')
  }
  const names: string[] = []
  for (const item of value) {
    if (typeof item !== 'string' || !item.trim() || item.trim().length > 120) {
      return configurationError('Each Agent tool name must be a non-empty string of at most 120 characters.')
    }
    names.push(item.trim())
  }
  return [...new Set(names)]
}

function resolveToolNames<TToolName extends string>(
  requested: unknown,
  options: ResolveAgentRuntimeConfigurationOptions<TToolName>,
  hostLabel: string
): { toolNames: TToolName[]; explicitToolNames?: TToolName[] } {
  const explicit = requested !== undefined
  const selected = normalizeToolNames(
    explicit ? requested : options.defaultToolNames,
    explicit ? 'requested' : 'default'
  )
  const required = normalizeToolNames(
    options.requiredToolNames ?? [],
    'required'
  )
  const allNames = [...new Set([...required, ...selected])]
  const unavailable = allNames.filter((name) => !options.availableToolNames.has(name))
  if (unavailable.length > 0) {
    throw new AgentRuntimeConfigurationError(
      `${hostLabel} does not provide Agent tool${unavailable.length === 1 ? '' : 's'}: ${unavailable.join(', ')}.`,
      'unavailable_agent_tool',
      { toolNames: unavailable, availableToolNames: [...options.availableToolNames] }
    )
  }
  return {
    toolNames: allNames as TToolName[],
    ...(explicit ? { explicitToolNames: selected as TToolName[] } : {})
  }
}

function resolveMaxTokens(requested: unknown): number | undefined {
  if (requested === undefined) return undefined
  if (typeof requested !== 'number' || !Number.isSafeInteger(requested) || requested < 1) {
    return configurationError('Agent maxTokens must be a positive safe integer.')
  }
  return requested
}

/** Strict host negotiation: omitted values receive host defaults; explicit unsupported values fail. */
export function resolveAgentRuntimeConfiguration<TToolName extends string = string>(
  options: ResolveAgentRuntimeConfigurationOptions<TToolName>
): AgentRuntimeConfiguration<TToolName> {
  const hostLabel = options.hostLabel?.trim() || 'This host'
  const resolvedTools = resolveToolNames(
    options.requestedToolNames,
    options,
    hostLabel
  )
  const maxTokens = resolveMaxTokens(options.requestedMaxTokens)
  return {
    model: resolveModel(options.requestedModel, options, hostLabel),
    ...resolvedTools,
    ...(maxTokens !== undefined ? { maxTokens } : {})
  }
}

/** Canonical Agent-node field ingress shared by every host policy. */
export function resolveAgentNodeRuntimeConfiguration<TToolName extends string = string>(
  data: Readonly<Record<string, unknown>>,
  options: ResolveAgentNodeRuntimeConfigurationOptions<TToolName>
): AgentRuntimeConfiguration<TToolName> {
  if (agentNodeUsesLegacyTools(data)) {
    throw new AgentRuntimeConfigurationError(
      'Agent nodes must use toolNames; the legacy tools field is unsupported.',
      'invalid_agent_runtime_config'
    )
  }
  return resolveAgentRuntimeConfiguration({
    ...options,
    requestedModel: data.model,
    requestedToolNames: migrateLegacyAutomaticAgentToolNames(
      data.toolNames,
      options.legacyAutomaticToolExtensionNames
    ),
    requestedMaxTokens: data.maxTokens
  })
}

/** Stable node-oriented policy layered on the generic strict host resolver. */
export function resolveNodeAgentRuntimeConfiguration(
  options: ResolveNodeAgentRuntimeConfigurationOptions
): NodeAgentRuntimeConfiguration {
  const resolved = resolveAgentRuntimeConfiguration<NodeAgentToolName>({
    ...options,
    defaultToolNames: NODE_AGENT_TOOL_NAMES,
    availableToolNames: new Set(NODE_AGENT_TOOL_NAMES),
    requiredToolNames: ['complete']
  })
  return {
    model: resolved.model,
    toolNames: NODE_AGENT_TOOL_NAMES.filter((name) => resolved.toolNames.includes(name)),
    ...(resolved.maxTokens !== undefined ? { maxTokens: resolved.maxTokens } : {})
  }
}

export function resolveNodeAgentNodeRuntimeConfiguration(
  data: Readonly<Record<string, unknown>>,
  options: ResolveNodeAgentNodeRuntimeConfigurationOptions
): NodeAgentRuntimeConfiguration {
  if (agentNodeUsesLegacyTools(data)) {
    throw new AgentRuntimeConfigurationError(
      'Agent nodes must use toolNames; the legacy tools field is unsupported.',
      'invalid_agent_runtime_config'
    )
  }
  return resolveNodeAgentRuntimeConfiguration({
    ...options,
    requestedModel: data.model,
    requestedToolNames: migrateLegacyAutomaticAgentToolNames(data.toolNames),
    requestedMaxTokens: data.maxTokens
  })
}
