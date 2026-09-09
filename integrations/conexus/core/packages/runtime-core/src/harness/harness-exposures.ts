import type {
  HarnessExposure,
  HarnessExposureSurface,
  PublicHarnessExposure,
  RuntimeNode
} from '../contracts.js'

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function harnessExposureId(value: string, fallback = 'exposure'): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return normalized || fallback
}

export function harnessExposureSurfaces(nodeType: string): HarnessExposureSurface[] {
  if (nodeType === 'agent') return ['agent_tool', 'page', 'api']
  if (nodeType === 'tool') return ['agent_tool', 'api']
  return ['page']
}

export function deriveHarnessExposures(
  nodes: readonly RuntimeNode[],
  declaredExposures: readonly HarnessExposure[] = []
): HarnessExposure[] {
  const declaredByNodeId = new Map(declaredExposures.map((exposure) => [exposure.nodeId, exposure]))
  const exposed = nodes
    .filter((node) => node.data.exposeInHarness === true)
    .map((node) => {
      const declared = declaredByNodeId.get(node.id)
      const name = text(declared?.name) || text(node.data.label) || node.id
      return {
        node,
        declared,
        baseId: harnessExposureId(text(declared?.id) || text(node.data.exposureId) || name, harnessExposureId(node.id))
      }
    })
    .sort((left, right) => left.baseId.localeCompare(right.baseId) || left.node.id.localeCompare(right.node.id))
  const counts = new Map<string, number>()
  return exposed.map(({ node, declared, baseId }) => {
    const count = (counts.get(baseId) ?? 0) + 1
    counts.set(baseId, count)
    const id = count === 1 ? baseId : `${baseId}-${count}`
    const name = text(declared?.name) || text(node.data.label) || node.id
    const description = text(declared?.description) || text(node.data.description ?? node.data.objective ?? node.data.summary)
    return {
      id,
      name,
      ...(description ? { description } : {}),
      nodeId: node.id,
      nodeType: node.type,
      surfaces: declared?.surfaces.length ? [...declared.surfaces] : harnessExposureSurfaces(node.type)
    }
  })
}

export function publicHarnessExposure(exposure: HarnessExposure): PublicHarnessExposure {
  return structuredClone({
    id: exposure.id,
    name: exposure.name,
    ...(exposure.description ? { description: exposure.description } : {}),
    nodeType: exposure.nodeType,
    surfaces: exposure.surfaces
  })
}
