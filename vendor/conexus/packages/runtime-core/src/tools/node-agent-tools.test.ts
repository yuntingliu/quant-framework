import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AGENT_COMPLETION_TOOL,
  createAgentToolProfile,
  createHostedAgentToolProfile,
  DEFAULT_AGENT_TOOL_NAMES,
  getNodeAgentToolSchema,
  NODE_AGENT_TOOL_METADATA,
  NODE_AGENT_TOOL_NAMES,
  NODE_AGENT_TOOL_SCHEMAS,
  resolveNodeAgentToolSchemas
} from '../index.js'

test('node Agent tools expose exactly one stable seven-operation ABI', () => {
  assert.deepEqual(NODE_AGENT_TOOL_NAMES, [
    'find',
    'observe',
    'create',
    'edit',
    'use',
    'request_user_input',
    'complete'
  ])
  assert.deepEqual(DEFAULT_AGENT_TOOL_NAMES, NODE_AGENT_TOOL_NAMES)
  assert.deepEqual(Object.keys(NODE_AGENT_TOOL_METADATA), NODE_AGENT_TOOL_NAMES)
  assert.equal(NODE_AGENT_TOOL_SCHEMAS.complete, AGENT_COMPLETION_TOOL)
  assert.deepEqual(
    resolveNodeAgentToolSchemas(NODE_AGENT_TOOL_NAMES),
    NODE_AGENT_TOOL_NAMES.map((name) => NODE_AGENT_TOOL_SCHEMAS[name])
  )
  assert.equal(getNodeAgentToolSchema('web_search'), undefined)
  assert.equal(getNodeAgentToolSchema('find_nodes'), undefined)
})

test('discovery, observation, and capability use expose the intended boundary', () => {
  const find = NODE_AGENT_TOOL_SCHEMAS.find.function.parameters as {
    properties: Record<string, unknown>
  }
  assert.deepEqual(Object.keys(find.properties), [
    'scope', 'harness_node_id', 'query', 'types', 'capabilities',
    'relation_to', 'statuses', 'cursor', 'limit'
  ])

  const observe = NODE_AGENT_TOOL_SCHEMAS.observe.function.parameters as {
    properties: { requests: { items: { properties: Record<string, unknown> } } }
  }
  assert.deepEqual(Object.keys(observe.properties.requests.items.properties), [
    'node_id', 'detail', 'fields', 'include_contracts', 'start_line', 'end_line'
  ])

  const use = NODE_AGENT_TOOL_SCHEMAS.use.function.parameters as {
    required: string[]
    properties: Record<string, unknown>
  }
  assert.deepEqual(use.required, ['node_id', 'capability', 'input'])
  assert.deepEqual(Object.keys(use.properties), ['node_id', 'capability', 'input'])
})

test('create embeds every type contract and edit groups atomic graph mutations', () => {
  const create = NODE_AGENT_TOOL_SCHEMAS.create.function.parameters as {
    properties: { nodes: { items: { oneOf: Array<Record<string, unknown>> } } }
  }
  assert.ok(create.properties.nodes.items.oneOf.length > 1)
  for (const alternative of create.properties.nodes.items.oneOf) {
    const properties = alternative.properties as Record<string, unknown>
    assert.ok(properties.type)
    const type = (properties.type as { const?: string }).const
    if (type !== 'app') assert.equal(properties.props, undefined)
  }
  const note = create.properties.nodes.items.oneOf.find((alternative) =>
    (alternative.properties as { type?: { const?: string } }).type?.const === 'note')
  assert.ok((note?.properties as Record<string, unknown>).content)

  const edit = NODE_AGENT_TOOL_SCHEMAS.edit.function.parameters as {
    properties: { operations: { items: { oneOf: Array<{ properties: { kind: { const: string } } }> } } }
  }
  assert.deepEqual(
    edit.properties.operations.items.oneOf.map((item) => item.properties.kind.const),
    ['patch', 'move', 'connect', 'disconnect', 'delete']
  )
  const patch = edit.properties.operations.items.oneOf[0] as { properties: Record<string, unknown> }
  assert.ok(patch.properties.set)
  assert.equal(patch.properties.props, undefined)
})

test('one profile owns the same model ABI for desktop and hosted runtimes', () => {
  const desktop = createAgentToolProfile({ name: 'test.desktop' })
  const hosted = createHostedAgentToolProfile({
    name: 'test.hosted',
    nodeTypes: ['cli'],
    toolRuntimes: [],
    permissions: [],
    isolation: [],
    capabilityIds: ['cli.exec', 'web.search'],
    runNodeTypes: []
  })
  for (const profile of [desktop, hosted]) {
    assert.deepEqual(profile.tools.map((tool) => tool.name), NODE_AGENT_TOOL_NAMES)
    assert.deepEqual(profile.availableToolNames, NODE_AGENT_TOOL_NAMES)
    for (const tool of profile.tools) assert.equal(tool.schema.function.name, tool.name)
  }
})

test('request_user_input distinguishes ephemeral structured input from persistent form nodes', () => {
  const schema = NODE_AGENT_TOOL_SCHEMAS.request_user_input.function.parameters as {
    properties: Record<string, unknown>
  }
  assert.deepEqual(Object.keys(schema.properties), [
    'prompt', 'choices', 'response_schema', 'ui_schema'
  ])
  assert.match(NODE_AGENT_TOOL_SCHEMAS.request_user_input.function.description, /does not create a persistent canvas node/)
})
