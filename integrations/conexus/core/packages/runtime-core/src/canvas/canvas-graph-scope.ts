export interface CanvasGraphNode {
  id: string
  type?: string
  parentId?: string
  data: Readonly<Record<string, unknown>>
}

export interface CanvasGraphEdge {
  source: string
  target: string
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

export function canvasNodeScope(node: CanvasGraphNode): string | null {
  return text(node.parentId) ?? text(node.data.harnessNodeId) ?? null
}

export function canvasNodeBelongsToHarness(node: CanvasGraphNode, harnessNodeId: string): boolean {
  return text(node.parentId) === harnessNodeId || text(node.data.harnessNodeId) === harnessNodeId
}

function expandCanvasDescendants<TNode extends CanvasGraphNode>(
  nodes: readonly TNode[],
  includedIds: Set<string>,
  rootNodeIds: readonly string[]
): void {
  const pendingParentIds = [...rootNodeIds]
  while (pendingParentIds.length > 0) {
    const parentNodeId = pendingParentIds.shift()!
    for (const node of nodes) {
      if (includedIds.has(node.id) || !canvasNodeBelongsToHarness(node, parentNodeId)) continue
      includedIds.add(node.id)
      pendingParentIds.push(node.id)
    }
  }
}

export function collectCanvasDescendantNodes<TNode extends CanvasGraphNode>(
  nodes: readonly TNode[],
  rootNodeId: string
): TNode[] {
  const includedIds = new Set<string>()
  expandCanvasDescendants(nodes, includedIds, [rootNodeId])
  return nodes.filter((node) => includedIds.has(node.id))
}

export function collectReachableCanvasNodes<TNode extends CanvasGraphNode>(
  nodes: readonly TNode[],
  edges: readonly CanvasGraphEdge[],
  startNodeId: string
): TNode[] {
  const startNode = nodes.find((node) => node.id === startNodeId)
  if (!startNode) return []
  const scope = canvasNodeScope(startNode)
  const scoped = nodes.filter((node) => canvasNodeScope(node) === scope)
  const scopedIds = new Set(scoped.map((node) => node.id))
  const adjacent = new Map<string, Set<string>>()
  for (const edge of edges) {
    if (!scopedIds.has(edge.source) || !scopedIds.has(edge.target)) continue
    if (!adjacent.has(edge.source)) adjacent.set(edge.source, new Set())
    if (!adjacent.has(edge.target)) adjacent.set(edge.target, new Set())
    adjacent.get(edge.source)!.add(edge.target)
    adjacent.get(edge.target)!.add(edge.source)
  }
  const includedIds = new Set([startNodeId])
  const pending = [startNodeId]
  while (pending.length > 0) {
    const current = pending.shift()!
    for (const neighbor of adjacent.get(current) ?? []) {
      if (includedIds.has(neighbor)) continue
      includedIds.add(neighbor)
      pending.push(neighbor)
    }
  }
  expandCanvasDescendants(
    nodes,
    includedIds,
    scoped.filter((node) => includedIds.has(node.id) && node.type === 'harness').map((node) => node.id)
  )
  return nodes.filter((node) => includedIds.has(node.id))
}
