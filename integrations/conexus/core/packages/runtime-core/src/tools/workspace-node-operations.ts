import { randomUUID } from 'node:crypto'
import type { RuntimeEdge, RuntimeNode, RuntimeWorkspace } from '../contracts.js'
import {
  defaultNodeDataForType,
  extractCreateNodeFields,
  getNodeTypeSpec,
  validateCreateFieldsForType
} from '../canvas/node-catalog.js'
import { applyPortableNodeDataUpdate } from './portable-graph-tools.js'

export interface WorkspaceNodeOperationResult {
  result: Record<string, unknown>
  updatedNodes?: RuntimeNode[]
  createdNodes?: RuntimeNode[]
  createdEdges?: RuntimeEdge[]
  deletedNodeIds?: string[]
  deletedEdgeIds?: string[]
}

export type CanvasNodeReferenceResolution =
  | { ok: true; nodeId: string }
  | { ok: false; error: string }

export interface CanvasNodeReferenceCandidate {
  id: string
  data?: Record<string, unknown>
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function scope(node: RuntimeNode): string {
  return node.parentId ?? (typeof node.data.harnessNodeId === 'string' ? node.data.harnessNodeId : '')
}

function failure(error: string): WorkspaceNodeOperationResult {
  return { result: { success: false, error } }
}

/** Resolves explicit references first, then an exact label only when it is unique across the Canvas. */
export function resolveCanvasNodeReference(
  nodes: readonly CanvasNodeReferenceCandidate[],
  clientRefs: ReadonlyMap<string, string>,
  value: unknown,
  field: string
): CanvasNodeReferenceResolution {
  const reference = text(value)
  if (!reference) {
    return { ok: false, error: `${field} is required.` }
  }

  const clientRefNodeId = clientRefs.get(reference)
  if (clientRefNodeId) return { ok: true, nodeId: clientRefNodeId }
  if (nodes.some((node) => node.id === reference)) return { ok: true, nodeId: reference }

  const labelMatches = nodes.filter((node) => text(node.data?.label) === reference)
  if (labelMatches.length === 1) return { ok: true, nodeId: labelMatches[0]!.id }
  if (labelMatches.length > 1) {
    return {
      ok: false,
      error: `${field} ${JSON.stringify(reference)} is ambiguous; matching node ids: ${labelMatches.map((node) => node.id).sort().join(', ')}. Use a client_ref or node id.`
    }
  }
  return {
    ok: false,
    error: `${field} ${JSON.stringify(reference)} does not resolve to a node. Use a client_ref, node id, or exact unique node label.`
  }
}

/** Pure, atomic implementation of the edit data-plane operation. */
export function editWorkspaceNodes(
  workspace: Readonly<RuntimeWorkspace>,
  args: Readonly<Record<string, unknown>>,
  ownerNodeId: string,
  createId: () => string = randomUUID
): WorkspaceNodeOperationResult {
  const operations = Array.isArray(args.operations) ? args.operations : []
  if (operations.length === 0) return failure('operations must contain at least one operation.')
  const nodes = new Map(workspace.nodes.map((node) => [node.id, structuredClone(node)]))
  let edges = workspace.edges.map((edge) => structuredClone(edge))
  const updatedIds = new Set<string>()
  const deletedNodeIds: string[] = []
  const deletedEdgeIds: string[] = []
  const createdEdges: RuntimeEdge[] = []

  for (let index = 0; index < operations.length; index += 1) {
    const operation = record(operations[index])
    if (!operation) return failure(`operations[${index}] must be an object.`)
    const kind = text(operation.kind)
    const allowedFields = kind === 'patch'
      ? new Set(['kind', 'node_id', 'set'])
      : kind === 'move'
        ? new Set(['kind', 'node_id', 'scope', 'harness_node_id'])
        : kind === 'connect'
          ? new Set(['kind', 'node_a_id', 'node_b_id', 'relation'])
          : kind === 'disconnect'
            ? new Set(['kind', 'edge_id'])
            : kind === 'delete'
              ? new Set(['kind', 'node_id'])
              : undefined
    if (allowedFields) {
      const unknown = Object.keys(operation).filter((field) => !allowedFields.has(field))
      if (unknown.length > 0) return failure(`operations[${index}] has unsupported fields: ${unknown.join(', ')}.`)
    }
    if (kind === 'patch') {
      const nodeId = text(operation.node_id)
      const node = nodeId ? nodes.get(nodeId) : undefined
      if (!node) return failure(`Node was not found: ${nodeId ?? '(missing)'}.`)
      const set = record(operation.set)
      if (!set || Object.keys(set).length === 0) return failure(`operations[${index}].set must be a non-empty object.`)
      const update = applyPortableNodeDataUpdate(node, {
        updateIndex: index,
        nodeId: node.id,
        patch: {
          set
        },
        hasDataUpdate: operation.set !== undefined
      })
      if (!update.ok) return failure(update.issue.error)
      node.data = {
        ...node.data,
        ...update.data
      }
      updatedIds.add(node.id)
      continue
    }
    if (kind === 'move') {
      const nodeId = text(operation.node_id)
      const node = nodeId ? nodes.get(nodeId) : undefined
      if (!node) return failure(`Node was not found: ${nodeId ?? '(missing)'}.`)
      if (operation.scope === 'root') {
        delete node.parentId
        delete node.data.harnessNodeId
      } else if (operation.scope === 'harness') {
        const harnessId = text(operation.harness_node_id)
        if (!harnessId || nodes.get(harnessId)?.type !== 'harness') {
          return failure(`Harness was not found: ${harnessId ?? '(missing)'}.`)
        }
        node.parentId = harnessId
        node.data.harnessNodeId = harnessId
      } else {
        return failure(`operations[${index}].scope must be root or harness.`)
      }
      updatedIds.add(node.id)
      continue
    }
    if (kind === 'connect') {
      const nodeA = nodes.get(text(operation.node_a_id) ?? '')
      const nodeB = nodes.get(text(operation.node_b_id) ?? '')
      const relation = text(operation.relation)
      if (!nodeA || !nodeB || !relation) {
        return failure(`operations[${index}] has an invalid connect target or relation.`)
      }
      if (nodeA.id === nodeB.id) return failure('A node cannot be connected to itself.')
      if (scope(nodeA) !== scope(nodeB)) {
        return failure(`Cannot connect nodes across Harness scopes: ${nodeA.id}, ${nodeB.id}.`)
      }
      const existing = edges.find((edge) =>
        (edge.source === nodeA.id && edge.target === nodeB.id)
        || (edge.source === nodeB.id && edge.target === nodeA.id))
      if (existing) {
        existing.relation = relation
        deletedEdgeIds.push(existing.id)
        createdEdges.push(existing)
      } else {
        const edge: RuntimeEdge = {
          id: `edge-${createId()}`,
          source: nodeA.id,
          target: nodeB.id,
          relation,
          data: { connectedByAgentNodeId: ownerNodeId }
        }
        edges.push(edge)
        createdEdges.push(edge)
      }
      continue
    }
    if (kind === 'disconnect') {
      const edgeId = text(operation.edge_id)
      if (!edgeId || !edges.some((edge) => edge.id === edgeId)) {
        return failure(`Edge was not found: ${edgeId ?? '(missing)'}.`)
      }
      edges = edges.filter((edge) => edge.id !== edgeId)
      deletedEdgeIds.push(edgeId)
      continue
    }
    if (kind === 'delete') {
      const nodeId = text(operation.node_id)
      if (!nodeId || !nodes.has(nodeId)) return failure(`Node was not found: ${nodeId ?? '(missing)'}.`)
      if (nodeId === ownerNodeId) return failure('The running Agent cannot delete itself.')
      nodes.delete(nodeId)
      const incident = edges.filter((edge) => edge.source === nodeId || edge.target === nodeId)
      edges = edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId)
      deletedNodeIds.push(nodeId)
      deletedEdgeIds.push(...incident.map((edge) => edge.id))
      continue
    }
    return failure(`Unsupported operations[${index}].kind: ${kind ?? '(missing)'}.`)
  }

  return {
    result: { success: true, edited: operations.length },
    updatedNodes: [...updatedIds].flatMap((id) => {
      const node = nodes.get(id)
      return node ? [node] : []
    }),
    deletedNodeIds,
    deletedEdgeIds: [...new Set(deletedEdgeIds)],
    createdEdges
  }
}

/** Pure, atomic implementation of ordinary create operations; hosts may extend it for templates. */
export function createWorkspaceNodes(
  workspace: Readonly<RuntimeWorkspace>,
  args: Readonly<Record<string, unknown>>,
  ownerNodeId: string,
  createId: () => string = randomUUID
): WorkspaceNodeOperationResult {
  const rawNodes = Array.isArray(args.nodes) ? args.nodes : []
  const rawRelations = Array.isArray(args.relations) ? args.relations : []
  if (rawNodes.length === 0) return failure('nodes must contain at least one node.')
  const createdNodes: RuntimeNode[] = []
  const refs = new Map<string, string>()
  const existingIds = new Set(workspace.nodes.map((node) => node.id))

  for (let index = 0; index < rawNodes.length; index += 1) {
    const raw = record(rawNodes[index])
    if (!raw) return failure(`nodes[${index}] must be an object.`)
    const type = text(raw.type)
    const label = text(raw.label)
    const description = text(raw.description)
    const spec = getNodeTypeSpec(type)
    if (!type || !spec || spec.creatable === false) {
      return failure(`Unsupported node type at nodes[${index}]: ${type ?? '(missing)'}.`)
    }
    if (!label || !description) return failure(`nodes[${index}] requires label and description.`)
    const fields = validateCreateFieldsForType(type, extractCreateNodeFields(raw))
    if (!fields.ok) return failure(`nodes[${index}]: ${fields.error}`)
    const parentId = text(raw.harness_node_id)
    if (type === 'harness' && parentId) return failure('Harness nodes must be top-level.')
    if (parentId && !workspace.nodes.some((node) => node.id === parentId && node.type === 'harness')) {
      return failure(`Harness parent was not found: ${parentId}.`)
    }
    const clientRef = text(raw.client_ref)
    if (clientRef && refs.has(clientRef)) return failure(`Duplicate client_ref: ${clientRef}.`)
    let nodeId = `${type}-${createId()}`
    while (existingIds.has(nodeId)) nodeId = `${type}-${createId()}`
    existingIds.add(nodeId)
    if (clientRef) refs.set(clientRef, nodeId)
    createdNodes.push({
      id: nodeId,
      type,
      ...(parentId ? { parentId } : {}),
      data: {
        ...defaultNodeDataForType(type, label),
        ...fields.fields,
        label,
        description,
        ...(parentId ? { harnessNodeId: parentId } : {}),
        createdByAgentNodeId: ownerNodeId
      }
    })
  }

  const createdEdges: RuntimeEdge[] = []
  const allNodeList = [...workspace.nodes, ...createdNodes]
  const allNodes = new Map(allNodeList.map((node) => [node.id, node]))
  for (let index = 0; index < rawRelations.length; index += 1) {
    const raw = record(rawRelations[index])
    if (!raw) return failure(`relations[${index}] must be an object.`)
    const nodeARef = resolveCanvasNodeReference(allNodeList, refs, raw.node_a_ref, `relations[${index}].node_a_ref`)
    if (!nodeARef.ok) return failure(nodeARef.error)
    const nodeBRef = resolveCanvasNodeReference(allNodeList, refs, raw.node_b_ref, `relations[${index}].node_b_ref`)
    if (!nodeBRef.ok) return failure(nodeBRef.error)
    const relation = text(raw.relation)
    const nodeA = allNodes.get(nodeARef.nodeId)
    const nodeB = allNodes.get(nodeBRef.nodeId)
    if (!nodeA || !nodeB || !relation) return failure(`relations[${index}] has an invalid node reference or relation.`)
    if (nodeA.id === nodeB.id) return failure('A node cannot be connected to itself.')
    if (scope(nodeA) !== scope(nodeB)) return failure(`Cannot connect nodes across Harness scopes: ${nodeA.id}, ${nodeB.id}.`)
    createdEdges.push({
      id: `edge-${createId()}`,
      source: nodeA.id,
      target: nodeB.id,
      relation,
      data: { connectedByAgentNodeId: ownerNodeId }
    })
  }

  return {
    result: { success: true, created_node_ids: createdNodes.map((node) => node.id) },
    createdNodes,
    createdEdges
  }
}
