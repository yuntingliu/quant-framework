import { randomUUID } from 'node:crypto'
import AjvModule, { type ErrorObject, type ValidateFunction } from 'ajv'
import {
  AgentRuntimeConfigurationError,
  agentNodeUsesLegacyTools,
  MAX_AGENT_RUN_TIMEOUT_MS,
  resolveAgentRunTimeoutMs,
  resolveAgentNodeRuntimeConfiguration
} from '../agent/agent-runtime-configuration.js'
import { AgentRunController } from '../agent/agent-run-controller.js'
import {
  materializedAutomaticAgentToolExtensions
} from '../agent/agent-tool-selection.js'
import {
  createAgentToolProfile,
  type AgentToolProfile
} from '../agent/agent-tool-profile.js'
import { AgentHostRuntimeSession } from '../agent/agent-host-runtime-session.js'
import {
  dispatchAgentHostNodeRun,
  startAgentHostNodeRun
} from '../agent/agent-host-node-run.js'
import {
  assembleCanvasAgentSessionMessages,
  buildRuntimeNodesSystemSection
} from '../agent/agent-session-engine.js'
import {
  buildCanvasAgentChangeContextSystemSection,
  captureCanvasAgentObservation
} from '../agent/agent-canvas-observation.js'
import { executeAgentNodeRuntime } from '../agent/agent-node-runtime.js'
import { createAgentTraceTranscript } from '../agent/agent-trace-transcript.js'
import { type AgentLLMResult, type AgentMessage } from '../agent/agent-loop.js'
import { executableAgentToolResult } from '../tools/agent-tool-results.js'
import {
  executeHarnessInvocation,
  type HarnessExecutionResult,
  type HarnessNodeRunRecord
} from '../harness/harness-runtime.js'
import {
  dispatchNodeAgentTool,
  requestAgentUserInput,
  type FindNodesRequest,
  type ObserveNodesRequest,
  type UseNodeRequest
} from '../tools/node-agent-tool-dispatcher.js'
import { NODE_AGENT_TOOL_NAMES } from '../tools/node-agent-tools.js'
import {
  getNodeCapabilityContract,
  getNodeCapabilityContracts,
  getNodeCapabilityDescriptors,
  getRuntimeCapabilityContract,
  runtimeManagedNodesForCapabilities
} from '../capabilities/node-capability-contracts.js'
import { validateNodeCapabilityInput } from '../capabilities/node-capability-validation.js'
import {
  createWorkspaceNodes,
  editWorkspaceNodes
} from '../tools/workspace-node-operations.js'
import {
  buildPortableNodeObservation,
  createPortableListNodesResult,
  createPortableObservationEnvelope,
  createPortableObservationSuccess,
  portableGraphIssue,
  portableNodeNotFoundIssue,
  portableObservationResultFailure,
  redactPortableGraphData,
  selectPortableGraphScope,
  type PortableObservationResult,
} from '../tools/portable-graph-tools.js'
import {
  bindHarnessReleaseExposure,
  compileHarnessRelease,
  harnessReleaseChecksum,
  instantiateHarnessWorkspace,
  validateHarnessRelease,
  HarnessContractError,
  type HarnessReleaseArtifact
} from '../release/release.js'
import { canvasToolDefinition } from '../release/canvas-tool-definition.js'
import { deriveHarnessExposures } from '../harness/harness-exposures.js'
import { canvasNodeBelongsToHarness } from '../canvas/canvas-graph-scope.js'
import { RuntimeJobRegistry } from '../runtime/runtime-job-registry.js'
import { asyncNodeRunToolResult } from '../runtime/runtime-node-run-control.js'
import type {
  HarnessExposure,
  HarnessToolDefinition,
  ModelCompletionRequest,
  ModelProvider,
  ModelTraceEvent,
  RuntimeClock,
  RuntimeEventSink,
  RuntimeEdge,
  RuntimeIdGenerator,
  RuntimeNode,
  RuntimeToolSchema,
  RuntimeWorkspace
} from '../contracts.js'
import { editFieldsSchemaForType } from '../canvas/node-catalog.js'

export const HOSTED_RUNTIME = 'conexus.runtime.v1' as const

const PASSIVE_NODE_TYPES = new Set(['note', 'document', 'custom', 'image', 'files', 'app'])
const HOSTED_NODE_TYPES = new Set(['agent', 'harness', ...PASSIVE_NODE_TYPES])
const HOSTED_PERMISSIONS = new Set(['model'])

const AjvConstructor = (
  (AjvModule as unknown as { default?: unknown }).default ?? AjvModule
) as new (options: { allErrors: boolean; strict: boolean }) => {
  compile: (schema: unknown) => ValidateFunction<unknown>
}
const ajv = new AjvConstructor({ allErrors: true, strict: false })

export type HostedCompletionRequest = Omit<ModelCompletionRequest, 'model' | 'signal'> & {
  model: string
  signal: AbortSignal
}

export interface HostedCompletion {
  (request: HostedCompletionRequest): Promise<AgentLLMResult>
  trace?: (event: ModelTraceEvent) => void | Promise<void>
}

export interface HostedCompatibilityIssue {
  code: string
  message: string
  nodeId?: string
  toolName?: string
  requirement?: { kind: string; name: string }
}

export interface HostedCompatibilityReport {
  deployable: boolean
  runtime: typeof HOSTED_RUNTIME
  exposures: Array<{
    id: string
    nodeType: string
    surfaces: HarnessExposure['surfaces']
    executable: boolean
  }>
  issues: HostedCompatibilityIssue[]
  warnings: string[]
  adapters: {
    nodes: string[]
    tools: string[]
    permissions: string[]
    runtimes: string[]
    models: ['*']
    toolCapabilities: {
      edit: { placement: true; relations: true }
      use: { nodeTypes: string[]; mode: 'capability-contract' }
    }
  }
  declaredCapabilities: string[]
}

export interface InspectedHostedRelease {
  release: HarnessReleaseArtifact
  checksum: string
  compatibility: HostedCompatibilityReport
}

export interface HostedRuntimeAdapterProfile {
  name: string
  nodeTypes: readonly string[]
  toolRuntimes: readonly NonNullable<HarnessToolDefinition['runtime']>[]
  permissions: readonly string[]
  isolation: readonly NonNullable<HarnessReleaseArtifact['runtime']['isolation']>[]
  capabilityIds: readonly string[]
  runNodeTypes: readonly string[]
}

/** Resolves the single Agent tool contract for a Hosted runtime capability profile. */
export function createHostedAgentToolProfile(
  profile?: HostedRuntimeAdapterProfile
): AgentToolProfile {
  return createAgentToolProfile({
    name: profile?.name ?? 'conexus.node-agent-tools.v1'
  })
}

export interface HostedShellExecutionResult {
  success: boolean
  cliNodeId: string
  shell: string
  cwd: string
  stdout: string
  stderr: string
  exitCode: number | null
  timedOut: boolean
  durationMs: number
}

export interface HostedInlineToolExecutionResult {
  success: boolean
  runtime: string
  stdout: string
  stderr: string
  exitCode: number | null
  timedOut: boolean
  durationMs: number
  result?: unknown
}

export interface HostedGatewayAuthorization {
  credential: string
  audience: string
  publicationSlug: string
}

export interface HostedHostToolResult {
  result: unknown
  updatedNodes?: RuntimeNode[]
  nodeUpdates?: Array<{ nodeId: string; data: Record<string, unknown> }>
  createdNodes?: RuntimeNode[]
  createdEdges?: RuntimeEdge[]
  deletedNodeIds?: string[]
  deletedEdgeIds?: string[]
}

export interface HostedRuntimeAdapter {
  profile: HostedRuntimeAdapterProfile
  executeShell(params: {
    runId: string
    cliNodeId: string
    command: string
    cwd?: string
    timeoutMs?: number
    env?: Record<string, string>
    signal: AbortSignal
  }): Promise<HostedShellExecutionResult>
  executeTool(params: {
    runId: string
    definition: HarnessToolDefinition
    args: Record<string, unknown>
    context: Record<string, unknown>
    signal: AbortSignal
    callNode?: (request: { nodeId: string; args?: Record<string, unknown> }) => Promise<unknown>
  }): Promise<HostedInlineToolExecutionResult>
  executeNodeOperation?(params: {
    runId: string
    operation: 'create' | 'edit'
    args: Record<string, unknown>
    ownerNodeId: string
    workspace: Readonly<RuntimeWorkspace>
    authorization?: HostedGatewayAuthorization
    signal: AbortSignal
  }): Promise<HostedHostToolResult>
  executeNodeCapability?(params: {
    runId: string
    nodeId: string
    capability: string
    input: Record<string, unknown>
    ownerNodeId: string
    workspace: Readonly<RuntimeWorkspace>
    authorization?: HostedGatewayAuthorization
    signal: AbortSignal
  }): Promise<HostedHostToolResult>
  disposeRun?(runId: string): void | Promise<void>
  resolveImageInput?(params: {
    node: RuntimeNode
    signal: AbortSignal
  }): Promise<{ url: string; detail?: 'auto' | 'low' | 'high' } | undefined>
  observeNode?(params: {
    runId: string
    node: RuntimeNode
    ownerNodeId: string
    workspace: Readonly<RuntimeWorkspace>
    signal: AbortSignal
  }): Promise<{ data?: Record<string, unknown>; error?: string }>
}

export interface HostedExecutionResult {
  runtime: typeof HOSTED_RUNTIME
  exposureId: string
  status: 'completed' | 'blocked'
  model: string
  models: string[]
  summary: string
  /** Direct return value of an exposed Tool. Agent deliverables live in workspace nodes. */
  result?: unknown
  workspaceChanges: HostedWorkspaceChanges
  workspace: RuntimeWorkspace
  invocationId: string
}

export interface HostedWorkspaceNodeResult {
  id: string
  type: string
  label: string
  description?: string
  values: Record<string, unknown>
}

export interface HostedWorkspaceChanges {
  created: HostedWorkspaceNodeResult[]
  updated: HostedWorkspaceNodeResult[]
  deleted: string[]
}

export interface ExecuteHostedReleaseOptions {
  runId: string
  release: HarnessReleaseArtifact | unknown
  exposureId?: string
  input: Record<string, unknown>
  workspace?: Readonly<RuntimeWorkspace>
  complete: HostedCompletion
  defaultModel?: string
  signal?: AbortSignal
  controller?: AgentRunController
  conversation?: {
    history: readonly AgentMessage[]
    currentRequest: string
    canvasObservation?: import('@conexus/runtime-protocol').CanvasAgentObservation
  }
  hostAuthorization?: HostedGatewayAuthorization
  events?: RuntimeEventSink
  clock?: RuntimeClock
  ids?: RuntimeIdGenerator
  maxAgentIterations?: number
  runtimeAdapter?: HostedRuntimeAdapter
  controlSession?: AgentHostRuntimeSession
  onBackgroundExecution?: (execution: Promise<void>) => void
  onWorkspaceChanged?: (workspace: Readonly<RuntimeWorkspace>) => void | Promise<void>
}

export class HostedRuntimeError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly details?: unknown
  ) {
    super(message)
    this.name = 'HostedRuntimeError'
  }
}

interface HostedAgentOutcome {
  success: boolean
  status: 'done' | 'blocked' | 'aborted'
  summary: string
  completionGaps: string[]
  result?: unknown
  model: string
}

interface HostedExecutionSession {
  runId: string
  modelTraceRequestId: string
  release: HarnessReleaseArtifact
  exposure: HarnessExposure
  workspace: RuntimeWorkspace
  models: ModelProvider
  events?: RuntimeEventSink
  clock: RuntimeClock
  ids: RuntimeIdGenerator
  signal: AbortSignal
  controller?: AgentRunController
  conversation?: {
    history: readonly AgentMessage[]
    currentRequest: string
    canvasObservation?: import('@conexus/runtime-protocol').CanvasAgentObservation
  }
  hostAuthorization?: HostedGatewayAuthorization
  defaultModel?: string
  maxAgentIterations?: number
  usedModels: Set<string>
  runtimeAdapter?: HostedRuntimeAdapter
  controlSession: AgentHostRuntimeSession
  backgroundRuns: Set<Promise<void>>
  pendingImageInputs: Map<string, Array<{
    nodeId: string
    label: string
    source: string
    url: string
    detail: 'auto' | 'low' | 'high'
  }>>
  onBackgroundExecution?: (execution: Promise<void>) => void
  onWorkspaceChanged?: (workspace: Readonly<RuntimeWorkspace>) => void | Promise<void>
}

interface AgentRunContext {
  callDepth: number
  parentRunId: string
  callStack: string[]
  supplementalTask?: string
}

async function emitModelTrace(session: HostedExecutionSession, event: ModelTraceEvent): Promise<void> {
  if (!session.models.trace) return
  try {
    await session.models.trace(event)
  } catch {
    // Observability must never change the Hosted Runtime outcome.
  }
}

interface PreparedHostedAgentRun {
  runId: string
  controller: AgentRunController
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function positiveInteger(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) {
    throw new HostedRuntimeError(`${label} must be a positive safe integer.`, 'invalid_agent_runtime_config')
  }
  return value
}

function resolveHostedReleaseTimeoutMs(value: unknown): number {
  return resolveAgentRunTimeoutMs({
    requestedTimeoutMs: value,
    defaultTimeoutMs: 0,
    maximumTimeoutMs: MAX_AGENT_RUN_TIMEOUT_MS,
    hostLabel: 'Linux Hosted Runtime'
  })
}

function createDefaultClock(): RuntimeClock {
  return { now: () => new Date().toISOString() }
}

function createDefaultIds(): RuntimeIdGenerator {
  return { create: (prefix) => `${prefix}_${randomUUID()}` }
}

function abortedError(signal: AbortSignal): HostedRuntimeError {
  const reasonName = signal.reason instanceof Error
    ? signal.reason.name
    : isRecord(signal.reason) && typeof signal.reason.name === 'string'
      ? signal.reason.name
      : ''
  return reasonName === 'TimeoutError'
    ? new HostedRuntimeError('Run exceeded the Harness timeout.', 'run_timed_out')
    : new HostedRuntimeError('Run was cancelled.', 'run_cancelled')
}

function throwIfAborted(signal: AbortSignal): void {
  if (signal.aborted) throw abortedError(signal)
}

function formatSchemaErrors(errors: ErrorObject[] | null | undefined): Array<Record<string, unknown>> {
  return (errors ?? []).map((error) => ({
    path: error.instancePath || '/',
    keyword: error.keyword,
    message: error.message ?? 'Schema validation failed.',
    params: error.params
  }))
}

function compileSchema(schema: unknown, label: 'input' | 'output'): ValidateFunction<unknown> {
  try {
    return ajv.compile(schema)
  } catch (error) {
    throw new HostedRuntimeError(
      `Harness ${label} schema cannot be compiled.`,
      `invalid_${label}_schema`,
      error instanceof Error ? error.message : String(error)
    )
  }
}

function schemaIssue(schema: unknown, label: 'input' | 'output'): HostedCompatibilityIssue | undefined {
  if (!schema) return undefined
  try {
    ajv.compile(schema)
    return undefined
  } catch (error) {
    return {
      code: `invalid_${label}_schema`,
      message: `${label} schema cannot be compiled: ${error instanceof Error ? error.message : String(error)}`
    }
  }
}

function addIssue(
  issues: HostedCompatibilityIssue[],
  seen: Set<string>,
  issue: HostedCompatibilityIssue
): void {
  const key = `${issue.code}:${issue.nodeId ?? ''}:${issue.toolName ?? ''}:${issue.requirement?.kind ?? ''}:${issue.requirement?.name ?? ''}`
  if (seen.has(key)) return
  seen.add(key)
  issues.push(issue)
}

function releaseToolRuntime(tool: HarnessToolDefinition): NonNullable<HarnessToolDefinition['runtime']> {
  return tool.runtime ?? 'node'
}

function availableToolNames(
  _release: HarnessReleaseArtifact,
  profile?: HostedRuntimeAdapterProfile
): Set<string> {
  return new Set<string>(createHostedAgentToolProfile(profile).availableToolNames)
}

function normalizeHostedAutomaticAgentToolNames(
  release: HarnessReleaseArtifact,
  profile?: HostedRuntimeAdapterProfile
): HarnessReleaseArtifact {
  const defaults = createHostedAgentToolProfile(profile).defaultToolNames
  let changed = false
  const nodes = release.graph.nodes.map((node) => {
    if (node.type !== 'agent') return node
    if (materializedAutomaticAgentToolExtensions(node.data.toolNames) === undefined) return node
    changed = true
    return {
      ...node,
      data: {
        ...node.data,
        toolNames: [...defaults]
      }
    }
  })
  if (!changed) return release
  return validateHarnessRelease({
    ...release,
    graph: { ...release.graph, nodes }
  })
}

/**
 * Hosted releases written before account workspaces allowed authoring contain a
 * materialized automatic tool list with only the authoring tools missing. Keep
 * their immutable release checksum stable and restore the current host defaults
 * only while resolving an Agent run.
 */
function currentHostedAutomaticToolNames(
  value: unknown,
  profile: HostedRuntimeAdapterProfile | undefined,
  resolvedToolNames: readonly string[],
  available: ReadonlySet<string>
): string[] {
  void available
  return materializedAutomaticAgentToolExtensions(value) === undefined
    ? [...resolvedToolNames]
    : [...createHostedAgentToolProfile(profile).defaultToolNames]
}

function inspectCompatibility(
  release: HarnessReleaseArtifact,
  profile?: HostedRuntimeAdapterProfile
): HostedCompatibilityReport {
  const issues: HostedCompatibilityIssue[] = []
  const warnings: string[] = []
  const seenIssues = new Set<string>()
  const nodeIds = new Set<string>()
  const nodeTypes = new Set([...HOSTED_NODE_TYPES, ...(profile?.nodeTypes ?? [])])
  const toolNames = availableToolNames(release, profile)
  const reservedToolNames = new Set<string>(createHostedAgentToolProfile(profile).availableToolNames)
  const toolRuntimes = new Set(profile?.toolRuntimes ?? [])
  const permissions = new Set([...HOSTED_PERMISSIONS, ...(profile?.permissions ?? [])])
  const isolation = new Set(profile?.isolation ?? [])

  for (const node of release.graph.nodes) {
    if (nodeIds.has(node.id)) {
      addIssue(issues, seenIssues, {
        code: 'duplicate_node_id',
        message: `Duplicate node id: ${node.id}.`,
        nodeId: node.id
      })
    }
    nodeIds.add(node.id)
    if (!nodeTypes.has(node.type)) {
      addIssue(issues, seenIssues, {
        code: 'missing_node_adapter',
        message: `Linux Hosted Runtime has no adapter for node type "${node.type}".`,
        nodeId: node.id,
        requirement: { kind: 'node', name: node.type }
      })
    }
    if (node.type === 'agent') {
      if (agentNodeUsesLegacyTools(node.data)) {
        addIssue(issues, seenIssues, {
          code: 'legacy_agent_tools_field',
          message: `Agent ${node.id} must use toolNames; the legacy tools field is unsupported.`,
          nodeId: node.id
        })
      } else {
        try {
          resolveHostedAgentRuntimeConfiguration(node, '', true, profile, toolNames)
        } catch (error) {
          if (error instanceof AgentRuntimeConfigurationError && error.code === 'unavailable_agent_tool') {
            const toolNames = Array.isArray(error.details?.toolNames)
              ? error.details.toolNames.filter((name): name is string => typeof name === 'string')
              : []
            for (const toolName of toolNames) {
              addIssue(issues, seenIssues, {
                code: 'missing_tool_adapter',
                message: `Agent ${node.id} requests tool "${toolName}", but no Linux Hosted adapter is registered.`,
                nodeId: node.id,
                toolName,
                requirement: { kind: 'tool', name: toolName }
              })
            }
          } else {
            addIssue(issues, seenIssues, {
              code: error instanceof AgentRuntimeConfigurationError
                ? error.code
                : 'invalid_agent_runtime_config',
              message: `Agent ${node.id} has invalid runtime configuration: ${error instanceof Error ? error.message : String(error)}`,
              nodeId: node.id,
            })
          }
        }
      }
    }
  }

  const exposureReports = release.manifest.exposures.map((exposure) => {
    const target = release.graph.nodes.find((node) => node.id === exposure.nodeId)
    const executable = target?.type === 'agent'
      || (target?.type === 'tool' && profile?.runNodeTypes.includes('tool') === true)
    if (!target) {
      addIssue(issues, seenIssues, {
        code: 'exposure_target_not_found',
        message: `Harness exposure "${exposure.id}" references a missing target.`,
        nodeId: exposure.nodeId
      })
    } else if (
      exposure.surfaces.some((surface) => surface === 'agent_tool' || surface === 'api')
      && !executable
    ) {
      addIssue(issues, seenIssues, {
        code: 'unsupported_exposure_target',
        message: `Harness exposure "${exposure.id}" requires an executable target, but ${target.type} is not executable on this host.`,
        nodeId: target.id,
        requirement: { kind: 'node', name: target.type }
      })
    }
    return {
      id: exposure.id,
      nodeType: exposure.nodeType,
      surfaces: [...exposure.surfaces],
      executable
    }
  })

  for (const tool of release.tools) {
    const inputSchemaIssue = schemaIssue(tool.inputSchema, 'input')
    const outputSchemaIssue = schemaIssue(tool.outputSchema, 'output')
    for (const issue of [inputSchemaIssue, outputSchemaIssue]) {
      if (!issue) continue
      addIssue(issues, seenIssues, {
        ...issue,
        code: `tool_${issue.code}`,
        message: `Tool "${tool.name}" ${issue.message}`,
        toolName: tool.name
      })
    }
    if (reservedToolNames.has(tool.name)) {
      addIssue(issues, seenIssues, {
        code: 'reserved_tool_name',
        message: `Packaged tool "${tool.name}" conflicts with a host-registered tool name.`,
        toolName: tool.name,
        requirement: { kind: 'tool', name: tool.name }
      })
      continue
    }
    if (tool.code) {
      const runtime = releaseToolRuntime(tool)
      if (!toolRuntimes.has(runtime)) {
        addIssue(issues, seenIssues, {
          code: 'inline_tool_runtime_forbidden',
          message: `Packaged tool "${tool.name}" requires unavailable inline runtime "${runtime}".`,
          toolName: tool.name,
          requirement: { kind: 'tool', name: tool.name }
        })
      }
      continue
    }
    if (tool.runtime === 'mcp') {
      addIssue(issues, seenIssues, {
        code: 'missing_mcp_adapter',
        message: `Packaged tool "${tool.name}" requires an MCP adapter that is not installed.`,
        toolName: tool.name,
        requirement: { kind: 'tool', name: tool.name }
      })
      continue
    }
    if (tool.runtime && tool.runtime !== 'registered') {
      addIssue(issues, seenIssues, {
        code: 'inline_tool_runtime_forbidden',
        message: `Packaged tool "${tool.name}" requests the forbidden inline runtime "${tool.runtime}".`,
        toolName: tool.name,
        requirement: { kind: 'tool', name: tool.name }
      })
      continue
    }
    if (!toolNames.has(tool.name)) {
      addIssue(issues, seenIssues, {
        code: 'missing_tool_adapter',
        message: `No Linux Hosted adapter is registered for packaged tool "${tool.name}".`,
        toolName: tool.name,
        requirement: { kind: 'tool', name: tool.name }
      })
    }
  }

  for (const permission of release.manifest.permissions) {
    if (permissions.has(permission.kind)) continue
    const message = `Linux Hosted Runtime does not grant the "${permission.kind}" permission.`
    if (permission.required === false) {
      warnings.push(`Optional permission omitted: ${permission.kind}.`)
    } else {
      addIssue(issues, seenIssues, {
        code: 'missing_permission_adapter',
        message,
        requirement: { kind: 'permission', name: permission.kind }
      })
    }
  }

  for (const dependency of release.manifest.dependencies) {
    let supported = false
    if (dependency.kind === 'model') supported = true
    if (dependency.kind === 'tool') supported = toolNames.has(dependency.name)
    if (dependency.kind === 'runtime') supported = dependency.name === HOSTED_RUNTIME
    if (supported) continue
    if (dependency.optional) {
      warnings.push(`Optional dependency adapter omitted: ${dependency.kind}:${dependency.name}.`)
    } else {
      addIssue(issues, seenIssues, {
        code: 'missing_dependency_adapter',
        message: `Linux Hosted Runtime has no adapter for required dependency ${dependency.kind}:${dependency.name}.`,
        requirement: { kind: dependency.kind, name: dependency.name }
      })
    }
  }

  if (release.runtime.isolation && release.runtime.isolation !== 'none' && !isolation.has(release.runtime.isolation)) {
    addIssue(issues, seenIssues, {
      code: 'missing_isolation_adapter',
      message: `Linux Hosted Runtime does not provide the requested "${release.runtime.isolation}" isolation adapter.`,
      requirement: { kind: 'runtime', name: `isolation:${release.runtime.isolation}` }
    })
  }

  try {
    resolveHostedReleaseTimeoutMs(release.runtime.timeoutMs)
  } catch (error) {
    addIssue(issues, seenIssues, {
      code: error instanceof AgentRuntimeConfigurationError
        ? error.code
        : 'invalid_agent_runtime_config',
      message: `Harness runtime timeout is invalid: ${error instanceof Error ? error.message : String(error)}`,
      requirement: { kind: 'runtime', name: 'timeout' }
    })
  }

  return {
    deployable: issues.length === 0,
    runtime: HOSTED_RUNTIME,
    exposures: exposureReports,
    issues,
    warnings,
    adapters: {
      nodes: [...nodeTypes].sort(),
      tools: [...toolNames],
      permissions: [...permissions].sort(),
      runtimes: [HOSTED_RUNTIME, ...(profile ? [profile.name] : [])],
      models: ['*'],
      toolCapabilities: {
        edit: { placement: true, relations: true },
        use: {
          nodeTypes: ['agent', 'harness', ...(profile?.runNodeTypes ?? [])],
          mode: 'capability-contract'
        }
      }
    },
    declaredCapabilities: [...release.manifest.capabilities]
  }
}

export function inspectHostedRelease(
  value: unknown,
  expectedChecksum?: string,
  profile?: HostedRuntimeAdapterProfile
): InspectedHostedRelease {
  try {
    const compiledRelease = isRecord(value) && value.schema === 'conexus.harness.release'
      ? validateHarnessRelease(value)
      : compileHarnessRelease(value)
    const release = normalizeHostedAutomaticAgentToolNames(compiledRelease, profile)
    const checksum = harnessReleaseChecksum(release)
    if (expectedChecksum !== undefined && checksum !== expectedChecksum) {
      throw new HostedRuntimeError(
        'Stored Harness release does not match its deployment checksum.',
        'release_checksum_mismatch',
        { expectedChecksum, actualChecksum: checksum }
      )
    }
    return {
      release,
      checksum,
      compatibility: inspectCompatibility(release, profile)
    }
  } catch (error) {
    if (error instanceof HarnessContractError) {
      throw new HostedRuntimeError(error.message, error.code, error.details)
    }
    throw error
  }
}

function releaseToolForExposure(
  release: HarnessReleaseArtifact,
  exposure: HarnessExposure
): HarnessToolDefinition | undefined {
  if (exposure.nodeType !== 'tool') return undefined
  const target = release.graph.nodes.find((node) => node.id === exposure.nodeId)
  return release.tools.find((tool) => tool.nodeId === exposure.nodeId)
    ?? release.tools.find((tool) => tool.name === textValue(target?.data.toolName ?? target?.data.name ?? target?.data.label))
}

/** Validates invocation data against the exposed node's own contract. */
export function validateHostedExposureInvocation(
  release: HarnessReleaseArtifact,
  exposure: HarnessExposure,
  input: unknown
): void {
  if (!isRecord(input)) {
    throw new HostedRuntimeError('Run input must be a JSON object.', 'invalid_run_input')
  }
  if (exposure.nodeType === 'agent') {
    const keys = Object.keys(input)
    const request = textValue(input.request)
    if (!request || keys.some((key) => key !== 'request')) {
      throw new HostedRuntimeError(
        'Agent invocation input must be exactly { request: non-empty string }.',
        'invalid_run_input'
      )
    }
    if (request.length > 100_000) {
      throw new HostedRuntimeError('Agent request exceeds 100000 characters.', 'invalid_run_input')
    }
    return
  }
  const definition = releaseToolForExposure(release, exposure)
  if (definition?.inputSchema) {
    const validate = compileSchema(definition.inputSchema, 'input')
    if (!validate(input)) {
      throw new HostedRuntimeError(
        `Run input does not match Tool "${definition.name}" input schema.`,
        'invalid_run_input',
        formatSchemaErrors(validate.errors)
      )
    }
  }
}

export function createHostedModelProvider(complete: HostedCompletion): ModelProvider {
  return {
    async complete(request) {
      const model = textValue(request.model)
      if (!model) throw new HostedRuntimeError('No model is configured for this Agent.', 'missing_model')
      return complete({
        ...request,
        model,
        signal: request.signal ?? new AbortController().signal
      })
    },
    ...(complete.trace ? { trace: complete.trace } : {})
  }
}

function resolveHostedAgentRuntimeConfiguration(
  node: RuntimeNode,
  defaultModel: string,
  allowUnconfiguredDefaultModel: boolean,
  profile?: HostedRuntimeAdapterProfile,
  availableTools: ReadonlySet<string> = new Set(createHostedAgentToolProfile(profile).availableToolNames)
) {
  const resolved = resolveAgentNodeRuntimeConfiguration(node.data, {
    defaultModel,
    defaultToolNames: createHostedAgentToolProfile(profile).defaultToolNames,
    availableToolNames: availableTools,
    requiredToolNames: ['complete'],
    allowUnconfiguredDefaultModel,
    hostLabel: 'Linux Hosted Runtime'
  })
  return {
    ...resolved,
    toolNames: NODE_AGENT_TOOL_NAMES.filter((name) => resolved.toolNames.includes(name))
  }
}

function jsonResult(value: Record<string, unknown>): string {
  return JSON.stringify(value)
}

function toolDefinitions(
  session: HostedExecutionSession,
  names: string[]
): RuntimeToolSchema[] {
  const hostTools = new Map<string, RuntimeToolSchema>(createHostedAgentToolProfile(session.runtimeAdapter?.profile).tools
    .map((tool) => [tool.name, tool.schema]))
  return names.flatMap((name) => {
    const schema = hostTools.get(name)
    return schema ? [schema] : []
  })
}

function connectedNodes(workspace: RuntimeWorkspace, node: RuntimeNode): RuntimeNode[] {
  const connectedIds = new Set<string>()
  for (const edge of workspace.edges) {
    if (edge.source !== node.id && edge.target !== node.id) continue
    if (edge.source === node.id) connectedIds.add(edge.target)
    if (edge.target === node.id) connectedIds.add(edge.source)
  }
  return workspace.nodes.filter((candidate) => connectedIds.has(candidate.id))
}

async function connectedImageInputs(
  session: HostedExecutionSession,
  connected: readonly RuntimeNode[]
): Promise<Array<{ type: 'image_url'; image_url: { url: string; detail?: 'auto' | 'low' | 'high' } }>> {
  if (!session.runtimeAdapter?.resolveImageInput) return []
  const images = connected.filter((candidate) => candidate.type === 'image')
  const resolved = await Promise.all(images.slice(0, 8).map((image) =>
    session.runtimeAdapter!.resolveImageInput!({ node: image, signal: session.signal })))
  return resolved.filter((value): value is NonNullable<typeof value> => Boolean(value))
    .map((image) => ({ type: 'image_url' as const, image_url: image }))
}

async function emitHostedAgentEvent(
  session: HostedExecutionSession,
  agentRunId: string,
  parentRunId: string,
  type: string,
  payload: Record<string, unknown>
): Promise<void> {
  await session.events?.emit({
    runId: agentRunId,
    parentRunId,
    at: session.clock.now(),
    type,
    payload: { agentRunId, ...payload }
  })
}

function packagedToolForNode(session: HostedExecutionSession, node: RuntimeNode): HarnessToolDefinition | undefined {
  if (node.type !== 'tool') return undefined
  const baseline = session.release.tools.find((tool) => tool.nodeId === node.id)
    ?? session.release.tools.find((tool) => tool.name === textValue(node.data.toolName))
  return canvasToolDefinition(node, baseline)
}

function availableSessionToolNames(session: HostedExecutionSession): Set<string> {
  return new Set<string>(createHostedAgentToolProfile(session.runtimeAdapter?.profile).availableToolNames)
}

async function executePackagedTool(params: {
  session: HostedExecutionSession
  definition: HarnessToolDefinition
  args: Record<string, unknown>
  ownerNodeId: string
  callDepth?: number
  callStack?: string[]
}): Promise<string> {
  const adapter = params.session.runtimeAdapter
  if (!adapter) return jsonResult({ success: false, error: `Packaged Tool runtime is unavailable: ${params.definition.name}.` })
  if (params.definition.inputSchema) {
    const validate = compileSchema(params.definition.inputSchema, 'input')
    if (!validate(params.args)) {
      return jsonResult({
        success: false,
        error: `Input does not match Tool "${params.definition.name}" input schema.`,
        details: formatSchemaErrors(validate.errors)
      })
    }
  }
  const callDepth = params.callDepth ?? 0
  const callStack = params.callStack ?? []
  if (callDepth >= 20) return jsonResult({ success: false, error: 'Packaged Tool call depth limit reached (20).' })
  const execution = await adapter.executeTool({
    runId: params.session.runId,
    definition: params.definition,
    args: params.args,
    context: {
      ownerNodeId: params.ownerNodeId,
      toolName: params.definition.name,
      runtime: HOSTED_RUNTIME
    },
    signal: params.session.signal,
    callNode: async (request) => {
      if (callStack.includes(request.nodeId)) {
        throw new HostedRuntimeError(
          `Recursive Tool cycle rejected: ${[...callStack, request.nodeId].join(' -> ')}.`,
          'recursive_tool_cycle'
        )
      }
      const target = params.session.workspace.nodes.find((node) => node.id === request.nodeId)
      if (!target || target.type !== 'tool') throw new Error(`Tool node was not found: ${request.nodeId}.`)
      const definition = packagedToolForNode(params.session, target)
      if (!definition) throw new Error(`Packaged Tool definition was not found: ${request.nodeId}.`)
      const nested = await executePackagedTool({
        session: params.session,
        definition,
        args: request.args ?? {},
        ownerNodeId: params.ownerNodeId,
        callDepth: callDepth + 1,
        callStack: [...callStack, request.nodeId]
      })
      const parsed = JSON.parse(nested) as Record<string, unknown>
      if (parsed.success !== true) throw new Error(typeof parsed.error === 'string' ? parsed.error : `Tool failed: ${definition.name}.`)
      return parsed.output
    }
  })
  const result = executableAgentToolResult(execution)
  if (result.success === true && params.definition.outputSchema) {
    const validate = compileSchema(params.definition.outputSchema, 'output')
    if (!validate(result.output)) {
      return jsonResult({
        success: false,
        error: `Output does not match Tool "${params.definition.name}" output schema.`,
        details: formatSchemaErrors(validate.errors)
      })
    }
  }
  return jsonResult(result)
}

async function applyHostedAdapterResult(params: {
  session: HostedExecutionSession
  execution: HostedHostToolResult
}): Promise<string> {
  const { execution } = params
  let changed = false
  const deletedNodeIds = new Set(execution.deletedNodeIds ?? [])
  const deletedEdgeIds = new Set(execution.deletedEdgeIds ?? [])
  if (deletedNodeIds.size > 0) {
    params.session.workspace.nodes = params.session.workspace.nodes.filter((node) => !deletedNodeIds.has(node.id))
    params.session.workspace.edges = params.session.workspace.edges.filter((edge) =>
      !deletedNodeIds.has(edge.source) && !deletedNodeIds.has(edge.target))
    changed = true
  }
  if (deletedEdgeIds.size > 0) {
    params.session.workspace.edges = params.session.workspace.edges.filter((edge) => !deletedEdgeIds.has(edge.id))
    changed = true
  }
  for (const replacement of execution.updatedNodes ?? []) {
    const index = params.session.workspace.nodes.findIndex((node) => node.id === replacement.id)
    if (index < 0) throw new HostedRuntimeError(`Hosted operation updated a missing node: ${replacement.id}.`, 'invalid_host_tool_result')
    params.session.workspace.nodes[index] = structuredClone(replacement)
    changed = true
  }
  for (const update of execution.nodeUpdates ?? []) {
    const target = params.session.workspace.nodes.find((node) => node.id === update.nodeId)
    if (!target) throw new HostedRuntimeError(`Hosted tool updated a missing node: ${update.nodeId}.`, 'invalid_host_tool_result')
    target.data = { ...target.data, ...structuredClone(update.data) }
    changed = true
  }
  for (const node of execution.createdNodes ?? []) {
    if (!node.id.trim() || !node.type.trim() || params.session.workspace.nodes.some((candidate) => candidate.id === node.id)) {
      throw new HostedRuntimeError(`Hosted tool created an invalid or duplicate node: ${node.id}.`, 'invalid_host_tool_result')
    }
    params.session.workspace.nodes.push(structuredClone(node))
    changed = true
  }
  const nodeIds = new Set(params.session.workspace.nodes.map((node) => node.id))
  for (const edge of execution.createdEdges ?? []) {
    if (!edge.id.trim()
      || params.session.workspace.edges.some((candidate) => candidate.id === edge.id)
      || !nodeIds.has(edge.source)
      || !nodeIds.has(edge.target)) {
      throw new HostedRuntimeError(`Hosted tool created an invalid edge: ${edge.id}.`, 'invalid_host_tool_result')
    }
    params.session.workspace.edges.push(structuredClone(edge))
    changed = true
  }
  if (changed) await params.session.onWorkspaceChanged?.(params.session.workspace)
  return isRecord(execution.result)
    ? JSON.stringify(execution.result)
    : jsonResult({ success: true, output: execution.result })
}

async function executeAdapterNodeOperation(params: {
  session: HostedExecutionSession
  operation: 'create' | 'edit'
  args: Record<string, unknown>
  ownerNodeId: string
}): Promise<string> {
  const adapter = params.session.runtimeAdapter
  if (!adapter?.executeNodeOperation) {
    const execution = params.operation === 'create'
      ? createWorkspaceNodes(params.session.workspace, params.args, params.ownerNodeId, () => params.session.ids.create('node'))
      : editWorkspaceNodes(params.session.workspace, params.args, params.ownerNodeId, () => params.session.ids.create('edge'))
    return applyHostedAdapterResult({ session: params.session, execution })
  }
  return applyHostedAdapterResult({
    session: params.session,
    execution: await adapter.executeNodeOperation({
      runId: params.session.runId,
      operation: params.operation,
      args: params.args,
      ownerNodeId: params.ownerNodeId,
      workspace: params.session.workspace,
      ...(params.session.hostAuthorization ? { authorization: params.session.hostAuthorization } : {}),
      signal: params.session.signal
    })
  })
}

async function executeAdapterNodeCapability(params: {
  session: HostedExecutionSession
  nodeId: string
  capability: string
  input: Record<string, unknown>
  ownerNodeId: string
}): Promise<string> {
  const adapter = params.session.runtimeAdapter
  if (!adapter?.executeNodeCapability || !adapter.profile.capabilityIds.includes(params.capability)) {
    return jsonResult({ success: false, error: `Hosted node capability is unavailable: ${params.capability}.` })
  }
  return applyHostedAdapterResult({
    session: params.session,
    execution: await adapter.executeNodeCapability({
      runId: params.session.runId,
      nodeId: params.nodeId,
      capability: params.capability,
      input: params.input,
      ownerNodeId: params.ownerNodeId,
      workspace: params.session.workspace,
      ...(params.session.hostAuthorization ? { authorization: params.session.hostAuthorization } : {}),
      signal: params.session.signal
    })
  })
}

function hostedAgentRunRecord(node: RuntimeNode, outcome: HostedAgentOutcome): HarnessNodeRunRecord {
  return {
    nodeId: node.id,
    nodeType: node.type,
    label: textValue(node.data.label) ?? node.id,
    status: outcome.status,
    success: outcome.success,
    summary: outcome.summary,
    completionGaps: outcome.completionGaps,
    ...(outcome.result === undefined ? {} : { result: outcome.result })
  }
}

async function executeHostedHarness(params: {
  session: HostedExecutionSession
  harness: RuntimeNode
  args: Record<string, unknown>
  context: AgentRunContext
  agentRunId: string
}): Promise<string> {
  const { session, harness, args, context, agentRunId } = params
  if (context.callStack.includes(harness.id)) {
    return jsonResult({
      success: false,
      error: `Recursive Harness cycle rejected: ${[...context.callStack, harness.id].join(' -> ')}.`
    })
  }
  const internalNodes = session.workspace.nodes.filter((node) => canvasNodeBelongsToHarness(node, harness.id))
  const internalIds = new Set(internalNodes.map((node) => node.id))
  const internalEdges = session.workspace.edges.filter((edge) => internalIds.has(edge.source) && internalIds.has(edge.target))
  const template = isRecord(harness.data.template) ? harness.data.template : {}
  const manifest = isRecord(template.manifest) ? template.manifest : {}
  const declaredExposures = Array.isArray(manifest.exposures)
    ? manifest.exposures as HarnessExposure[]
    : []
  const exposures = deriveHarnessExposures(internalNodes, declaredExposures)
  const requestedExposureId = textValue(args.exposure_id)
    ?? textValue(harness.data.defaultExposureId)
  const exposure = requestedExposureId
    ? exposures.find((candidate) => candidate.id === requestedExposureId)
    : exposures.length === 1
      ? exposures[0]
      : undefined
  if (!exposure) {
    return jsonResult({
      success: false,
      error: exposures.length === 0
        ? `Harness exposes no executable capability: ${harness.id}.`
        : requestedExposureId
          ? `Harness exposure was not found: ${requestedExposureId}.`
          : `Harness exposes multiple capabilities; exposure_id is required.`,
      exposure_ids: exposures.map((candidate) => candidate.id)
    })
  }
  const input = isRecord(args.input)
    ? structuredClone(args.input)
    : textValue(args.task)
      ? { request: textValue(args.task) }
      : {}
  try {
    validateHostedExposureInvocation(session.release, exposure, input)
  } catch (error) {
    return jsonResult({
      success: false,
      harness_node_id: harness.id,
      exposure_id: exposure.id,
      error: error instanceof Error ? error.message : String(error)
    })
  }
  const harnessStack = [...context.callStack, harness.id]
  const result = await executeHarnessInvocation({
    harnessId: harness.id,
    targetNodeId: exposure.nodeId,
    exposureId: exposure.id,
    nodes: internalNodes,
    edges: internalEdges,
    existingRuntime: harness.data,
    signal: session.signal,
    host: {
      clock: session.clock,
      ids: session.ids,
      ...(session.events ? { events: session.events } : {}),
      async updateHarness(patch) {
        harness.data = { ...harness.data, ...structuredClone(patch) }
        await session.onWorkspaceChanged?.(session.workspace)
      },
      async executeNode({ invocationId, node, priorRuns }) {
        if (harnessStack.includes(node.id)) {
          throw new HostedRuntimeError(
            `Recursive node cycle rejected: ${[...harnessStack, node.id].join(' -> ')}.`,
            'recursive_node_cycle'
          )
        }
        if (node.type === 'agent') {
          const harnessGoal = textValue(input.request)
            ?? textValue(args.task)
            ?? textValue(harness.data.task)
            ?? textValue(harness.data.description)
            ?? textValue(harness.data.summary)
          const outcome = await runHostedAgent(session, node, {
            callDepth: context.callDepth + 1,
            parentRunId: invocationId,
            callStack: [...harnessStack, node.id],
            ...(harnessGoal ? { supplementalTask: harnessGoal } : {})
          })
          return hostedAgentRunRecord(node, outcome)
        }
        if (node.type === 'tool' && session.runtimeAdapter?.profile.runNodeTypes.includes('tool')) {
          const definition = packagedToolForNode(session, node)
          if (!definition) throw new Error(`Packaged Tool definition was not found: ${node.id}.`)
          const raw = await executePackagedTool({
            session,
            definition,
            args: input,
            ownerNodeId: context.callStack.at(-1) ?? agentRunId,
            callDepth: context.callDepth + 1,
            callStack: [...harnessStack, node.id]
          })
          const parsed = JSON.parse(raw) as Record<string, unknown>
          const success = parsed.success === true
          return {
            nodeId: node.id,
            nodeType: node.type,
            label: textValue(node.data.label) ?? node.id,
            status: success ? 'completed' : 'error',
            success,
            summary: success ? 'completed' : textValue(parsed.error) ?? 'Tool execution failed.',
            completionGaps: success ? [] : ['node_runtime_error'],
            ...(parsed.output === undefined ? {} : { result: parsed.output })
          }
        }
        throw new HostedRuntimeError(
          `Hosted Runtime cannot execute Harness node type ${node.type}.`,
          'missing_node_adapter',
          { nodeId: node.id, nodeType: node.type, priorRuns }
        )
      }
    }
  })
  return jsonResult({
    success: result.success,
    status: result.status,
    ...(result.result === undefined ? {} : { result: result.result }),
    ...(result.summary ? { summary: result.summary } : {}),
    ...(result.completionGaps.length > 0 ? { completion_gaps: result.completionGaps } : {}),
    ...(result.error ? { error: result.error } : {})
  })
}

function trackBackgroundRun(
  session: HostedExecutionSession,
  execution: Promise<void>
): void {
  session.backgroundRuns.add(execution)
  session.onBackgroundExecution?.(execution)
  void execution.then(
    () => session.backgroundRuns.delete(execution),
    () => session.backgroundRuns.delete(execution)
  )
}

function startHostedAgentNodeRun(params: {
  session: HostedExecutionSession
  target: RuntimeNode
  context: AgentRunContext
  agentRunId: string
  task: string
}): string {
  const { session, target, context, agentRunId, task } = params
  const parentJobId = context.callDepth === 0 ? session.runId : agentRunId
  startAgentHostNodeRun({
    session: session.controlSession,
    kind: 'agent',
    ownerNodeId: target.id,
    parentSignal: session.signal,
    status: 'starting',
    metadata: {
      runtime: 'agent',
      parentJobId,
      rootJobId: session.runId,
      calledByRunId: agentRunId
    },
    run: async ({ jobId: runId, controller, signal }) => {
      const childSession: HostedExecutionSession = {
        ...session,
        signal
      }
      const outcome = await runHostedAgent(childSession, target, {
        callDepth: context.callDepth + 1,
        parentRunId: agentRunId,
        callStack: [...context.callStack, target.id],
        supplementalTask: task
      }, { runId, controller })
      return outcome
    },
    finalize: (outcome) => {
      const status = outcome.status === 'done'
        ? 'done'
        : outcome.status === 'aborted'
          ? 'aborted'
          : 'blocked'
      return {
        status,
        result: {
          nodeId: target.id,
          status,
          summary: outcome.summary,
          completionGaps: outcome.completionGaps,
          ...(outcome.result === undefined ? {} : { output: outcome.result })
        },
        statusPayload: { nodeId: target.id, status, summary: outcome.summary }
      }
    },
    onError: async (error, { controller }) => {
      const aborted = controller.signal.aborted
      const message = aborted
        ? `Agent ${target.id} was aborted.`
        : error instanceof Error ? error.message : String(error)
      target.data = {
        ...target.data,
        status: aborted ? 'aborted' : 'error',
        summary: message,
        lastError: message,
        completionGaps: [aborted ? 'aborted' : 'runtime_error']
      }
      await session.onWorkspaceChanged?.(session.workspace)
      return {
        status: aborted ? 'aborted' : 'error',
        result: { nodeId: target.id, status: aborted ? 'aborted' : 'error', error: message },
        statusPayload: { nodeId: target.id, status: aborted ? 'aborted' : 'error', error: message }
      }
    },
    onExecution: (execution) => trackBackgroundRun(session, execution)
  })
  return asyncNodeRunToolResult({ targetId: target.id })
}

function startHostedHarnessNodeRun(params: {
  session: HostedExecutionSession
  target: RuntimeNode
  args: Record<string, unknown>
  context: AgentRunContext
  agentRunId: string
}): string {
  const { session, target, args, context, agentRunId } = params
  const parentJobId = context.callDepth === 0 ? session.runId : agentRunId
  startAgentHostNodeRun({
    session: session.controlSession,
    kind: 'harness',
    ownerNodeId: target.id,
    parentSignal: session.signal,
    status: 'starting',
    metadata: {
      runtime: 'harness',
      parentJobId,
      rootJobId: session.runId,
      calledByRunId: agentRunId,
      exposureId: textValue(args.exposure_id) ?? ''
    },
    run: async ({ jobId: runId, signal }) => {
      const childSession: HostedExecutionSession = {
        ...session,
        signal
      }
      const raw = await executeHostedHarness({
        session: childSession,
        harness: target,
        args,
        context,
        agentRunId: runId
      })
      return JSON.parse(raw) as Record<string, unknown>
    },
    finalize: (result) => {
      const status = result.status === 'aborted'
        ? 'aborted'
        : result.success === true
          ? 'done'
          : 'error'
      return {
        status,
        result: { nodeId: target.id, ...result },
        statusPayload: {
          nodeId: target.id,
          status,
          ...(typeof result.summary === 'string' ? { summary: result.summary } : {}),
          ...(typeof result.error === 'string' ? { error: result.error } : {})
        }
      }
    },
    onError: async (error, { controller }) => {
      const aborted = controller.signal.aborted
      const message = aborted
        ? `Harness ${target.id} was aborted.`
        : error instanceof Error ? error.message : String(error)
      target.data = {
        ...target.data,
        status: aborted ? 'aborted' : 'error',
        summary: message,
        lastError: message
      }
      await session.onWorkspaceChanged?.(session.workspace)
      return {
        status: aborted ? 'aborted' : 'error',
        result: { nodeId: target.id, status: aborted ? 'aborted' : 'error', error: message },
        statusPayload: { nodeId: target.id, status: aborted ? 'aborted' : 'error', error: message }
      }
    },
    onExecution: (execution) => trackBackgroundRun(session, execution)
  })
  return asyncNodeRunToolResult({ targetId: target.id })
}

type RoutedNodeAgentRequest =
  | { name: 'find'; request: FindNodesRequest }
  | { name: 'observe'; request: ObserveNodesRequest }
  | { name: 'edit'; args: Record<string, unknown> }
  | { name: 'use'; request: UseNodeRequest }

async function executeBuiltinTool(params: {
  name: string
  args: Record<string, unknown>
  session: HostedExecutionSession
  context: AgentRunContext
  agentRunId: string
  controller: AgentRunController
}, routedRequest?: RoutedNodeAgentRequest): Promise<string> {
  const { name, args, session, context, agentRunId, controller } = params
  throwIfAborted(session.signal)
  const ownerNodeId = context.callStack.at(-1) ?? ''

  if (!routedRequest) {
    const dispatched = await dispatchNodeAgentTool({
      name,
      args,
      context: undefined,
      handlers: {
        requestUserInput: (request) => requestAgentUserInput({
          controller,
          ownerNodeId,
          interactionId: session.ids.create('interaction'),
          signal: session.signal,
          ...request,
          noControllerError: 'This Hosted execution has no interactive Agent controller.',
          emit: (interaction) => emitHostedAgentEvent(
            session,
            agentRunId,
            context.parentRunId,
            'agent.interaction_requested',
            {
              nodeId: ownerNodeId,
              interactionId: interaction.interactionId,
              kind: interaction.kind,
              prompt: interaction.prompt,
              question: interaction.prompt,
              choices: interaction.choices,
              ...(interaction.responseSchema ? { responseSchema: interaction.responseSchema } : {}),
              ...(interaction.uiSchema ? { uiSchema: interaction.uiSchema } : {})
            }
          )
        }),
        findNodes: (request) => executeBuiltinTool(params, { name: 'find', request }),
        observeNodes: (request) => executeBuiltinTool(params, { name: 'observe', request }),
        createNodes: (createArgs) => executeAdapterNodeOperation({
          session,
          operation: 'create',
          args: createArgs,
          ownerNodeId
        }),
        editNodes: (editArgs) => executeBuiltinTool(params, { name: 'edit', args: editArgs }),
        useNode: (request) => executeBuiltinTool(params, { name: 'use', request })
      }
    })
    if (dispatched.handled) return dispatched.result
  }

  if (name === 'find') {
    if (routedRequest?.name !== 'find') {
      throw new HostedRuntimeError('find bypassed the node Agent dispatcher.', 'invalid_node_agent_route')
    }
    const request = routedRequest.request
    const listResult = request.scope === 'root'
      ? createPortableListNodesResult(
          session.workspace.nodes.filter((node) => !node.parentId && !textValue(node.data.harnessNodeId)),
          session.workspace.edges,
          {}
        )
      : createPortableListNodesResult(session.workspace.nodes, session.workspace.edges, {
          ownerNodeId,
          ...(request.scope === 'harness' && request.harnessNodeId
            ? { harnessNodeId: request.harnessNodeId }
            : {})
        })
    if (!listResult.success) return JSON.stringify(listResult)
    const visibleIds = new Set(listResult.nodes.map((node) => node.id))
    const relationIds = request.relationTo
      ? new Set(session.workspace.edges.flatMap((edge) => {
          if (edge.source === request.relationTo) return [edge.target]
          if (edge.target === request.relationTo) return [edge.source]
          return []
        }))
      : undefined
    const canvasEntries = session.workspace.nodes
      .filter((node) => visibleIds.has(node.id))
      .map((node) => {
        const capabilities = getNodeCapabilityDescriptors(node)
        const status = textValue(node.data.status) ?? 'idle'
        return {
          id: node.id,
          type: node.type,
          label: textValue(node.data.label) ?? node.id,
          description: textValue(node.data.description) ?? textValue(node.data.summary) ?? '',
          status,
          ...(node.parentId ? { parent_id: node.parentId } : {}),
          capabilities: capabilities.map((item) => item.id)
        }
      })
    const availableRuntimeNodes = runtimeManagedNodesForCapabilities(
      session.runtimeAdapter?.profile.capabilityIds ?? []
    )
    const runtimeEntries = availableRuntimeNodes.map((node) => ({
      ...node,
      capabilities: node.capabilities.map((item) => item.id)
    }))
    const query = request.query?.toLocaleLowerCase()
    const matches = [...canvasEntries, ...runtimeEntries]
      .filter((node) => request.types.length === 0 || request.types.includes(node.type))
      .filter((node) => request.statuses.length === 0 || request.statuses.includes(node.status))
      .filter((node) => request.capabilities.every((capability) => node.capabilities.includes(capability)))
      .filter((node) => !relationIds || relationIds.has(node.id))
      .filter((node) => !query || [node.id, node.type, node.label, node.description, ...node.capabilities]
        .join(' ').toLocaleLowerCase().includes(query))
    const offset = request.cursor && /^\d+$/.test(request.cursor) ? Number(request.cursor) : 0
    const nodes = matches.slice(offset, offset + request.limit)
    const nextOffset = offset + nodes.length
    const returnedIds = new Set(nodes.map((node) => node.id))
    return JSON.stringify({
      success: true,
      nodes,
      relations: session.workspace.edges
        .filter((edge) => (
          returnedIds.has(edge.source) && returnedIds.has(edge.target)
        ) || (
          request.relationTo !== undefined
          && ((returnedIds.has(edge.source) && edge.target === request.relationTo)
            || (returnedIds.has(edge.target) && edge.source === request.relationTo))
        ))
        .map((edge) => ({
          edge_id: edge.id,
          node_ids: [edge.source, edge.target],
          relation: edge.relation
        })),
      ...(nextOffset < matches.length ? { next_cursor: String(nextOffset) } : {})
    })
  }

  if (name === 'observe') {
    if (routedRequest?.name !== 'observe') {
      throw new HostedRuntimeError('observe bypassed the node Agent dispatcher.', 'invalid_node_agent_route')
    }
    const results: PortableObservationResult[] = []
    for (let requestIndex = 0; requestIndex < routedRequest.request.requests.length; requestIndex += 1) {
      const request = routedRequest.request.requests[requestIndex]!
      const nodeId = request.nodeId
      const runtimeNode = runtimeManagedNodesForCapabilities(
        session.runtimeAdapter?.profile.capabilityIds ?? []
      ).find((candidate) => candidate.id === nodeId)
      if (runtimeNode) {
        const contracts = runtimeNode.capabilities
          .map((item) => getRuntimeCapabilityContract(runtimeNode.id, item.id))
          .filter((item): item is NonNullable<typeof item> => Boolean(item))
          .filter((item) => session.runtimeAdapter?.profile.capabilityIds.includes(item.id) === true)
        results.push({
          node_id: runtimeNode.id,
          type: runtimeNode.type,
          values: {
            label: runtimeNode.label,
            description: runtimeNode.description,
            status: runtimeNode.status,
            capabilities: contracts.map((item) => ({
              id: item.id,
              contract_ref: `${item.id}@${item.version}#${item.schema_hash}`,
              summary: item.summary
            })),
            ...(request.includeContracts ? { capability_contracts: contracts } : {})
          }
        })
        continue
      }
      const target = session.workspace.nodes.find((candidate) => candidate.id === nodeId)
      if (!target) {
        results.push(portableObservationResultFailure(
          requestIndex,
          portableNodeNotFoundIssue(nodeId),
          nodeId
        ))
        continue
      }
      try {
        if (target.type === 'browser' && session.runtimeAdapter?.observeNode) {
          const observed = await session.runtimeAdapter.observeNode({
            runId: session.runId,
            node: target,
            ownerNodeId,
            workspace: session.workspace,
            signal: session.signal
          })
          if (observed.data) {
            target.data = { ...target.data, ...structuredClone(observed.data) }
            await session.onWorkspaceChanged?.(session.workspace)
          }
          if (observed.error) {
            results.push(portableObservationResultFailure(
              requestIndex,
              portableGraphIssue('observation_failed', observed.error, { node_id: nodeId }),
              nodeId
            ))
            continue
          }
        }
        if (target.type === 'image' && session.runtimeAdapter?.resolveImageInput) {
          const resolved = await session.runtimeAdapter.resolveImageInput({
            node: target,
            signal: session.signal
          })
          if (!resolved) {
            results.push(portableObservationResultFailure(
              requestIndex,
              portableGraphIssue('observation_failed', `Image node has no resolvable input: ${nodeId}.`, { node_id: nodeId }),
              nodeId
            ))
            continue
          }
          const pending = session.pendingImageInputs.get(ownerNodeId) ?? []
          pending.push({
            nodeId,
            label: textValue(target.data.label) ?? nodeId,
            source: textValue(target.data.src) ?? textValue(target.data.path) ?? textValue(target.data.url) ?? '',
            url: resolved.url,
            detail: resolved.detail ?? 'auto'
          })
          session.pendingImageInputs.set(ownerNodeId, pending)
        }
      } catch (error) {
        results.push(portableObservationResultFailure(
          requestIndex,
          portableGraphIssue(
            'observation_failed',
            error instanceof Error ? error.message : String(error),
            { node_id: nodeId }
          ),
          nodeId
        ))
        continue
      }
      const observation = buildPortableNodeObservation(target, {
        nodeId: request.nodeId,
        detail: request.detail,
        fields: request.fields ?? [],
        ...(request.startLine !== undefined ? { startLine: request.startLine } : {}),
        ...(request.endLine !== undefined ? { endLine: request.endLine } : {})
      })
      if (!observation.ok) {
        results.push(portableObservationResultFailure(requestIndex, observation.issue, nodeId))
        continue
      }
      const capabilities = getNodeCapabilityContracts(target)
      results.push({
        ...createPortableObservationSuccess(observation.observation),
        values: {
          ...(observation.observation.values ?? {}),
          relations: session.workspace.edges.flatMap((edge) => {
            if (edge.source === target.id) return [{ edge_id: edge.id, node_id: edge.target, relation: edge.relation }]
            if (edge.target === target.id) return [{ edge_id: edge.id, node_id: edge.source, relation: edge.relation }]
            return []
          }),
          capabilities: getNodeCapabilityDescriptors(target),
          ...(request.includeContracts ? {
            capability_contracts: capabilities,
            ...(editFieldsSchemaForType(target.type)
              ? { edit_schema: editFieldsSchemaForType(target.type) }
              : {})
          } : {})
        }
      })
    }
    return JSON.stringify(createPortableObservationEnvelope(results))
  }

  if (name === 'edit') {
    if (routedRequest?.name !== 'edit') {
      throw new HostedRuntimeError('edit bypassed the node Agent dispatcher.', 'invalid_node_agent_route')
    }
    return executeAdapterNodeOperation({
      session,
      operation: 'edit',
      args: routedRequest.args,
      ownerNodeId
    })
  }

  if (name === 'use') {
    if (routedRequest?.name !== 'use') {
      throw new HostedRuntimeError('use bypassed the node Agent dispatcher.', 'invalid_node_agent_route')
    }
    const request = routedRequest.request
    const runtimeNode = runtimeManagedNodesForCapabilities(
      session.runtimeAdapter?.profile.capabilityIds ?? []
    ).find((candidate) => candidate.id === request.nodeId
      && candidate.capabilities.some((capability) => capability.id === request.capability))
    const runtimeContract = runtimeNode
      ? getRuntimeCapabilityContract(request.nodeId, request.capability)
      : undefined
    const target = session.workspace.nodes.find((candidate) => candidate.id === request.nodeId)
    const capabilityContract = runtimeContract ?? (target
      ? getNodeCapabilityContract(target, request.capability)
      : undefined)
    if (!capabilityContract) {
      return jsonResult({
        success: false,
        error: `Node ${request.nodeId} does not declare capability ${request.capability}.`
      })
    }
    const inputValidation = validateNodeCapabilityInput(capabilityContract, request.input)
    if (!inputValidation.ok) {
      return jsonResult({
        success: false,
        error: inputValidation.error,
        details: inputValidation.details
      })
    }
    if (request.capability === 'run.wait' || request.capability === 'run.cancel') {
      return session.controlSession.controlNodeRun({
        action: request.capability === 'run.wait' ? 'wait' : 'cancel',
        node_id: request.nodeId
      }, session.signal, ownerNodeId)
    }
    if (runtimeContract) {
      return executeAdapterNodeCapability({
        session,
        nodeId: request.nodeId,
        capability: request.capability,
        input: request.input,
        ownerNodeId
      })
    }
    if (!target) return jsonResult({ success: false, error: `Node was not found: ${request.nodeId}.` })
    if (request.capability !== 'agent.run'
      && request.capability !== 'harness.run'
      && request.capability !== 'tool.invoke') {
      return executeAdapterNodeCapability({
        session,
        nodeId: request.nodeId,
        capability: request.capability,
        input: request.input,
        ownerNodeId
      })
    }
    return dispatchAgentHostNodeRun({
      session: session.controlSession,
      args: {
        node_id: request.nodeId,
        ...(textValue(request.input.task) ? { task: textValue(request.input.task) } : {}),
        ...(textValue(request.input.exposure_id) ? { exposure_id: textValue(request.input.exposure_id) } : {}),
        input: isRecord(request.input.input) ? request.input.input : request.input
      },
      requesterNodeId: ownerNodeId,
      callDepth: context.callDepth,
      callStack: context.callStack,
      findNode: (nodeId) => session.workspace.nodes.find((candidate) => candidate.id === nodeId),
      runAgent: (target, task) => startHostedAgentNodeRun({
        session,
        target,
        context,
        agentRunId,
        task
      }),
      runHarness: (target) => startHostedHarnessNodeRun({
        session,
        target,
        args: request.input,
        context,
        agentRunId
      }),
      runOther: async (target) => {
        if (target.type !== 'tool' || !session.runtimeAdapter?.profile.runNodeTypes.includes('tool')) {
          return undefined
        }
        const definition = packagedToolForNode(session, target)
        if (!definition) {
          return jsonResult({ success: false, error: `Packaged Tool definition was not found: ${target.id}.` })
        }
        return executePackagedTool({
          session,
          definition,
          args: request.input,
          ownerNodeId,
          callStack: [target.id]
        })
      }
    })
  }

  return jsonResult({ success: false, error: `Tool is not registered in Linux Hosted Runtime: ${name}.` })
}

async function runHostedAgent(
  session: HostedExecutionSession,
  node: RuntimeNode,
  context: AgentRunContext,
  prepared?: PreparedHostedAgentRun
): Promise<HostedAgentOutcome> {
  throwIfAborted(session.signal)
  const defaultModel = textValue(session.defaultModel) ?? ''
  if (node.data.model === undefined && !defaultModel) {
    throw new HostedRuntimeError(`No model is configured for Agent ${node.id}.`, 'missing_model', { nodeId: node.id })
  }
  const availableNames = availableSessionToolNames(session)
  const runtimeConfiguration = resolveHostedAgentRuntimeConfiguration(
    node,
    defaultModel,
    false,
    session.runtimeAdapter?.profile,
    availableNames
  )
  const { model, maxTokens } = runtimeConfiguration
  const configuredToolNames = currentHostedAutomaticToolNames(
    node.data.toolNames,
    session.runtimeAdapter?.profile,
    runtimeConfiguration.toolNames,
    availableNames
  )
  const toolNames = configuredToolNames
  session.usedModels.add(model)

  const agentRunId = prepared?.runId ?? session.ids.create('agent')
  let traceIteration = 1
  let traceModelCallId = randomUUID()
  const controller = prepared?.controller ?? (
    context.callDepth === 0 && session.controller
      ? session.controller
      : new AgentRunController({ signal: session.signal })
  )
  const connected = connectedNodes(session.workspace, node)
  const nodeSystemPrompt = textValue(node.data.systemPrompt)
  const task = textValue(node.data.task) ?? textValue(node.data.prompt) ?? session.release.manifest.description
  const accountWorkspaceInstructionFor = (names: readonly string[]) => names.includes('create')
    ? 'This run has a mutable, authorized Harness workspace. Canvas mutations affect only this workspace, never the published release or administrator canvas.'
    : ''
  const accountWorkspaceInstruction = accountWorkspaceInstructionFor(toolNames)
  const imageInputs = await connectedImageInputs(session, connected)
  const rootConversation = context.callDepth === 0 ? session.conversation : undefined
  const initialCanvasScope = selectPortableGraphScope(session.workspace.nodes, session.workspace.edges, node.id)
  const initialCanvasObservation = captureCanvasAgentObservation(
    initialCanvasScope.nodes,
    initialCanvasScope.edges,
    node.id
  )
  const canvasChangeContext = buildCanvasAgentChangeContextSystemSection(
    rootConversation?.canvasObservation,
    initialCanvasObservation
  )
  const runtimeNodeContext = buildRuntimeNodesSystemSection(
    runtimeManagedNodesForCapabilities(session.runtimeAdapter?.profile.capabilityIds ?? [])
  )
  const runtimeSystemContextSections = [runtimeNodeContext, canvasChangeContext]
    .filter((section): section is string => Boolean(section))
  const requestText = rootConversation?.currentRequest
    ?? (context.callDepth === 0 && context.supplementalTask
      ? context.supplementalTask
      : [
          task,
          context.supplementalTask ? `Delegated task:\n${context.supplementalTask}` : ''
        ].filter(Boolean).join('\n\n'))
  const request: AgentMessage = {
    role: 'user',
    content: imageInputs.length > 0
      ? [{ type: 'text', text: requestText }, ...imageInputs]
      : requestText
  }
  const messages = assembleCanvasAgentSessionMessages({
    nodeSystemPrompt,
    hostPolicySections: [accountWorkspaceInstruction],
    canvasNodes: initialCanvasScope.nodes,
    connectedNodes: connected,
    history: rootConversation?.history,
    ...(runtimeSystemContextSections.length > 0 ? { runtimeSystemContextSections } : {}),
    repairHistory: true,
    request
  })
  await emitHostedAgentEvent(session, agentRunId, context.parentRunId, 'agent.started', {
    nodeId: node.id,
    callDepth: context.callDepth,
    model
  })
  if (context.callDepth === 0) {
    void emitModelTrace(session, {
      type: 'request.started',
      requestId: session.modelTraceRequestId,
      nodeId: node.id
    })
  }

  const runtimeOutcome = await executeAgentNodeRuntime({
    messages,
    ...(session.maxAgentIterations !== undefined ? { maxIterations: session.maxAgentIterations } : {}),
    controller,
    onContinuations: (continuations: readonly string[]) => emitHostedAgentEvent(
      session,
      agentRunId,
      context.parentRunId,
      'agent.continued',
      { nodeId: node.id, continuations: [...continuations] }
    ),
    signal: session.signal,
    beforeIteration: (iteration) => {
      traceIteration = iteration + 1
      traceModelCallId = randomUUID()
    },
    callModel: (currentMessages) => {
      const pendingImages = session.pendingImageInputs.get(node.id) ?? []
      session.pendingImageInputs.delete(node.id)
      const imageMessages: AgentMessage[] = pendingImages.map((image) => ({
        role: 'user',
        content: [
          {
            type: 'text',
            text: `Inspect this image from connected image node id="${image.nodeId}" name="${image.label}" source="${image.source}". Use your own visual understanding of the image. Do not use OCR unless the user explicitly asks for OCR.`
          },
          {
            type: 'image_url',
            image_url: { url: image.url, detail: image.detail }
          }
        ]
      }))
      const currentNode = session.workspace.nodes.find((candidate) => candidate.id === node.id) ?? node
      const currentAvailableNames = availableSessionToolNames(session)
      const currentRuntimeConfiguration = resolveHostedAgentRuntimeConfiguration(
        currentNode,
        defaultModel,
        false,
        session.runtimeAdapter?.profile,
        currentAvailableNames
      )
      const currentToolNames = currentHostedAutomaticToolNames(
        currentNode.data.toolNames,
        session.runtimeAdapter?.profile,
        currentRuntimeConfiguration.toolNames,
        currentAvailableNames
      )
      const currentTools = toolDefinitions(session, currentToolNames)
      return session.models.complete({
        model,
        messages: [...currentMessages, ...imageMessages],
        tools: currentTools,
        toolChoice: 'auto',
        ...(maxTokens !== undefined ? { maxTokens } : {}),
        trace: {
          requestId: session.modelTraceRequestId,
          modelCallId: traceModelCallId,
          iteration: traceIteration,
          nodeId: node.id
        },
        signal: session.signal
      })
    },
    onAssistantTurn: ({ content, toolCalls }) => emitHostedAgentEvent(session, agentRunId, context.parentRunId, 'agent.assistant_turn', {
      nodeId: node.id,
      content,
      toolCalls
    }),
    onToolCall: (toolCall, args) => {
      void emitModelTrace(session, {
        type: 'tool.started',
        requestId: session.modelTraceRequestId,
        modelCallId: traceModelCallId,
        iteration: traceIteration,
        nodeId: node.id,
        toolCallId: toolCall.id,
        toolName: toolCall.function.name,
        arguments: args
      })
    },
    dispatchTool: (toolCall, args) => executeBuiltinTool({
      name: toolCall.function.name,
      args,
      session,
      context,
      agentRunId,
      controller
    }),
    onToolResult: (toolCall, result) => {
      void emitModelTrace(session, {
        type: 'tool.completed',
        requestId: session.modelTraceRequestId,
        modelCallId: traceModelCallId,
        iteration: traceIteration,
        nodeId: node.id,
        toolCallId: toolCall.id,
        toolName: toolCall.function.name,
        result
      })
      return emitHostedAgentEvent(session, agentRunId, context.parentRunId, 'agent.tool_result', {
        nodeId: node.id,
        toolCallId: toolCall.id,
        toolName: toolCall.function.name,
        result
      })
    },
    onModelError: (error) => {
      if (error instanceof HostedRuntimeError) throw error
      const detail = error instanceof Error ? error.message : String(error)
      throw new HostedRuntimeError(
        `Model provider failed for Agent ${node.id}: ${detail}`,
        'model_provider_error',
        detail
      )
    }
  }).catch(async (error) => {
    if (context.callDepth === 0) {
      await emitModelTrace(session, {
        type: session.signal.aborted ? 'request.aborted' : 'request.failed',
        requestId: session.modelTraceRequestId,
        nodeId: node.id,
        result: createAgentTraceTranscript(messages),
        ...(!session.signal.aborted ? { errorCode: 'runtime_error' } : {})
      })
    }
    throw error
  })

  if (runtimeOutcome.status === 'aborted') {
    if (session.signal.aborted || context.callDepth === 0) {
      if (context.callDepth === 0) {
        await emitModelTrace(session, {
          type: 'request.aborted',
          requestId: session.modelTraceRequestId,
          nodeId: node.id,
          result: createAgentTraceTranscript(runtimeOutcome.session.messages)
        })
      }
      throw abortedError(session.signal)
    }
    const outcome: HostedAgentOutcome = {
      success: false,
      status: 'aborted',
      summary: `Agent ${node.id} was aborted.`,
      completionGaps: ['aborted'],
      model
    }
    const currentNode = session.workspace.nodes.find((candidate) => candidate.id === node.id) ?? node
    currentNode.data = {
      ...currentNode.data,
      status: outcome.status,
      summary: outcome.summary,
      completionGaps: outcome.completionGaps
    }
    await session.onWorkspaceChanged?.(session.workspace)
    await emitHostedAgentEvent(session, agentRunId, context.parentRunId, 'agent.aborted', {
      nodeId: node.id,
      summary: outcome.summary,
      completionGaps: outcome.completionGaps
    })
    return outcome
  }
  const outcome: HostedAgentOutcome = runtimeOutcome.completionGaps.includes('max_iterations')
    ? {
      success: false,
      status: runtimeOutcome.status,
      summary: `Agent ${node.id} reached the iteration limit.`,
      completionGaps: runtimeOutcome.completionGaps,
      model
    }
    : {
        success: runtimeOutcome.success,
        status: runtimeOutcome.status,
        summary: runtimeOutcome.summary ?? '',
        completionGaps: runtimeOutcome.completionGaps,
        model
      }

  const currentNode = session.workspace.nodes.find((candidate) => candidate.id === node.id) ?? node
  currentNode.data = {
    ...currentNode.data,
    status: outcome.status,
    summary: outcome.summary,
    completionGaps: outcome.completionGaps
  }
  await session.onWorkspaceChanged?.(session.workspace)
  await emitHostedAgentEvent(session, agentRunId, context.parentRunId, outcome.success ? 'agent.completed' : 'agent.blocked', {
    nodeId: node.id,
    summary: outcome.summary,
    completionGaps: outcome.completionGaps
  })
  if (context.callDepth === 0) {
    await emitModelTrace(session, {
      type: outcome.success ? 'request.completed' : 'request.failed',
      requestId: session.modelTraceRequestId,
      nodeId: node.id,
      result: createAgentTraceTranscript(runtimeOutcome.session.messages)
    })
  }
  return outcome
}

function workspaceResultChanges(
  initialWorkspace: Readonly<RuntimeWorkspace>,
  workspace: Readonly<RuntimeWorkspace>
): HostedWorkspaceChanges {
  const initialById = new Map(initialWorkspace.nodes.map((node) => [node.id, node]))
  const currentIds = new Set(workspace.nodes.map((node) => node.id))
  const created: HostedWorkspaceNodeResult[] = []
  const updated: HostedWorkspaceNodeResult[] = []
  for (const node of workspace.nodes) {
    if (!PASSIVE_NODE_TYPES.has(node.type)) continue
    const safeData = redactPortableGraphData(node.data, node.type)
    const initial = initialById.get(node.id)
    const initialData = initial ? redactPortableGraphData(initial.data, initial.type) : undefined
    if (initial
      && initialData
      && initial.type === node.type
      && JSON.stringify(initialData) === JSON.stringify(safeData)) continue
    const label = textValue(safeData.label) ?? node.id
    const description = textValue(safeData.description) ?? textValue(safeData.summary)
    const values = { ...safeData }
    delete values.label
    delete values.description
    const result: HostedWorkspaceNodeResult = {
      id: node.id,
      type: node.type,
      label,
      ...(description ? { description } : {}),
      values
    }
    if (initial) updated.push(result)
    else created.push(result)
  }
  const deleted = initialWorkspace.nodes
    .filter((node) => PASSIVE_NODE_TYPES.has(node.type) && !currentIds.has(node.id))
    .map((node) => node.id)
  return { created, updated, deleted }
}

export async function executeHostedRelease(options: ExecuteHostedReleaseOptions): Promise<HostedExecutionResult> {
  const inspected = inspectHostedRelease(options.release, undefined, options.runtimeAdapter?.profile)
  if (!inspected.compatibility.deployable) {
    throw new HostedRuntimeError(
      `Harness is not compatible with ${HOSTED_RUNTIME}.`,
      'unsupported_harness',
      inspected.compatibility
    )
  }
  let bound: ReturnType<typeof bindHarnessReleaseExposure>
  try {
    bound = bindHarnessReleaseExposure(inspected.release, options.exposureId)
  } catch (error) {
    if (error instanceof HarnessContractError) {
      throw new HostedRuntimeError(error.message, error.code, error.details)
    }
    throw error
  }
  const executableTarget = bound.release.graph.nodes.find((node) => node.id === bound.exposure.nodeId)
  if (!executableTarget || (
    executableTarget.type !== 'agent'
    && !(executableTarget.type === 'tool' && options.runtimeAdapter?.profile.runNodeTypes.includes('tool'))
  )) {
    throw new HostedRuntimeError(
      `Harness exposure "${bound.exposure.id}" is not executable on this host.`,
      'unsupported_exposure_target',
      { exposureId: bound.exposure.id, nodeId: bound.exposure.nodeId, nodeType: bound.exposure.nodeType }
    )
  }
  validateHostedExposureInvocation(bound.release, bound.exposure, options.input)

  const suppliedSignals = [options.signal, options.controller?.signal]
    .filter((signal): signal is AbortSignal => Boolean(signal))
  const baseSignal = suppliedSignals.length > 1
    ? AbortSignal.any(suppliedSignals)
    : suppliedSignals[0] ?? new AbortController().signal
  const timeoutMs = resolveHostedReleaseTimeoutMs(bound.release.runtime.timeoutMs)
  const signal = timeoutMs > 0
    ? AbortSignal.any([baseSignal, AbortSignal.timeout(timeoutMs)])
    : baseSignal
  throwIfAborted(signal)
  const workspace = instantiateHarnessWorkspace(
    bound.release,
    bound.exposure.id,
    options.workspace
  )
  const initialWorkspace = structuredClone(workspace)
  const clock = options.clock ?? createDefaultClock()
  const ids = options.ids ?? createDefaultIds()
  const ownedRuntimeJobs = options.controlSession
    ? undefined
    : new RuntimeJobRegistry()
  const controlSession = options.controlSession ?? new AgentHostRuntimeSession({
    sessionId: `hosted:${options.runId}`,
    jobs: ownedRuntimeJobs!,
    onIdle: () => undefined
  })
  const ownedRootController = ownedRuntimeJobs && executableTarget.type === 'agent'
    ? options.controller ?? new AgentRunController({ signal })
    : undefined
  const rootController = options.controller ?? ownedRootController
  const runtimeState: Record<string, unknown> = {}
  const session: HostedExecutionSession = {
    runId: options.runId,
    modelTraceRequestId: randomUUID(),
    release: bound.release,
    exposure: bound.exposure,
    workspace,
    models: createHostedModelProvider(options.complete),
    ...(options.events ? { events: options.events } : {}),
    clock,
    ids,
    signal,
    ...(rootController ? { controller: rootController } : {}),
    ...(options.conversation ? { conversation: options.conversation } : {}),
    ...(options.hostAuthorization ? { hostAuthorization: options.hostAuthorization } : {}),
    ...(options.defaultModel ? { defaultModel: options.defaultModel } : {}),
    ...(options.maxAgentIterations !== undefined
      ? { maxAgentIterations: positiveInteger(options.maxAgentIterations, 'maxAgentIterations') }
      : {}),
    usedModels: new Set<string>(),
    ...(options.runtimeAdapter ? { runtimeAdapter: options.runtimeAdapter } : {}),
    controlSession,
    backgroundRuns: new Set<Promise<void>>(),
    pendingImageInputs: new Map(),
    ...(options.onBackgroundExecution ? { onBackgroundExecution: options.onBackgroundExecution } : {}),
    ...(options.onWorkspaceChanged ? { onWorkspaceChanged: options.onWorkspaceChanged } : {})
  }
  if (ownedRootController) {
    controlSession.registerJob({
      jobId: options.runId,
      kind: 'agent',
      ownerNodeId: executableTarget.id,
      controller: ownedRootController,
      status: 'running',
      metadata: {
        runtime: 'agent',
        rootJobId: options.runId
      }
    })
  }
  let ownedRootStatus: 'done' | 'blocked' | 'error' | 'aborted' = 'error'
  try {
    let exposureOutcome: HostedAgentOutcome | undefined
    let executionError: unknown
    const harnessResult: HarnessExecutionResult = await executeHarnessInvocation({
      harnessId: inspected.release.manifest.id,
      targetNodeId: bound.exposure.nodeId,
      exposureId: bound.exposure.id,
      nodes: workspace.nodes,
      edges: workspace.edges,
      existingRuntime: runtimeState,
      signal,
      host: {
        clock,
        ids,
        ...(options.events ? { events: options.events } : {}),
        async updateHarness(patch) {
          Object.assign(runtimeState, patch)
        },
        async executeNode({ invocationId, node }) {
          try {
            if (node.type === 'tool' && options.runtimeAdapter?.profile.runNodeTypes.includes('tool')) {
              const definition = packagedToolForNode(session, node)
              if (!definition) {
                throw new HostedRuntimeError(
                  `Packaged Tool definition was not found for exposure "${bound.exposure.id}".`,
                  'missing_tool_adapter',
                  { exposureId: bound.exposure.id, nodeId: node.id }
                )
              }
              const raw = await executePackagedTool({
                session,
                definition,
                args: options.input,
                ownerNodeId: node.id,
                callDepth: 0,
                callStack: [node.id]
              })
              const parsed = JSON.parse(raw) as Record<string, unknown>
              const success = parsed.success === true
              exposureOutcome = {
                success,
                status: success ? 'done' : 'blocked',
                summary: success ? 'completed' : textValue(parsed.error) ?? 'Tool execution failed.',
                completionGaps: success ? [] : ['node_runtime_error'],
                ...(parsed.output === undefined ? {} : { result: parsed.output }),
                model: ''
              }
              return hostedAgentRunRecord(node, exposureOutcome)
            }
            if (node.type !== 'agent') {
              throw new HostedRuntimeError(
                `Linux Hosted Runtime cannot execute exposure target type ${node.type}.`,
                'missing_node_adapter',
                { exposureId: bound.exposure.id, nodeId: node.id, nodeType: node.type }
              )
            }
            exposureOutcome = await runHostedAgent(session, node, {
              callDepth: 0,
              parentRunId: invocationId,
              callStack: [node.id],
              supplementalTask: textValue(options.input.request)
            })
            const record: HarnessNodeRunRecord = {
              nodeId: node.id,
              nodeType: node.type,
              label: textValue(node.data.label) ?? node.id,
              status: exposureOutcome.status,
              success: exposureOutcome.success,
              summary: exposureOutcome.summary,
              completionGaps: exposureOutcome.completionGaps,
              ...(exposureOutcome.result === undefined ? {} : { result: exposureOutcome.result })
            }
            return record
          } catch (error) {
            executionError = error
            throw error
          }
        }
      }
    })
    throwIfAborted(signal)
    if (executionError) throw executionError
    if (!exposureOutcome) {
      throw new HostedRuntimeError(
        harnessResult.error ?? 'Harness exposure did not produce a result.',
        'hosted_execution_failed',
        harnessResult
      )
    }
    if (harnessResult.status === 'error') {
      throw new HostedRuntimeError(
        harnessResult.error ?? 'Harness execution failed.',
        'hosted_execution_failed',
        harnessResult
      )
    }
    if (exposureOutcome.status === 'aborted') {
      ownedRootStatus = 'aborted'
      throw new HostedRuntimeError('The Harness exposure run was aborted.', 'run_cancelled')
    }

    const models = [...session.usedModels]
    ownedRootStatus = exposureOutcome.status === 'done' ? 'done' : 'blocked'
    return {
      runtime: HOSTED_RUNTIME,
      exposureId: bound.exposure.id,
      status: exposureOutcome.status === 'done' ? 'completed' : 'blocked',
      model: exposureOutcome.model,
      models,
      summary: exposureOutcome.summary,
      ...(exposureOutcome.result === undefined ? {} : { result: exposureOutcome.result }),
      workspaceChanges: workspaceResultChanges(initialWorkspace, workspace),
      workspace: structuredClone(workspace),
      invocationId: harnessResult.invocationId
    }
  } catch (error) {
    if (signal.aborted || (error instanceof HostedRuntimeError && error.code === 'run_cancelled')) {
      ownedRootStatus = 'aborted'
    }
    throw error
  } finally {
    if (ownedRootController) {
      controlSession.finalizeJob(
        options.runId,
        ownedRootStatus,
        undefined,
        { nodeId: executableTarget.id, status: ownedRootStatus }
      )
    }
    const cleanup = async (): Promise<void> => {
      while (session.backgroundRuns.size > 0) {
        await Promise.allSettled([...session.backgroundRuns])
      }
      ownedRuntimeJobs?.dispose()
      await options.runtimeAdapter?.disposeRun?.(options.runId)
    }
    if (session.backgroundRuns.size > 0) {
      const cleanupExecution = cleanup().catch(() => undefined)
      options.onBackgroundExecution?.(cleanupExecution)
    } else {
      await cleanup()
    }
  }
}
