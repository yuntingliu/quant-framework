import type { RuntimeNode } from '../contracts.js'

export type NodeCapabilityEffect = 'none' | 'read' | 'write' | 'external'
export type NodeCapabilityExecution = 'sync' | 'async' | 'interactive'

export interface NodeCapabilityContract {
  id: string
  version: string
  summary: string
  input_schema: Record<string, unknown>
  output_schema?: Record<string, unknown>
  effect: NodeCapabilityEffect
  execution: NodeCapabilityExecution
  schema_hash: string
}

export interface NodeCapabilityDescriptor {
  id: string
  contract_ref: string
  summary: string
}

export interface RuntimeManagedNodeDescriptor {
  id: string
  type: 'runtime'
  label: string
  description: string
  status: 'available'
  capabilities: NodeCapabilityDescriptor[]
}

const OBJECT_OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: true
} as const

function stableSchemaHash(value: unknown): string {
  const stable = (input: unknown): string => {
    if (Array.isArray(input)) return `[${input.map(stable).join(',')}]`
    if (input && typeof input === 'object') {
      return `{${Object.entries(input as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, nested]) => `${JSON.stringify(key)}:${stable(nested)}`)
        .join(',')}}`
    }
    return JSON.stringify(input)
  }
  let hash = 0x811c9dc5
  for (const char of stable(value)) {
    hash ^= char.charCodeAt(0)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

function contract(
  value: Omit<NodeCapabilityContract, 'version' | 'schema_hash'> & { version?: string }
): NodeCapabilityContract {
  const version = value.version ?? '1'
  return {
    ...value,
    version,
    schema_hash: stableSchemaHash({
      id: value.id,
      version,
      input_schema: value.input_schema,
      output_schema: value.output_schema
    })
  }
}

const BUILTIN_CONTRACTS = [
  contract({
    id: 'agent.run',
    summary: 'Start an Agent with a task and optional structured context.',
    input_schema: {
      type: 'object',
      properties: {
        task: { type: 'string', minLength: 1 },
        context: { type: 'object', additionalProperties: true },
        wait: { type: 'boolean', description: 'Wait for a terminal result when true.' }
      },
      required: ['task'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'write',
    execution: 'async'
  }),
  contract({
    id: 'harness.run',
    summary: 'Run a published Harness capability.',
    input_schema: {
      type: 'object',
      properties: {
        exposure_id: { type: 'string', minLength: 1 },
        task: { type: 'string', minLength: 1 },
        input: { type: 'object', additionalProperties: true },
        wait: { type: 'boolean', description: 'Wait for a terminal result when true.' }
      },
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'write',
    execution: 'async'
  }),
  contract({
    id: 'run.wait',
    summary: 'Wait for the node current run to reach a terminal or attention state.',
    input_schema: {
      type: 'object',
      properties: {},
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'read',
    execution: 'async'
  }),
  contract({
    id: 'run.cancel',
    summary: 'Request cancellation of the node current run.',
    input_schema: {
      type: 'object',
      properties: {},
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'write',
    execution: 'sync'
  }),
  contract({
    id: 'cli.exec',
    summary: 'Execute a command in this persistent CLI session.',
    input_schema: {
      type: 'object',
      properties: {
        command: { type: 'string', minLength: 1, description: 'Command text using the shell syntax reported by observe.' },
        cwd: { type: 'string', description: 'Optional working-directory override. Defaults to the cwd reported by observe.' },
        timeout_ms: { type: 'integer', minimum: 1_000, maximum: 600_000, description: 'Optional timeout override. Defaults to timeoutMs reported by observe.' }
      },
      required: ['command'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'external',
    execution: 'sync'
  }),
  contract({
    id: 'browser.navigate',
    summary: 'Navigate this Browser node to a URL.',
    input_schema: {
      type: 'object',
      properties: { url: { type: 'string', minLength: 1 } },
      required: ['url'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'external',
    execution: 'sync'
  }),
  ...(['refresh', 'focus'] as const).map((action) => contract({
    id: `browser.${action}`,
    summary: `${action === 'refresh' ? 'Refresh' : 'Focus'} this Browser node.`,
    input_schema: { type: 'object', properties: {}, additionalProperties: false },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'external',
    execution: 'sync'
  })),
  contract({
    id: 'browser.click',
    summary: 'Click a semantic element in this Browser node.',
    input_schema: {
      type: 'object',
      properties: { element_id: { type: 'string', minLength: 1 } },
      required: ['element_id'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'external',
    execution: 'sync'
  }),
  contract({
    id: 'browser.type',
    summary: 'Type text into a semantic element in this Browser node.',
    input_schema: {
      type: 'object',
      properties: {
        element_id: { type: 'string', minLength: 1 },
        text: { type: 'string' }
      },
      required: ['element_id', 'text'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'external',
    execution: 'sync'
  }),
  contract({
    id: 'browser.scroll',
    summary: 'Scroll this Browser node vertically.',
    input_schema: {
      type: 'object',
      properties: { delta_y: { type: 'number' } },
      required: ['delta_y'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'external',
    execution: 'sync'
  }),
  contract({
    id: 'datasource.query',
    summary: 'Run a bounded read-only SQL query against this Data Source.',
    input_schema: {
      type: 'object',
      properties: { sql: { type: 'string', minLength: 1, maxLength: 100_000 } },
      required: ['sql'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'read',
    execution: 'sync'
  }),
  contract({
    id: 'datasource.execute',
    summary: 'Run an allowed write SQL statement against this Data Source.',
    input_schema: {
      type: 'object',
      properties: { sql: { type: 'string', minLength: 1, maxLength: 100_000 } },
      required: ['sql'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'external',
    execution: 'sync'
  }),
  contract({
    id: 'web.search',
    summary: 'Search the public web for current information.',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', minLength: 1, maxLength: 2_000 },
        count: { type: 'integer', minimum: 1, maximum: 20 },
        freshness: { type: 'string', enum: ['day', 'week', 'month', 'year'] }
      },
      required: ['query'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'external',
    execution: 'sync'
  }),
  contract({
    id: 'harness.search',
    summary: 'Search the reusable Harness library.',
    input_schema: {
      type: 'object',
      properties: { query: { type: 'string', minLength: 1, maxLength: 2_000 } },
      required: ['query'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'read',
    execution: 'sync'
  }),
  contract({
    id: 'image.generate',
    summary: 'Generate one image asset and add it to the workspace.',
    input_schema: {
      type: 'object',
      properties: {
        prompt: { type: 'string', minLength: 1, maxLength: 20_000 },
        label: { type: 'string', minLength: 1, maxLength: 160 },
        size: { type: 'string', enum: ['1024x1024', '1536x1024', '1024x1536'] }
      },
      required: ['prompt', 'label'],
      additionalProperties: false
    },
    output_schema: OBJECT_OUTPUT_SCHEMA,
    effect: 'external',
    execution: 'async'
  })
] satisfies NodeCapabilityContract[]

const BUILTIN_CONTRACT_BY_ID = new Map(BUILTIN_CONTRACTS.map((item) => [item.id, item]))

export const RUNTIME_MANAGED_NODES: readonly RuntimeManagedNodeDescriptor[] = [
  runtimeNode('runtime:web-search', 'Web Search', 'Current public web search service.', ['web.search']),
  runtimeNode('runtime:harness-library', 'Harness Library', 'Reusable Harness manifest catalog.', ['harness.search']),
  runtimeNode('runtime:image-generator', 'Image Generator', 'Configured image generation service.', ['image.generate'])
]

export function runtimeManagedNodesForCapabilities(
  capabilityIds: Iterable<string>
): RuntimeManagedNodeDescriptor[] {
  const available = new Set(capabilityIds)
  return RUNTIME_MANAGED_NODES.flatMap((node) => {
    const capabilities = node.capabilities.filter((capability) => available.has(capability.id))
    return capabilities.length > 0 ? [{ ...node, capabilities }] : []
  })
}

function descriptor(value: NodeCapabilityContract): NodeCapabilityDescriptor {
  return {
    id: value.id,
    contract_ref: `${value.id}@${value.version}#${value.schema_hash}`,
    summary: value.summary
  }
}

function runtimeNode(
  id: string,
  label: string,
  description: string,
  capabilityIds: string[]
): RuntimeManagedNodeDescriptor {
  return {
    id,
    type: 'runtime',
    label,
    description,
    status: 'available',
    capabilities: capabilityIds.map((capabilityId) => descriptor(BUILTIN_CONTRACT_BY_ID.get(capabilityId)!))
  }
}

function toolContract(node: RuntimeNode): NodeCapabilityContract {
  const inputSchema = node.data.inputSchema && typeof node.data.inputSchema === 'object' && !Array.isArray(node.data.inputSchema)
    ? node.data.inputSchema as Record<string, unknown>
    : { type: 'object', properties: {}, additionalProperties: true }
  const outputSchema = node.data.outputSchema && typeof node.data.outputSchema === 'object' && !Array.isArray(node.data.outputSchema)
    ? node.data.outputSchema as Record<string, unknown>
    : OBJECT_OUTPUT_SCHEMA
  return contract({
    id: 'tool.invoke',
    summary: typeof node.data.description === 'string' && node.data.description.trim()
      ? node.data.description.trim()
      : 'Invoke this Tool node.',
    input_schema: inputSchema,
    output_schema: outputSchema,
    effect: node.data.sideEffects === 'none' || node.data.sideEffects === 'read' || node.data.sideEffects === 'write'
      ? node.data.sideEffects
      : 'external',
    execution: 'sync'
  })
}

export function getNodeCapabilityContracts(node: RuntimeNode): NodeCapabilityContract[] {
  let ids: string[] = []
  if (node.type === 'agent') ids = ['agent.run', 'run.wait', 'run.cancel']
  else if (node.type === 'harness') ids = ['harness.run', 'run.wait', 'run.cancel']
  else if (node.type === 'cli') ids = ['cli.exec']
  else if (node.type === 'browser') {
    ids = ['browser.navigate', 'browser.refresh', 'browser.focus', 'browser.click', 'browser.type', 'browser.scroll']
  } else if (node.type === 'datasource' && node.data.exposeSqlTool !== false) {
    ids = node.data.allowWrites === true
      ? ['datasource.query', 'datasource.execute']
      : ['datasource.query']
  }
  const contracts = ids.map((id) => BUILTIN_CONTRACT_BY_ID.get(id)!).filter(Boolean)
  if (node.type === 'tool' && node.data.exposeAsTool !== false) contracts.push(toolContract(node))
  return contracts
}

export function getNodeCapabilityDescriptors(node: RuntimeNode): NodeCapabilityDescriptor[] {
  return getNodeCapabilityContracts(node).map(descriptor)
}

export function getRuntimeCapabilityContract(
  nodeId: string,
  capabilityId: string
): NodeCapabilityContract | undefined {
  const runtimeNode = RUNTIME_MANAGED_NODES.find((node) => node.id === nodeId)
  if (!runtimeNode?.capabilities.some((item) => item.id === capabilityId)) return undefined
  return BUILTIN_CONTRACT_BY_ID.get(capabilityId)
}

export function getNodeCapabilityContract(
  node: RuntimeNode,
  capabilityId: string
): NodeCapabilityContract | undefined {
  return getNodeCapabilityContracts(node).find((item) => item.id === capabilityId)
}

export function listBuiltinNodeCapabilityContracts(): readonly NodeCapabilityContract[] {
  return BUILTIN_CONTRACTS
}
