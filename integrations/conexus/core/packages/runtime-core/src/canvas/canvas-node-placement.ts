export interface PlaceableCanvasNode {
  id: string
  type?: string
  parentId?: string
  extent?: unknown
  data?: Record<string, unknown>
}

export type CanvasNodePlacement =
  | { type: 'root' }
  | { type: 'harness'; harnessNodeId: string }

export interface CanvasNodePlacementUpdate {
  nodeId: string
  placement: CanvasNodePlacement
}

export type ApplyCanvasNodePlacementsResult<T extends PlaceableCanvasNode> =
  | { ok: true; nodes: T[]; changedNodes: T[] }
  | { ok: false; error: string }

export function canvasNodeParentId(node: PlaceableCanvasNode | undefined): string | undefined {
  const harnessNodeId = typeof node?.data?.harnessNodeId === 'string' && node.data.harnessNodeId.trim()
    ? node.data.harnessNodeId.trim()
    : undefined
  return node?.parentId ?? harnessNodeId
}

function nodeAtRoot<T extends PlaceableCanvasNode>(node: T): T {
  const { parentId: _parentId, extent: _extent, ...rest } = node
  const data = { ...(node.data ?? {}) }
  delete data.harnessNodeId
  delete data.spaceNodeId
  return { ...rest, data } as T
}

function nodeInHarness<T extends PlaceableCanvasNode>(node: T, harnessNodeId: string): T {
  const data: Record<string, unknown> = { ...(node.data ?? {}), harnessNodeId }
  delete data.spaceNodeId
  return {
    ...node,
    parentId: harnessNodeId,
    extent: 'parent',
    data
  } as T
}

export function applyCanvasNodePlacements<T extends PlaceableCanvasNode>(
  nodes: T[],
  updates: CanvasNodePlacementUpdate[]
): ApplyCanvasNodePlacementsResult<T> {
  if (updates.length === 0) return { ok: true, nodes, changedNodes: [] }

  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const placementByNodeId = new Map<string, CanvasNodePlacement>()
  for (const update of updates) {
    if (!nodeById.has(update.nodeId)) return { ok: false, error: `node not found: ${update.nodeId}` }
    if (placementByNodeId.has(update.nodeId)) return { ok: false, error: `duplicate node placement: ${update.nodeId}` }
    if (update.placement.type === 'harness') {
      const harness = nodeById.get(update.placement.harnessNodeId)
      if (!harness) return { ok: false, error: `target harness not found: ${update.placement.harnessNodeId}` }
      if (harness.type !== 'harness') return { ok: false, error: `placement target is not a Harness: ${update.placement.harnessNodeId}` }
      if (harness.id === update.nodeId) return { ok: false, error: `a Harness cannot contain itself: ${update.nodeId}` }
    }
    placementByNodeId.set(update.nodeId, update.placement)
  }

  const proposedParentByNodeId = new Map<string, string | undefined>()
  for (const node of nodes) {
    const placement = placementByNodeId.get(node.id)
    proposedParentByNodeId.set(
      node.id,
      placement?.type === 'root'
        ? undefined
        : placement?.type === 'harness'
          ? placement.harnessNodeId
          : canvasNodeParentId(node)
    )
  }

  for (const node of nodes) {
    const visited = new Set<string>([node.id])
    let parentId = proposedParentByNodeId.get(node.id)
    while (parentId) {
      if (visited.has(parentId)) return { ok: false, error: `node placement would create a parent cycle involving: ${parentId}` }
      visited.add(parentId)
      parentId = proposedParentByNodeId.get(parentId)
    }
  }

  const changedNodes: T[] = []
  const nextNodes = nodes.map((node) => {
    const placement = placementByNodeId.get(node.id)
    if (!placement) return node
    const nextNode = placement.type === 'root'
      ? nodeAtRoot(node)
      : nodeInHarness(node, placement.harnessNodeId)
    changedNodes.push(nextNode)
    return nextNode
  })
  return { ok: true, nodes: nextNodes, changedNodes }
}
