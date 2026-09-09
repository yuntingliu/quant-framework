import type {
  AgentProtocolEdge,
  AgentProtocolNode,
  TextOnlyAgentRunCommand
} from '@conexus/runtime-protocol'
import {
  AgentRuntimeConfigurationError,
  MAX_AGENT_RUN_TIMEOUT_MS,
  resolveAgentNodeRuntimeConfiguration,
  resolveAgentRunTimeoutMs,
  type AgentRuntimeConfiguration
} from '../agent/agent-runtime-configuration.js'
import { materializedAutomaticAgentToolExtensions } from '../agent/agent-tool-selection.js'
import type { HarnessPermission, HarnessToolDefinition } from '../contracts.js'
import {
  createHostedAgentToolProfile,
  HOSTED_RUNTIME,
  HostedRuntimeError,
  type HostedRuntimeAdapterProfile
} from '../hosted/hosted-runtime.js'
import { compileHarnessRelease, type HarnessReleaseArtifact } from './release.js'
import {
  prepareAgentRunConversation,
  type PreparedAgentRunConversation
} from '../agent/agent-session-engine.js'
import { collectReachableCanvasNodes } from '../canvas/canvas-graph-scope.js'
import { canvasToolDefinition } from './canvas-tool-definition.js'
import { NODE_AGENT_TOOL_NAMES } from '../tools/node-agent-tools.js'

const BASE_CANVAS_NODE_TYPES = ['agent', 'harness', 'note', 'document', 'custom'] as const

export interface CanvasAgentReleaseOptions {
  hostLabel: string
  releaseIdPrefix: string
  defaultModel: string
  allowedModels?: ReadonlySet<string>
  allowUnconfiguredDefaultModel?: boolean
  supportedNodeTypes?: readonly string[]
  adapterProfile?: HostedRuntimeAdapterProfile
  defaultTimeoutMs?: number
  maximumTimeoutMs?: number
  capabilities?: readonly string[]
  tags?: readonly string[]
}

export interface CanvasAgentRunPlan {
  release: HarnessReleaseArtifact
  conversation: PreparedAgentRunConversation
  runtimeConfiguration: AgentRuntimeConfiguration
  timeoutMs: number
}

interface CanvasAgentReleaseSource {
  included: AgentProtocolNode[]
  includedIds: ReadonlySet<string>
  packagedTools: HarnessToolDefinition[]
  runtimeConfigurations: ReadonlyMap<string, AgentRuntimeConfiguration>
}

function optionalText(value: unknown, maximum: number): string | undefined {
  if (typeof value !== 'string' || !value.trim()) return undefined
  return value.trim().slice(0, maximum)
}

function executionScope(
  request: TextOnlyAgentRunCommand,
  supportedNodeTypes: ReadonlySet<string>
): AgentProtocolNode[] {
  const entry = request.canvasState.nodes.find((node) => node.id === request.nodeId)
  if (!entry) throw new HostedRuntimeError('The active Agent is missing from the Canvas.', 'unsupported_agent_node')
  const included = collectReachableCanvasNodes(
    request.canvasState.nodes,
    request.canvasState.edges,
    request.nodeId
  )
  const unsupported = included.filter((node) => !supportedNodeTypes.has(node.type ?? 'custom'))
  if (unsupported.length > 0) {
    const labels = unsupported.slice(0, 3).map((node) => `${node.type ?? 'custom'}:${node.id}`).join(', ')
    throw new HostedRuntimeError(
      `The Canvas Agent graph contains unsupported connected context nodes: ${labels}.`,
      'unsupported_agent_context'
    )
  }
  return included
}

function runtimeTimeout(request: TextOnlyAgentRunCommand, options: CanvasAgentReleaseOptions): number {
  try {
    return resolveAgentRunTimeoutMs({
      requestedTimeoutMs: request.timeoutMs,
      defaultTimeoutMs: options.defaultTimeoutMs ?? 0,
      maximumTimeoutMs: options.maximumTimeoutMs ?? MAX_AGENT_RUN_TIMEOUT_MS,
      hostLabel: options.hostLabel
    })
  } catch (error) {
    if (error instanceof AgentRuntimeConfigurationError) {
      throw new HostedRuntimeError(error.message, error.code, error.details)
    }
    throw error
  }
}

export function resolveCanvasAgentRuntimeConfiguration(
  data: Readonly<Record<string, unknown>>,
  options: CanvasAgentReleaseOptions,
  availableToolNames: ReadonlySet<string>
): AgentRuntimeConfiguration {
  try {
    const persistedAutomaticSelection = materializedAutomaticAgentToolExtensions(data.toolNames)
    const migratedData = persistedAutomaticSelection !== undefined
      ? { ...data, toolNames: undefined }
      : data
    const resolved = resolveAgentNodeRuntimeConfiguration(migratedData, {
      defaultModel: options.defaultModel,
      defaultToolNames: [...availableToolNames],
      availableToolNames,
      requiredToolNames: ['complete'],
      ...(options.allowedModels ? { allowedModels: options.allowedModels } : {}),
      ...(options.allowUnconfiguredDefaultModel ? { allowUnconfiguredDefaultModel: true } : {}),
      hostLabel: options.hostLabel
    })
    return {
      ...resolved,
      toolNames: NODE_AGENT_TOOL_NAMES.filter((name) => resolved.toolNames.includes(name))
    }
  } catch (error) {
    if (error instanceof AgentRuntimeConfigurationError) {
      throw new HostedRuntimeError(error.message, error.code, error.details)
    }
    throw error
  }
}

function resolveCanvasAgentReleaseSource(
  request: TextOnlyAgentRunCommand,
  options: CanvasAgentReleaseOptions
): CanvasAgentReleaseSource {
  const supportedNodeTypes = new Set([
    ...BASE_CANVAS_NODE_TYPES,
    ...(options.supportedNodeTypes ?? []),
    ...(options.adapterProfile?.nodeTypes ?? [])
  ])
  const included = executionScope(request, supportedNodeTypes)
  const includedIds = new Set(included.map((node) => node.id))
  if (!includedIds.has(request.nodeId)) {
    throw new HostedRuntimeError('The active Agent is not supported by this runtime.', 'unsupported_agent_node')
  }

  const packagedTools = included
    .filter((node) => node.type === 'tool')
    .map((node) => canvasToolDefinition(node))
  const availableToolNames = new Set(createHostedAgentToolProfile(options.adapterProfile).availableToolNames)
  const runtimeConfigurations = new Map(
    included
      .filter((node) => node.type === 'agent')
      .map((node) => [
        node.id,
        resolveCanvasAgentRuntimeConfiguration(node.data, options, availableToolNames)
      ])
  )
  return { included, includedIds, packagedTools, runtimeConfigurations }
}

function compileCanvasAgentReleaseSource(
  request: TextOnlyAgentRunCommand,
  options: CanvasAgentReleaseOptions,
  source: CanvasAgentReleaseSource
): HarnessReleaseArtifact {
  const { included, includedIds, packagedTools, runtimeConfigurations } = source
  const nodes = included.map((node) => {
    const type = node.type ?? 'custom'
    const data = { ...node.data }
    if (type === 'agent') {
      const resolved = runtimeConfigurations.get(node.id)!
      data.model = resolved.model
      data.toolNames = resolved.toolNames
      if (resolved.maxTokens === undefined) delete data.maxTokens
      else data.maxTokens = resolved.maxTokens
    }
    return {
      id: node.id,
      type,
      data,
      ...(node.parentId && includedIds.has(node.parentId) ? { parentId: node.parentId } : {})
    }
  })
  const entry = nodes.find((node) => node.id === request.nodeId)!
  entry.data = { ...entry.data, task: '{{request}}', exposeInHarness: true }
  const edges: AgentProtocolEdge[] = request.canvasState.edges
    .filter((edge) => includedIds.has(edge.source) && includedIds.has(edge.target))
  const timeoutMs = runtimeTimeout(request, options)
  const needsShell = packagedTools.length > 0 || included.some((node) => node.type === 'cli')
  const permissions: HarnessPermission[] = [
    { kind: 'model', required: true },
    ...(needsShell ? [{ kind: 'shell' as const, required: true }] : [])
  ]

  return compileHarnessRelease({
    schema: 'conexus.harness',
    manifest: {
      id: `${options.releaseIdPrefix}.${request.nodeId}`,
      name: optionalText(entry.data.label, 160) ?? 'Canvas Agent',
      description: 'Execute one Canvas Agent through the shared Conexus runtime.',
      version: 1,
      exposures: [{
        id: 'agent',
        name: optionalText(entry.data.label, 160) ?? 'Canvas Agent',
        nodeId: request.nodeId,
        nodeType: 'agent',
        surfaces: ['agent_tool', 'api']
      }],
      defaultExposureId: 'agent',
      permissions,
      dependencies: [
        { kind: 'model', name: 'openrouter' },
        { kind: 'runtime', name: HOSTED_RUNTIME }
      ],
      capabilities: ['canvas-agent', 'portable-tools', ...(options.capabilities ?? [])],
      triggers: [],
      tags: ['canvas', 'ephemeral', ...(options.tags ?? [])]
    },
    graph: { nodes, edges },
    tools: packagedTools,
    runtime: {
      ...(timeoutMs > 0 ? { timeoutMs } : {}),
      isolation: needsShell ? 'shell' : 'none'
    }
  })
}

export function compileCanvasAgentRelease(
  request: TextOnlyAgentRunCommand,
  options: CanvasAgentReleaseOptions
): HarnessReleaseArtifact {
  return compileCanvasAgentReleaseSource(
    request,
    options,
    resolveCanvasAgentReleaseSource(request, options)
  )
}

export function prepareCanvasAgentRun(
  request: TextOnlyAgentRunCommand,
  options: CanvasAgentReleaseOptions
): CanvasAgentRunPlan {
  const source = resolveCanvasAgentReleaseSource(request, options)
  const entry = source.included.find((node) => node.id === request.nodeId)
  if (!entry || entry.type !== 'agent') {
    throw new HostedRuntimeError('The active Canvas node is not an Agent.', 'unsupported_agent_node')
  }
  const release = compileCanvasAgentReleaseSource(request, options, source)
  return {
    release,
    conversation: prepareAgentRunConversation(request),
    runtimeConfiguration: source.runtimeConfigurations.get(entry.id)!,
    timeoutMs: release.runtime.timeoutMs ?? 0
  }
}
