export {
  canvasNodeBelongsToHarness,
  canvasNodeScope,
  collectCanvasDescendantNodes,
  collectReachableCanvasNodes
} from '../canvas/canvas-graph-scope.js'
export {
  applyCanvasNodePlacements,
  canvasNodeParentId,
  type ApplyCanvasNodePlacementsResult,
  type CanvasNodePlacement,
  type CanvasNodePlacementUpdate,
  type PlaceableCanvasNode
} from '../canvas/canvas-node-placement.js'
export {
  agentHistoryClearPatch,
  clearAgentHistoryData
} from '../agent/agent-history.js'
export {
  DEFAULT_SEMANTIC_EDGE_RELATION,
  semanticEdgeFields,
  semanticEdgeSentence
} from '../canvas/semantic-edge.js'
export {
  deriveHarnessExposures,
  harnessExposureId,
  harnessExposureSurfaces,
  publicHarnessExposure
} from '../harness/harness-exposures.js'
export {
  DEFAULT_AGENT_TOOL_NAMES,
  normalizeAgentToolNames,
  usesAutomaticAgentToolSelection
} from '../agent/agent-tool-selection.js'
export type { HarnessExposure } from '../contracts.js'
export {
  buildPortableNodeObservation,
  type PortableNodeObservation,
  type PortableObservationDetail,
  type PortableObservationRequest
} from './portable-graph-tools.js'

export type SemanticElement = {
  id: string
  kind: string
  label: string
  value?: string
  disabled?: boolean
}

export type SemanticSnapshot = {
  version?: number
  url: string
  title: string
  pageText: string
  elements: SemanticElement[]
}

export type BrowserProfileChannel = 'conexus' | 'user'

export type BrowserActionInput =
  | { kind: 'refresh' }
  | { kind: 'focus' }
  | { kind: 'navigate'; url: string }
  | { kind: 'click'; elementId: string }
  | { kind: 'type'; elementId: string; text: string }
  | { kind: 'scroll'; deltaY?: number }

export type BrowserToolActionInput =
  | { kind: 'refresh' }
  | { kind: 'focus' }
  | { kind: 'navigate'; url: string }
  | { kind: 'click'; element_id: string }
  | { kind: 'type'; element_id: string; text: string }
  | { kind: 'scroll'; delta_y: number }

function browserActionRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasOnlyBrowserActionFields(value: Record<string, unknown>, fields: readonly string[]): boolean {
  const allowed = new Set(['browser_node_id', ...fields])
  return Object.keys(value).every((field) => allowed.has(field))
}

function browserActionText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

/** Parses the snake_case control_browser tool ABI into the internal browser runtime action. */
export function parseBrowserToolAction(value: unknown): BrowserActionInput | undefined {
  if (!browserActionRecord(value) || typeof value.kind !== 'string') return undefined
  if (value.kind === 'refresh' || value.kind === 'focus') {
    return hasOnlyBrowserActionFields(value, ['kind']) ? { kind: value.kind } : undefined
  }
  if (value.kind === 'navigate') {
    const url = browserActionText(value.url)
    return url && hasOnlyBrowserActionFields(value, ['kind', 'url'])
      ? { kind: 'navigate', url }
      : undefined
  }
  if (value.kind === 'click') {
    const elementId = browserActionText(value.element_id)
    return elementId && hasOnlyBrowserActionFields(value, ['kind', 'element_id'])
      ? { kind: 'click', elementId }
      : undefined
  }
  if (value.kind === 'type') {
    const elementId = browserActionText(value.element_id)
    return elementId && typeof value.text === 'string' && hasOnlyBrowserActionFields(value, ['kind', 'element_id', 'text'])
      ? { kind: 'type', elementId, text: value.text }
      : undefined
  }
  if (value.kind === 'scroll') {
    return typeof value.delta_y === 'number' && Number.isFinite(value.delta_y) && hasOnlyBrowserActionFields(value, ['kind', 'delta_y'])
      ? { kind: 'scroll', deltaY: value.delta_y }
      : undefined
  }
  return undefined
}

/** Returns only the Browser state needed for the Agent's next action. */
export function browserAgentToolResult(
  nodeId: string,
  value: BrowserRuntimeActionResult
): Record<string, unknown> {
  const error = value.loadError?.description
    ?? (value.success ? undefined : browserActionText(value.message))
  return {
    success: value.success,
    browser_node_id: nodeId,
    ...(value.snapshot
      ? {
          snapshot: {
            url: value.snapshot.url,
            title: value.snapshot.title,
            text: value.snapshot.pageText,
            elements: value.snapshot.elements
          }
        }
      : {}),
    ...(error ? { error } : {})
  }
}

export type BrowserLoadError = {
  code: number | null
  description: string
  url: string
}

export type BrowserSnapshotResult = {
  snapshot: SemanticSnapshot
  loadError?: BrowserLoadError | null
}

export type BrowserNodeObservation = {
  success: boolean
  browser_node_id: string
  snapshot: SemanticSnapshot
  loadError: BrowserLoadError | null
}

export type BrowserActionResult = {
  success: boolean
  message: string
  snapshot?: SemanticSnapshot
  loadError?: BrowserLoadError | null
}

export type BrowserRuntimeSnapshotResult = {
  snapshot: SemanticSnapshot
  loadError: BrowserLoadError | null
  previewDataUrl: string | null
}

export type BrowserRuntimePreviewResult = {
  url: string
  title: string
  loadError: BrowserLoadError | null
  previewDataUrl: string | null
}

export type BrowserRuntimeActionResult = BrowserActionResult & {
  previewDataUrl?: string | null
}

export function normalizeBrowserProfileChannel(raw: unknown): BrowserProfileChannel {
  return raw === 'user' ? 'user' : 'conexus'
}

export function normalizeBrowserUrl(raw: string): string {
  return /^https?:\/\//i.test(raw) ? raw : `https://${raw}`
}
