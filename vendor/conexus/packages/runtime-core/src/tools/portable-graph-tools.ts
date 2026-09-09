import type { SemanticEdgeFields } from '@conexus/runtime-protocol'
import {
  getNodeValuesFromData,
  editFieldsSchemaForType,
  validateEditFieldsForType
} from '../canvas/node-catalog.js'

const PORTABLE_OBSERVATION_LIMIT = 20
const PORTABLE_OBSERVATION_FIELD_LIMIT = 64
const PORTABLE_EDIT_LIMIT = 50
const MAX_PORTABLE_VALUE_DEPTH = 16
const MAX_PORTABLE_OBJECT_KEYS = 256
const MAX_PORTABLE_ARRAY_ITEMS = 1_000

const PORTABLE_SENSITIVE_KEY = /(?:api[_-]?key|authorization|credential|password|private[_-]?key|secret|token)/i
const PORTABLE_PUBLIC_TOKEN_KEYS = new Set(['maxtokens'])
const PORTABLE_UNSAFE_KEYS = new Set(['__proto__', 'prototype', 'constructor'])
const PORTABLE_HIDDEN_OBSERVATION_KEYS = new Set([
  'backingpath',
  'createdbyagentnodeid',
  'historysessionid'
])
const PORTABLE_HIDDEN_AGENT_OBSERVATION_KEYS = new Set([
  'task',
  'prompt',
  'tools',
  'messages',
  'runtime',
  'waitingfornodeid',
  'waitingforjobid'
])
const PORTABLE_PROTECTED_AGENT_UPDATE_KEYS = new Set([
  'task',
  'prompt',
  'tools',
  'messages',
  'runtime',
  'waitingfornodeid',
  'waitingforjobid',
  'status',
  'summary',
  'completiongaps',
  'toolcounts',
  'lasterror'
])
const EMBEDDED_URL_CREDENTIALS = /^[a-z][a-z0-9+.-]*:\/\/[^/@\s]+:[^/@\s]*@/i

export interface PortableGraphNode {
  id: string
  type?: string
  parentId?: string
  data?: Record<string, unknown>
}

export interface PortableToolNameRename {
  nodeId: string
  parentId?: string
  previousName: string
  nextName: string
}

function portableToolName(node: PortableGraphNode): string | undefined {
  if (node.type !== 'tool') return undefined
  for (const value of [node.data?.toolName, node.data?.name, node.data?.label]) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return undefined
}

export function portableToolNameRename(
  previous: PortableGraphNode,
  nextData: Record<string, unknown> | undefined
): PortableToolNameRename | undefined {
  if (previous.type !== 'tool' || !nextData) return undefined
  const previousName = portableToolName(previous)
  const nextName = portableToolName({
    ...previous,
    data: { ...(previous.data ?? {}), ...nextData }
  })
  if (!previousName || !nextName || previousName === nextName) return undefined
  return {
    nodeId: previous.id,
    ...(previous.parentId ? { parentId: previous.parentId } : {}),
    previousName,
    nextName
  }
}

export function applyPortableToolNameRenames<TNode extends PortableGraphNode>(
  nodes: readonly TNode[],
  renames: readonly PortableToolNameRename[]
): { nodes: TNode[]; updatedAgentNodeIds: string[] } {
  if (renames.length === 0) return { nodes: [...nodes], updatedAgentNodeIds: [] }
  const renamesByScope = new Map<string, Map<string, string>>()
  for (const rename of renames) {
    const scope = rename.parentId ?? ''
    const scoped = renamesByScope.get(scope) ?? new Map<string, string>()
    scoped.set(rename.previousName, rename.nextName)
    renamesByScope.set(scope, scoped)
  }
  const updatedAgentNodeIds: string[] = []
  const renamedNodes = nodes.map((node) => {
    if (node.type !== 'agent' || !Array.isArray(node.data?.toolNames)) return node
    const scoped = renamesByScope.get(node.parentId ?? '')
    if (!scoped) return node
    const current = node.data.toolNames.filter((name): name is string => typeof name === 'string')
    const next = [...new Set(current.map((name) => scoped.get(name) ?? name))]
    if (next.length === current.length && next.every((name, index) => name === current[index])) return node
    updatedAgentNodeIds.push(node.id)
    return {
      ...node,
      data: { ...(node.data ?? {}), toolNames: next }
    }
  })
  return { nodes: renamedNodes, updatedAgentNodeIds }
}

export const PORTABLE_GRAPH_NODE_TYPES = ['agent', 'note', 'document', 'custom'] as const
const PORTABLE_GRAPH_NODE_TYPE_SET = new Set<string>(PORTABLE_GRAPH_NODE_TYPES)

export function isPortableGraphNodeType(type: string | undefined): boolean {
  return typeof type === 'string' && PORTABLE_GRAPH_NODE_TYPE_SET.has(type)
}

export interface PortableGraphEdge extends SemanticEdgeFields {
  id: string
  source: string
  target: string
}

export type PortableGraphIssueCode =
  | 'invalid_arguments'
  | 'limit_exceeded'
  | 'unknown_field'
  | 'required_field'
  | 'invalid_type'
  | 'node_not_found'
  | 'protected_field'
  | 'unsupported_field'
  | 'invalid_update'
  | 'observation_failed'
  | 'update_failed'

export interface PortableGraphIssue {
  code: PortableGraphIssueCode
  error: string
  path?: string
  request_index?: number
  update_index?: number
  node_id?: string
  fields?: string[]
}

export interface PortableGraphFailureEnvelope {
  success: false
  error: string
  at?: string
}

export function portableGraphIssue(
  code: PortableGraphIssueCode,
  error: string,
  details: Omit<PortableGraphIssue, 'code' | 'error'> = {}
): PortableGraphIssue {
  return {
    code,
    error,
    ...(details.path !== undefined ? { path: details.path } : {}),
    ...(details.request_index !== undefined ? { request_index: details.request_index } : {}),
    ...(details.update_index !== undefined ? { update_index: details.update_index } : {}),
    ...(details.node_id !== undefined ? { node_id: details.node_id } : {}),
    ...(details.fields !== undefined ? { fields: [...details.fields] } : {})
  }
}

export function portableGraphFailure(issue: PortableGraphIssue): PortableGraphFailureEnvelope {
  return {
    success: false,
    error: issue.error,
    ...(issue.path ? { at: issue.path } : {})
  }
}

export function portableNodeNotFoundIssue(
  nodeId: string,
  details: Omit<PortableGraphIssue, 'code' | 'error' | 'node_id'> = {}
): PortableGraphIssue {
  return portableGraphIssue('node_not_found', `Node was not found: ${nodeId}.`, { ...details, node_id: nodeId })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function unknownKeys(value: Record<string, unknown>, allowed: ReadonlySet<string>): string[] {
  return Object.keys(value).filter((key) => !allowed.has(key))
}

function invalidJsonValue(value: unknown, path: string, depth = 0): PortableGraphIssue | undefined {
  if (depth > MAX_PORTABLE_VALUE_DEPTH) {
    return portableGraphIssue('limit_exceeded', `${path} exceeds the maximum nesting depth.`, { path })
  }
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return undefined
  if (typeof value === 'number') {
    return Number.isFinite(value)
      ? undefined
      : portableGraphIssue('invalid_type', `${path} must contain only finite JSON numbers.`, { path })
  }
  if (Array.isArray(value)) {
    if (value.length > MAX_PORTABLE_ARRAY_ITEMS) {
      return portableGraphIssue('limit_exceeded', `${path} accepts at most ${MAX_PORTABLE_ARRAY_ITEMS} array items.`, { path })
    }
    for (let index = 0; index < value.length; index += 1) {
      const issue = invalidJsonValue(value[index], `${path}[${index}]`, depth + 1)
      if (issue) return issue
    }
    return undefined
  }
  if (!isRecord(value)) {
    return portableGraphIssue('invalid_type', `${path} must contain only JSON values.`, { path })
  }
  const keys = Object.keys(value)
  if (keys.length > MAX_PORTABLE_OBJECT_KEYS) {
    return portableGraphIssue('limit_exceeded', `${path} accepts at most ${MAX_PORTABLE_OBJECT_KEYS} object fields.`, { path })
  }
  for (const key of keys) {
    if (PORTABLE_UNSAFE_KEYS.has(key)) {
      return portableGraphIssue('protected_field', `${path}.${key} is not allowed.`, {
        path: `${path}.${key}`,
        fields: [`${path}.${key}`]
      })
    }
    const issue = invalidJsonValue(value[key], `${path}.${key}`, depth + 1)
    if (issue) return issue
  }
  return undefined
}

export interface PortableListNodesResult {
  success: true
  nodes: Array<{
    id: string
    type: string
    label: string
    description?: string
    parent_id?: string
  }>
  edges: Array<{
    node_ids: [string, string]
    relation: string
  }>
}

export interface PortableGraphScope {
  nodes: PortableGraphNode[]
  edges: PortableGraphEdge[]
}

export function portableGraphNodeScopeId(node: PortableGraphNode | undefined): string | undefined {
  if (!node) return undefined
  return textValue(node.parentId) ?? textValue(node.data?.harnessNodeId)
}

/** Selects the current Agent's root or Harness-local graph identically for every host. */
export function selectPortableGraphScope(
  nodes: readonly PortableGraphNode[],
  edges: readonly PortableGraphEdge[],
  ownerNodeId: string
): PortableGraphScope {
  const scopeId = portableGraphNodeScopeId(nodes.find((node) => node.id === ownerNodeId))
  const scopedNodes = nodes.filter((node) => portableGraphNodeScopeId(node) === scopeId)
  const scopedNodeIds = new Set(scopedNodes.map((node) => node.id))
  return {
    nodes: [...scopedNodes],
    edges: edges.filter((edge) => scopedNodeIds.has(edge.source) && scopedNodeIds.has(edge.target))
  }
}

export function createPortableListNodesResult(
  nodes: readonly PortableGraphNode[],
  edges: readonly PortableGraphEdge[],
  options: { ownerNodeId?: string; harnessNodeId?: string } = {}
): PortableListNodesResult | PortableGraphFailureEnvelope {
  let visible: PortableGraphScope
  if (options.harnessNodeId !== undefined) {
    const harness = nodes.find((node) => node.id === options.harnessNodeId)
    if (!harness) return portableGraphFailure(portableNodeNotFoundIssue(options.harnessNodeId))
    if (harness.type !== 'harness') {
      return portableGraphFailure(portableGraphIssue(
        'invalid_type',
        `Node is not a Harness: ${options.harnessNodeId}.`,
        { node_id: options.harnessNodeId }
      ))
    }
    if (options.ownerNodeId !== undefined) {
      const owner = nodes.find((node) => node.id === options.ownerNodeId)
      const ownerScopeId = portableGraphNodeScopeId(owner)
      const harnessScopeId = portableGraphNodeScopeId(harness)
      const isOwnHarness = ownerScopeId === harness.id
      const isVisibleHarness = harnessScopeId === ownerScopeId
      if (!isOwnHarness && !isVisibleHarness) {
        return portableGraphFailure(portableGraphIssue(
          'invalid_arguments',
          `Harness is outside the current Agent scope: ${options.harnessNodeId}.`,
          { node_id: options.harnessNodeId }
        ))
      }
    }
    const childNodes = nodes.filter((node) => portableGraphNodeScopeId(node) === harness.id)
    const childNodeIds = new Set(childNodes.map((node) => node.id))
    visible = {
      nodes: childNodes,
      edges: edges.filter((edge) => childNodeIds.has(edge.source) && childNodeIds.has(edge.target))
    }
  } else {
    visible = options.ownerNodeId === undefined
      ? { nodes: [...nodes], edges: [...edges] }
      : selectPortableGraphScope(nodes, edges, options.ownerNodeId)
  }
  return {
    success: true,
    nodes: visible.nodes.map((node) => {
      const description = textValue(node.data?.description) ?? textValue(node.data?.summary)
      return {
        id: node.id,
        type: node.type ?? 'unknown',
        label: textValue(node.data?.label) ?? node.id,
        ...(description ? { description } : {}),
        ...(portableGraphNodeScopeId(node) ? { parent_id: portableGraphNodeScopeId(node) } : {})
      }
    }),
    edges: visible.edges.map((edge) => ({
      node_ids: [edge.source, edge.target],
      relation: edge.relation
    }))
  }
}

export type PortableObservationDetail = 'values' | 'full'

export interface PortableObservationRequest {
  nodeId: string
  detail: PortableObservationDetail
  fields: string[]
  includeContracts?: boolean
  startLine?: number
  endLine?: number
}

export type PortableObservationEntry =
  | { ok: true; requestIndex: number; request: PortableObservationRequest }
  | { ok: false; result: PortableObservationFailureResult }

export interface PortableObserveNodesRequest {
  entries: PortableObservationEntry[]
}

export type PortableObserveNodesParseResult =
  | { ok: true; value: PortableObserveNodesRequest }
  | { ok: false; result: PortableGraphFailureEnvelope }

const OBSERVE_TOP_LEVEL_KEYS = new Set(['requests'])
const OBSERVATION_KEYS = new Set(['node_id', 'detail', 'fields', 'include_contracts', 'start_line', 'end_line'])

function observationFailure(
  requestIndex: number,
  code: PortableGraphIssueCode,
  error: string,
  details: Omit<PortableGraphIssue, 'code' | 'error' | 'request_index'> = {}
): PortableObservationFailureResult {
  return portableObservationResultFailure(
    requestIndex,
    portableGraphIssue(code, error, { request_index: requestIndex, ...details }),
    details.node_id
  )
}

function observeTopLevelFailure(issue: PortableGraphIssue): PortableObserveNodesParseResult {
  return { ok: false, result: portableGraphFailure(issue) }
}

function positiveInteger(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 ? value : undefined
}

export function parsePortableObservationArgs(args: Record<string, unknown>): PortableObserveNodesParseResult {
  const extras = unknownKeys(args, OBSERVE_TOP_LEVEL_KEYS)
  if (extras.length > 0) {
    return observeTopLevelFailure(portableGraphIssue(
      'unknown_field',
      `observe accepts only requests; unsupported fields: ${extras.join(', ')}.`,
      { path: extras[0] }
    ))
  }
  if (!Array.isArray(args.requests) || args.requests.length === 0) {
    return observeTopLevelFailure(portableGraphIssue('required_field', 'observe requires requests.', { path: 'requests' }))
  }
  if (args.requests.length > PORTABLE_OBSERVATION_LIMIT) {
    return observeTopLevelFailure(portableGraphIssue(
      'limit_exceeded',
      `observe accepts at most ${PORTABLE_OBSERVATION_LIMIT} requests.`,
      { path: 'requests' }
    ))
  }

  const entries: PortableObservationEntry[] = args.requests.map((raw, requestIndex) => {
    const path = `requests[${requestIndex}]`
    if (!isRecord(raw)) {
      return { ok: false, result: observationFailure(requestIndex, 'invalid_type', `${path} must be an object.`, { path }) }
    }
    const itemExtras = unknownKeys(raw, OBSERVATION_KEYS)
    if (itemExtras.length > 0) {
      return {
        ok: false,
        result: observationFailure(
          requestIndex,
          'unknown_field',
          `${path} contains unsupported fields: ${itemExtras.join(', ')}.`,
          { path: `${path}.${itemExtras[0]}` }
        )
      }
    }
    const nodeId = textValue(raw.node_id)
    if (!nodeId) {
      return { ok: false, result: observationFailure(requestIndex, 'required_field', `${path}.node_id is required.`, { path: `${path}.node_id` }) }
    }
    const detail = raw.detail === undefined ? 'values' : raw.detail
    if (detail !== 'values' && detail !== 'full') {
      return {
        ok: false,
        result: observationFailure(requestIndex, 'invalid_type', `${path}.detail must be values or full.`, {
          path: `${path}.detail`,
          node_id: nodeId
        })
      }
    }
    if (raw.fields !== undefined && !Array.isArray(raw.fields)) {
      return {
        ok: false,
        result: observationFailure(requestIndex, 'invalid_type', `${path}.fields must be an array of non-empty strings.`, {
          path: `${path}.fields`,
          node_id: nodeId
        })
      }
    }
    if (Array.isArray(raw.fields) && raw.fields.length > PORTABLE_OBSERVATION_FIELD_LIMIT) {
      return {
        ok: false,
        result: observationFailure(
          requestIndex,
          'limit_exceeded',
          `${path}.fields accepts at most ${PORTABLE_OBSERVATION_FIELD_LIMIT} fields.`,
          { path: `${path}.fields`, node_id: nodeId }
        )
      }
    }
    const fields: string[] = []
    for (const field of (raw.fields ?? []) as unknown[]) {
      const normalized = textValue(field)
      if (!normalized) {
        return {
          ok: false,
          result: observationFailure(requestIndex, 'invalid_type', `${path}.fields must contain non-empty strings.`, {
            path: `${path}.fields`,
            node_id: nodeId
          })
        }
      }
      if (!fields.includes(normalized)) fields.push(normalized)
    }
    if (fields.length > PORTABLE_OBSERVATION_FIELD_LIMIT) {
      return {
        ok: false,
        result: observationFailure(
          requestIndex,
          'limit_exceeded',
          `${path}.fields accepts at most ${PORTABLE_OBSERVATION_FIELD_LIMIT} fields.`,
          { path: `${path}.fields`, node_id: nodeId }
        )
      }
    }
    if (raw.include_contracts !== undefined && typeof raw.include_contracts !== 'boolean') {
      return {
        ok: false,
        result: observationFailure(requestIndex, 'invalid_type', `${path}.include_contracts must be a boolean.`, {
          path: `${path}.include_contracts`, node_id: nodeId
        })
      }
    }
    const startLine = raw.start_line === undefined ? undefined : positiveInteger(raw.start_line)
    const endLine = raw.end_line === undefined ? undefined : positiveInteger(raw.end_line)
    if (raw.start_line !== undefined && startLine === undefined) {
      return {
        ok: false,
        result: observationFailure(requestIndex, 'invalid_type', `${path}.start_line must be a positive integer.`, {
          path: `${path}.start_line`, node_id: nodeId
        })
      }
    }
    if (raw.end_line !== undefined && endLine === undefined) {
      return {
        ok: false,
        result: observationFailure(requestIndex, 'invalid_type', `${path}.end_line must be a positive integer.`, {
          path: `${path}.end_line`, node_id: nodeId
        })
      }
    }
    if (startLine !== undefined && endLine !== undefined && startLine > endLine) {
      return {
        ok: false,
        result: observationFailure(requestIndex, 'invalid_arguments', `${path}.start_line cannot exceed end_line.`, {
          path, node_id: nodeId
        })
      }
    }
    return {
      ok: true,
      requestIndex,
      request: {
        nodeId,
        detail,
        fields,
        includeContracts: raw.include_contracts === true,
        ...(startLine === undefined ? {} : { startLine }),
        ...(endLine === undefined ? {} : { endLine })
      }
    }
  })
  return { ok: true, value: { entries } }
}

const OMIT = Symbol('portable-graph-omit')

function isSensitivePortableKey(key: string): boolean {
  const normalized = key.replace(/[_-]/g, '').toLowerCase()
  return !PORTABLE_PUBLIC_TOKEN_KEYS.has(normalized) && PORTABLE_SENSITIVE_KEY.test(key)
}

function shouldHideObservationKey(key: string, nodeType?: string): boolean {
  const normalized = key.toLowerCase()
  return key.startsWith('_')
    || PORTABLE_UNSAFE_KEYS.has(key)
    || isSensitivePortableKey(key)
    || PORTABLE_HIDDEN_OBSERVATION_KEYS.has(normalized)
    || (nodeType === 'agent' && PORTABLE_HIDDEN_AGENT_OBSERVATION_KEYS.has(normalized))
}

function redactedValue(
  value: unknown,
  nodeType: string | undefined,
  seen: WeakSet<object>
): unknown | typeof OMIT {
  if (value === null || typeof value === 'boolean' || typeof value === 'number') return value
  if (typeof value === 'string') return EMBEDDED_URL_CREDENTIALS.test(value) ? '[redacted]' : value
  if (Array.isArray(value)) {
    if (seen.has(value)) return '[circular]'
    seen.add(value)
    const result = value.map((item) => {
      const redacted = redactedValue(item, nodeType, seen)
      return redacted === OMIT ? null : redacted
    })
    seen.delete(value)
    return result
  }
  if (!isRecord(value)) return OMIT
  if (seen.has(value)) return '[circular]'
  seen.add(value)
  const result: Record<string, unknown> = {}
  for (const [key, nested] of Object.entries(value)) {
    if (shouldHideObservationKey(key, nodeType)) continue
    const redacted = redactedValue(nested, nodeType, seen)
    if (redacted !== OMIT) result[key] = redacted
  }
  seen.delete(value)
  return result
}

export function redactPortableGraphValue(value: unknown, nodeType?: string): unknown {
  const redacted = redactedValue(value, nodeType, new WeakSet())
  return redacted === OMIT ? null : redacted
}

export function redactPortableGraphData(data: Record<string, unknown>, nodeType?: string): Record<string, unknown> {
  const redacted = redactPortableGraphValue(data, nodeType)
  return isRecord(redacted) ? redacted : {}
}

export interface PortableObservationLineRange {
  start_line: number
  end_line: number
  total_lines: number
}

export interface PortableNodeObservation {
  node_id: string
  type: string
  values?: Record<string, unknown>
  line_ranges?: Record<string, PortableObservationLineRange>
}

export type PortableNodeObservationBuildResult =
  | { ok: true; observation: PortableNodeObservation }
  | { ok: false; issue: PortableGraphIssue }

const PORTABLE_DEFAULT_VALUE_FIELDS: Record<string, readonly string[]> = {
  agent: ['objective', 'model', 'toolNames', 'maxTokens', 'exposeInHarness'],
  harness: ['summary', 'purpose', 'defaultExposureId'],
  tool: ['toolName', 'runtime', 'exposeAsTool', 'input', 'inputRefs', 'inputSchema', 'outputSchema', 'timeoutMs', 'dependencies', 'permissions', 'sideEffects'],
  app: ['props', 'state', 'dependencies'],
  cli: ['shell', 'cwd', 'platform', 'timeoutMs', 'commandSyntax'],
  datasource: ['adapter', 'connectionUrl', 'username', 'database', 'passwordConfigured', 'exposeSqlTool', 'allowWrites']
}

function defaultObservationFields(
  nodeType: string | undefined,
  detail: PortableObservationDetail,
  values: Record<string, unknown>
): string[] {
  const hasMeaningfulValue = (field: string): boolean => {
    if (!Object.prototype.hasOwnProperty.call(values, field)) return false
    const value = values[field]
    if (value === null || value === undefined) return false
    if (typeof value === 'string') return value.trim().length > 0
    if (Array.isArray(value)) return value.length > 0
    if (isRecord(value)) return Object.keys(value).length > 0
    return true
  }
  if (detail === 'full') return Object.keys(values).filter(hasMeaningfulValue)
  const configured = PORTABLE_DEFAULT_VALUE_FIELDS[nodeType ?? ''] ?? Object.keys(values)
  return configured.filter(hasMeaningfulValue)
}

export function buildPortableNodeObservation(
  node: PortableGraphNode,
  request: PortableObservationRequest,
  options: { values?: Record<string, unknown> } = {}
): PortableNodeObservationBuildResult {
  const projectedValues = options.values ?? getNodeValuesFromData(node)
  const safeValues = redactPortableGraphData(projectedValues, node.type)
  delete safeValues.label
  delete safeValues.description
  const observation: PortableNodeObservation = {
    node_id: node.id,
    type: node.type ?? 'unknown'
  }

  const selectedFields = request.fields.length > 0
    ? request.fields
    : defaultObservationFields(node.type, request.detail, safeValues)
  const missing = selectedFields.filter((field) => !Object.prototype.hasOwnProperty.call(safeValues, field))
  if (missing.length > 0) {
    return {
      ok: false,
      issue: portableGraphIssue(
        'unknown_field',
        `Unknown or protected value fields: ${missing.join(', ')}.`,
        { node_id: node.id, fields: missing }
      )
    }
  }
  const values: Record<string, unknown> = {}
  const lineRanges: Record<string, PortableObservationLineRange> = {}
  for (const field of selectedFields) {
    const value = safeValues[field]
    if (typeof value !== 'string' || (request.startLine === undefined && request.endLine === undefined)) {
      values[field] = value
      continue
    }
    const lines = value.split(/\r?\n/)
    const startLine = request.startLine ?? 1
    const requestedEndLine = request.endLine ?? lines.length
    const endLine = Math.min(requestedEndLine, lines.length)
    values[field] = lines.slice(startLine - 1, requestedEndLine).join('\n')
    lineRanges[field] = {
      start_line: startLine,
      end_line: endLine >= startLine ? endLine : startLine - 1,
      total_lines: lines.length
    }
  }
  return {
    ok: true,
    observation: {
      ...observation,
      values,
      ...(Object.keys(lineRanges).length > 0 ? { line_ranges: lineRanges } : {})
    }
  }
}

export function portableObservationResultFailure(
  requestIndex: number,
  issue: PortableGraphIssue,
  nodeId?: string
): PortableObservationFailureResult {
  return {
    ...(nodeId ? { node_id: nodeId } : {}),
    error: issue.error,
    at: issue.path ?? `requests[${requestIndex}]`
  }
}

export type PortableObservationSuccessResult = PortableNodeObservation

export interface PortableObservationFailureResult {
  node_id?: string
  error: string
  at?: string
}
export type PortableObservationResult = PortableObservationSuccessResult | PortableObservationFailureResult

export interface PortableObservationEnvelope {
  success: boolean
  results: PortableObservationResult[]
}

export function createPortableObservationSuccess(
  observation: PortableNodeObservation
): PortableObservationSuccessResult {
  return {
    node_id: observation.node_id,
    type: observation.type,
    ...(observation.values !== undefined ? { values: { ...observation.values } } : {}),
    ...(observation.line_ranges !== undefined
      ? { line_ranges: Object.fromEntries(Object.entries(observation.line_ranges).map(([field, range]) => [field, { ...range }])) }
      : {})
  }
}

export function createPortableObservationEnvelope(
  results: readonly PortableObservationResult[]
): PortableObservationEnvelope {
  const canonicalResults = results.map((result) => ({
    ...result,
    ...('values' in result && result.values !== undefined ? { values: { ...result.values } } : {}),
    ...('line_ranges' in result && result.line_ranges !== undefined
      ? { line_ranges: Object.fromEntries(Object.entries(result.line_ranges).map(([field, range]) => [field, { ...range }])) }
      : {})
  })) as PortableObservationResult[]
  return {
    success: canonicalResults.every((result) => !('error' in result)),
    results: canonicalResults
  }
}

export interface PortableGraphToolCapabilities {
  updateNodePlacement?: boolean
}

export type PortableNodePlacement =
  | { type: 'root' }
  | { type: 'harness'; harnessNodeId: string }

export interface PortableNodeUpdate {
  updateIndex: number
  nodeId: string
  patch: {
    set: Record<string, unknown>
  }
  hasDataUpdate: boolean
  placement?: PortableNodePlacement
}

export interface PortableUpdateNodesRequest {
  updates: PortableNodeUpdate[]
}

export type PortableUpdateNodesParseResult =
  | { ok: true; value: PortableUpdateNodesRequest }
  | { ok: false; result: PortableUpdateFailureEnvelope }

export interface PortableUpdateFailureEnvelope {
  success: false
  errors: Array<{ error: string; at?: string }>
}

const UPDATE_TOP_LEVEL_KEYS = new Set(['updates'])
const BASE_UPDATE_KEYS = new Set(['node_id', 'set'])
const PLACEMENT_KEYS = new Set(['type', 'harness_node_id'])

export function createPortableUpdateFailureEnvelope(
  issues: readonly PortableGraphIssue[]
): PortableUpdateFailureEnvelope {
  return {
    success: false,
    errors: issues.map((issue) => ({
      error: issue.error,
      ...(issue.path ? { at: issue.path } : {})
    }))
  }
}

function updateIssue(
  updateIndex: number,
  code: PortableGraphIssueCode,
  error: string,
  details: Omit<PortableGraphIssue, 'code' | 'error' | 'update_index'> = {}
): PortableGraphIssue {
  return portableGraphIssue(code, error, { update_index: updateIndex, ...details })
}

export function parsePortableNodeUpdates(
  args: Record<string, unknown>,
  capabilities: PortableGraphToolCapabilities = {}
): PortableUpdateNodesParseResult {
  const extras = unknownKeys(args, UPDATE_TOP_LEVEL_KEYS)
  if (extras.length > 0) {
    return {
      ok: false,
      result: createPortableUpdateFailureEnvelope([
        portableGraphIssue('unknown_field', `edit patch parsing accepts only updates; unsupported fields: ${extras.join(', ')}.`, {
          path: extras[0]
        })
      ])
    }
  }
  if (!Array.isArray(args.updates) || args.updates.length === 0) {
    return {
      ok: false,
      result: createPortableUpdateFailureEnvelope([
        portableGraphIssue('required_field', 'edit patch parsing requires updates.', { path: 'updates' })
      ])
    }
  }
  if (args.updates.length > PORTABLE_EDIT_LIMIT) {
    return {
      ok: false,
      result: createPortableUpdateFailureEnvelope([
        portableGraphIssue('limit_exceeded', `edit accepts at most ${PORTABLE_EDIT_LIMIT} updates.`, { path: 'updates' })
      ])
    }
  }

  const allowedUpdateKeys = new Set(BASE_UPDATE_KEYS)
  if (capabilities.updateNodePlacement) allowedUpdateKeys.add('placement')
  const issues: PortableGraphIssue[] = []
  const updates: PortableNodeUpdate[] = []

  for (let updateIndex = 0; updateIndex < args.updates.length; updateIndex += 1) {
    const raw = args.updates[updateIndex]
    const path = `updates[${updateIndex}]`
    if (!isRecord(raw)) {
      issues.push(updateIssue(updateIndex, 'invalid_type', `${path} must be an object.`, { path }))
      continue
    }
    const itemExtras = unknownKeys(raw, allowedUpdateKeys)
    if (itemExtras.length > 0) {
      const portableFields = capabilities.updateNodePlacement
        ? 'set or placement'
        : 'set'
      issues.push(updateIssue(
        updateIndex,
        itemExtras.some((field) => field === 'placement') ? 'unsupported_field' : 'unknown_field',
        `${path} must use ${portableFields}; unsupported fields: ${itemExtras.join(', ')}.`,
        { path: `${path}.${itemExtras[0]}` }
      ))
      continue
    }
    const nodeId = textValue(raw.node_id)
    if (!nodeId) {
      issues.push(updateIssue(updateIndex, 'required_field', `${path}.node_id is required.`, { path: `${path}.node_id` }))
      continue
    }
    if (raw.set !== undefined && !isRecord(raw.set)) {
      issues.push(updateIssue(updateIndex, 'invalid_type', `${path}.set must be an object.`, { node_id: nodeId, path: `${path}.set` }))
      continue
    }
    if (raw.set !== undefined) {
      const issue = invalidJsonValue(raw.set, `${path}.set`)
      if (issue) {
        issues.push({ ...issue, update_index: updateIndex, node_id: nodeId })
        continue
      }
    }
    let placement: PortableNodePlacement | undefined
    if (raw.placement !== undefined) {
      if (!isRecord(raw.placement)) {
        issues.push(updateIssue(updateIndex, 'invalid_type', `${path}.placement must be an object.`, {
          node_id: nodeId, path: `${path}.placement`
        }))
        continue
      }
      const placementExtras = unknownKeys(raw.placement, PLACEMENT_KEYS)
      if (placementExtras.length > 0) {
        issues.push(updateIssue(updateIndex, 'unknown_field', `${path}.placement contains unsupported fields: ${placementExtras.join(', ')}.`, {
          node_id: nodeId, path: `${path}.placement.${placementExtras[0]}`
        }))
        continue
      }
      if (raw.placement.type === 'root') {
        if (raw.placement.harness_node_id !== undefined) {
          issues.push(updateIssue(updateIndex, 'invalid_arguments', `${path}.placement.harness_node_id is not allowed for type=root.`, {
            node_id: nodeId, path: `${path}.placement.harness_node_id`
          }))
          continue
        }
        placement = { type: 'root' }
      } else if (raw.placement.type === 'harness') {
        const harnessNodeId = textValue(raw.placement.harness_node_id)
        if (!harnessNodeId) {
          issues.push(updateIssue(updateIndex, 'required_field', `${path}.placement.harness_node_id is required for type=harness.`, {
            node_id: nodeId, path: `${path}.placement.harness_node_id`
          }))
          continue
        }
        placement = { type: 'harness', harnessNodeId }
      } else {
        issues.push(updateIssue(updateIndex, 'invalid_type', `${path}.placement.type must be root or harness.`, {
          node_id: nodeId, path: `${path}.placement.type`
        }))
        continue
      }
    }

    const hasDataUpdate = isRecord(raw.set) && Object.keys(raw.set).length > 0
    if (!hasDataUpdate && !placement) {
      issues.push(updateIssue(
        updateIndex,
        'invalid_update',
        `${path} must include a non-empty set${capabilities.updateNodePlacement ? ' or placement' : ''}.`,
        { node_id: nodeId, path }
      ))
      continue
    }
    updates.push({
      updateIndex,
      nodeId,
      patch: {
        set: isRecord(raw.set) ? raw.set : {}
      },
      hasDataUpdate,
      ...(placement ? { placement } : {})
    })
  }

  return issues.length > 0
    ? { ok: false, result: createPortableUpdateFailureEnvelope(issues) }
    : { ok: true, value: { updates } }
}

export function validatePortableNodeUpdateProtection(
  update: PortableNodeUpdate,
  nodeType?: string
): PortableGraphIssue | undefined {
  const fields: string[] = []
  const seen = new WeakSet<object>()
  const visit = (value: unknown, path: string): void => {
    if (typeof value === 'string' && EMBEDDED_URL_CREDENTIALS.test(value)) {
      fields.push(path)
      return
    }
    if (Array.isArray(value)) {
      if (seen.has(value)) return
      seen.add(value)
      value.forEach((item, index) => visit(item, `${path}[${index}]`))
      return
    }
    if (!isRecord(value) || seen.has(value)) return
    seen.add(value)
    for (const [key, nested] of Object.entries(value)) {
      const nestedPath = `${path}.${key}`
      const protectsAgentRuntimeField = nodeType === 'agent'
        && PORTABLE_PROTECTED_AGENT_UPDATE_KEYS.has(key.toLowerCase())
      if (shouldHideObservationKey(key, nodeType) || protectsAgentRuntimeField) fields.push(nestedPath)
      else visit(nested, nestedPath)
    }
  }
  visit(update.patch.set, `updates[${update.updateIndex}].set`)
  if (fields.length === 0) return undefined
  return portableGraphIssue('protected_field', `Update contains protected fields: ${fields.join(', ')}.`, {
    update_index: update.updateIndex,
    node_id: update.nodeId,
    fields
  })
}

export type PortableNodeDataUpdateResult =
  | { ok: true; data: Record<string, unknown> }
  | { ok: false; issue: PortableGraphIssue }

function validatePortableAgentConfigurationUpdate(
  fields: Record<string, unknown>,
  update: PortableNodeUpdate
): PortableGraphIssue | undefined {
  const invalid = (field: string, message: string): PortableGraphIssue => portableGraphIssue(
    'invalid_update',
    `updates[${update.updateIndex}].set.${field} ${message}`,
    { update_index: update.updateIndex, node_id: update.nodeId, fields: [`updates[${update.updateIndex}].set.${field}`] }
  )
  for (const field of ['objective', 'systemPrompt'] as const) {
    if (fields[field] !== undefined && typeof fields[field] !== 'string') {
      return invalid(field, 'must be a string.')
    }
  }
  if (fields.model !== undefined && (
    typeof fields.model !== 'string'
    || !fields.model.trim()
    || fields.model.trim().length > 240
  )) {
    return invalid('model', 'must be a non-empty string of at most 240 characters.')
  }
  if (fields.toolNames !== undefined && (
    !Array.isArray(fields.toolNames)
    || fields.toolNames.some((name) => typeof name !== 'string' || !name.trim() || name.trim().length > 120)
  )) {
    return invalid('toolNames', 'must be an array of non-empty tool names of at most 120 characters each.')
  }
  if (fields.maxTokens !== undefined && (
    typeof fields.maxTokens !== 'number'
    || !Number.isSafeInteger(fields.maxTokens)
    || fields.maxTokens < 1
  )) {
    return invalid('maxTokens', 'must be a positive safe integer.')
  }
  if (fields.exposeInHarness !== undefined && typeof fields.exposeInHarness !== 'boolean') {
    return invalid('exposeInHarness', 'must be a boolean.')
  }
  return undefined
}

function portableTextPatch(
  fields: Record<string, unknown>,
  controlField: 'contentPatch' | 'codePatch' | 'systemPromptPatch',
  valueField: 'content' | 'code' | 'systemPrompt',
  currentData: Record<string, unknown>,
  update: PortableNodeUpdate
): PortableNodeDataUpdateResult | undefined {
  if (fields[controlField] === undefined) return undefined
  if (fields[valueField] !== undefined) {
    return {
      ok: false,
      issue: portableGraphIssue('invalid_update', `${controlField} cannot be used with ${valueField} in the same update.`, {
        update_index: update.updateIndex,
        node_id: update.nodeId
      })
    }
  }
  const patch = fields[controlField]
  if (!isRecord(patch) || typeof patch.find !== 'string' || !patch.find || typeof patch.replace !== 'string') {
    return {
      ok: false,
      issue: portableGraphIssue('invalid_update', `${controlField} requires non-empty find and string replace fields.`, {
        update_index: update.updateIndex,
        node_id: update.nodeId
      })
    }
  }
  const current = typeof currentData[valueField] === 'string' ? currentData[valueField] : ''
  if (!current.includes(patch.find)) {
    return {
      ok: false,
      issue: portableGraphIssue('invalid_update', `Patch target was not found in ${valueField}.`, {
        update_index: update.updateIndex,
        node_id: update.nodeId
      })
    }
  }
  return { ok: true, data: { [valueField]: current.replace(patch.find, patch.replace) } }
}

function portableDocumentContentPatch(
  fields: Record<string, unknown>,
  currentData: Record<string, unknown>,
  update: PortableNodeUpdate
): PortableNodeDataUpdateResult | undefined {
  if (fields.contentPatch === undefined) return undefined
  if (fields.content !== undefined) {
    return {
      ok: false,
      issue: portableGraphIssue('invalid_update', 'contentPatch cannot be used with content in the same update.', {
        update_index: update.updateIndex,
        node_id: update.nodeId
      })
    }
  }
  const patch = fields.contentPatch
  if (!isRecord(patch)) {
    return {
      ok: false,
      issue: portableGraphIssue('invalid_update', 'contentPatch must be an object.', {
        update_index: update.updateIndex,
        node_id: update.nodeId
      })
    }
  }
  const current = typeof currentData.content === 'string' ? currentData.content : ''
  if (patch.operation === 'append') {
    const unknown = Object.keys(patch).filter((key) => key !== 'operation' && key !== 'text')
    if (typeof patch.text !== 'string' || !patch.text || unknown.length > 0) {
      return {
        ok: false,
        issue: portableGraphIssue('invalid_update', 'contentPatch operation=append requires only operation and non-empty text fields.', {
          update_index: update.updateIndex,
          node_id: update.nodeId
        })
      }
    }
    return { ok: true, data: { content: current + patch.text } }
  }
  if (patch.operation === 'replace') {
    const unknown = Object.keys(patch).filter((key) => key !== 'operation' && key !== 'find' && key !== 'replace')
    if (typeof patch.find !== 'string' || !patch.find || typeof patch.replace !== 'string' || unknown.length > 0) {
      return {
        ok: false,
        issue: portableGraphIssue('invalid_update', 'contentPatch operation=replace requires only operation, non-empty find, and string replace fields.', {
          update_index: update.updateIndex,
          node_id: update.nodeId
        })
      }
    }
    if (!current.includes(patch.find)) {
      return {
        ok: false,
        issue: portableGraphIssue('invalid_update', 'Patch target was not found in content.', {
          update_index: update.updateIndex,
          node_id: update.nodeId
        })
      }
    }
    return { ok: true, data: { content: current.replace(patch.find, patch.replace) } }
  }
  return {
    ok: false,
    issue: portableGraphIssue('invalid_update', 'contentPatch.operation must be replace or append.', {
      update_index: update.updateIndex,
      node_id: update.nodeId
    })
  }
}

/** Pure portable data mutation; hosts only commit the validated patch to their Canvas store. */
export function applyPortableNodeDataUpdate(
  node: PortableGraphNode,
  update: PortableNodeUpdate
): PortableNodeDataUpdateResult {
  const protectionIssue = validatePortableNodeUpdateProtection(update, node.type)
  if (protectionIssue) return { ok: false, issue: protectionIssue }
  const fields = { ...update.patch.set }
  const contractType = node.type === 'document' ? 'note' : node.type
  const fieldsValidation = validateEditFieldsForType(contractType, fields)
  if (!fieldsValidation.ok) {
    const allowed = new Set(Object.keys(editFieldsSchemaForType(contractType)?.properties ?? {}))
    const unknownFields = Object.keys(fields).filter((field) => !allowed.has(field))
    return {
      ok: false,
      issue: portableGraphIssue('unsupported_field', fieldsValidation.error, {
        update_index: update.updateIndex,
        node_id: update.nodeId,
        fields: unknownFields.map((field) => `updates[${update.updateIndex}].set.${field}`)
      })
    }
  }
  if (node.type === 'agent') {
    const configurationIssue = validatePortableAgentConfigurationUpdate(fields, update)
    if (configurationIssue) return { ok: false, issue: configurationIssue }
  }
  const contentPatch = contractType === 'note'
    ? portableDocumentContentPatch(fields, node.data ?? {}, update)
    : portableTextPatch(fields, 'contentPatch', 'content', node.data ?? {}, update)
  if (contentPatch && !contentPatch.ok) return contentPatch
  const codePatch = portableTextPatch(fields, 'codePatch', 'code', node.data ?? {}, update)
  if (codePatch && !codePatch.ok) return codePatch
  const systemPromptPatch = portableTextPatch(fields, 'systemPromptPatch', 'systemPrompt', node.data ?? {}, update)
  if (systemPromptPatch && !systemPromptPatch.ok) return systemPromptPatch
  delete fields.contentPatch
  delete fields.codePatch
  delete fields.systemPromptPatch
  const data: Record<string, unknown> = {
    ...fields,
    ...(contentPatch?.ok ? contentPatch.data : {}),
    ...(codePatch?.ok ? codePatch.data : {}),
    ...(systemPromptPatch?.ok ? systemPromptPatch.data : {})
  }
  if (typeof fields.label === 'string') {
    const label = fields.label.trim()
    if (!label) {
      return {
        ok: false,
        issue: portableGraphIssue('invalid_update', 'label must be a non-empty string.', {
          update_index: update.updateIndex,
          node_id: update.nodeId
        })
      }
    }
    data.label = label
  }
  return { ok: true, data }
}

export interface PortableUpdateSuccessEnvelope {
  success: true
}

export function createPortableUpdateSuccessEnvelope(): PortableUpdateSuccessEnvelope {
  return { success: true }
}
