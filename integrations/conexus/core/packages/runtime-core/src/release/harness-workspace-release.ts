import { collectCanvasDescendantNodes } from '../canvas/canvas-graph-scope.js'
import { compileHarnessRelease, type HarnessReleaseArtifact } from './release.js'
import { deriveHarnessExposures } from '../harness/harness-exposures.js'
import type { HarnessExposure, RuntimeNode, RuntimeWorkspace } from '../contracts.js'


const OMITTED_RUNTIME_FIELDS = new Set([
  'sessionId', 'harnessNodeId', 'targetBrowserNodeId', 'browserLog', 'browserEventSeq',
  'snapshotVersion', 'pageTitle', 'pageText', 'semanticElements', 'selectedElementId', 'loadError',
  'taskLog', 'cliSessionId', 'lastArgs', 'lastRunStartedAt', 'lastRunCompletedAt', 'lastStdout',
  'lastStderr', 'lastExitCode', 'lastTimedOut', 'lastDurationMs', 'lastResult', 'lastError',
  'activeAgentSessionId', 'activeAgentJobId', 'pendingAsk', 'connectionUrl', 'password', 'apiKey'
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? [...new Set(value.filter((item): item is string => typeof item === 'string').map((item) => item.trim()).filter(Boolean))]
    : []
}

function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord).map((item) => structuredClone(item)) : []
}

function sanitizeData(data: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(data)
    .filter(([key]) => !OMITTED_RUNTIME_FIELDS.has(key) && !key.startsWith('_'))
    .map(([key, value]) => [key, structuredClone(value)]))
}

function toolDefinitions(nodes: RuntimeNode[]): Record<string, unknown>[] {
  return nodes.flatMap((node): Record<string, unknown>[] => {
    if (node.type !== 'tool') return []
    const name = text(node.data.toolName) || text(node.data.name) || node.id
    return [{
      name,
      ...(text(node.data.description) ? { description: text(node.data.description) } : {}),
      ...(text(node.data.runtime) ? { runtime: text(node.data.runtime) } : {}),
      ...(typeof node.data.code === 'string' ? { code: node.data.code } : {}),
      ...(isRecord(node.data.inputSchema) ? { inputSchema: structuredClone(node.data.inputSchema) } : {}),
      ...(isRecord(node.data.outputSchema) ? { outputSchema: structuredClone(node.data.outputSchema) } : {}),
      ...(typeof node.data.exposeAsTool === 'boolean' ? { exposeAsTool: node.data.exposeAsTool } : {}),
      nodeId: node.id
    }]
  })
}

export function buildHarnessPackage(params: {
  workspace: Readonly<RuntimeWorkspace>
  harnessNodeId: string
  sourceKey: string
  name: string
  description: string
  tags: string[]
  successSummary: string
}): Record<string, unknown> {
  const harness = params.workspace.nodes.find((node) => node.id === params.harnessNodeId && node.type === 'harness')
  if (!harness) throw new Error(`Harness node was not found: ${params.harnessNodeId}.`)
  const descendants = collectCanvasDescendantNodes(params.workspace.nodes, harness.id)
  if (descendants.length === 0) throw new Error('The Harness has no internal nodes to save.')
  const contained = new Set(descendants.map((node) => node.id))
  const harnessTemplate = isRecord(harness.data.template) ? harness.data.template : {}
  const templateManifest = isRecord(harnessTemplate.manifest) ? harnessTemplate.manifest : {}
  const nodes = descendants.map((node) => ({
    id: node.id,
    type: node.type,
    ...(node.parentId && contained.has(node.parentId) ? { parentId: node.parentId } : {}),
    data: sanitizeData(node.data)
  }))
  const edges = params.workspace.edges
    .filter((edge) => contained.has(edge.source) && contained.has(edge.target))
    .map((edge) => structuredClone(edge))
  const declaredExposures = recordList(templateManifest.exposures) as unknown as HarnessExposure[]
  const exposures = deriveHarnessExposures(nodes, declaredExposures)
  if (exposures.length === 0) {
    throw new Error('Expose at least one Harness node.')
  }
  const requestedDefaultExposureId = text(harness.data.defaultExposureId)
    || text(templateManifest.defaultExposureId)
  const defaultExposureId = exposures.some((exposure) => text(exposure.id) === requestedDefaultExposureId)
    ? requestedDefaultExposureId
    : exposures.length === 1
      ? text(exposures[0]?.id)
      : ''
  const permissions = recordList(templateManifest.permissions)
  if (descendants.some((node) => node.type === 'agent')
    && !permissions.some((permission) => permission.kind === 'model')) {
    permissions.push({ kind: 'model', required: true })
  }
  if (descendants.some((node) => node.type === 'tool' || node.type === 'cli')
    && !permissions.some((permission) => permission.kind === 'shell')) {
    permissions.push({ kind: 'shell', required: true })
  }
  if (descendants.some((node) => node.type === 'files' || node.type === 'datasource')
    && !permissions.some((permission) => permission.kind === 'filesystem')) {
    permissions.push({ kind: 'filesystem', required: true })
  }
  const dependencies = recordList(templateManifest.dependencies)
  if (descendants.some((node) => node.type === 'agent')
    && !dependencies.some((dependency) => dependency.kind === 'model')) {
    dependencies.push({ kind: 'model', name: 'openrouter' })
  }
  const templateSchemas = isRecord(harnessTemplate.schemas) ? harnessTemplate.schemas : {}
  const schemas = isRecord(templateSchemas.definitions)
    ? { definitions: structuredClone(templateSchemas.definitions) }
    : {}
  const runtime = isRecord(harnessTemplate.runtime) ? structuredClone(harnessTemplate.runtime) : undefined
  return {
    schema: 'conexus.harness',
    manifest: {
      id: params.sourceKey,
      name: params.name,
      summary: params.description,
      description: params.description,
      version: 1,
      exposures,
      ...(defaultExposureId ? { defaultExposureId } : {}),
      permissions,
      dependencies,
      capabilities: stringList(templateManifest.capabilities ?? harnessTemplate.capabilities),
      triggers: stringList(templateManifest.triggers ?? harnessTemplate.triggers),
      tags: params.tags
    },
    graph: { nodes, edges },
    ...(toolDefinitions(descendants).length ? { tools: toolDefinitions(descendants) } : {}),
    ...(Object.keys(schemas).length ? { schemas } : {}),
    ...(runtime ? { runtime } : {}),
    verification: { successCriteria: [params.successSummary] }
  }
}

export function compileHarnessWorkspace(params: {
  workspace: Readonly<RuntimeWorkspace>
  harnessNodeId: string
}): HarnessReleaseArtifact {
  const harness = params.workspace.nodes.find((node) =>
    node.id === params.harnessNodeId && node.type === 'harness')
  if (!harness) throw new Error(`Harness node was not found: ${params.harnessNodeId}.`)
  const template = isRecord(harness.data.template) ? harness.data.template : {}
  const manifest = isRecord(template.manifest) ? template.manifest : {}
  const verification = isRecord(template.verification) ? template.verification : {}
  const name = text(harness.data.label) || text(manifest.name) || harness.id
  const description = text(harness.data.summary)
    || text(harness.data.description)
    || text(template.summary)
    || text(template.description)
    || `Hosted Harness ${name}.`
  const successSummary = stringList(verification.successCriteria)[0] || description
  return compileHarnessRelease(buildHarnessPackage({
    ...params,
    sourceKey: harness.id,
    name,
    description,
    tags: stringList(manifest.tags ?? template.tags),
    successSummary
  }))
}
