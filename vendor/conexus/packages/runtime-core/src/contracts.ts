import type { AgentLLMResult, AgentMessage } from './agent/agent-loop.js'
import type { SemanticEdgeFields } from '@conexus/runtime-protocol'

export interface RuntimeNode {
  id: string
  type: string
  data: Record<string, unknown>
  parentId?: string
}

export interface RuntimeEdge extends SemanticEdgeFields {
  id: string
  source: string
  target: string
  data?: Record<string, unknown>
}

export interface RuntimeWorkspace {
  nodes: RuntimeNode[]
  edges: RuntimeEdge[]
}

export interface RuntimeToolSchema {
  type: 'function'
  function: {
    name: string
    description: string
    parameters: Record<string, unknown>
  }
}

export interface ModelTraceContext {
  requestId: string
  modelCallId: string
  iteration: number
  nodeId?: string
}

export type ModelTraceEvent =
  | { type: 'request.started'; requestId: string; nodeId?: string; at?: string }
  | { type: 'request.completed' | 'request.failed' | 'request.aborted'; requestId: string; nodeId?: string; result?: unknown; errorCode?: string; at?: string }
  | { type: 'tool.started'; requestId: string; modelCallId?: string; iteration?: number; nodeId?: string; toolCallId: string; toolName: string; arguments?: unknown; at?: string }
  | { type: 'tool.completed' | 'tool.failed'; requestId: string; modelCallId?: string; iteration?: number; nodeId?: string; toolCallId: string; toolName: string; result?: unknown; errorCode?: string; at?: string }

export interface ModelCompletionRequest {
  model?: string
  messages: AgentMessage[]
  tools?: RuntimeToolSchema[]
  toolChoice?: 'auto' | 'none'
  maxTokens?: number
  trace?: ModelTraceContext
  signal?: AbortSignal
}

export interface ModelProvider {
  complete(request: ModelCompletionRequest): Promise<AgentLLMResult>
  trace?(event: ModelTraceEvent): void | Promise<void>
}

export interface RuntimeToolContext {
  runId: string
  parentRunId?: string
  workspace: RuntimeWorkspace
  signal: AbortSignal
  callDepth: number
}

export interface RuntimeToolAdapter {
  definition: RuntimeToolSchema
  execute(args: Record<string, unknown>, context: RuntimeToolContext): Promise<string>
}

export type RuntimeStatus =
  | 'queued'
  | 'running'
  | 'waiting'
  | 'completed'
  | 'blocked'
  | 'failed'
  | 'cancelled'
  | 'aborted'

export type RuntimeEvent = {
  runId: string
  parentRunId?: string
  sequence?: number
  at: string
  type: string
  payload: Record<string, unknown>
}

export interface RuntimeEventSink {
  emit(event: RuntimeEvent): void | Promise<void>
}

export interface RuntimeClock {
  now(): string
}

export interface RuntimeIdGenerator {
  create(prefix: string): string
}

export interface RuntimeHost {
  models: ModelProvider
  tools: ReadonlyMap<string, RuntimeToolAdapter>
  events: RuntimeEventSink
  clock: RuntimeClock
  ids: RuntimeIdGenerator
}

export interface SubharnessTemplateNode {
  id: string
  type: string
  position?: { x: number; y: number }
  data: Record<string, unknown>
  parentId?: string
  extent?: string
  width?: number
  height?: number
  style?: Record<string, unknown>
}

export interface SubharnessTemplateEdge extends RuntimeEdge {}

export interface SubharnessExample {
  userRequest: string
  suggestedInputs?: Record<string, unknown>
  expectedResult?: string
}

export interface SubharnessRuntimePolicy {
  autoStart?: boolean
  maxAttempts?: number
  allowParallel?: boolean
}

export type HarnessPackageSchema = 'conexus.harness'

export interface HarnessDependency {
  kind: 'tool' | 'runtime' | 'model' | 'command' | 'package' | 'subharness'
  name: string
  version?: string
  optional?: boolean
}

export interface HarnessPermission {
  kind: 'filesystem' | 'network' | 'shell' | 'secret' | 'browser' | 'model'
  scope?: string
  reason?: string
  required?: boolean
}

export type HarnessExposureSurface = 'agent_tool' | 'page' | 'api'

export interface HarnessExposure {
  id: string
  name: string
  description?: string
  nodeId: string
  nodeType: string
  surfaces: HarnessExposureSurface[]
}

export interface PublicHarnessExposure {
  id: string
  name: string
  description?: string
  nodeType: string
  surfaces: HarnessExposureSurface[]
}

export interface HarnessManifest {
  id: string
  name: string
  summary?: string
  description: string
  version: number
  exposures: HarnessExposure[]
  defaultExposureId?: string
  permissions: HarnessPermission[]
  dependencies: HarnessDependency[]
  capabilities: string[]
  triggers: string[]
  tags: string[]
  author?: string
}

export interface HarnessGraph {
  nodes: SubharnessTemplateNode[]
  edges: SubharnessTemplateEdge[]
}

export interface HarnessToolDefinition {
  name: string
  description?: string
  runtime?: 'node' | 'python' | 'powershell' | 'bash' | 'shell' | 'registered' | 'mcp'
  code?: string
  inputSchema?: Record<string, unknown>
  outputSchema?: Record<string, unknown>
  permissions?: HarnessPermission[]
  exposeAsTool?: boolean
  nodeId?: string
}

export interface HarnessSchemas {
  definitions?: Record<string, Record<string, unknown>>
}

export interface HarnessRuntime {
  policy?: SubharnessRuntimePolicy
  modelPolicy?: Record<string, unknown>
  toolPolicy?: Record<string, unknown>
  timeoutMs?: number
  retryPolicy?: Record<string, unknown>
  isolation?: 'none' | 'shell' | 'sandbox'
}

export interface HarnessVerification {
  successCriteria?: string[]
  checks?: Array<Record<string, unknown>>
}

export interface SubharnessLibraryEntry {
  schema: HarnessPackageSchema
  id: string
  name: string
  summary?: string
  description: string
  version?: number
  whenToUse?: string[]
  whenNotToUse?: string[]
  examples?: SubharnessExample[]
  capabilities?: string[]
  runtimePolicy?: SubharnessRuntimePolicy
  triggers?: string[]
  completionCriteria?: string[]
  failureModes?: string[]
  toolNames?: string[]
  internalNodes: SubharnessTemplateNode[]
  internalEdges: SubharnessTemplateEdge[]
  manifest: HarnessManifest
  graph: HarnessGraph
  tools?: HarnessToolDefinition[]
  schemas?: HarnessSchemas
  runtime?: HarnessRuntime
  verification?: HarnessVerification
  tags: string[]
  createdAt: string
  updatedAt: string
}
