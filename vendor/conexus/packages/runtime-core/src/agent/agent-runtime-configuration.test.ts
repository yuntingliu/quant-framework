import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AgentRuntimeConfigurationError,
  DEFAULT_AGENT_TOOL_NAMES,
  DEFAULT_AGENT_RUN_TIMEOUT_MS,
  MAX_AGENT_RUN_TIMEOUT_MS,
  NODE_AGENT_TOOL_NAMES,
  resolveAgentRunTimeoutMs,
  resolveAgentNodeRuntimeConfiguration,
  resolveAgentRuntimeConfiguration,
  resolveNodeAgentRuntimeConfiguration,
  selectAgentRuntimeDynamicTools,
  usesAutomaticAgentToolSelection
} from '../index.js'

const base = {
  defaultModel: 'test/default',
  allowedModels: new Set(['test/default', 'test/allowed']),
  hostLabel: 'Test Host'
}

test('node Agent runtime configuration defaults omitted values and preserves supported requests', () => {
  assert.deepEqual(resolveNodeAgentRuntimeConfiguration(base), {
    model: 'test/default',
    toolNames: [...NODE_AGENT_TOOL_NAMES]
  })
  assert.deepEqual(resolveNodeAgentRuntimeConfiguration({
    ...base,
    requestedModel: ' test/allowed ',
    requestedToolNames: ['observe', 'observe'],
    requestedMaxTokens: 1_000_000
  }), {
    model: 'test/allowed',
    toolNames: ['observe', 'complete'],
    maxTokens: 1_000_000
  })
})

test('node Agent runtime configuration rejects every explicit unsupported value', () => {
  const cases: Array<{
    request: Record<string, unknown>
    code: AgentRuntimeConfigurationError['code']
  }> = [
    { request: { requestedModel: 'test/unknown' }, code: 'model_not_allowed' },
    { request: { requestedToolNames: ['desktop_control'] }, code: 'unavailable_agent_tool' },
    { request: { requestedMaxTokens: 1.5 }, code: 'invalid_agent_runtime_config' }
  ]

  for (const { request, code } of cases) {
    assert.throws(
      () => resolveNodeAgentRuntimeConfiguration({ ...base, ...request }),
      (error) => error instanceof AgentRuntimeConfigurationError && error.code === code
    )
  }
})

const desktopBase = {
  defaultModel: 'desktop/default',
  defaultToolNames: [...NODE_AGENT_TOOL_NAMES, 'custom_tool'],
  availableToolNames: new Set([...NODE_AGENT_TOOL_NAMES, 'custom_tool']),
  requiredToolNames: ['request_user_input', 'complete'],
  hostLabel: 'Desktop Agent runtime'
}

test('generic strict configuration preserves explicit Desktop model, tools, and maxTokens', () => {
  assert.deepEqual(resolveAgentRuntimeConfiguration({
    ...desktopBase,
    requestedModel: ' provider/explicit ',
    requestedToolNames: ['custom_tool', 'custom_tool'],
    requestedMaxTokens: 1_000_000
  }), {
    model: 'provider/explicit',
    toolNames: ['request_user_input', 'complete', 'custom_tool'],
    explicitToolNames: ['custom_tool'],
    maxTokens: 1_000_000
  })
})

test('generic strict configuration uses Desktop host defaults without materializing an explicit tool selection', () => {
  const resolved = resolveAgentRuntimeConfiguration(desktopBase)
  assert.deepEqual(resolved, {
    model: 'desktop/default',
    toolNames: ['request_user_input', 'complete', 'find', 'observe', 'create', 'edit', 'use', 'custom_tool']
  })
  assert.equal(Object.prototype.hasOwnProperty.call(resolved, 'explicitToolNames'), false)
})

test('Agent node runtime ingress maps current fields once and rejects the removed tools field', () => {
  assert.deepEqual(resolveAgentNodeRuntimeConfiguration({
    model: 'provider/explicit',
    toolNames: ['custom_tool'],
    maxTokens: 1_000_000
  }, desktopBase), {
    model: 'provider/explicit',
    toolNames: ['request_user_input', 'complete', 'custom_tool'],
    explicitToolNames: ['custom_tool'],
    maxTokens: 1_000_000
  })
  assert.throws(
    () => resolveAgentNodeRuntimeConfiguration({ tools: [] }, desktopBase),
    /must use toolNames/
  )
})

test('an explicit current node ABI selection remains explicit', () => {
  const resolved = resolveAgentNodeRuntimeConfiguration({
    toolNames: [...DEFAULT_AGENT_TOOL_NAMES].reverse()
  }, desktopBase)

  assert.deepEqual(resolved, {
    model: 'desktop/default',
    toolNames: ['request_user_input', 'complete', 'use', 'edit', 'create', 'observe', 'find'],
    explicitToolNames: [...DEFAULT_AGENT_TOOL_NAMES].reverse()
  })
  assert.equal(usesAutomaticAgentToolSelection(
    [...DEFAULT_AGENT_TOOL_NAMES, 'connected_custom_tool'],
    new Set(['connected_custom_tool'])
  ), false)
  assert.equal(usesAutomaticAgentToolSelection(
    [...DEFAULT_AGENT_TOOL_NAMES, 'unknown_tool'],
    new Set(['connected_custom_tool'])
  ), false)

  assert.throws(
    () => resolveAgentNodeRuntimeConfiguration({
      toolNames: [...DEFAULT_AGENT_TOOL_NAMES, 'user_custom_tool']
    }, desktopBase),
    (error) => error instanceof AgentRuntimeConfigurationError && error.code === 'unavailable_agent_tool'
  )
})

test('a historical automatic Desktop tool set migrates to the node ABI', () => {
  const preControlDefaults = [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_nodes', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_nodes', 'update_nodes', 'generate_image',
    'shell_exec', 'control_browser', 'run_node', 'publish_harness', 'execute_sql',
    'web_search', 'search_harness_library', 'complete'
  ]
  const resolved = resolveAgentNodeRuntimeConfiguration({
    toolNames: preControlDefaults
  }, {
    ...desktopBase,
    defaultToolNames: DEFAULT_AGENT_TOOL_NAMES,
    availableToolNames: new Set(DEFAULT_AGENT_TOOL_NAMES)
  })
  assert.equal(resolved.explicitToolNames, undefined)
  assert.deepEqual(new Set(resolved.toolNames), new Set(DEFAULT_AGENT_TOOL_NAMES))
})

test('historical materialized Canvas defaults remain host-resolved data migrations', () => {
  const attachedNodeDefaults = [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_node', 'connect_nodes',
    'delete_node', 'list_nodes', 'attach_node', 'web_search',
    'search_subharness_library', 'complete'
  ]
  const nodeRuntimeDefaults = [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_node', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_node', 'update_node', 'generate_image',
    'shell_exec', 'control_browser', 'call_agent', 'run_node', 'run_harness',
    'save_harness', 'execute_sql', 'wait_node_run', 'cancel_node_run', 'invoke_tool',
    'web_search', 'search_harness_library', 'complete'
  ]

  assert.equal(usesAutomaticAgentToolSelection(attachedNodeDefaults), true)
  assert.equal(usesAutomaticAgentToolSelection(nodeRuntimeDefaults), true)
  assert.equal(usesAutomaticAgentToolSelection([
    ...attachedNodeDefaults,
    'connected_custom_tool'
  ], new Set(['connected_custom_tool'])), true)
})

test('generic strict configuration rejects invalid explicit Desktop model, tool, and maxTokens values', () => {
  const cases: Array<{
    request: Record<string, unknown>
    code: AgentRuntimeConfigurationError['code']
  }> = [
    { request: { requestedModel: '' }, code: 'invalid_agent_runtime_config' },
    { request: { requestedModel: 42 }, code: 'invalid_agent_runtime_config' },
    { request: { requestedToolNames: ['unknown_tool'] }, code: 'unavailable_agent_tool' },
    { request: { requestedToolNames: [''] }, code: 'invalid_agent_runtime_config' },
    { request: { requestedMaxTokens: 0 }, code: 'invalid_agent_runtime_config' },
    { request: { requestedMaxTokens: 1.5 }, code: 'invalid_agent_runtime_config' }
  ]

  for (const { request, code } of cases) {
    assert.throws(
      () => resolveAgentRuntimeConfiguration({ ...desktopBase, ...request }),
      (error) => error instanceof AgentRuntimeConfigurationError && error.code === code
    )
  }
})

test('strict Agent timeout policy preserves explicit values and rejects every out-of-range value', () => {
  const policy = {
    defaultTimeoutMs: DEFAULT_AGENT_RUN_TIMEOUT_MS,
    maximumTimeoutMs: MAX_AGENT_RUN_TIMEOUT_MS,
    hostLabel: 'Test Agent host'
  }
  assert.equal(resolveAgentRunTimeoutMs(policy), DEFAULT_AGENT_RUN_TIMEOUT_MS)
  assert.equal(resolveAgentRunTimeoutMs({ ...policy, requestedTimeoutMs: 1 }), 1)
  assert.equal(
    resolveAgentRunTimeoutMs({ ...policy, requestedTimeoutMs: MAX_AGENT_RUN_TIMEOUT_MS }),
    MAX_AGENT_RUN_TIMEOUT_MS
  )

  const invalidCases: Array<{ requestedTimeoutMs: number; code: AgentRuntimeConfigurationError['code'] }> = [
    { requestedTimeoutMs: 0, code: 'invalid_agent_runtime_config' },
    { requestedTimeoutMs: 1.5, code: 'invalid_agent_runtime_config' },
    { requestedTimeoutMs: Number.MAX_SAFE_INTEGER + 1, code: 'invalid_agent_runtime_config' },
    { requestedTimeoutMs: MAX_AGENT_RUN_TIMEOUT_MS + 1, code: 'agent_timeout_exceeded' }
  ]
  for (const { requestedTimeoutMs, code } of invalidCases) {
    assert.throws(
      () => resolveAgentRunTimeoutMs({ ...policy, requestedTimeoutMs }),
      (error) => error instanceof AgentRuntimeConfigurationError
        && error.code === code
    )
  }
  assert.throws(
    () => resolveAgentRunTimeoutMs({
      ...policy,
      requestedTimeoutMs: MAX_AGENT_RUN_TIMEOUT_MS + 1
    }),
    (error) => error instanceof AgentRuntimeConfigurationError
      && error.code === 'agent_timeout_exceeded'
      && error.details?.maximumTimeoutMs === MAX_AGENT_RUN_TIMEOUT_MS
  )
})

test('strict Agent timeout policy permits no-timeout only as the omitted host default', () => {
  assert.equal(resolveAgentRunTimeoutMs({ defaultTimeoutMs: 0 }), 0)
  assert.throws(
    () => resolveAgentRunTimeoutMs({ defaultTimeoutMs: 0, requestedTimeoutMs: 0 }),
    (error) => error instanceof AgentRuntimeConfigurationError
      && error.code === 'invalid_agent_runtime_config'
  )
})

test('explicit Agent tool selection exposes only selected dynamic schemas', () => {
  const dynamicTools = [
    { function: { name: 'local_alpha' } },
    { function: { name: 'local_beta' } }
  ]
  assert.deepEqual(selectAgentRuntimeDynamicTools(dynamicTools, undefined), dynamicTools)
  assert.deepEqual(selectAgentRuntimeDynamicTools(dynamicTools, ['local_beta']), [dynamicTools[1]])
  assert.deepEqual(selectAgentRuntimeDynamicTools(dynamicTools, []), [])
})
