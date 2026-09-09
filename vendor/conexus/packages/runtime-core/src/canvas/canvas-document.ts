export const CANVAS_DOCUMENT_SCHEMA = 'conexus.canvas'
export const CANVAS_DOCUMENT_VERSION = 3

export interface CanvasAssetRecord {
  name: string
  relativePath: string
  size: number
  updatedAt: string
}

export interface CanvasProjectMetadata {
  name: string
  savedAt: string
}

export interface CanvasRuntimeStatePolicy {
  persisted: false
  reason: string
}

export interface CanvasDocumentV3 {
  schema: typeof CANVAS_DOCUMENT_SCHEMA
  version: typeof CANVAS_DOCUMENT_VERSION
  metadata: CanvasProjectMetadata
  nodes: unknown
  edges: unknown
  assets: CanvasAssetRecord[]
  runtime: CanvasRuntimeStatePolicy
  /** Optional opaque optimistic-concurrency token used by multi-writer hosts. */
  revision?: string
}

export type CanvasDocument = CanvasDocumentV3
export type StoredCanvasDocument = Omit<CanvasDocumentV3, 'version'> & { version: 1 | 2 | 3 }

export function isCanvasDocument(value: unknown): value is StoredCanvasDocument {
  if (!value || typeof value !== 'object') return false
  const candidate = value as { schema?: unknown; version?: unknown }
  return candidate.schema === CANVAS_DOCUMENT_SCHEMA && (
    candidate.version === 1 ||
    candidate.version === 2 ||
    candidate.version === CANVAS_DOCUMENT_VERSION
  )
}

export function normalizeCanvasPayload(value: unknown): { nodes: unknown; edges: unknown } | null {
  if (!value || typeof value !== 'object') return null
  if (isCanvasDocument(value)) return { nodes: value.nodes, edges: value.edges }

  const legacy = value as { nodes?: unknown; edges?: unknown }
  if ('nodes' in legacy) return { nodes: legacy.nodes, edges: legacy.edges ?? [] }
  return null
}
