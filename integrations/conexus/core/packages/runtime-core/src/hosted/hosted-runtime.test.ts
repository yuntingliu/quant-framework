import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AgentRunController,
  AgentHostRuntimeSession,
  captureCanvasAgentObservation,
  createHostedAgentToolProfile,
  DEFAULT_AGENT_TOOL_NAMES,
  MAX_AGENT_RUN_TIMEOUT_MS,
  RuntimeJobRegistry,
  type AgentLLMResult,
  type HarnessReleaseArtifact,
  type RuntimeEvent
} from '../index.js'
import {
  executeHostedRelease,
  HOSTED_RUNTIME,
  HostedRuntimeError,
  inspectHostedRelease,
  validateHostedExposureInvocation,
  type HostedCompletionRequest,
  type HostedRuntimeAdapter
} from './hosted-runtime.js'

function multiAgentPackage(): Record<string, unknown> {
  return {
    schema: 'conexus.harness',
    manifest: {
      id: 'test.hosted.multi-agent',
      name: 'Hosted Multi Agent',
      description: 'Research and write a concise answer.',
      version: 1,
      exposures: [{
        id: 'writer',
        name: 'Writer',
        nodeId: 'writer',
        nodeType: 'agent',
        surfaces: ['agent_tool', 'page', 'api']
      }],
      defaultExposureId: 'writer',
      permissions: [{ kind: 'model', required: true }],
      dependencies: [{ kind: 'model', name: 'openrouter' }],
      capabilities: ['research', 'summarization'],
      triggers: [],
      tags: ['hosted']
    },
    graph: {
      nodes: [
        {
          id: 'brief',
          type: 'note',
          data: { label: 'Brief', content: 'Be concrete.' }
        },
        {
          id: 'researcher',
          type: 'agent',
          data: {
            label: 'Researcher',
            model: 'test/researcher',
            systemPrompt: 'Research accurately.',
            task: 'Research the delegated topic.'
          }
        },
        {
          id: 'writer',
          type: 'agent',
          data: {
            label: 'Writer',
            exposeInHarness: true,
            model: 'test/writer',
            systemPrompt: 'Write concisely.',
            task: 'Write the requested answer.'
          }
        }
      ],
      edges: [
        { id: 'brief-writer', source: 'brief', target: 'writer' },
        { id: 'researcher-writer', source: 'researcher', target: 'writer' }
      ]
    },
    runtime: { isolation: 'none' }
  }
}

function toolCall(id: string, name: string, args: Record<string, unknown>): AgentLLMResult {
  return {
    content: null,
    finish_reason: 'tool_calls',
    tool_calls: [
      {
        id,
        type: 'function',
        function: { name, arguments: JSON.stringify(args) }
      }
    ]
  }
}

function interactivePackage(): Record<string, unknown> {
  return multiAgentPackage()
}

test('compatibility report accepts multiple Agents and lists the exact Hosted adapters', () => {
  const inspected = inspectHostedRelease(multiAgentPackage())
  assert.equal(inspected.compatibility.deployable, true)
  assert.equal(inspected.compatibility.runtime, HOSTED_RUNTIME)
  assert.deepEqual(inspected.compatibility.exposures.map((exposure) => exposure.id), ['writer'])
  assert.deepEqual(inspected.compatibility.adapters.tools, createHostedAgentToolProfile().availableToolNames)
  assert.deepEqual(inspected.compatibility.adapters.toolCapabilities, {
    edit: { placement: true, relations: true },
    use: { nodeTypes: ['agent', 'harness'], mode: 'capability-contract' }
  })
  assert.deepEqual(inspected.compatibility.declaredCapabilities, ['research', 'summarization'])
  assert.match(inspected.checksum, /^[a-f0-9]{64}$/)
})

test('stored releases are rejected when their immutable checksum no longer matches', () => {
  const inspected = inspectHostedRelease(multiAgentPackage())
  assert.throws(
    () => inspectHostedRelease(inspected.release, '0'.repeat(64)),
    (error) => error instanceof HostedRuntimeError
      && error.code === 'release_checksum_mismatch'
      && (error.details as Record<string, unknown>).actualChecksum === inspected.checksum
  )
})

test('compatibility report rejects browser, file, shell, inline and MCP requirements explicitly', () => {
  const source = multiAgentPackage()
  const graph = source.graph as { nodes: Array<Record<string, unknown>> }
  graph.nodes.push({ id: 'browser', type: 'browser', data: {} })
  const manifest = source.manifest as {
    permissions: Array<Record<string, unknown>>
    dependencies: Array<Record<string, unknown>>
  }
  manifest.permissions.push({ kind: 'filesystem', required: true })
  manifest.dependencies.push({ kind: 'command', name: 'ffmpeg' })
  source.tools = [
    { name: 'inline_node', runtime: 'node', code: 'return 1' },
    { name: 'remote_mcp', runtime: 'mcp' }
  ]

  const report = inspectHostedRelease(source).compatibility
  assert.equal(report.deployable, false)
  const codes = new Set(report.issues.map((issue) => issue.code))
  assert.equal(codes.has('missing_node_adapter'), true)
  assert.equal(codes.has('missing_permission_adapter'), true)
  assert.equal(codes.has('missing_dependency_adapter'), true)
  assert.equal(codes.has('inline_tool_runtime_forbidden'), true)
  assert.equal(codes.has('missing_mcp_adapter'), true)
})

test('unknown Agent tools are deployment errors, not runtime fallbacks', () => {
  const source = multiAgentPackage()
  const graph = source.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
  graph.nodes.find((node) => node.id === 'writer')!.data.toolNames = ['complete', 'desktop_control']
  const report = inspectHostedRelease(source).compatibility
  assert.equal(report.deployable, false)
  assert.ok(report.issues.some((issue) => issue.code === 'missing_tool_adapter' && issue.toolName === 'desktop_control'))
})

test('packaged Tools cannot reuse host-registered tool names', () => {
  const source = multiAgentPackage()
  source.tools = [{
    name: 'complete',
    runtime: 'node',
    code: 'async function run() { return { ok: true } }'
  }]

  const report = inspectHostedRelease(source).compatibility
  assert.equal(report.deployable, false)
  assert.ok(report.issues.some((issue) =>
    issue.code === 'reserved_tool_name' && issue.toolName === 'complete'))
})

test('Hosted Runtime migrates a persisted historical automatic Desktop tool set', () => {
  const source = multiAgentPackage()
  const graph = source.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
  graph.nodes.find((node) => node.id === 'writer')!.data.toolNames = [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_nodes', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_nodes', 'update_nodes', 'generate_image',
    'shell_exec', 'control_browser', 'run_node', 'save_harness', 'execute_sql',
    'control_node_run', 'web_search', 'search_harness_library', 'complete'
  ]

  const inspected = inspectHostedRelease(source)
  const hostedWriter = inspected.release.graph.nodes.find((node) => node.id === 'writer')

  assert.equal(inspected.compatibility.deployable, true)
  assert.deepEqual(hostedWriter?.data.toolNames, createHostedAgentToolProfile().defaultToolNames)
})

test('Hosted Runtime does not rematerialize packaged Tools as top-level model tools', () => {
  const source = multiAgentPackage()
  const graph = source.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
  graph.nodes.find((node) => node.id === 'writer')!.data.toolNames = [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_nodes', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_nodes', 'update_nodes', 'generate_image',
    'shell_exec', 'control_browser', 'run_node', 'save_harness', 'execute_sql',
    'control_node_run', 'web_search', 'search_harness_library', 'complete',
    'double_value'
  ]
  const manifest = source.manifest as { permissions: Array<Record<string, unknown>> }
  manifest.permissions.push({ kind: 'shell', required: true })
  source.runtime = { isolation: 'shell' }
  source.tools = [{
    name: 'double_value',
    runtime: 'node',
    code: 'async function run(input) { return { doubled: input.value * 2 } }'
  }]
  const inspected = inspectHostedRelease(source, undefined, {
    name: 'test.trusted-runtime.v1',
    nodeTypes: [],
    toolRuntimes: ['node'],
    permissions: ['shell'],
    isolation: ['shell'],
    capabilityIds: [],
    runNodeTypes: []
  })
  const hostedWriter = inspected.release.graph.nodes.find((node) => node.id === 'writer')

  assert.equal(inspected.compatibility.deployable, true)
  assert.deepEqual(hostedWriter?.data.toolNames, createHostedAgentToolProfile().defaultToolNames)
})

test('Hosted Runtime resolves persisted automatic tools from the active host profile', () => {
  const source = multiAgentPackage()
  const graph = source.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
  graph.nodes.find((node) => node.id === 'writer')!.data.toolNames = [...DEFAULT_AGENT_TOOL_NAMES]
  const profile = {
    name: 'test.shell-host.v1',
    nodeTypes: [],
    toolRuntimes: [],
    permissions: [],
    isolation: [],
    capabilityIds: ['cli.exec'],
    runNodeTypes: []
  } as const

  const inspected = inspectHostedRelease(source, undefined, profile)
  const hostedWriter = inspected.release.graph.nodes.find((node) => node.id === 'writer')

  assert.equal(inspected.compatibility.deployable, true)
  assert.deepEqual(
    hostedWriter?.data.toolNames,
    createHostedAgentToolProfile(profile).defaultToolNames
  )
})

test('Hosted Runtime exposes the stable node Agent ABI at execution time', async () => {
  const source = multiAgentPackage()
  const profile = {
    name: 'test.account-workspace-host.v1',
    nodeTypes: [],
    toolRuntimes: [],
    permissions: [],
    isolation: [],
    capabilityIds: ['web.search'],
    runNodeTypes: []
  } as const
  const currentDefaults = createHostedAgentToolProfile(profile).defaultToolNames
  const graph = source.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
  graph.nodes.find((node) => node.id === 'writer')!.data.toolNames = currentDefaults
  const runtimeAdapter: HostedRuntimeAdapter = {
    profile,
    async executeShell() { throw new Error('not used') },
    async executeTool() { throw new Error('not used') },
    async executeNodeOperation() { return { result: { success: true } } }
  }

  const result = await executeHostedRelease({
    runId: 'run_restore_hosted_authoring',
    release: inspectHostedRelease(source, undefined, profile).release,
    input: { request: 'workspace authoring' },
    runtimeAdapter,
    complete: async (request) => {
      const toolNames = request.tools?.map((tool) => tool.function.name) ?? []
      const systemPrompt = request.messages.find((message) => message.role === 'system')?.content
      assert.deepEqual(toolNames, ['find', 'observe', 'create', 'edit', 'use', 'request_user_input', 'complete'])
      assert.equal(typeof systemPrompt, 'string')
      assert.match(systemPrompt as string, /You are a Conexus canvas Agent\./)
      assert.match(systemPrompt as string, /## Node-Level Instructions\nWrite concisely\./)
      assert.match(systemPrompt as string, /mutable, authorized Harness workspace/)
      assert.match(systemPrompt as string, /`note`: Durable rich document/)
      assert.match(systemPrompt as string, /## Available Runtime Nodes/)
      assert.match(systemPrompt as string, /runtime:web-search/)
      assert.match(systemPrompt as string, /web\.search/)
      assert.doesNotMatch(systemPrompt as string, /input_schema|output_schema|contract_ref/)
      return toolCall('complete-restored-authoring', 'complete', {
        status: 'done',
        summary: 'Authoring is available.',
      })
    }
  })

  assert.equal(result.status, 'completed')
})

test('the legacy Agent tools field is a deployment error, not a default-tool fallback', () => {
  const source = multiAgentPackage()
  const graph = source.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
  const writer = graph.nodes.find((node) => node.id === 'writer')!
  delete writer.data.toolNames
  writer.data.tools = ['complete']

  const report = inspectHostedRelease(source).compatibility

  assert.equal(report.deployable, false)
  assert.ok(report.issues.some((issue) =>
    issue.code === 'legacy_agent_tools_field' && issue.nodeId === 'writer'
  ))
})

test('Hosted Agent configuration rejects malformed values without imposing a token ceiling', () => {
  const cases: Array<{ patch: Record<string, unknown>; code: string }> = [
    { patch: { model: 42 }, code: 'invalid_agent_runtime_config' },
    { patch: { toolNames: 'complete' }, code: 'invalid_agent_runtime_config' },
    { patch: { toolNames: [''] }, code: 'invalid_agent_runtime_config' },
    { patch: { maxTokens: 1.5 }, code: 'invalid_agent_runtime_config' }
  ]

  for (const { patch, code } of cases) {
    const source = multiAgentPackage()
    const graph = source.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
    Object.assign(graph.nodes.find((node) => node.id === 'writer')!.data, patch)
    const report = inspectHostedRelease(source).compatibility
    assert.equal(report.deployable, false)
    assert.ok(report.issues.some((issue) => issue.nodeId === 'writer' && issue.code === code))
  }

  const unbounded = multiAgentPackage()
  const graph = unbounded.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
  graph.nodes.find((node) => node.id === 'writer')!.data.maxTokens = 1_000_000
  assert.equal(inspectHostedRelease(unbounded).compatibility.deployable, true)
})

test('Hosted compatibility rejects every explicit invalid runtime timeout without normalization', () => {
  const cases: Array<{ timeoutMs: unknown; code: string }> = [
    { timeoutMs: 0, code: 'invalid_agent_runtime_config' },
    { timeoutMs: 1.5, code: 'invalid_agent_runtime_config' },
    { timeoutMs: '1000', code: 'invalid_agent_runtime_config' },
    { timeoutMs: Number.MAX_SAFE_INTEGER + 1, code: 'invalid_agent_runtime_config' },
    { timeoutMs: MAX_AGENT_RUN_TIMEOUT_MS + 1, code: 'agent_timeout_exceeded' }
  ]

  for (const { timeoutMs, code } of cases) {
    const source = multiAgentPackage()
    source.runtime = { isolation: 'none', timeoutMs }
    const report = inspectHostedRelease(source).compatibility
    assert.equal(report.deployable, false)
    assert.ok(report.issues.some((issue) =>
      issue.code === code
      && issue.requirement?.kind === 'runtime'
      && issue.requirement.name === 'timeout'
    ))
  }
})

test('Hosted execution rejects an oversized explicit timeout before invoking the model', async () => {
  const source = multiAgentPackage()
  source.runtime = { isolation: 'none', timeoutMs: MAX_AGENT_RUN_TIMEOUT_MS + 1 }
  let called = false
  await assert.rejects(
    executeHostedRelease({
      runId: 'run_invalid_timeout',
      release: source,
      input: { request: 'strict timeout' },
      complete: async () => {
        called = true
        return { content: 'should not run', finish_reason: 'stop' }
      }
    }),
    (error) => error instanceof HostedRuntimeError
      && error.code === 'unsupported_harness'
      && (error.details as { issues?: Array<{ code?: string }> }).issues?.some(
        (issue) => issue.code === 'agent_timeout_exceeded'
      ) === true
  )
  assert.equal(called, false)
})

test('Agent invocation validation uses the Agent request contract', () => {
  const release = inspectHostedRelease(multiAgentPackage()).release
  assert.throws(
    () => validateHostedExposureInvocation(release, release.manifest.exposures[0]!, {}),
    (error) => error instanceof HostedRuntimeError && error.code === 'invalid_run_input'
  )
  assert.doesNotThrow(() => validateHostedExposureInvocation(
    release,
    release.manifest.exposures[0]!,
    { request: 'Do the work.' }
  ))
})

test('Hosted Runtime delegates to another Agent and returns the final complete summary', async () => {
  const release = inspectHostedRelease(multiAgentPackage()).release
  const requests: HostedCompletionRequest[] = []
  const events: RuntimeEvent[] = []
  let writerCalls = 0

  const result = await executeHostedRelease({
    runId: 'run_multi_agent',
    release,
    input: { request: 'portable Agent runtimes' },
    events: { emit: (event) => { events.push(event) } },
    complete: async (request) => {
      requests.push(request)
      const toolNames = request.tools?.map((tool) => tool.function.name)
      const hostedTools = createHostedAgentToolProfile()
      const executionToolNames = [...hostedTools.defaultToolNames]
      assert.deepEqual(toolNames, executionToolNames)
      assert.deepEqual(
        request.tools,
        executionToolNames.map((name) => hostedTools.tools.find((tool) => tool.name === name)!.schema)
      )
      if (request.model === 'test/researcher') {
        assert.match(String(request.messages.at(-1)?.content), /Research the delegated topic/)
        assert.match(String(request.messages.at(-1)?.content), /Collect concrete facts/)
        return toolCall('research-complete', 'complete', {
          status: 'done',
          summary: 'The Runtime Core is shared.',
        })
      }
      writerCalls += 1
      if (writerCalls === 1) {
        const systemContent = String(request.messages[0]?.content)
        const serializedMessages = request.messages.map((message) => String(message.content)).join('\n')
        assert.equal(request.messages[0]?.role, 'system')
        assert.match(systemContent, /## Canvas Context/)
        assert.match(systemContent, /label: "Brief"/)
        assert.match(systemContent, /label: "Researcher"/)
        assert.doesNotMatch(systemContent, /Be concrete/)
        assert.doesNotMatch(serializedMessages, /## Runtime Context/)
        assert.doesNotMatch(serializedMessages, /Run input:/)
        assert.equal(serializedMessages.match(/portable Agent runtimes/g)?.length, 1)
        assert.equal(String(request.messages.at(-1)?.content), 'portable Agent runtimes')
        return toolCall('delegate', 'use', {
          node_id: 'researcher',
          capability: 'agent.run',
          input: { task: 'Collect concrete facts.' }
        })
      }
      if (writerCalls === 2) {
        const delegatedRun = request.messages.find((message) => message.role === 'tool' && message.name === 'use')
        assert.deepEqual(JSON.parse(String(delegatedRun?.content)), {
          success: true,
          node_id: 'researcher',
          status: 'running'
        })
        return toolCall('wait-research', 'use', {
          node_id: 'researcher',
          capability: 'run.wait',
          input: {}
        })
      }
      const delegatedResult = request.messages.find((message) =>
        message.role === 'tool' && message.name === 'use' && message.tool_call_id === 'wait-research')
      assert.match(String(delegatedResult?.content), /The Runtime Core is shared/)
      const delegatedPayload = JSON.parse(String(delegatedResult?.content)) as {
        result?: Record<string, unknown>
      }
      assert.equal(Object.prototype.hasOwnProperty.call(delegatedPayload.result ?? {}, 'output'), false)
      return toolCall('writer-complete', 'complete', {
        status: 'done',
        summary: 'Portable runtime summary complete.'
      })
    }
  })

  assert.equal(result.status, 'completed')
  assert.equal(result.model, 'test/writer')
  assert.deepEqual(result.models, ['test/writer', 'test/researcher'])
  assert.equal(result.result, undefined)
  assert.equal(requests.length, 4)
  assert.ok(events.some((event) => event.runId.startsWith('agent_') && event.type === 'agent.completed'))
  assert.ok(events.some((event) => event.runId.startsWith('inv_') && event.type === 'harness.completed'))
})

test('owned Hosted control sessions remain open across sequential delegated Agent runs', async () => {
  const release = inspectHostedRelease(interactivePackage()).release
  let writerCalls = 0
  let researcherCalls = 0

  const result = await executeHostedRelease({
    runId: 'run_sequential_agents',
    release,
    input: { request: 'sequential delegation' },
    complete: async (request) => {
      if (request.model === 'test/researcher') {
        researcherCalls += 1
        return toolCall(`research-complete-${researcherCalls}`, 'complete', {
          status: 'done',
          summary: `Research pass ${researcherCalls} completed.`,
        })
      }

      writerCalls += 1
      if (writerCalls === 1 || writerCalls === 3) {
        return toolCall(`delegate-${writerCalls}`, 'use', {
          node_id: 'researcher',
          capability: 'agent.run',
          input: { task: `Run research pass ${writerCalls === 1 ? 1 : 2}.` }
        })
      }
      if (writerCalls === 2 || writerCalls === 4) {
        return toolCall(`wait-${writerCalls}`, 'use', {
          node_id: 'researcher',
          capability: 'run.wait',
          input: {}
        })
      }
      return toolCall('writer-complete', 'complete', {
        status: 'done',
        summary: 'Both sequential research passes completed.',
      })
    }
  })

  assert.equal(result.status, 'completed')
  assert.equal(writerCalls, 5)
  assert.equal(researcherCalls, 2)
})

test('Hosted Agent keeps policy and Canvas metadata in system and sends current user content once', async () => {
  const release = inspectHostedRelease(interactivePackage()).release
  const currentRequest = 'Use the latest user request exactly once.'

  const result = await executeHostedRelease({
    runId: 'run_prompt_roles',
    release,
    input: { request: 'duplicated-run-input-marker' },
    conversation: {
      history: [
        { role: 'user', content: 'Earlier request.' },
        { role: 'assistant', content: 'Earlier answer.' }
      ],
      currentRequest
    },
    complete: async (request) => {
      const serializedMessages = request.messages.map((message) => String(message.content)).join('\n')
      const systemMessages = request.messages.filter((message) => message.role === 'system')
      assert.equal(systemMessages.length, 1)
      assert.match(String(systemMessages[0]?.content), /## Canvas Context/)
      assert.match(String(systemMessages[0]?.content), /Runtime-authored state snapshot/)
      assert.doesNotMatch(serializedMessages, /## Runtime Context/)
      assert.doesNotMatch(serializedMessages, /Run input:/)
      assert.doesNotMatch(serializedMessages, /duplicated-run-input-marker/)
      assert.equal(serializedMessages.match(new RegExp(currentRequest, 'g'))?.length, 1)
      assert.equal(request.messages.at(-1)?.role, 'user')
      assert.equal(request.messages.at(-1)?.content, currentRequest)
      return toolCall('prompt-role-complete', 'complete', {
        status: 'blocked',
        summary: 'Prompt roles verified.',
        blocker: 'test_terminal'
      })
    }
  })

  assert.equal(result.status, 'blocked')
})

test('Hosted Agent injects inter-run Canvas delta once and keeps system context fixed during the run', async () => {
  const release = inspectHostedRelease(interactivePackage()).release
  const previous = captureCanvasAgentObservation(release.graph.nodes, release.graph.edges, 'writer')
  const workspace = {
    nodes: structuredClone(release.graph.nodes),
    edges: structuredClone(release.graph.edges)
  }
  const brief = workspace.nodes.find((node) => node.id === 'brief')!
  brief.data.content = 'Changed between runs.'
  let calls = 0
  let initialSystem = ''

  const result = await executeHostedRelease({
    runId: 'run_canvas_delta',
    release,
    workspace,
    input: { request: 'Canvas deltas' },
    conversation: {
      history: [{ role: 'user', content: 'Earlier request.' }],
      currentRequest: 'Continue with the changed Canvas.',
      canvasObservation: previous
    },
    complete: async (request) => {
      calls += 1
      const systemMessages = request.messages.filter((message) => message.role === 'system')
      assert.equal(systemMessages.length, 1)
      assert.equal(String(systemMessages[0]?.content).match(/## Canvas Changes Since Previous Run/g)?.length, 1)
      assert.match(String(systemMessages[0]?.content), /id="brief".*changed_fields=\["content"\]/)
      assert.equal(request.messages.filter((message) =>
        message.content === 'Continue with the changed Canvas.').length, 1)
      if (calls === 1) {
        assert.equal(request.messages.at(-1)?.content, 'Continue with the changed Canvas.')
        initialSystem = String(request.messages[0]?.content)
        assert.match(initialSystem, /## Canvas Context/)
        assert.match(initialSystem, /label: "Brief"/)
        return toolCall('rename-brief', 'edit', {
          operations: [{ kind: 'patch', node_id: 'brief', set: { label: 'Renamed during run' } }]
        })
      }
      assert.equal(String(request.messages[0]?.content), initialSystem)
      assert.doesNotMatch(String(request.messages[0]?.content), /Renamed during run/)
      const updateResult = request.messages.find((message) =>
        message.role === 'tool' && message.name === 'edit')
      assert.ok(updateResult)
      assert.match(String(updateResult.content), /"success":true/)
      return toolCall('canvas-delta-complete', 'complete', {
        status: 'done',
        summary: 'Canvas delta verified.',
      })
    }
  })

  assert.equal(calls, 2)
  assert.equal(result.status, 'completed')
  assert.equal(result.workspace.nodes.find((node) => node.id === 'brief')?.data.label, 'Renamed during run')
})

test('use executes a packaged Harness through the same shared invocation runtime', async () => {
  const source = multiAgentPackage()
  const graph = source.graph as {
    nodes: Array<{ id: string; type: string; parentId?: string; data: Record<string, unknown> }>
    edges: Array<{ id: string; source: string; target: string }>
  }
  graph.nodes.push(
    {
      id: 'review-harness',
      type: 'harness',
      data: {
        label: 'Review Harness',
        description: 'Review the draft.',
        entryNodeId: 'reviewer'
      }
    },
    {
      id: 'reviewer',
      type: 'agent',
      parentId: 'review-harness',
      data: {
        label: 'Reviewer',
        model: 'test/reviewer',
        task: 'Review the current draft.',
        exposeInHarness: true,
        toolNames: ['complete']
      }
    }
  )
  graph.edges.push({ id: 'writer-review-harness', source: 'writer', target: 'review-harness' })
  const release = inspectHostedRelease(source).release
  let writerCalls = 0
  let reviewerCalls = 0
  let harnessCompleted = false
  const result = await executeHostedRelease({
    runId: 'run_nested_harness',
    release,
    input: { request: 'shared Harness execution' },
    onWorkspaceChanged: (workspace) => {
      const harness = workspace.nodes.find((node) => node.id === 'review-harness')
      if (harness?.data.status === 'completed') harnessCompleted = true
    },
    complete: async (request) => {
      if (request.model === 'test/reviewer') {
        reviewerCalls += 1
        assert.match(String(request.messages.at(-1)?.content), /Review the draft/)
        return toolCall('review-complete', 'complete', {
          status: 'done',
          summary: 'The draft passed review.',
        })
      }
      writerCalls += 1
      if (writerCalls === 1) {
        const useNode = request.tools?.find((tool) => tool.function.name === 'use')
        const properties = (useNode?.function.parameters as { properties?: Record<string, unknown> }).properties
        assert.ok(properties?.capability)
        return toolCall('run-review-harness', 'use', {
          node_id: 'review-harness',
          capability: 'harness.run',
          input: { exposure_id: 'reviewer', input: { request: 'Review the draft.' } }
        })
      }
      if (writerCalls === 2) {
        const runResult = request.messages.find((message) => message.role === 'tool' && message.name === 'use')
        const payload = JSON.parse(String(runResult?.content)) as Record<string, unknown>
        assert.deepEqual(payload, {
          success: true,
          node_id: 'review-harness',
          status: 'running'
        })
        return toolCall('wait-review-harness', 'use', {
          node_id: 'review-harness',
          capability: 'run.wait',
          input: {}
        })
      }
      const waitResult = request.messages.filter((message) =>
        message.role === 'tool' && message.name === 'use').at(-1)
      assert.match(String(waitResult?.content), /passed review/)
      return toolCall('review-observed', 'complete', {
        status: 'blocked',
        summary: 'Nested Harness result was observed.',
        blocker: 'test_complete'
      })
    }
  })
  assert.equal(writerCalls, 3)
  assert.equal(reviewerCalls, 1)
  assert.equal(result.status, 'blocked')
  assert.equal(harnessCompleted, true)
})

test('safe graph tools update public Agent configuration but reject runtime-owned Agent state', async () => {
  const release = inspectHostedRelease(multiAgentPackage()).release
  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_graph_tools',
    release,
    input: { request: 'graph state' },
    complete: async (request) => {
      call += 1
      if (call === 1) {
        return toolCall('update-note', 'edit', {
          operations: [{ kind: 'patch', node_id: 'brief', set: { content: 'Updated safely.' } }]
        })
      }
      if (call === 2) {
        const updateResult = request.messages.filter((message) => message.role === 'tool' && message.name === 'edit').at(-1)
        assert.deepEqual(JSON.parse(String(updateResult?.content)), { success: true, edited: 1 })
        return toolCall('observe-note', 'observe', {
          requests: [{ node_id: 'brief', detail: 'values', fields: ['content'], include_contracts: true }]
        })
      }
      if (call === 3) {
        const observation = request.messages.find((message) => message.role === 'tool' && message.name === 'observe')
        const observed = JSON.parse(String(observation?.content)) as {
          success: boolean
          results: Array<{ values?: Record<string, unknown> & { edit_schema?: unknown } }>
        }
        assert.equal(observed.success, true)
        assert.equal(observed.results[0]?.values?.content, 'Updated safely.')
        assert.ok(observed.results[0]?.values?.edit_schema)
        return toolCall('protect-agent', 'edit', {
          operations: [
            { kind: 'patch', node_id: 'brief', set: { content: 'This partial update must not apply.' } },
            { kind: 'patch', node_id: 'writer', set: { status: 'done' } }
          ]
        })
      }
      if (call === 4) {
        const protectedResult = request.messages.filter((message) => message.role === 'tool' && message.name === 'edit').at(-1)
        assert.match(String(protectedResult?.content), /protected fields/)
        assert.doesNotMatch(String(protectedResult?.content), /"updates"/)
        return toolCall('update-agent-configuration', 'edit', {
          operations: [{
            kind: 'patch',
            node_id: 'writer',
            set: {
              systemPrompt: 'Updated node instructions.',
              maxTokens: 8_192
            }
          }]
        })
      }
      if (call === 5) {
        const updateResult = request.messages.filter((message) => message.role === 'tool' && message.name === 'edit').at(-1)
        assert.match(String(updateResult?.content), /"success":true/)
        return toolCall('observe-atomic-state-and-agent-config', 'observe', {
          requests: [
            { node_id: 'brief', detail: 'values', fields: ['content'] },
            { node_id: 'writer', detail: 'values', fields: ['systemPrompt', 'maxTokens'] }
          ]
        })
      }
      if (call === 6) {
        const observation = request.messages.filter((message) => message.role === 'tool' && message.name === 'observe').at(-1)
        assert.match(String(observation?.content), /Updated safely/)
        assert.doesNotMatch(String(observation?.content), /partial update/)
        assert.match(String(observation?.content), /Updated node instructions/)
        assert.match(String(observation?.content), /8192/)
        return toolCall('done', 'complete', {
          status: 'done',
          summary: 'Graph tools checked.'
        })
      }
      throw new Error('Unexpected model invocation.')
    }
  })
  assert.equal(call, 6)
  assert.deepEqual(result.workspaceChanges, {
    created: [],
    updated: [{
      id: 'brief',
      type: 'note',
      label: 'Brief',
      values: { content: 'Updated safely.' }
    }],
    deleted: []
  })
  const updatedWriter = result.workspace.nodes.find((node) => node.id === 'writer')
  assert.equal(updatedWriter?.data.systemPrompt, 'Updated node instructions.')
  assert.equal(updatedWriter?.data.maxTokens, 8_192)
})

test('Hosted edit applies repeated node patches in order and rolls back the batch on failure', async () => {
  const release = inspectHostedRelease(multiAgentPackage()).release
  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_ordered_atomic_graph_updates',
    release,
    input: { request: 'ordered graph updates' },
    complete: async (request) => {
      call += 1
      if (call === 1) {
        return toolCall('ordered-update', 'edit', {
          operations: [
            { kind: 'patch', node_id: 'brief', set: { content: 'alpha' } },
            { kind: 'patch', node_id: 'brief', set: { contentPatch: { operation: 'replace', find: 'alpha', replace: 'beta' } } }
          ]
        })
      }
      if (call === 2) {
        const updateResult = request.messages
          .filter((message) => message.role === 'tool' && message.name === 'edit')
          .at(-1)
        assert.deepEqual(JSON.parse(String(updateResult?.content)), { success: true, edited: 2 })
        return toolCall('rejected-ordered-update', 'edit', {
          operations: [
            { kind: 'patch', node_id: 'brief', set: { contentPatch: { operation: 'replace', find: 'beta', replace: 'gamma' } } },
            { kind: 'patch', node_id: 'brief', set: { contentPatch: { operation: 'replace', find: 'missing', replace: 'delta' } } }
          ]
        })
      }
      if (call === 3) {
        const rejectedResult = request.messages
          .filter((message) => message.role === 'tool' && message.name === 'edit')
          .at(-1)
        const rejected = JSON.parse(String(rejectedResult?.content)) as { success: boolean; error?: string }
        assert.equal(rejected.success, false)
        assert.match(rejected.error ?? '', /patch target was not found/i)
        return toolCall('observe-rolled-back-update', 'observe', {
          requests: [{ node_id: 'brief', detail: 'values', fields: ['content'] }]
        })
      }
      if (call === 4) {
        const observation = request.messages
          .filter((message) => message.role === 'tool' && message.name === 'observe')
          .at(-1)
        assert.match(String(observation?.content), /beta/)
        assert.doesNotMatch(String(observation?.content), /gamma/)
        return toolCall('ordered-update-complete', 'complete', {
          status: 'done',
          summary: 'Ordered updates remained atomic.'
        })
      }
      throw new Error('Unexpected model invocation.')
    }
  })

  assert.equal(call, 4)
  assert.equal(result.workspace.nodes.find((node) => node.id === 'brief')?.data.content, 'beta')
})

test('Hosted edit shares Desktop placement semantics', async () => {
  const source = multiAgentPackage()
  const graph = source.graph as { nodes: Array<{ id: string; type: string; data: Record<string, unknown> }> }
  graph.nodes.push({
    id: 'review-flow',
    type: 'harness',
    data: { label: 'Review flow' }
  })
  const release = inspectHostedRelease(source).release
  let latestWorkspace: { nodes: Array<{ id: string; parentId?: string; data: Record<string, unknown> }> } | undefined
  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_extended_updates',
    release,
    input: { request: 'extended updates' },
    onWorkspaceChanged: (workspace) => {
      latestWorkspace = structuredClone(workspace)
    },
    complete: async (request) => {
      call += 1
      if (call === 1) {
        return toolCall('extended-update', 'edit', {
          operations: [
            {
              kind: 'move',
              node_id: 'brief',
              scope: 'harness',
              harness_node_id: 'review-flow'
            }
          ]
        })
      }
      const updateResult = request.messages.find((message) =>
        message.role === 'tool' && message.name === 'edit')
      assert.deepEqual(JSON.parse(String(updateResult?.content)), {
        success: true,
        edited: 1
      })
      return toolCall('extended-update-complete', 'complete', {
        status: 'blocked',
        summary: 'Extended updates verified.',
        blocker: 'test_complete'
      })
    }
  })

  assert.equal(call, 2)
  assert.equal(result.status, 'blocked')
  const moved = latestWorkspace?.nodes.find((node) => node.id === 'brief')
  assert.equal(moved?.parentId, 'review-flow')
  assert.equal(moved?.data.harnessNodeId, 'review-flow')
})

test('find returns the lightweight node index and relation shape', async () => {
  const source = multiAgentPackage()
  const graph = source.graph as {
    nodes: Array<{ id: string; type: string; data: Record<string, unknown>; parentId?: string }>
    edges: Array<{ id: string; source: string; target: string; relation?: string }>
  }
  graph.nodes.push(
    { id: 'review-flow', type: 'harness', data: { label: 'Review flow' } },
    { id: 'review-agent', type: 'agent', parentId: 'review-flow', data: { label: 'Reviewer' } },
    { id: 'review-note', type: 'note', parentId: 'review-flow', data: { label: 'Review notes' } }
  )
  graph.edges.push({
    id: 'review-agent-note',
    source: 'review-agent',
    target: 'review-note',
    relation: 'The Reviewer writes the review notes.'
  })
  const release = inspectHostedRelease(source).release
  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_list_nodes_shape',
    release,
    input: { request: 'portable result shapes' },
    complete: async (request) => {
      call += 1
      if (call === 1) return toolCall('list-graph', 'find', {})
      if (call === 2) {
        const listResult = request.messages.find((message) => message.role === 'tool' && message.name === 'find')
        const parsed = JSON.parse(String(listResult?.content)) as {
          success: boolean
          nodes: Array<Record<string, unknown>>
          relations: Array<Record<string, unknown>>
        }
        assert.equal(parsed.success, true)
        assert.deepEqual(parsed.nodes.find((node) => node.id === 'writer'), {
          id: 'writer',
          type: 'agent',
          label: 'Writer',
          description: '',
          status: 'idle',
          capabilities: ['agent.run', 'run.wait', 'run.cancel']
        })
        assert.deepEqual(parsed.relations.find((edge) =>
          Array.isArray(edge.node_ids) && edge.node_ids.includes('brief') && edge.node_ids.includes('writer')
        ), {
          edge_id: 'brief-writer',
          node_ids: ['brief', 'writer'],
          relation: 'These nodes are related.'
        })
        assert.equal(parsed.nodes.some((node) => node.id === 'review-agent'), false)
        return toolCall('list-harness', 'find', { scope: 'harness', harness_node_id: 'review-flow' })
      }
      if (call === 3) {
        const listResults = request.messages.filter((message) => message.role === 'tool' && message.name === 'find')
        const parsed = JSON.parse(String(listResults.at(-1)?.content)) as {
          success: boolean
          nodes: Array<Record<string, unknown>>
          relations: Array<Record<string, unknown>>
        }
        assert.equal(parsed.success, true)
        assert.deepEqual(parsed.nodes.map((node) => node.id), ['review-agent', 'review-note'])
        assert.deepEqual(parsed.nodes.map((node) => node.parent_id), ['review-flow', 'review-flow'])
        assert.deepEqual(parsed.relations, [{
          edge_id: 'review-agent-note',
          node_ids: ['review-agent', 'review-note'],
          relation: 'The Reviewer writes the review notes.'
        }])
        return toolCall('find-writer', 'find', { query: 'Writer' })
      }
      if (call === 4) {
        const listResults = request.messages.filter((message) => message.role === 'tool' && message.name === 'find')
        const parsed = JSON.parse(String(listResults.at(-1)?.content)) as {
          success: boolean
          nodes: Array<Record<string, unknown>>
          relations: Array<Record<string, unknown>>
        }
        assert.equal(parsed.success, true)
        assert.deepEqual(parsed.nodes.map((node) => node.id), ['writer'])
        assert.deepEqual(parsed.relations, [])
        return toolCall('blocked', 'complete', {
          status: 'blocked',
          summary: 'The portable list result shape was verified.',
          blocker: 'shape_test_complete'
        })
      }
      throw new Error('Unexpected model invocation.')
    }
  })
  assert.equal(call, 4)
  assert.equal(result.status, 'blocked')
})

test('Hosted operations reject removed node_ids and data parameter shapes', async () => {
  const release = inspectHostedRelease(multiAgentPackage()).release
  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_removed_tool_parameters',
    release,
    input: { request: 'portable ABI' },
    complete: async (request) => {
      call += 1
      if (call === 1) {
        return toolCall('legacy-update', 'edit', {
          operations: [{ kind: 'patch', node_id: 'brief', data: { content: 'Must not be applied.' } }]
        })
      }
      if (call === 2) {
        const updateResult = request.messages.find((message) => message.role === 'tool' && message.name === 'edit')
        assert.match(String(updateResult?.content), /requires a non-empty set object/)
        return toolCall('legacy-observe', 'observe', { node_ids: ['brief'] })
      }
      const observationResult = request.messages.find((message) => message.role === 'tool' && message.name === 'observe')
      assert.match(String(observationResult?.content), /requests must contain/)
      return toolCall('blocked', 'complete', {
        status: 'blocked',
        summary: 'Removed parameter shapes were rejected.',
        blocker: 'invalid_tool_arguments'
      })
    }
  })
  assert.equal(call, 3)
  assert.equal(result.status, 'blocked')
})

test('use rejects a capability not declared by the target node', async () => {
  const release = inspectHostedRelease(multiAgentPackage()).release
  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_non_agent_target',
    release,
    input: { request: 'adapter boundaries' },
    complete: async (request) => {
      call += 1
      if (call === 1) return toolCall('run-note', 'use', {
        node_id: 'brief', capability: 'agent.run', input: { task: 'Try to execute this note.' }
      })
      const runResult = request.messages.find((message) => message.role === 'tool' && message.name === 'use')
      assert.match(String(runResult?.content), /does not declare capability agent.run/)
      return toolCall('blocked', 'complete', {
        status: 'blocked',
        summary: 'The Hosted adapter rejected a non-Agent target.',
        blocker: 'missing_node_adapter'
      })
    }
  })
  assert.equal(call, 2)
  assert.equal(result.status, 'blocked')
})

test('an explicit runtime adapter admits and executes a packaged Tool', async () => {
  const source = multiAgentPackage()
  const graph = source.graph as { nodes: Array<{ id: string; type: string; data: Record<string, unknown> }> }
  graph.nodes.push({
    id: 'doubler',
    type: 'tool',
    data: {
      label: 'Doubler',
      toolName: 'double_value',
      inputSchema: {
        type: 'object',
        properties: { value: { type: 'number' } },
        required: ['value'],
        additionalProperties: false
      }
    }
  })
  const manifest = source.manifest as { permissions: Array<Record<string, unknown>> }
  manifest.permissions.push({ kind: 'shell', required: true })
  source.runtime = { isolation: 'shell' }
  source.tools = [{
    nodeId: 'doubler',
    name: 'double_value',
    description: 'Double one number.',
    runtime: 'node',
    code: 'async function run(input) { return { doubled: input.value * 2 } }',
    inputSchema: {
      type: 'object',
      properties: { value: { type: 'number' } },
      required: ['value'],
      additionalProperties: false
    }
  }]
  const adapter: HostedRuntimeAdapter = {
    profile: {
      name: 'test.trusted-runtime.v1',
      nodeTypes: ['tool'],
      toolRuntimes: ['node'],
      permissions: ['shell'],
      isolation: ['shell'],
      capabilityIds: [],
      runNodeTypes: ['tool']
    },
    async executeShell() {
      throw new Error('Shell execution was not expected.')
    },
    async executeTool(params) {
      assert.equal(params.definition.name, 'double_value')
      assert.deepEqual(params.args, { value: 7 })
      return {
        success: true,
        runtime: 'node',
        stdout: '',
        stderr: '',
        exitCode: 0,
        timedOut: false,
        durationMs: 1,
        result: { doubled: 14 }
      }
    }
  }
  const inspected = inspectHostedRelease(source, undefined, adapter.profile)
  assert.equal(inspected.compatibility.deployable, true, JSON.stringify(inspected.compatibility.issues))
  assert.equal(inspected.compatibility.adapters.tools.includes('double_value'), false)

  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_packaged_tool',
    release: inspected.release,
    runtimeAdapter: adapter,
    input: { request: 'packaged tools' },
    complete: async (request) => {
      call += 1
      if (call === 1) return toolCall('double', 'use', {
        node_id: 'doubler', capability: 'tool.invoke', input: { value: 7 }
      })
      const message = request.messages.find((entry) => entry.role === 'tool' && entry.name === 'use')
      const payload = JSON.parse(String(message?.content)) as { success: boolean; output: unknown }
      assert.equal(payload.success, true)
      assert.deepEqual(payload.output, { doubled: 14 })
      return toolCall('done', 'complete', {
        status: 'blocked',
        summary: 'Packaged Tool executed.',
        blocker: 'test_complete'
      })
    }
  })
  assert.equal(call, 2)
  assert.equal(result.status, 'blocked')
})

test('a Tool exposure uses the Tool node schema and returns its result directly', async () => {
  const source = multiAgentPackage()
  const graph = source.graph as { nodes: Array<{ id: string; type: string; data: Record<string, unknown> }> }
  graph.nodes.push({
    id: 'doubler',
    type: 'tool',
    data: {
      label: 'Doubler',
      toolName: 'double_value',
      runtime: 'node',
      exposeInHarness: true,
      inputSchema: {
        type: 'object',
        properties: { value: { type: 'number' } },
        required: ['value'],
        additionalProperties: false
      },
      outputSchema: {
        type: 'object',
        properties: { doubled: { type: 'number' } },
        required: ['doubled'],
        additionalProperties: false
      }
    }
  })
  const manifest = source.manifest as {
    exposures: Array<Record<string, unknown>>
    defaultExposureId: string
    permissions: Array<Record<string, unknown>>
  }
  manifest.exposures = [{
    id: 'double',
    name: 'Double',
    nodeId: 'doubler',
    nodeType: 'tool',
    surfaces: ['agent_tool', 'api']
  }]
  manifest.defaultExposureId = 'double'
  manifest.permissions.push({ kind: 'shell', required: true })
  source.runtime = { isolation: 'shell' }
  source.tools = [{
    nodeId: 'doubler',
    name: 'double_value',
    description: 'Double one number.',
    runtime: 'node',
    code: 'async function run(input) { return { doubled: input.value * 2 } }',
    inputSchema: graph.nodes.at(-1)!.data.inputSchema,
    outputSchema: graph.nodes.at(-1)!.data.outputSchema
  }]
  const adapter: HostedRuntimeAdapter = {
    profile: {
      name: 'test.trusted-runtime.v1',
      nodeTypes: ['tool'],
      toolRuntimes: ['node'],
      permissions: ['shell'],
      isolation: ['shell'],
      capabilityIds: [],
      runNodeTypes: ['tool']
    },
    async executeShell() {
      throw new Error('Shell execution was not expected.')
    },
    async executeTool(params) {
      assert.deepEqual(params.args, { value: 7 })
      return {
        success: true,
        runtime: 'node',
        stdout: '',
        stderr: '',
        exitCode: 0,
        timedOut: false,
        durationMs: 1,
        result: { doubled: 14 }
      }
    }
  }
  const inspected = inspectHostedRelease(source, undefined, adapter.profile)
  const exposure = inspected.release.manifest.exposures[0]!
  assert.throws(
    () => validateHostedExposureInvocation(inspected.release, exposure, { value: '7' }),
    (error) => error instanceof HostedRuntimeError && error.code === 'invalid_run_input'
  )

  const result = await executeHostedRelease({
    runId: 'run_direct_tool_exposure',
    release: inspected.release,
    runtimeAdapter: adapter,
    input: { value: 7 },
    complete: async () => {
      throw new Error('A direct Tool exposure must not invoke a model.')
    }
  })

  assert.equal(result.status, 'completed')
  assert.deepEqual(result.result, { doubled: 14 })
  assert.equal(result.model, '')
})

test('recursive Agent cycles are rejected without invoking a second model recursively', async () => {
  const release = inspectHostedRelease(multiAgentPackage()).release
  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_cycle',
    release,
    input: { request: 'cycles' },
    complete: async (request) => {
      call += 1
      if (call === 1) return toolCall('self-call', 'use', {
        node_id: 'writer', capability: 'agent.run', input: { task: 'Call myself.' }
      })
      const cycleResult = request.messages.find((message) => message.role === 'tool' && message.name === 'use')
      assert.match(String(cycleResult?.content), /Recursive Agent cycle rejected/)
      return toolCall('blocked', 'complete', {
        status: 'blocked',
        summary: 'A recursive cycle was rejected.',
        blocker: 'recursive_dependency'
      })
    }
  })
  assert.equal(call, 2)
  assert.equal(result.status, 'blocked')
  assert.equal(result.result, undefined)
})

test('plain model text continues without an injected completion-reminder request', async () => {
  const release = inspectHostedRelease(multiAgentPackage()).release
  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_terminal_contract',
    release,
    input: { request: 'termination' },
    complete: async (request) => {
      call += 1
      assert.equal(request.toolChoice, 'auto')
      if (call === 1) {
        return { content: 'Here is an answer without a terminal tool call.', finish_reason: 'stop' }
      }
      assert.deepEqual(request.messages.at(-1), {
        role: 'assistant',
        content: 'Here is an answer without a terminal tool call.'
      })
      assert.equal(
        request.messages.some((message) =>
          message.role === 'user'
          && String(message.content).includes('has not terminated the Agent run')),
        false
      )
      return toolCall('terminal-complete', 'complete', {
        status: 'done',
        summary: 'Termination contract followed.'
      })
    }
  })
  assert.equal(call, 2)
  assert.equal(result.status, 'completed')
  assert.equal(result.result, undefined)
})

test('updated Canvas Tool code and names execute from the live workspace in the same Agent run', async () => {
  const source = multiAgentPackage()
  const graph = source.graph as {
    nodes: Array<{ id: string; type: string; data: Record<string, unknown> }>
  }
  graph.nodes.find((node) => node.id === 'writer')!.data.toolNames = [
    'edit',
    'use',
    'complete'
  ]
  graph.nodes.push({
    id: 'mutable-tool',
    type: 'tool',
    data: {
      label: 'Mutable Tool',
      toolName: 'old_tool_name',
      runtime: 'node',
      exposeAsTool: true,
      code: 'old tool code',
      inputSchema: { type: 'object', additionalProperties: true },
      outputSchema: { type: 'object', additionalProperties: true }
    }
  })
  const manifest = source.manifest as { permissions: Array<Record<string, unknown>> }
  manifest.permissions.push({ kind: 'shell', required: true })
  source.runtime = { isolation: 'shell' }
  source.tools = [{
    name: 'old_tool_name',
    runtime: 'node',
    code: 'old tool code',
    exposeAsTool: true,
    nodeId: 'mutable-tool'
  }]

  const executions: Array<{ name: string; code: string | undefined }> = []
  const adapter: HostedRuntimeAdapter = {
    profile: {
      name: 'test.trusted-runtime.v1',
      nodeTypes: ['tool'],
      toolRuntimes: ['node'],
      permissions: ['shell'],
      isolation: ['shell'],
      capabilityIds: [],
      runNodeTypes: ['tool']
    },
    async executeShell() {
      throw new Error('Shell execution was not expected.')
    },
    async executeTool(params) {
      executions.push({ name: params.definition.name, code: params.definition.code })
      return {
        success: true,
        runtime: 'node',
        stdout: '',
        stderr: '',
        exitCode: 0,
        timedOut: false,
        durationMs: 1,
        result: { name: params.definition.name, code: params.definition.code }
      }
    }
  }

  let call = 0
  const result = await executeHostedRelease({
    runId: 'run_live_tool_update',
    release: inspectHostedRelease(source, undefined, adapter.profile).release,
    runtimeAdapter: adapter,
    input: { request: 'live Tool updates' },
    complete: async (request) => {
      call += 1
      if (call === 1) {
        return toolCall('update-code', 'edit', {
          operations: [{ kind: 'patch', node_id: 'mutable-tool', set: { code: 'updated direct-call code' } }]
        })
      }
      if (call === 2) return toolCall('direct-call', 'use', {
        node_id: 'mutable-tool', capability: 'tool.invoke', input: {}
      })
      if (call === 3) {
        const direct = request.messages.filter((message) => message.role === 'tool' && message.name === 'use').at(-1)
        assert.match(String(direct?.content), /updated direct-call code/)
        return toolCall('rename-tool', 'edit', {
          operations: [{
            kind: 'patch',
            node_id: 'mutable-tool',
            set: { toolName: 'new_tool_name', code: 'updated run-node code' }
          }]
        })
      }
      if (call === 4) {
        const available = request.tools?.map((tool) => tool.function.name) ?? []
        assert.equal(available.includes('old_tool_name'), false)
        assert.equal(available.includes('new_tool_name'), false)
        assert.doesNotMatch(String(request.messages[0]?.content), /new_tool_name/)
        assert.doesNotMatch(String(request.messages[0]?.content), /old_tool_name/)
        const rename = request.messages
          .filter((message) => message.role === 'tool' && message.name === 'edit')
          .at(-1)
        assert.deepEqual(JSON.parse(String(rename?.content)), { success: true, edited: 1 })
        return toolCall('direct-renamed-call', 'use', {
          node_id: 'mutable-tool', capability: 'tool.invoke', input: {}
        })
      }
      const renamedCall = request.messages.filter((message) => message.role === 'tool' && message.name === 'use').at(-1)
      assert.match(String(renamedCall?.content), /new_tool_name/)
      assert.match(String(renamedCall?.content), /updated run-node code/)
      return toolCall('live-tool-complete', 'complete', {
        status: 'blocked',
        summary: 'Live Tool updates executed.',
        blocker: 'test_complete'
      })
    }
  })

  assert.equal(call, 5)
  assert.equal(result.status, 'blocked')
  assert.deepEqual(executions, [
    { name: 'old_tool_name', code: 'updated direct-call code' },
    { name: 'new_tool_name', code: 'updated run-node code' }
  ])
})
test('Hosted maps shared max-iteration exhaustion to blocked with the canonical gap', async () => {
  const release = inspectHostedRelease(multiAgentPackage()).release
  let writerData: Record<string, unknown> | undefined
  const result = await executeHostedRelease({
    runId: 'run_max_iterations',
    release,
    input: { request: 'budget exhaustion' },
    maxAgentIterations: 1,
    complete: async (request) => {
      assert.equal(request.toolChoice, 'auto')
      return toolCall('list-before-budget', 'find', {})
    },
    onWorkspaceChanged: (workspace) => {
      writerData = workspace.nodes.find((node) => node.id === 'writer')?.data
    }
  })

  assert.equal(result.status, 'blocked')
  assert.deepEqual(writerData?.completionGaps, ['max_iterations'])
  assert.match(result.summary, /iteration limit/)
})

test('an already-aborted signal stops execution before calling the model', async () => {
  const release: HarnessReleaseArtifact = inspectHostedRelease(multiAgentPackage()).release
  const controller = new AbortController()
  controller.abort()
  let called = false
  await assert.rejects(
    executeHostedRelease({
      runId: 'run_cancelled',
      release,
      input: { request: 'cancellation' },
      signal: controller.signal,
      complete: async () => {
        called = true
        return { content: 'should not run', finish_reason: 'stop' }
      }
    }),
    (error) => error instanceof HostedRuntimeError && error.code === 'run_cancelled'
  )
  assert.equal(called, false)
})

test('runtime.timeoutMs aborts the whole Harness with a distinct timeout error', async () => {
  const source = multiAgentPackage()
  source.runtime = { isolation: 'none', timeoutMs: 20 }
  const release = inspectHostedRelease(source).release
  await assert.rejects(
    executeHostedRelease({
      runId: 'run_timeout',
      release,
      input: { request: 'timeout' },
      complete: (request) => new Promise<AgentLLMResult>((_resolve, reject) => {
        request.signal.addEventListener('abort', () => reject(request.signal.reason), { once: true })
      })
    }),
    (error) => error instanceof HostedRuntimeError && error.code === 'run_timed_out'
  )
})

test('request_user_input is registered before its event is published so an immediate answer cannot race it', async () => {
  const release = inspectHostedRelease(interactivePackage()).release
  const controller = new AgentRunController()
  let modelCalls = 0
  let interactionId = ''
  const result = await executeHostedRelease({
    runId: 'run_immediate_answer',
    release,
    input: { request: 'interaction ordering' },
    controller,
    events: {
      emit: (event) => {
        if (event.type !== 'agent.interaction_requested') return
        interactionId = String(event.payload.interactionId)
        assert.equal(controller.resolveInteraction(interactionId, 'Proceed'), true)
      }
    },
    complete: async (request) => {
      modelCalls += 1
      if (modelCalls === 1) return toolCall('ask-now', 'request_user_input', { prompt: 'Proceed?' })
      const answer = request.messages.find((message) => message.tool_call_id === 'ask-now')
      assert.deepEqual(JSON.parse(String(answer?.content)), { success: true, value: 'Proceed' })
      return toolCall('complete-after-immediate-answer', 'complete', {
        status: 'done',
        summary: 'Immediate answer accepted.',
      })
    }
  })

  assert.equal(result.status, 'completed')
  assert.match(interactionId, /^interaction_/)
  assert.equal(modelCalls, 2)
})

test('request_user_input settles on both the effective timeout and an external cancellation signal', async () => {
  const timedSource = interactivePackage()
  timedSource.runtime = { isolation: 'none', timeoutMs: 20 }
  const timedRelease = inspectHostedRelease(timedSource).release
  await assert.rejects(
    executeHostedRelease({
      runId: 'run_ask_timeout',
      release: timedRelease,
      input: { request: 'timeout interaction' },
      controller: new AgentRunController(),
      complete: async () => toolCall('ask-unanswered', 'request_user_input', { prompt: 'This will time out?' })
    }),
    (error) => error instanceof HostedRuntimeError && error.code === 'run_timed_out'
  )

  const cancellation = new AbortController()
  const controller = new AgentRunController()
  await assert.rejects(
    executeHostedRelease({
      runId: 'run_ask_cancelled',
      release: inspectHostedRelease(interactivePackage()).release,
      input: { request: 'cancel interaction' },
      signal: cancellation.signal,
      controller,
      events: {
        emit: (event) => {
          if (event.type === 'agent.interaction_requested') cancellation.abort()
        }
      },
      complete: async () => toolCall('ask-before-cancel', 'request_user_input', { prompt: 'This will be cancelled?' })
    }),
    (error) => error instanceof HostedRuntimeError && error.code === 'run_cancelled'
  )
  assert.deepEqual(controller.pendingInteractions, [])
})

test('nested Agents share interactive delivery with unique interaction ids', async () => {
  const release = inspectHostedRelease(interactivePackage()).release
  const controller = new AgentRunController()
  const jobs = new RuntimeJobRegistry()
  const controlSession = new AgentHostRuntimeSession({
    sessionId: 'run_nested_interaction',
    jobs,
    onIdle: () => undefined
  })
  let nestedController: AgentRunController | undefined
  const interactionIds: string[] = []
  let writerCalls = 0
  let researcherCalls = 0

  const result = await executeHostedRelease({
    runId: 'run_nested_interaction',
    release,
    input: { request: 'nested interaction' },
    controller,
    controlSession,
    events: {
      emit: (event) => {
        if (event.type !== 'agent.interaction_requested') return
        assert.equal(event.payload.nodeId, 'researcher')
        nestedController = controlSession.currentJobForOwner('researcher')?.controller
        const interactionId = String(event.payload.interactionId)
        interactionIds.push(interactionId)
        assert.notEqual(nestedController, controller)
        assert.equal(nestedController?.resolveInteraction(interactionId, `answer-${interactionIds.length}`), true)
      }
    },
    complete: async (request) => {
      if (request.model === 'test/researcher') {
        researcherCalls += 1
        if (researcherCalls === 1) return toolCall('nested-ask-1', 'request_user_input', { prompt: 'First nested question?' })
        if (researcherCalls === 2) {
          assert.deepEqual(
            JSON.parse(String(request.messages.find((message) => message.tool_call_id === 'nested-ask-1')?.content)),
            { success: true, value: 'answer-1' }
          )
          return toolCall('nested-ask-2', 'request_user_input', { prompt: 'Second nested question?' })
        }
        assert.deepEqual(
          JSON.parse(String(request.messages.find((message) => message.tool_call_id === 'nested-ask-2')?.content)),
          { success: true, value: 'answer-2' }
        )
        return toolCall('nested-complete', 'complete', {
          status: 'done',
          summary: 'Nested answers received.',
        })
      }
      writerCalls += 1
      if (writerCalls === 1) {
        return toolCall('delegate-interactive', 'use', {
          node_id: 'researcher',
          capability: 'agent.run',
          input: { task: 'Collect both answers.' }
        })
      }
      if (writerCalls === 2) {
        return toolCall('wait-interactive', 'use', {
          node_id: 'researcher',
          capability: 'run.wait',
          input: {}
        })
      }
      return toolCall('root-complete-after-nested', 'complete', {
        status: 'done',
        summary: 'Nested interaction completed.',
      })
    }
  })

  assert.equal(result.status, 'completed')
  assert.equal(researcherCalls, 3)
  assert.equal(writerCalls, 3)
  assert.equal(interactionIds.length, 2)
  assert.notEqual(interactionIds[0], interactionIds[1])
  jobs.dispose()
})
