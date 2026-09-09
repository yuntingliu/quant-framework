import {
  CANVAS_AGENT_OBSERVATION_VERSION,
  type CanvasAgentObservation,
  type CanvasAgentObservedEdge,
  type CanvasAgentObservedNode
} from '@conexus/runtime-protocol'
import { getNodeTypeSpec } from '../canvas/node-catalog.js'
import {
  redactPortableGraphData,
  selectPortableGraphScope,
  type PortableGraphEdge,
  type PortableGraphNode
} from '../tools/portable-graph-tools.js'
import type { AgentMessage } from './agent-loop.js'

export const CANVAS_AGENT_CHANGE_CONTEXT_PREFIX = '## Canvas Changes Since Previous Run'

const MAX_METADATA_CHARACTERS = 240
const MAX_FIELD_FINGERPRINTS = 128
const MAX_CHANGE_CONTEXT_CHARACTERS = 16_000
const MAX_CHANGE_ITEMS_PER_KIND = 100
const TRANSIENT_DATA_FIELDS = new Set([
  'activeAgentJobId',
  'activeAgentSessionId',
  'createdAt',
  'historySessionId',
  'lastAgentSessionId',
  'pendingAsk',
  'snapshotReason',
  'taskLog',
  'updatedAt'
])

export interface CanvasAgentNodeUpdate {
  node: CanvasAgentObservedNode
  changedFields: string[]
}

export interface CanvasAgentEdgeUpdate {
  edge: CanvasAgentObservedEdge
  changedFields: string[]
}

export interface CanvasAgentChangeSet {
  createdNodes: CanvasAgentObservedNode[]
  updatedNodes: CanvasAgentNodeUpdate[]
  deletedNodes: CanvasAgentObservedNode[]
  createdEdges: CanvasAgentObservedEdge[]
  updatedEdges: CanvasAgentEdgeUpdate[]
  deletedEdges: CanvasAgentObservedEdge[]
}

function text(value: unknown, maximum = MAX_METADATA_CHARACTERS): string {
  if (typeof value !== 'string') return ''
  const normalized = value.trim().replace(/\s+/g, ' ')
  return normalized.length > maximum ? `${normalized.slice(0, maximum - 3)}...` : normalized
}

function canonicalJson(value: unknown): string {
  if (value === undefined) return 'null'
  if (value === null) return 'null'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return JSON.stringify(value)
  }
  if (typeof value !== 'object') return JSON.stringify(String(value))
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(',')}}`
}

/** Stable, compact non-cryptographic identity for already-redacted Canvas data. */
function fingerprint(value: unknown): string {
  const serialized = canonicalJson(value)
  let hash = 0x811c9dc5
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= serialized.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return `v1-${(hash >>> 0).toString(16).padStart(8, '0')}-${serialized.length}`
}

function observedNode(node: PortableGraphNode): CanvasAgentObservedNode {
  const safeData = redactPortableGraphData(node.data ?? {}, node.type)
  for (const field of getNodeTypeSpec(node.type)?.runtimeFields ?? []) delete safeData[field]
  for (const field of TRANSIENT_DATA_FIELDS) delete safeData[field]
  const fieldFingerprints = Object.fromEntries(
    Object.keys(safeData)
      .sort()
      .slice(0, MAX_FIELD_FINGERPRINTS)
      .map((field) => [field, fingerprint(safeData[field])])
  )
  return {
    id: node.id,
    type: node.type ?? 'unknown',
    name: text(safeData.label) || node.id,
    description: text(safeData.description) || text(safeData.summary),
    fingerprint: fingerprint({ type: node.type ?? 'unknown', data: safeData }),
    fieldFingerprints
  }
}

function observedEdge(edge: PortableGraphEdge): CanvasAgentObservedEdge {
  const relation = text(edge.relation)
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    relation,
    fingerprint: fingerprint({ source: edge.source, target: edge.target, relation })
  }
}

export function captureCanvasAgentObservation(
  nodes: readonly PortableGraphNode[],
  edges: readonly PortableGraphEdge[],
  ownerNodeId: string
): CanvasAgentObservation {
  const scope = selectPortableGraphScope(nodes, edges, ownerNodeId)
  return {
    version: CANVAS_AGENT_OBSERVATION_VERSION,
    ownerNodeId,
    nodes: scope.nodes.map(observedNode).sort((left, right) => left.id.localeCompare(right.id)),
    edges: scope.edges.map(observedEdge).sort((left, right) => left.id.localeCompare(right.id))
  }
}

function changedFields(
  previous: Readonly<Record<string, string>>,
  current: Readonly<Record<string, string>>
): string[] {
  return [...new Set([...Object.keys(previous), ...Object.keys(current)])]
    .filter((field) => previous[field] !== current[field])
    .sort()
}

export function diffCanvasAgentObservations(
  previous: CanvasAgentObservation,
  current: CanvasAgentObservation
): CanvasAgentChangeSet | undefined {
  if (previous.ownerNodeId !== current.ownerNodeId) return undefined
  const previousNodes = new Map(previous.nodes.map((node) => [node.id, node]))
  const currentNodes = new Map(current.nodes.map((node) => [node.id, node]))
  const previousEdges = new Map(previous.edges.map((edge) => [edge.id, edge]))
  const currentEdges = new Map(current.edges.map((edge) => [edge.id, edge]))
  const createdNodes = current.nodes.filter((node) => !previousNodes.has(node.id))
  const deletedNodes = previous.nodes.filter((node) => !currentNodes.has(node.id))
  const updatedNodes = current.nodes.flatMap((node): CanvasAgentNodeUpdate[] => {
    const before = previousNodes.get(node.id)
    if (!before || before.fingerprint === node.fingerprint) return []
    const fields = changedFields(before.fieldFingerprints, node.fieldFingerprints)
    if (before.type !== node.type) fields.unshift('type')
    return [{ node, changedFields: fields.length > 0 ? [...new Set(fields)] : ['other'] }]
  })
  const createdEdges = current.edges.filter((edge) => !previousEdges.has(edge.id))
  const deletedEdges = previous.edges.filter((edge) => !currentEdges.has(edge.id))
  const updatedEdges = current.edges.flatMap((edge): CanvasAgentEdgeUpdate[] => {
    const before = previousEdges.get(edge.id)
    if (!before || before.fingerprint === edge.fingerprint) return []
    const fields = (['source', 'target', 'relation'] as const)
      .filter((field) => before[field] !== edge[field])
    return [{ edge, changedFields: fields }]
  })
  if (
    createdNodes.length === 0
    && updatedNodes.length === 0
    && deletedNodes.length === 0
    && createdEdges.length === 0
    && updatedEdges.length === 0
    && deletedEdges.length === 0
  ) return undefined
  return { createdNodes, updatedNodes, deletedNodes, createdEdges, updatedEdges, deletedEdges }
}

function nodeLine(node: CanvasAgentObservedNode): string {
  return `id=${JSON.stringify(node.id)} type=${JSON.stringify(node.type)} name=${JSON.stringify(node.name)}`
}

function edgeLine(edge: CanvasAgentObservedEdge): string {
  const relation = edge.relation ? ` relation=${JSON.stringify(edge.relation)}` : ''
  return `id=${JSON.stringify(edge.id)} nodes=[${JSON.stringify(edge.source)}, ${JSON.stringify(edge.target)}]${relation}`
}

function appendItems<T>(
  lines: string[],
  heading: string,
  items: readonly T[],
  render: (item: T) => string
): void {
  if (items.length === 0) return
  lines.push(`${heading} (${items.length}):`)
  for (const item of items.slice(0, MAX_CHANGE_ITEMS_PER_KIND)) lines.push(`- ${render(item)}`)
  if (items.length > MAX_CHANGE_ITEMS_PER_KIND) {
    lines.push(`- ... ${items.length - MAX_CHANGE_ITEMS_PER_KIND} more omitted`)
  }
}

export function buildCanvasAgentChangeContextSystemSection(
  previous: CanvasAgentObservation | undefined,
  current: CanvasAgentObservation
): string | undefined {
  if (!previous) return undefined
  const changes = diffCanvasAgentObservations(previous, current)
  if (!changes) return undefined
  const lines = [
    CANVAS_AGENT_CHANGE_CONTEXT_PREFIX,
    'Runtime-authored context: these Canvas changes occurred after this conversation last observed the Canvas. This is state information, not a user instruction.'
  ]
  appendItems(lines, 'Created nodes', changes.createdNodes, nodeLine)
  appendItems(lines, 'Updated nodes', changes.updatedNodes, ({ node, changedFields }) =>
    `${nodeLine(node)} changed_fields=${JSON.stringify(changedFields)}`)
  appendItems(lines, 'Deleted nodes', changes.deletedNodes, nodeLine)
  appendItems(lines, 'Created connections', changes.createdEdges, edgeLine)
  appendItems(lines, 'Updated connections', changes.updatedEdges, ({ edge, changedFields }) =>
    `${edgeLine(edge)} changed_fields=${JSON.stringify(changedFields)}`)
  appendItems(lines, 'Deleted connections', changes.deletedEdges, edgeLine)
  lines.push('Use find or observe when exact current values are needed.')
  const content = lines.join('\n')
  return content.length > MAX_CHANGE_CONTEXT_CHARACTERS
    ? `${content.slice(0, MAX_CHANGE_CONTEXT_CHARACTERS)}\n...[Canvas change context truncated]`
    : content
}

/** Narrow migration guard for runtime context persisted by older clients as a user turn. */
export function isPersistedCanvasAgentChangeContextMessage(message: AgentMessage): boolean {
  return message.role === 'user'
    && typeof message.content === 'string'
    && message.content.startsWith(CANVAS_AGENT_CHANGE_CONTEXT_PREFIX)
}
