import { createHash } from 'node:crypto'
import type {
  HarnessExposure,
  HarnessExposureSurface,
  HarnessGraph,
  HarnessManifest,
  HarnessRuntime,
  HarnessSchemas,
  HarnessToolDefinition,
  HarnessVerification,
  RuntimeEdge,
  RuntimeNode,
  RuntimeWorkspace
} from '../contracts.js'
import { deriveHarnessExposures, harnessExposureId, harnessExposureSurfaces } from '../harness/harness-exposures.js'
import { semanticEdgeFields } from '../canvas/semantic-edge.js'

export const HARNESS_RELEASE_SCHEMA = 'conexus.harness.release' as const
export const HARNESS_RELEASE_SCHEMA_VERSION = 4 as const
export const HARNESS_RUNTIME_VERSION = 1 as const

export interface RuntimeAdapterRequirement {
  kind: 'node' | 'tool' | 'model' | 'runtime'
  name: string
  version?: string
  optional?: boolean
}

export interface HarnessReleaseArtifact {
  schema: typeof HARNESS_RELEASE_SCHEMA
  schemaVersion: typeof HARNESS_RELEASE_SCHEMA_VERSION
  runtimeVersion: typeof HARNESS_RUNTIME_VERSION
  manifest: HarnessManifest
  graph: HarnessGraph
  tools: HarnessToolDefinition[]
  schemas: HarnessSchemas
  runtime: HarnessRuntime
  verification: HarnessVerification
  requirements: RuntimeAdapterRequirement[]
}

export class HarnessContractError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly details?: unknown
  ) {
    super(message)
  }
}

const MUTABLE_NODE_DATA_FIELDS = new Set([
  'sessionId',
  'status',
  'summary',
  'messages',
  'completionGaps',
  'toolCounts',
  'lastError',
  'harnessNodeId',
  'browserLog',
  'browserEventSeq',
  'snapshotVersion',
  'pageTitle',
  'pageText',
  'semanticElements',
  'selectedElementId',
  'loadError',
  'taskLog',
  'cliSessionId',
  'lastArgs',
  'lastRunStartedAt',
  'lastRunCompletedAt',
  'lastStdout',
  'lastStderr',
  'lastExitCode',
  'lastTimedOut',
  'lastDurationMs',
  'lastResult'
])

const SENSITIVE_DATA_FIELDS = new Set([
  'apikey',
  'accesstoken',
  'refreshtoken',
  'authtoken',
  'bearertoken',
  'token',
  'password',
  'clientsecret',
  'secret',
  'privatekey',
  'connectionstring',
  'authorization',
  'cookie',
  'credentials'
])

const DECLARATIVE_SCHEMA_FIELDS = new Set(['inputSchema', 'outputSchema', 'schema', 'schemas'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).map((item) => item.trim())
    : []
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function rejectDuplicateValues(values: string[], code: string, label: string): void {
  const seen = new Set<string>()
  const duplicates = [...new Set(values.filter((value) => {
    if (seen.has(value)) return true
    seen.add(value)
    return false
  }))]
  if (duplicates.length === 0) return
  throw new HarnessContractError(`Harness ${label} values must be unique.`, code, { duplicates })
}

function cloneReleaseValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(cloneReleaseValue)
  if (!isRecord(value)) return value
  return Object.fromEntries(
    Object.entries(value).map(([key, nested]) => [key, cloneReleaseValue(nested)])
  )
}

function cleanNodeData(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !key.startsWith('_') && !MUTABLE_NODE_DATA_FIELDS.has(key))
      .map(([key, nested]) => [key, cloneReleaseValue(nested)])
  )
}

function normalizedSensitiveKey(key: string): string {
  return key.toLowerCase().replace(/[-_]/g, '')
}

function isSecretReference(value: unknown): boolean {
  if (typeof value === 'string') return /^\s*\{\{\s*[^{}]+\s*\}\}\s*$/.test(value)
  if (!isRecord(value)) return false
  const keys = Object.keys(value)
  return keys.length > 0
    && keys.every((key) => ['secretRef', 'provider', 'name', 'version'].includes(key))
    && typeof value.secretRef === 'string'
    && Boolean(value.secretRef.trim())
}

function embeddedSecretFields(value: unknown, path = ''): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => embeddedSecretFields(item, `${path}[${index}]`))
  }
  if (!isRecord(value)) return []

  return Object.entries(value).flatMap(([key, nested]) => {
    const fieldPath = path ? `${path}.${key}` : key
    if (DECLARATIVE_SCHEMA_FIELDS.has(key)) return []
    const containsValue = nested !== undefined
      && nested !== null
      && nested !== ''
      && (!Array.isArray(nested) || nested.length > 0)
      && (!isRecord(nested) || Object.keys(nested).length > 0)
    if (SENSITIVE_DATA_FIELDS.has(normalizedSensitiveKey(key)) && containsValue && !isSecretReference(nested)) {
      return [fieldPath]
    }
    return embeddedSecretFields(nested, fieldPath)
  })
}

function rejectEmbeddedSecrets(nodeId: string, value: Record<string, unknown>): void {
  const fields = embeddedSecretFields(value)
  if (fields.length === 0) return
  throw new HarnessContractError(
        `Harness release data at ${nodeId} embeds secret material. Use a runtime secret reference instead.`,
    'embedded_secret',
    { nodeId, fields }
  )
}

function sourceGraph(source: Record<string, unknown>): { nodes: RuntimeNode[]; edges: RuntimeEdge[] } {
  const graph = isRecord(source.graph) ? source.graph : source
  const rawNodes = Array.isArray(graph.nodes)
    ? graph.nodes
    : Array.isArray(source.internalNodes)
      ? source.internalNodes
      : []
  const rawEdges = Array.isArray(graph.edges)
    ? graph.edges
    : Array.isArray(source.internalEdges)
      ? source.internalEdges
      : []
  const nodes: RuntimeNode[] = rawNodes
    .filter(isRecord)
    .map((node) => {
      const id = stringValue(node.id) ?? ''
      const data = isRecord(node.data) ? node.data : {}
      rejectEmbeddedSecrets(id || '(unknown)', data)
      return {
        id,
        type: stringValue(node.type) ?? 'unknown',
        data: cleanNodeData(data),
        ...(stringValue(node.parentId) ? { parentId: stringValue(node.parentId) } : {})
      }
    })
    .filter((node) => Boolean(node.id))
  const edges: RuntimeEdge[] = rawEdges
    .filter(isRecord)
    .map((edge, index) => {
      const id = stringValue(edge.id) ?? `edge-${index}`
      const data = isRecord(edge.data) ? edge.data : undefined
      if (data) rejectEmbeddedSecrets(`edge:${id}`, data)
      return {
        id,
        source: stringValue(edge.source) ?? '',
        target: stringValue(edge.target) ?? '',
        ...semanticEdgeFields(edge),
        ...(data ? { data: cloneReleaseValue(data) as Record<string, unknown> } : {})
      }
    })
    .filter((edge) => Boolean(edge.source && edge.target))
  return { nodes, edges }
}

const EXPOSURE_SURFACES = new Set<HarnessExposureSurface>(['agent_tool', 'page', 'api'])

function normalizeExposureSurfaces(value: unknown, nodeType: string): HarnessExposureSurface[] {
  const surfaces = stringArray(value)
    .filter((surface): surface is HarnessExposureSurface => EXPOSURE_SURFACES.has(surface as HarnessExposureSurface))
  return [...new Set(surfaces.length > 0 ? surfaces : harnessExposureSurfaces(nodeType))]
}

function normalizeExposures(params: {
  value: unknown
  nodes: RuntimeNode[]
}): HarnessExposure[] {
  const nodesById = new Map(params.nodes.map((node) => [node.id, node]))
  const rawExposures = recordArray(params.value)
  const exposures = rawExposures.flatMap<HarnessExposure>((raw) => {
    if (['inputs', 'outputs', 'inputSchema', 'outputSchema'].some((key) => key in raw)) {
      throw new HarnessContractError(
        'Harness exposures only select nodes and surfaces; Harness-owned IO contracts are not supported.',
        'obsolete_harness_exposure_io',
        { exposureId: stringValue(raw.id), nodeId: stringValue(raw.nodeId) }
      )
    }
    const nodeId = stringValue(raw.nodeId)
    if (!nodeId) return []
    const node = nodesById.get(nodeId)
    if (!node) {
      throw new HarnessContractError(
        `Harness exposure ${stringValue(raw.id) ?? nodeId} references an unknown node.`,
        'invalid_exposure_node',
        { exposureId: stringValue(raw.id), nodeId }
      )
    }
    if (node.data.exposeInHarness !== true) {
      throw new HarnessContractError(
        `Harness exposure ${stringValue(raw.id) ?? nodeId} targets a node that is not explicitly exposed.`,
        'unexposed_harness_node',
        { exposureId: stringValue(raw.id), nodeId }
      )
    }
    const nodeType = node?.type ?? stringValue(raw.nodeType) ?? 'unknown'
    const name = stringValue(raw.name) ?? stringValue(node?.data.label) ?? nodeId
    const id = harnessExposureId(stringValue(raw.id) ?? name, harnessExposureId(nodeId))
    return [{
      id,
      name,
      ...(stringValue(raw.description) ? { description: stringValue(raw.description) } : {}),
      nodeId,
      nodeType,
      surfaces: normalizeExposureSurfaces(raw.surfaces, nodeType)
    }]
  })
  return deriveHarnessExposures(params.nodes, exposures)
}

function normalizeTools(value: unknown): HarnessToolDefinition[] {
  return recordArray(value).flatMap<HarnessToolDefinition>((tool) => {
      const name = stringValue(tool.name)
      if (!name) return []
      const runtime = tool.runtime === 'node' || tool.runtime === 'python' || tool.runtime === 'powershell' || tool.runtime === 'bash' || tool.runtime === 'shell' || tool.runtime === 'registered' || tool.runtime === 'mcp'
        ? tool.runtime
        : undefined
      const normalized: HarnessToolDefinition = {
        name,
        ...(stringValue(tool.description) ? { description: stringValue(tool.description) } : {}),
        ...(runtime ? { runtime } : {}),
        ...(typeof tool.code === 'string' ? { code: tool.code } : {}),
        ...(isRecord(tool.inputSchema) ? { inputSchema: cloneReleaseValue(tool.inputSchema) as Record<string, unknown> } : {}),
        ...(isRecord(tool.outputSchema) ? { outputSchema: cloneReleaseValue(tool.outputSchema) as Record<string, unknown> } : {}),
        ...(Array.isArray(tool.permissions) ? { permissions: cloneReleaseValue(tool.permissions) as HarnessToolDefinition['permissions'] } : {}),
        ...(typeof tool.exposeAsTool === 'boolean' ? { exposeAsTool: tool.exposeAsTool } : {}),
        ...(stringValue(tool.nodeId) ? { nodeId: stringValue(tool.nodeId) } : {})
      }
      return [normalized]
    })
}

function requirementsFor(nodes: RuntimeNode[], tools: HarnessToolDefinition[], manifest: HarnessManifest): RuntimeAdapterRequirement[] {
  const requirements = new Map<string, RuntimeAdapterRequirement>()
  for (const node of nodes) {
    const value: RuntimeAdapterRequirement = { kind: 'node', name: node.type }
    requirements.set(`${value.kind}:${value.name}`, value)
  }
  for (const tool of tools) {
    const value: RuntimeAdapterRequirement = { kind: 'tool', name: tool.name, ...(tool.runtime ? { version: tool.runtime } : {}) }
    requirements.set(`${value.kind}:${value.name}`, value)
  }
  for (const dependency of manifest.dependencies) {
    if (dependency.kind !== 'model' && dependency.kind !== 'runtime' && dependency.kind !== 'tool') continue
    const value: RuntimeAdapterRequirement = {
      kind: dependency.kind,
      name: dependency.name,
      ...(dependency.version ? { version: dependency.version } : {}),
      ...(dependency.optional ? { optional: true } : {})
    }
    requirements.set(`${value.kind}:${value.name}`, value)
  }
  return [...requirements.values()].sort((a, b) => `${a.kind}:${a.name}`.localeCompare(`${b.kind}:${b.name}`))
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(',')}}`
  }
  return JSON.stringify(value) ?? 'null'
}

function cloneRuntimeValue<T>(value: T): T {
  if (Array.isArray(value)) return value.map((item) => cloneRuntimeValue(item)) as T
  if (!isRecord(value)) return value
  return Object.fromEntries(
    Object.entries(value).map(([key, nested]) => [key, cloneRuntimeValue(nested)])
  ) as T
}

/**
 * Creates an isolated runtime workspace for one exposed node. A durable
 * instance snapshot may be supplied to continue prior node state.
 */
export function instantiateHarnessWorkspace(
  value: HarnessReleaseArtifact,
  exposureId: string,
  instanceWorkspace?: Readonly<RuntimeWorkspace>
): RuntimeWorkspace {
  const release = validateHarnessRelease(value)
  const exposure = resolveHarnessExposure(release, exposureId)
  if (instanceWorkspace && (!Array.isArray(instanceWorkspace.nodes) || !Array.isArray(instanceWorkspace.edges))) {
    throw new HarnessContractError(
      'Harness instance workspace must contain node and edge arrays.',
      'invalid_instance_workspace'
    )
  }
  const workspace = cloneRuntimeValue(instanceWorkspace
    ? { nodes: instanceWorkspace.nodes, edges: instanceWorkspace.edges }
    : { nodes: release.graph.nodes, edges: release.graph.edges })
  const nodeIds = new Set<string>()
  for (const node of workspace.nodes) {
    if (!node?.id?.trim() || !node.type?.trim() || !isRecord(node.data) || nodeIds.has(node.id)) {
      throw new HarnessContractError(
        'Harness instance workspace contains an invalid or duplicate node.',
        'invalid_instance_workspace',
        { nodeId: node?.id }
      )
    }
    nodeIds.add(node.id)
  }
  const edgeIds = new Set<string>()
  for (const edge of workspace.edges) {
    if (!edge?.id?.trim()
      || edgeIds.has(edge.id)
      || !nodeIds.has(edge.source)
      || !nodeIds.has(edge.target)) {
      throw new HarnessContractError(
        'Harness instance workspace contains an invalid edge.',
        'invalid_instance_workspace',
        { edgeId: edge?.id }
      )
    }
    edgeIds.add(edge.id)
  }
  const releaseTarget = release.graph.nodes.find((node) => node.id === exposure.nodeId)
  const instanceTarget = workspace.nodes.find((node) => node.id === exposure.nodeId)
  if (!releaseTarget || !instanceTarget || releaseTarget.type !== instanceTarget.type) {
    throw new HarnessContractError(
      `Harness instance workspace is missing exposure target ${exposure.nodeId}.`,
      'invalid_instance_workspace',
      { exposureId, nodeId: exposure.nodeId }
    )
  }
  return workspace
}

export function harnessReleaseChecksum(release: HarnessReleaseArtifact): string {
  return createHash('sha256').update(canonicalJson(release)).digest('hex')
}

export function resolveHarnessExposure(
  value: HarnessReleaseArtifact,
  exposureId?: string,
  requiredSurface?: HarnessExposureSurface
): HarnessExposure {
  const release = validateHarnessRelease(value)
  const candidates = requiredSurface
    ? release.manifest.exposures.filter((exposure) => exposure.surfaces.includes(requiredSurface))
    : release.manifest.exposures
  const requestedId = stringValue(exposureId) ?? release.manifest.defaultExposureId
  if (requestedId) {
    const exposure = candidates.find((candidate) => candidate.id === requestedId)
    if (exposure) return exposure
    throw new HarnessContractError(
      `Harness exposure was not found or is unavailable on this surface: ${requestedId}.`,
      'exposure_not_found',
      { exposureId: requestedId, requiredSurface }
    )
  }
  if (candidates.length === 1) return candidates[0]!
  throw new HarnessContractError(
    candidates.length === 0
      ? 'Harness has no exposure available on this surface.'
      : 'Harness exposes multiple capabilities; exposureId is required.',
    candidates.length === 0 ? 'exposure_not_found' : 'exposure_id_required',
    { requiredSurface, exposureIds: candidates.map((candidate) => candidate.id) }
  )
}

export function bindHarnessReleaseExposure(
  value: HarnessReleaseArtifact,
  exposureId?: string,
  requiredSurface?: HarnessExposureSurface
): { release: HarnessReleaseArtifact; exposure: HarnessExposure } {
  const validated = validateHarnessRelease(value)
  const exposure = resolveHarnessExposure(validated, exposureId, requiredSurface)
  return {
    release: cloneRuntimeValue(validated),
    exposure: cloneRuntimeValue(exposure)
  }
}

export function compileHarnessRelease(value: unknown): HarnessReleaseArtifact {
  if (!isRecord(value)) throw new HarnessContractError('Harness package must be a JSON object.', 'invalid_harness_package')
  const source = value
  if (source.schema === HARNESS_RELEASE_SCHEMA) return validateHarnessRelease(source)

  const manifestRaw = isRecord(source.manifest) ? source.manifest : source
  const graph = sourceGraph(source)
  if (graph.nodes.length === 0) throw new HarnessContractError('Harness graph must contain at least one node.', 'empty_harness_graph')
  rejectDuplicateValues(graph.nodes.map((node) => node.id), 'duplicate_node_id', 'node id')
  rejectDuplicateValues(graph.edges.map((edge) => edge.id), 'duplicate_edge_id', 'edge id')
  const nodeIds = new Set(graph.nodes.map((node) => node.id))
  for (const edge of graph.edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      throw new HarnessContractError(`Edge ${edge.id} references a node outside the graph.`, 'invalid_edge', edge)
    }
  }

  const schemasRaw = isRecord(source.schemas) ? source.schemas : {}
  const obsoleteContractFields = [
    'inputs' in manifestRaw,
    'outputs' in manifestRaw,
    'entrypoint' in manifestRaw,
    'entrypoint' in (isRecord(source.graph) ? source.graph : {}),
    'requiredInputs' in source,
    'required_inputs' in source,
    'recommendedInputs' in source,
    'recommended_inputs' in source,
    'expectedOutputs' in source,
    'expected_outputs' in source,
    'inputSchema' in source,
    'input_schema' in source,
    'outputSchema' in source,
    'output_schema' in source,
    'exposedInputs' in source,
    'exposed_inputs' in source,
    'exposedOutputs' in source,
    'exposed_outputs' in source,
    'artifacts' in source,
    'traces' in source,
    'state' in source,
    'input' in schemasRaw,
    'output' in schemasRaw
  ]
  if (obsoleteContractFields.some(Boolean)) {
    throw new HarnessContractError(
      'Harness-owned IO, runtime state, and entrypoint fields are not supported. Exposures only select nodes and surfaces.',
      'obsolete_harness_contract'
    )
  }
  const exposures = normalizeExposures({
    value: manifestRaw.exposures,
    nodes: graph.nodes
  })
  if (exposures.length === 0) {
    throw new HarnessContractError(
      'Harness manifest must expose at least one capability.',
      'missing_harness_exposure'
    )
  }
  rejectDuplicateValues(exposures.map((exposure) => exposure.id), 'duplicate_exposure_id', 'exposure id')
  for (const exposure of exposures) {
    const target = graph.nodes.find((node) => node.id === exposure.nodeId)
    if (!target) {
      throw new HarnessContractError(
        `Harness exposure ${exposure.id} references an unknown node.`,
        'invalid_exposure_node',
        { exposureId: exposure.id, nodeId: exposure.nodeId }
      )
    }
    if (target.data.exposeInHarness !== true) {
      throw new HarnessContractError(
        `Harness exposure ${exposure.id} targets a node that is not explicitly exposed.`,
        'unexposed_harness_node',
        { exposureId: exposure.id, nodeId: exposure.nodeId }
      )
    }
    exposure.nodeType = target.type
  }
  const requestedDefaultExposureId = stringValue(manifestRaw.defaultExposureId)
  const defaultExposureId = requestedDefaultExposureId
    ?? (exposures.length === 1 ? exposures[0]!.id : undefined)
  if (defaultExposureId && !exposures.some((exposure) => exposure.id === defaultExposureId)) {
    throw new HarnessContractError(
      'Harness defaultExposureId does not reference a declared exposure.',
      'invalid_default_exposure',
      { defaultExposureId }
    )
  }
  const name = stringValue(manifestRaw.name)
  if (!name) throw new HarnessContractError('Harness manifest name is required.', 'missing_manifest_name')
  const id = stringValue(manifestRaw.id) ?? `harness.${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`
  const manifest: HarnessManifest = {
    id,
    name,
    ...(stringValue(manifestRaw.summary) ? { summary: stringValue(manifestRaw.summary) } : {}),
    description: stringValue(manifestRaw.description) ?? stringValue(manifestRaw.summary) ?? name,
    version: typeof manifestRaw.version === 'number' && Number.isInteger(manifestRaw.version) && manifestRaw.version > 0 ? manifestRaw.version : 1,
    exposures,
    ...(defaultExposureId ? { defaultExposureId } : {}),
    permissions: cloneReleaseValue(recordArray(manifestRaw.permissions)) as HarnessManifest['permissions'],
    dependencies: cloneReleaseValue(recordArray(manifestRaw.dependencies)) as HarnessManifest['dependencies'],
    capabilities: stringArray(manifestRaw.capabilities),
    triggers: stringArray(manifestRaw.triggers),
    tags: stringArray(manifestRaw.tags),
    ...(stringValue(manifestRaw.author) ? { author: stringValue(manifestRaw.author) } : {})
  }
  rejectEmbeddedSecrets('manifest', manifest as unknown as Record<string, unknown>)
  const tools = normalizeTools(source.tools)
  rejectDuplicateValues(tools.map((tool) => tool.name), 'duplicate_tool_name', 'tool name')
  for (const tool of tools) rejectEmbeddedSecrets(`tool:${tool.name}`, tool as unknown as Record<string, unknown>)
  const schemas: HarnessSchemas = {
    ...(isRecord(schemasRaw.definitions) ? { definitions: cloneReleaseValue(schemasRaw.definitions) as Record<string, Record<string, unknown>> } : {})
  }
  const runtime = isRecord(source.runtime) ? cloneReleaseValue(source.runtime) as HarnessRuntime : {}
  const verification = isRecord(source.verification)
    ? cloneReleaseValue(source.verification) as HarnessVerification
    : { successCriteria: stringArray(source.completionCriteria) }
  rejectEmbeddedSecrets('runtime', runtime as unknown as Record<string, unknown>)
  rejectEmbeddedSecrets('verification', verification as unknown as Record<string, unknown>)

  return {
    schema: HARNESS_RELEASE_SCHEMA,
    schemaVersion: HARNESS_RELEASE_SCHEMA_VERSION,
    runtimeVersion: HARNESS_RUNTIME_VERSION,
    manifest,
    graph: { nodes: graph.nodes, edges: graph.edges },
    tools,
    schemas,
    runtime,
    verification,
    requirements: requirementsFor(graph.nodes, tools, manifest)
  }
}

export function validateHarnessRelease(value: unknown): HarnessReleaseArtifact {
  if (!isRecord(value)) throw new HarnessContractError('Harness release must be a JSON object.', 'invalid_release')
  if (value.schema !== HARNESS_RELEASE_SCHEMA || value.schemaVersion !== HARNESS_RELEASE_SCHEMA_VERSION || value.runtimeVersion !== HARNESS_RUNTIME_VERSION) {
    throw new HarnessContractError('Unsupported Harness release contract version.', 'unsupported_release_version', {
      schema: value.schema,
      schemaVersion: value.schemaVersion,
      runtimeVersion: value.runtimeVersion
    })
  }
  const manifest = value.manifest
  const graph = value.graph
  if (!isRecord(manifest) || !isRecord(graph)) throw new HarnessContractError('Harness release manifest and graph are required.', 'invalid_release')
  const normalized = compileHarnessRelease({
    schema: 'conexus.harness',
    manifest,
    graph,
    tools: value.tools,
    schemas: value.schemas,
    runtime: value.runtime,
    verification: value.verification
  })
  return normalized
}
