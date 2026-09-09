import {
  MAX_SEMANTIC_EDGE_RELATION_LENGTH,
  type SemanticEdgeFields
} from '@conexus/runtime-protocol'

export const DEFAULT_SEMANTIC_EDGE_RELATION = 'These nodes are related.'

interface SemanticEdgeInput {
  relation?: unknown
  /** Persisted Canvas migration inputs. These fields are removed from the canonical result. */
  kind?: unknown
  label?: unknown
  data?: unknown
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim()
    ? value.trim().slice(0, MAX_SEMANTIC_EDGE_RELATION_LENGTH)
    : undefined
}

function legacyContracts(value: unknown): string[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  const contracts = (value as { contracts?: unknown }).contracts
  return Array.isArray(contracts)
    ? contracts.filter((item): item is string => typeof item === 'string').map((item) => item.toLowerCase())
    : []
}

function legacyKind(value: unknown, label: string | undefined, data: unknown): string | undefined {
  if (typeof value === 'string' && value.trim()) return value.trim().toLowerCase()
  const contracts = legacyContracts(data)
  if (contracts.includes('runtime')) return 'invokes'
  if (contracts.includes('write')) return 'writes'
  if (contracts.includes('context')) return 'reads'
  switch (label?.toLowerCase()) {
    case 'context':
    case 'data':
      return 'reads'
    case 'output':
      return 'writes'
    case 'runtime':
    case 'workflow':
    case 'tool':
    case 'shell':
      return 'invokes'
    case 'delegate':
      return 'delegates'
    default:
      return undefined
  }
}

function legacyRelation(label: string | undefined, kind: string | undefined): string {
  if (label && !['context', 'output', 'runtime', 'workflow'].includes(label.toLowerCase())) {
    return label
  }
  switch (kind) {
    case 'reads':
      return 'One node uses the other as context.'
    case 'writes':
      return 'One node stores output in the other.'
    case 'invokes':
      if (label?.toLowerCase() === 'workflow') return 'One node runs the other as a workflow.'
      if (label?.toLowerCase() === 'tool') return 'One node uses the other as a tool.'
      if (label?.toLowerCase() === 'shell') return 'One node uses the other as a shell.'
      return 'One node uses the other as a runtime.'
    case 'delegates':
      return 'One Agent delegates work to the other.'
    case 'depends_on':
      return 'One node depends on the other.'
    default:
      return label ?? DEFAULT_SEMANTIC_EDGE_RELATION
  }
}

/**
 * Canonicalizes current semantic fields and narrowly migrates persisted legacy
 * Canvas edges that only carried an optional `label`.
 */
export function semanticEdgeFields(value: SemanticEdgeInput): SemanticEdgeFields {
  const existingRelation = text(value.relation)
  if (existingRelation) return { relation: existingRelation }

  const label = text(value.label)
  return {
    relation: legacyRelation(label, legacyKind(value.kind, label, value.data))
  }
}

export function semanticEdgeSentence(
  edge: SemanticEdgeFields,
  sourceName: string,
  targetName: string
): string {
  return `${sourceName} ↔ ${targetName}: ${edge.relation}`
}
