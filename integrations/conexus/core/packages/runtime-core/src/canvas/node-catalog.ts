export interface NodeTypeSpec {
  type: string
  label: string
  description: string
  defaultData: Record<string, unknown>
  createFieldsSchema?: NodeFieldsSchema
  editFieldsSchema?: NodeFieldsSchema
  runtimeFields?: string[]
  creatable?: boolean
  writable?: boolean
}

export interface NodeFieldsSchema {
  type: 'object'
  properties: Record<string, unknown>
  additionalProperties: false
}

export const HARNESS_NODE_WIDTH = 336
export const HARNESS_NODE_HEIGHT = 144
export const HARNESS_SETUP_WIDTH = 768
export const HARNESS_SETUP_HEIGHT = 528

const STRING_PROP = { type: 'string' }
const BOOLEAN_PROP = { type: 'boolean' }
const NUMBER_PROP = { type: 'number' }
const STRING_ARRAY_PROP = { type: 'array', items: { type: 'string' } }
const OBJECT_PROP = { type: 'object', additionalProperties: true }
const BASE_EDIT_FIELDS = {
  label: { type: 'string', minLength: 1, description: 'Replace the node label.' },
  description: { type: 'string', description: 'Replace the node description.' }
}
const CREATE_NODE_ENVELOPE_FIELDS = new Set(['client_ref', 'type', 'label', 'description', 'harness_node_id'])
const TEXT_PATCH_PROP = {
  type: 'object',
  properties: {
    find: { type: 'string', minLength: 1 },
    replace: { type: 'string' }
  },
  required: ['find', 'replace'],
  additionalProperties: false
}
const DOCUMENT_CONTENT_PATCH_PROP = {
  oneOf: [
    {
      type: 'object',
      properties: {
        operation: { type: 'string', const: 'replace' },
        find: { type: 'string', minLength: 1 },
        replace: { type: 'string' }
      },
      required: ['operation', 'find', 'replace'],
      additionalProperties: false
    },
    {
      type: 'object',
      properties: {
        operation: { type: 'string', const: 'append' },
        text: { type: 'string', minLength: 1 }
      },
      required: ['operation', 'text'],
      additionalProperties: false
    }
  ],
  description: 'Targeted Document content edit. Use operation=replace with find/replace for an exact replacement, or operation=append with text for a bounded verbatim append.'
}

export const DEFAULT_APP_CODE = `export default function App({ props, state, setState, submit }) {
  const count = Number(state.count ?? 0)
  const title = props.title ?? 'Interactive App'

  return (
    <main className="screen">
      <section className="toolbar">
        <div>
          <p className="eyebrow">Conexus App</p>
          <h1>{title}</h1>
        </div>
        <button onClick={() => submit({ count })}>Submit</button>
      </section>

      <section className="counter">
        <button onClick={() => setState({ count: count - 1 })}>-</button>
        <strong>{count}</strong>
        <button onClick={() => setState({ count: count + 1 })}>+</button>
      </section>
    </main>
  )
}`

export const DEFAULT_APP_PROPS = { title: 'Interactive App' }
export const DEFAULT_APP_STATE = { count: 0 }
export const DEFAULT_APP_DEPENDENCIES = {}

function fieldsSchema(properties: Record<string, unknown>): NodeFieldsSchema {
  return {
    type: 'object',
    properties,
    additionalProperties: false
  }
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function normalizeCliShellValue(value: unknown, defaultShell: string | undefined): 'powershell' | 'bash' {
  const platformDefault = defaultShell === 'powershell' ? 'powershell' : 'bash'
  if (platformDefault === 'powershell') return 'powershell'
  return value === 'powershell' ? 'powershell' : 'bash'
}

function redactedConnectionUrl(value: unknown): string {
  const raw = stringValue(value) ?? ''
  if (!raw) return ''
  try {
    const parsed = new URL(raw)
    if (parsed.password) parsed.password = '***'
    return parsed.toString()
  } catch {
    return raw.replace(/:\/\/([^:/?#]+):([^@/?#]+)@/, '://$1:***@')
  }
}

export const NODE_TYPE_SPECS: NodeTypeSpec[] = [
  {
    type: 'agent',
    label: 'Agent',
    description: 'AI worker that can reason, use tools, inspect nodes, create nodes, and delegate work.',
    defaultData: { label: 'New Agent', objective: '' },
    createFieldsSchema: fieldsSchema({
      objective: { ...STRING_PROP, description: 'Current task text shown on this Agent node.' },
      systemPrompt: { ...STRING_PROP, description: 'Node-level system prompt used when this Agent runs.' },
      model: { ...STRING_PROP, description: 'Model identifier requested for this Agent. The active host must support it.' },
      toolNames: { ...STRING_ARRAY_PROP, description: 'Explicit Agent tool names. Omit to use the host default tool selection.' },
      maxTokens: { type: 'integer', minimum: 1, description: 'Optional positive token limit for each model response.' },
      exposeInHarness: { ...BOOLEAN_PROP, description: 'Whether this Agent is exposed as a callable Harness capability.' }
    }),
    editFieldsSchema: fieldsSchema({
      objective: { ...STRING_PROP, description: 'Current task text shown on this Agent node.' },
      systemPrompt: { ...STRING_PROP, description: 'Node-level system prompt used when this Agent runs.' },
      systemPromptPatch: { ...TEXT_PATCH_PROP, description: 'Exact replacement patch for systemPrompt: { find, replace }. Use for small prompt edits instead of replacing the full prompt.' },
      model: { ...STRING_PROP, description: 'Model identifier requested for this Agent. The active host must support it.' },
      toolNames: { ...STRING_ARRAY_PROP, description: 'Explicit Agent tool names. Omit to use the host default tool selection.' },
      maxTokens: { type: 'integer', minimum: 1, description: 'Optional positive token limit for each model response.' },
      exposeInHarness: { ...BOOLEAN_PROP, description: 'Whether this Agent is exposed as a callable Harness capability.' }
    }),
    runtimeFields: ['messages', 'status', 'summary', 'completionGaps', 'toolCounts', 'lastError']
  },
  {
    type: 'note',
    label: 'Document',
    description: 'Durable rich document for markdown, raw static HTML, inline SVG, images, tables, checklists, reports, and PDF export.',
    defaultData: { label: 'New Document', content: '' },
    createFieldsSchema: fieldsSchema({
      content: { ...STRING_PROP, description: 'Document content. Supports markdown, GitHub-style tables, standard markdown images, LaTeX math with $...$, $$...$$, \\(...\\), or \\[...\\], and safe static HTML snippets. Write HTML as raw inline/block HTML when it should render; use fenced ```html code blocks only when showing source code literally. Do not include scripts, event handlers, iframes, or interactive JavaScript; use an App node for interaction.' }
    }),
    editFieldsSchema: fieldsSchema({
      content: { ...STRING_PROP, description: 'Replace the complete Document content.' },
      contentPatch: DOCUMENT_CONTENT_PATCH_PROP
    }),
    runtimeFields: ['snapshots']
  },
  {
    type: 'app',
    label: 'App',
    description: 'Sandboxed React TSX app surface for stateful interactive views, dashboards, and small tools with browser npm imports.',
    defaultData: {
      label: 'App',
      code: DEFAULT_APP_CODE,
      props: DEFAULT_APP_PROPS,
      state: DEFAULT_APP_STATE,
      dependencies: DEFAULT_APP_DEPENDENCIES
    },
    createFieldsSchema: fieldsSchema({
      code: { ...STRING_PROP, description: 'React TSX app source. Must export a default function component. May import React hooks and browser-compatible npm packages. Tailwind utility classes are supported; provide extra CSS via style objects or <style> tags when needed. App code may call await conexus.agent.run({ nodeId, task, data, timeoutMs }) for an authorized connected Agent. In a published App, nodeId is the public Agent exposure id.' },
      codePatch: { ...TEXT_PATCH_PROP, description: 'Exact replacement patch for code: { find, replace }. Use for small edits instead of replacing full code.' },
      props: { ...OBJECT_PROP, description: 'Structured props passed to the app. Set agentNodeId, targetAgentNodeId, or allowedAgentNodeIds here when App code should call specific Agent nodes through conexus.agent.run. Publishing translates authorized pinned Agent node ids to public exposure ids.' },
      state: { ...OBJECT_PROP, description: 'Persisted app state.' },
      dependencies: { ...OBJECT_PROP, description: 'Optional npm package version map for browser-compatible imports, for example { "canvas-confetti": "^1.9.3" }. Omitted packages are installed automatically from imported bare package names.' }
    }),
    runtimeFields: ['lastSubmission', 'submissions', 'lastSdkCall', 'lastSdkResult', 'lastError']
  },
  {
    type: 'image',
    label: 'Image',
    description: 'Image preview or image asset reference that Agents can inspect visually.',
    defaultData: { label: 'Image', src: '', path: '', status: 'pending' },
    createFieldsSchema: fieldsSchema({
      src: { ...STRING_PROP, description: 'Image URL or local image path.' },
      path: { ...STRING_PROP, description: 'Local image path.' },
      caption: { ...STRING_PROP, description: 'Human-readable image caption.' }
    }),
    runtimeFields: ['status']
  },
  {
    type: 'files',
    label: 'Files',
    description: 'Reference to generated or existing filesystem content: a single file or a folder of files.',
    defaultData: { label: 'Files', path: '', status: 'pending' },
    createFieldsSchema: fieldsSchema({
      path: { ...STRING_PROP, description: 'Filesystem path for a file or directory.' }
    }),
    runtimeFields: ['status']
  },
  {
    type: 'datasource',
    label: 'Data Source',
    description: 'Database connection descriptor that stores non-secret connection fields on the canvas and keeps the password in secure OS storage.',
    defaultData: {
      label: 'Data Source',
      adapter: 'postgres',
      connectionUrl: '',
      username: '',
      database: '',
      passwordSecretRef: '',
      passwordConfigured: false,
      exposeSqlTool: true,
      allowWrites: false,
      schemaSummary: '',
      status: 'pending'
    },
    createFieldsSchema: fieldsSchema({
      adapter: { type: 'string', enum: ['postgres', 'mysql', 'sqlite', 'mssql', 'supabase'], description: 'Database adapter.' },
      connectionUrl: { ...STRING_PROP, description: 'Connection URL without embedded password. Store passwords through the node UI secret field.' },
      username: { ...STRING_PROP, description: 'Database username. Use a least-privilege account.' },
      database: { ...STRING_PROP, description: 'Database/schema name for server databases, or local database file path for SQLite.' },
      exposeSqlTool: { ...BOOLEAN_PROP, description: 'Whether connected Agents can use datasource SQL tools.' },
      allowWrites: { ...BOOLEAN_PROP, description: 'Whether execute_sql may run write statements. Defaults to false.' }
    }),
    runtimeFields: ['status', 'schemaSummary', 'lastError', 'lastConnectedAt']
  },
  {
    type: 'browser',
    writable: false,
    label: 'Browser',
    description: 'Interactive browser runtime for web navigation, page inspection, and browser actions.',
    defaultData: { label: 'Browser', url: '', browserProfileChannel: 'conexus' },
    createFieldsSchema: fieldsSchema({
      url: { ...STRING_PROP, description: 'Initial or current browser URL.' },
      browserProfileChannel: {
        type: 'string',
        enum: ['conexus', 'user'],
        description: 'Browser channel. Use conexus for the persistent Conexus browser profile; use user for the main signed-in Chrome/Edge profile.'
      }
    }),
    runtimeFields: ['browserProfileChannel', 'pageTitle', 'pageText', 'semanticElements', 'snapshotVersion', 'browserEventSeq', 'loadError']
  },
  {
    type: 'cli',
    writable: false,
    label: 'CLI',
    description: 'Persistent shell runtime for commands, scripts, file processing, builds, tests, and deterministic tools.',
    defaultData: { label: 'CLI', shell: 'powershell' },
    createFieldsSchema: fieldsSchema({
      shell: { type: 'string', enum: ['powershell', 'bash'], description: 'Shell used by this CLI runtime.' }
    }),
    runtimeFields: ['cwd', 'history', 'status']
  },
  {
    type: 'tool',
    label: 'Tool',
    description: 'Executable function node with typed inputs, explicit references, durable output, and optional Agent tool exposure.',
    defaultData: {
      label: 'Tool',
      toolName: 'custom_tool',
      description: 'Executable function node.',
      runtime: 'node',
      exposeAsTool: true,
      input: {},
      inputRefs: {},
      inputSchema: {
        type: 'object',
        properties: {},
        additionalProperties: true
      },
      outputSchema: {
        type: 'object',
        additionalProperties: true
      },
      output: null,
      code: "export async function run(input, context) {\n  return { ok: true, input }\n}\n"
    },
    createFieldsSchema: fieldsSchema({
      toolName: { ...STRING_PROP, description: 'Function name for this executable node and optional Agent tool exposure.' },
      runtime: { type: 'string', enum: ['node', 'python', 'powershell', 'bash'], description: 'Runtime used to execute the tool code.' },
      exposeAsTool: { ...BOOLEAN_PROP, description: 'Whether this executable node is exposed as a callable local Agent tool.' },
      input: { ...OBJECT_PROP, description: 'Static JSON input merged before inputRefs are resolved.' },
      inputRefs: { ...OBJECT_PROP, description: 'Map of input keys to upstream node references such as { source: { nodeId, field } }. Defaults to field output.' },
      inputSchema: { ...OBJECT_PROP, description: 'JSON schema object for tool input.' },
      outputSchema: { ...OBJECT_PROP, description: 'JSON schema object for tool output.' },
      output: { description: 'Most recent durable JSON output produced or accepted for this function node.' },
      code: { ...STRING_PROP, description: 'Executable tool source code.' },
      codePatch: { ...TEXT_PATCH_PROP, description: 'Exact replacement patch for code: { find, replace }. Use for small edits instead of replacing full code.' },
      timeoutMs: { ...NUMBER_PROP, description: 'Optional execution timeout in milliseconds.' },
      dependencies: { ...OBJECT_PROP, description: 'Package, command, runtime, or service dependencies required by this function node.' },
      permissions: { ...OBJECT_PROP, description: 'Permissions required by this function node, such as filesystem, network, browser, or shell access.' },
      sideEffects: { type: 'string', enum: ['none', 'read', 'write', 'external'], description: 'Declared side effect level for review and future policy checks.' }
    }),
    runtimeFields: ['status', 'output', 'lastArgs', 'lastRunStartedAt', 'lastRunCompletedAt', 'lastResult', 'lastStdout', 'lastStderr', 'lastExitCode', 'lastTimedOut', 'lastDurationMs', 'lastError']
  },
  {
    type: 'custom',
    label: 'Custom',
    description: 'Durable structured node for domain-specific data, labels, state, or custom workflow outputs.',
    defaultData: { label: 'Custom Node', customType: 'custom', content: '', data: {} },
    createFieldsSchema: fieldsSchema({
      customType: { ...STRING_PROP, description: 'Concise domain-specific custom node type.' },
      content: { ...STRING_PROP, description: 'Human-readable text content.' },
      contentPatch: { ...TEXT_PATCH_PROP, description: 'Exact replacement patch for content: { find, replace }. Use for small edits instead of replacing full content.' },
      data: { ...OBJECT_PROP, description: 'Structured domain-specific data.' }
    }),
    runtimeFields: []
  },
  {
    type: 'harness',
    label: 'Harness',
    description: 'Canvas node that contains a scoped reusable App/workflow with callable Agent and Tool nodes, durable artifacts, and packaging.',
    defaultData: {
      label: 'Harness',
      template: {}
    },
    createFieldsSchema: fieldsSchema({
      summary: { ...STRING_PROP, description: 'Current workflow summary.' },
      template: { ...STRING_PROP, description: 'Template id or exact template name. Omit to create an empty harness.' },
      variables: { ...OBJECT_PROP, description: 'String, number, or boolean template variables.' },
      initialTask: { ...STRING_PROP, description: 'Initial task text passed into template placeholders.' },
      startImmediately: { ...BOOLEAN_PROP, description: 'Whether instantiated internal Agents should auto-start. Defaults to false.' },
      defaultExposureId: { ...STRING_PROP, description: 'Optional default published capability id.' },
      purpose: { type: 'string', enum: ['primary', 'repair', 'draft', 'parallel_branch'], description: 'Harness budget purpose. Defaults to primary.' }
    }),
    editFieldsSchema: fieldsSchema({
      summary: { ...STRING_PROP, description: 'Current workflow summary.' },
      defaultExposureId: { ...STRING_PROP, description: 'Optional default published capability id.' }
    }),
    runtimeFields: ['template']
  }
]

export function listNodeTypeSpecs(): NodeTypeSpec[] {
  return NODE_TYPE_SPECS
}

export function listCreatableNodeTypes(): string[] {
  return NODE_TYPE_SPECS.filter((spec) => spec.creatable !== false).map((spec) => spec.type)
}

export function listWritableNodeTypes(): string[] {
  return NODE_TYPE_SPECS.filter((spec) => spec.writable !== false).map((spec) => spec.type)
}

export function getNodeTypeSpec(type: string | undefined): NodeTypeSpec | undefined {
  return NODE_TYPE_SPECS.find((spec) => spec.type === type)
}

export function defaultNodeDataForType(type: string, label?: string): Record<string, unknown> {
  const spec = getNodeTypeSpec(type)
  return {
    ...(spec?.defaultData ?? {}),
    label: label ?? spec?.defaultData.label ?? spec?.label ?? 'New Node'
  }
}

export function createFieldsSchemaForType(type: string | undefined): NodeTypeSpec['createFieldsSchema'] {
  const schema = getNodeTypeSpec(type)?.createFieldsSchema
  if (!schema) return undefined
  const properties = Object.fromEntries(
    Object.entries(schema.properties).filter(([name]) => name !== 'contentPatch' && name !== 'codePatch')
  )
  return { ...schema, properties }
}

export function editFieldsSchemaForType(type: string | undefined): NodeTypeSpec['createFieldsSchema'] {
  const spec = getNodeTypeSpec(type)
  if (!spec || spec.writable === false) return undefined
  const typeFields = spec.editFieldsSchema ?? spec.createFieldsSchema
  return fieldsSchema({
    ...BASE_EDIT_FIELDS,
    ...(typeFields?.properties ?? {})
  })
}

/** Separates create-envelope controls from the type-specific fields placed directly on a create item. */
export function extractCreateNodeFields(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([field]) => !CREATE_NODE_ENVELOPE_FIELDS.has(field)))
}

function validateFieldsAgainstSchema(
  type: string | undefined,
  value: unknown,
  schema: NodeTypeSpec['createFieldsSchema'],
  operation: 'create' | 'update'
): { ok: true; fields: Record<string, unknown> } | { ok: false; error: string } {
  if (value === undefined || value === null) return { ok: true, fields: {} }
  if (!isRecord(value)) return { ok: false, error: 'fields must be an object' }
  const allowed = new Set(Object.keys(schema?.properties ?? {}))
  const unknown = Object.keys(value).filter((key) => !allowed.has(key))
  if (unknown.length > 0) {
    return {
      ok: false,
      error: `unsupported ${operation} fields for ${type ?? 'unknown'} node: ${unknown.join(', ')}. Allowed fields: ${[...allowed].join(', ') || '(none)'}`
    }
  }
  return { ok: true, fields: value }
}

export function validateCreateFieldsForType(
  type: string | undefined,
  value: unknown
): { ok: true; fields: Record<string, unknown> } | { ok: false; error: string } {
  return validateFieldsAgainstSchema(type, value, createFieldsSchemaForType(type), 'create')
}

export function validateEditFieldsForType(
  type: string | undefined,
  value: unknown
): { ok: true; fields: Record<string, unknown> } | { ok: false; error: string } {
  return validateFieldsAgainstSchema(type, value, editFieldsSchemaForType(type), 'update')
}

export type NodeTypeFieldsContractResult =
  | {
      success: true
      fields_schema: NodeFieldsSchema
    }
  | { success: false; error: string }

/** Returns only the authoritative type-specific field contract for one mutation operation. */
export function nodeTypeFieldsContract(
  typeValue: unknown,
  operationValue: unknown
): NodeTypeFieldsContractResult {
  const type = stringValue(typeValue)
  if (!type) return { success: false, error: 'type is required.' }
  if (operationValue !== 'create' && operationValue !== 'update') {
    return { success: false, error: 'operation must be create or update.' }
  }
  const spec = getNodeTypeSpec(type)
  if (!spec) return { success: false, error: `unknown node type: ${type}.` }

  const allowed = operationValue === 'create' ? spec.creatable !== false : spec.writable !== false
  if (!allowed) return { success: false, error: `${spec.type} nodes do not support ${operationValue}.` }
  const schema = operationValue === 'create'
    ? createFieldsSchemaForType(spec.type)
    : editFieldsSchemaForType(spec.type)
  return {
    success: true,
    fields_schema: schema ?? fieldsSchema({})
  }
}

export function nodeTypePromptSummary(spec: NodeTypeSpec): string {
  return `- \`${spec.type}\`: ${spec.description}`
}

export function nodeCatalogPromptSummary(): string {
  return listNodeTypeSpecs()
    .filter((spec) => spec.creatable !== false)
    .map(nodeTypePromptSummary)
    .join('\n')
}

export function getNodeValuesFromData(node: {
  id: string
  type?: string
  data?: Record<string, unknown>
}, options: { defaultShell?: string } = {}): Record<string, unknown> {
  const data = node.data ?? {}
  const values: Record<string, unknown> = {}

  if (node.type === 'note' || node.type === 'document') {
    values.content = stringValue(data.content) ?? ''
  } else if (node.type === 'files') {
    values.path = stringValue(data.path) ?? ''
    values.status = stringValue(data.status) ?? ''
  } else if (node.type === 'app') {
    values.code = stringValue(data.code) ?? ''
    values.props = isRecord(data.props) ? data.props : {}
    values.state = isRecord(data.state) ? data.state : {}
    values.dependencies = isRecord(data.dependencies) ? data.dependencies : {}
    values.lastSubmission = data.lastSubmission ?? null
    values.lastFormSubmission = data.lastFormSubmission ?? null
    values.lastSdkCall = data.lastSdkCall ?? null
    values.lastSdkResult = data.lastSdkResult ?? null
    values.lastError = stringValue(data.lastError) ?? ''
  } else if (node.type === 'image') {
    values.src = stringValue(data.src) ?? stringValue(data.path) ?? stringValue(data.url) ?? ''
    values.path = stringValue(data.path) ?? ''
    values.caption = stringValue(data.caption) ?? stringValue(data.description) ?? ''
  } else if (node.type === 'custom') {
    values.customType = stringValue(data.customType) ?? 'custom'
    values.content = stringValue(data.content) ?? ''
    values.data = data.data && typeof data.data === 'object' && !Array.isArray(data.data) ? data.data : {}
  } else if (node.type === 'datasource') {
    values.adapter = stringValue(data.adapter) ?? 'postgres'
    values.connectionUrl = redactedConnectionUrl(data.connectionUrl)
    values.username = stringValue(data.username) ?? ''
    values.database = stringValue(data.database) ?? ''
    values.passwordConfigured = data.passwordConfigured === true || Boolean(stringValue(data.passwordSecretRef))
    values.exposeSqlTool = data.exposeSqlTool !== false
    values.allowWrites = data.allowWrites === true
    values.schemaSummary = stringValue(data.schemaSummary) ?? ''
    values.status = stringValue(data.status) ?? ''
    values.lastError = stringValue(data.lastError) ?? ''
  } else if (node.type === 'browser') {
    values.url = stringValue(data.url) ?? ''
    values.browserProfileChannel = stringValue(data.browserProfileChannel) ?? 'conexus'
    values.title = stringValue(data.pageTitle) ?? ''
    values.snapshot = stringValue(data.pageText) ?? ''
  } else if (node.type === 'cli') {
    values.shell = normalizeCliShellValue(data.shell, options.defaultShell)
    values.cwd = stringValue(data.cwd) ?? ''
    values.status = stringValue(data.status) ?? ''
  } else if (node.type === 'tool') {
    values.toolName = stringValue(data.toolName) ?? stringValue(data.name) ?? ''
    values.runtime = stringValue(data.runtime) ?? 'node'
    values.exposeAsTool = data.exposeAsTool !== false
    values.description = stringValue(data.description) ?? ''
    values.input = data.input && typeof data.input === 'object' && !Array.isArray(data.input) ? data.input : {}
    values.inputRefs = data.inputRefs && typeof data.inputRefs === 'object' && !Array.isArray(data.inputRefs) ? data.inputRefs : {}
    values.inputSchema = data.inputSchema ?? {}
    values.outputSchema = data.outputSchema ?? {}
    values.output = data.output ?? null
    values.code = stringValue(data.code) ?? ''
    values.timeoutMs = typeof data.timeoutMs === 'number' ? data.timeoutMs : null
    values.dependencies = isRecord(data.dependencies) ? data.dependencies : {}
    values.permissions = isRecord(data.permissions) ? data.permissions : {}
    values.sideEffects = stringValue(data.sideEffects) ?? ''
    values.status = stringValue(data.status) ?? ''
    values.lastArgs = isRecord(data.lastArgs) ? data.lastArgs : {}
    values.lastStdout = stringValue(data.lastStdout) ?? ''
    values.lastStderr = stringValue(data.lastStderr) ?? ''
    values.lastExitCode = typeof data.lastExitCode === 'number' ? data.lastExitCode : null
    values.lastTimedOut = typeof data.lastTimedOut === 'boolean' ? data.lastTimedOut : null
    values.lastError = stringValue(data.lastError) ?? ''
  } else if (node.type === 'agent') {
    values.objective = stringValue(data.objective) ?? ''
    values.systemPrompt = typeof data.systemPrompt === 'string' ? data.systemPrompt : ''
    values.model = stringValue(data.model) ?? ''
    values.toolNames = Array.isArray(data.toolNames)
      ? data.toolNames.filter((name): name is string => typeof name === 'string')
      : []
    values.maxTokens = typeof data.maxTokens === 'number' ? data.maxTokens : null
    values.exposeInHarness = data.exposeInHarness === true
    values.status = stringValue(data.status) ?? ''
    values.summary = stringValue(data.summary) ?? ''
    values.completionGaps = Array.isArray(data.completionGaps) ? data.completionGaps : []
    values.toolCounts = isRecord(data.toolCounts) ? data.toolCounts : {}
    values.lastError = stringValue(data.lastError) ?? ''
    const messages = Array.isArray(data.messages) ? data.messages : []
    const lastAssistant = [...messages].reverse().find((message) => {
      if (!message || typeof message !== 'object') return false
      const msg = message as { role?: unknown; content?: unknown; error?: unknown }
      return msg.role === 'assistant' && typeof msg.content === 'string' && msg.content.trim() && !msg.error
    }) as { content?: string } | undefined
    const lastMessage = stringValue(lastAssistant?.content) ?? ''
    values.lastMessage = lastMessage
  } else if (node.type === 'harness') {
    values.summary = stringValue(data.summary) ?? ''
    values.purpose = stringValue(data.purpose) ?? 'primary'
    values.template = isRecord(data.template) ? data.template : {}
    values.defaultExposureId = stringValue(data.defaultExposureId) ?? ''
    values.status = stringValue(data.status) ?? ''
    values.lastError = stringValue(data.lastError) ?? ''
  }

  return values
}
