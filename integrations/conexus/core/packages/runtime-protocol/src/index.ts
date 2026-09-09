export type RuntimeKind =
  | 'agent'
  | 'harness'

export type RuntimeJobStatus =
  | 'idle'
  | 'starting'
  | 'running'
  | 'thinking'
  | 'waiting'
  | 'done'
  | 'blocked'
  | 'error'
  | 'aborted'

export type RuntimeJobTerminalStatus = Extract<
  RuntimeJobStatus,
  'done' | 'blocked' | 'error' | 'aborted'
>

export type RuntimeJobEventName =
  | 'created'
  | 'status'
  | 'message'
  | 'tool_call'
  | 'tool_result'
  | 'output'
  | 'result'
  | 'error'
  | 'health'

export type RuntimeToolCallEventPayload = Record<string, unknown> & {
  callId: string
  tool: string
  args?: unknown
}

export type RuntimeToolResultEventPayload = Record<string, unknown> & {
  callId: string
  tool: string
  result?: unknown
}

export type RuntimeJobEventPayload<TEvent extends RuntimeJobEventName> =
  TEvent extends 'tool_call'
    ? RuntimeToolCallEventPayload
    : TEvent extends 'tool_result'
      ? RuntimeToolResultEventPayload
      : Record<string, unknown>

export interface RuntimeJobEvent {
  eventId: string
  jobId: string
  sequence: number
  kind: RuntimeKind
  status: RuntimeJobStatus
  sessionId?: string | null
  metadata?: Record<string, unknown>
  event: RuntimeJobEventName
  payload?: Record<string, unknown>
  at: string
}

export type RuntimeHealthStatus =
  | 'healthy'
  | 'idle'
  | 'stuck'
  | 'needs_attention'

export interface RuntimeHealth {
  status: RuntimeHealthStatus
  reason: string
  idleMs: number
  currentTool?: string
  currentToolCallId?: string
  currentToolElapsedMs?: number
  checkedAt: string
}

export interface RuntimeJobEventCursor {
  firstRetainedSequence: number | null
  firstReturnedSequence: number | null
  lastSequence: number
  nextSequence: number
  retentionTruncated: boolean
}

export interface RuntimeJobSnapshot {
  id: string
  kind: RuntimeKind
  status: RuntimeJobStatus
  startedAt: string
  updatedAt: string
  sessionId?: string | null
  metadata?: Record<string, unknown>
  health?: RuntimeHealth
  events: RuntimeJobEvent[]
  eventCursor: RuntimeJobEventCursor
  canAbort: boolean
  canContinue: boolean
}

export interface AgentProtocolToolCall {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string
  }
}

export type CommandReceiptCode =
  | 'started'
  | 'aborted'
  | 'continued'
  | 'answered'
  | 'submitted'
  | 'invalid_request'
  | 'not_found'
  | 'already_running'
  | 'capacity_rejected'
  | 'control_rejected'
  | 'interaction_rejected'
  | 'persistence_conflict'
  | 'unsupported'
  | 'internal_error'

export interface CommandReceipt {
  accepted: boolean
  code: CommandReceiptCode
  message: string
}

export function createCommandReceipt(
  accepted: boolean,
  code: CommandReceiptCode,
  message: string
): CommandReceipt {
  return { accepted, code, message }
}

export class PlatformCommandError extends Error {
  constructor(
    message: string,
    readonly path?: string
  ) {
    super(message)
    this.name = 'PlatformCommandError'
  }
}

export interface AgentProtocolNode {
  id: string
  type?: string
  position?: { x: number; y: number }
  data: Record<string, unknown>
  parentId?: string
  extent?: string
  width?: number
  height?: number
  style?: Record<string, unknown>
}

export const MAX_SEMANTIC_EDGE_RELATION_LENGTH = 240

export interface SemanticEdgeFields {
  /** Complete natural-language statement about the unordered pair of endpoint nodes. */
  relation: string
}

export interface AgentProtocolEdge extends SemanticEdgeFields {
  id: string
  source: string
  target: string
  data?: Record<string, unknown>
}

export const CANVAS_AGENT_OBSERVATION_VERSION = 1 as const

export interface CanvasAgentObservedNode {
  id: string
  type: string
  name: string
  description: string
  fingerprint: string
  fieldFingerprints: Record<string, string>
}

export interface CanvasAgentObservedEdge {
  id: string
  source: string
  target: string
  relation: string
  fingerprint: string
}

/** Lightweight, safe baseline used to report Canvas changes once at the next run. */
export interface CanvasAgentObservation {
  version: typeof CANVAS_AGENT_OBSERVATION_VERSION
  ownerNodeId: string
  nodes: CanvasAgentObservedNode[]
  edges: CanvasAgentObservedEdge[]
}

export function canvasAgentObservationRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function isCanvasAgentObservation(value: unknown): value is CanvasAgentObservation {
  if (!canvasAgentObservationRecord(value)
    || value.version !== CANVAS_AGENT_OBSERVATION_VERSION
    || typeof value.ownerNodeId !== 'string'
    || !value.ownerNodeId.trim()
    || !Array.isArray(value.nodes)
    || !Array.isArray(value.edges)) return false
  const nodeIds = new Set<string>()
  for (const node of value.nodes) {
    if (!canvasAgentObservationRecord(node)
      || typeof node.id !== 'string'
      || !node.id.trim()
      || nodeIds.has(node.id)
      || typeof node.type !== 'string'
      || typeof node.name !== 'string'
      || typeof node.description !== 'string'
      || typeof node.fingerprint !== 'string'
      || !canvasAgentObservationRecord(node.fieldFingerprints)
      || Object.values(node.fieldFingerprints).some((fingerprint) => typeof fingerprint !== 'string')) return false
    nodeIds.add(node.id)
  }
  const edgeIds = new Set<string>()
  for (const edge of value.edges) {
    if (!canvasAgentObservationRecord(edge)
      || typeof edge.id !== 'string'
      || !edge.id.trim()
      || edgeIds.has(edge.id)
      || typeof edge.source !== 'string'
      || typeof edge.target !== 'string'
      || typeof edge.relation !== 'string'
      || typeof edge.fingerprint !== 'string') return false
    edgeIds.add(edge.id)
  }
  return true
}

export type AgentProtocolMessageContent =
  | string
  | Array<
      | { type: 'text'; text: string }
      | { type: 'image_url'; image_url: { url: string; detail?: 'auto' | 'low' | 'high' } }
    >
  | null

export interface AgentProtocolMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: AgentProtocolMessageContent
  tool_calls?: AgentProtocolToolCall[]
  tool_call_id?: string
  name?: string
}

/** Canonical persisted and rendered Agent transcript message. */
export interface AgentTranscriptMessage {
  role: 'user' | 'assistant' | 'tool' | 'thinking'
  content: string | null
  error?: boolean
  tool_calls?: AgentProtocolToolCall[]
  tool_call_id?: string
  name?: string
}

export interface AgentRuntimeScopeState {
  kind: 'root' | 'harness'
  scopeId: string
  status: string
  [key: string]: unknown
}

export interface AgentRunCommand {
  nodeId: string
  sessionId: string
  /** Durable node-history session. Distinct from the one-run runtime sessionId. */
  historySessionId?: string
  messages: AgentProtocolMessage[]
  task?: string
  browserNodeId?: string
  timeoutMs?: number
  projectPath?: string
  baseRevision?: string | null
  /** Canvas baseline last delivered to this durable conversation. */
  canvasObservation?: CanvasAgentObservation
  canvasState: {
    nodes: AgentProtocolNode[]
    edges: AgentProtocolEdge[]
    runtimeStateByScope?: Record<string, AgentRuntimeScopeState>
  }
}

export type ParsedAgentRunCommand = Omit<AgentRunCommand, 'task'> & { task: string }

export type TextOnlyAgentProtocolMessage = Omit<AgentProtocolMessage, 'content'> & {
  content: string | null
}

export type TextOnlyAgentRunCommand = Omit<ParsedAgentRunCommand, 'messages'> & {
  messages: TextOnlyAgentProtocolMessage[]
}

export type NodeHistoryRunStatus =
  | 'queued'
  | 'starting'
  | 'running'
  | 'thinking'
  | 'waiting'
  | 'done'
  | 'blocked'
  | 'error'
  | 'aborted'

export type NodeHistoryVisibility = 'public' | 'internal'

export const PROJECT_NODE_HISTORY_WORKSPACE_ID = 'project' as const

export interface NodeHistoryMessage extends AgentTranscriptMessage {
  id: string
  workspaceId: string
  nodeId: string
  sessionId: string
  runId?: string
  conversationId?: string
  visibility: NodeHistoryVisibility
  createdAt: string
}

export interface NodeHistorySessionSummary {
  id: string
  workspaceId: string
  nodeId: string
  nodeType: string
  title: string
  createdAt: string
  updatedAt: string
  messageCount: number
  lastRunId?: string
  conversationId?: string
  subjectUserId?: string
  exposureId?: string
  releaseChecksum?: string
}

export interface NodeHistorySession extends NodeHistorySessionSummary {
  canvasObservation?: CanvasAgentObservation
  messages: NodeHistoryMessage[]
}

export interface NodeHistoryRun {
  id: string
  workspaceId: string
  nodeId: string
  nodeType: string
  rootRunId: string
  parentRunId?: string
  sessionId?: string
  conversationId?: string
  status: NodeHistoryRunStatus
  startedAt: string
  updatedAt: string
  completedAt?: string
  definitionChecksum?: string
  input?: unknown
  output?: unknown
  summary?: string
  error?: string
}

export interface NodeHistoryRevision {
  id: string
  workspaceId: string
  nodeId: string
  nodeType: string
  kind: 'content' | 'state' | 'asset'
  checksum: string
  createdAt: string
  parentRevisionId?: string
  runId?: string
  value: unknown
}

export interface NodeHistorySnapshot {
  workspaceId: string
  nodeId: string
  sessions: NodeHistorySessionSummary[]
  runs: NodeHistoryRun[]
  revisions: NodeHistoryRevision[]
}

export interface CreateNodeHistorySessionCommand {
  workspaceId: string
  nodeId: string
  nodeType: string
  title?: string
}

export interface SaveNodeHistorySessionCommand {
  workspaceId: string
  nodeId: string
  nodeType: string
  sessionId: string
  title?: string
  conversationId?: string
  subjectUserId?: string
  exposureId?: string
  releaseChecksum?: string
  canvasObservation?: CanvasAgentObservation
  messages: AgentTranscriptMessage[]
  run?: {
    id: string
    rootRunId?: string
    parentRunId?: string
    status: NodeHistoryRunStatus
    startedAt?: string
    completedAt?: string
    definitionChecksum?: string
    input?: unknown
    output?: unknown
    summary?: string
    error?: string
  }
}

export interface SaveNodeHistoryRunCommand {
  id: string
  workspaceId: string
  nodeId: string
  nodeType: string
  rootRunId?: string
  parentRunId?: string
  sessionId?: string
  conversationId?: string
  status: NodeHistoryRunStatus
  startedAt?: string
  completedAt?: string
  definitionChecksum?: string
  input?: unknown
  output?: unknown
  summary?: string
  error?: string
}

export interface AgentUserInputResponseCommand {
  sessionId: string
  interactionId: string
  value: unknown
}

export interface RuntimeAbortCommand {
  id: string
}

export interface RuntimeContinueCommand {
  id: string
  input: unknown
}

export function commandRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function commandText(value: unknown, label: string, path: string, maximum = 240): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new PlatformCommandError(`${label} is required.`, path)
  }
  const normalized = value.trim()
  if (normalized.length > maximum) {
    throw new PlatformCommandError(`${label} must not exceed ${maximum} characters.`, path)
  }
  return normalized
}

export function parseRuntimeAbortCommand(value: unknown): RuntimeAbortCommand {
  if (!commandRecord(value)) throw new PlatformCommandError('A runtime abort payload is required.')
  return { id: commandText(value.id, 'Runtime job id', 'id') }
}

export function parseRuntimeContinueCommand(value: unknown): RuntimeContinueCommand {
  if (!commandRecord(value)) throw new PlatformCommandError('A runtime continuation payload is required.')
  if (!Object.prototype.hasOwnProperty.call(value, 'input')) {
    throw new PlatformCommandError('Runtime continuation input is required.', 'input')
  }
  return { id: commandText(value.id, 'Runtime job id', 'id'), input: value.input }
}

export function parseAgentUserInputResponseCommand(value: unknown): AgentUserInputResponseCommand {
  if (!commandRecord(value)) throw new PlatformCommandError('An Agent user input response payload is required.')
  if (!Object.prototype.hasOwnProperty.call(value, 'value')) {
    throw new PlatformCommandError('Agent user input response value is required.', 'value')
  }
  return {
    sessionId: commandText(value.sessionId, 'Agent session id', 'sessionId'),
    interactionId: commandText(value.interactionId, 'Agent interaction id', 'interactionId'),
    value: structuredClone(value.value)
  }
}
