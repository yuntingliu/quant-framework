import assert from 'node:assert/strict'
import test from 'node:test'
import { nodeTypeFieldsContract } from '../canvas/node-catalog.js'
import {
  applyPortableNodeDataUpdate,
  applyPortableToolNameRenames,
  buildPortableNodeObservation,
  createPortableListNodesResult,
  createPortableObservationEnvelope,
  createPortableObservationSuccess,
  createPortableUpdateSuccessEnvelope,
  parsePortableObservationArgs,
  parsePortableNodeUpdates,
  portableNodeNotFoundIssue,
  portableObservationResultFailure,
  portableToolNameRename,
  redactPortableGraphData,
  validatePortableNodeUpdateProtection,
  type PortableNodeObservation
} from './portable-graph-tools.js'

test('Tool renames migrate selected Agent tool names only inside the same Canvas scope', () => {
  const tool = {
    id: 'tool-1',
    type: 'tool',
    parentId: 'harness-1',
    data: { toolName: 'old_name' }
  }
  const rename = portableToolNameRename(tool, { toolName: 'new_name' })
  assert.ok(rename)
  const result = applyPortableToolNameRenames([
    { ...tool, data: { toolName: 'new_name' } },
    {
      id: 'agent-1',
      type: 'agent',
      parentId: 'harness-1',
      data: { toolNames: ['complete', 'old_name'] }
    },
    {
      id: 'agent-2',
      type: 'agent',
      parentId: 'harness-2',
      data: { toolNames: ['complete', 'old_name'] }
    }
  ], [rename])
  assert.deepEqual(result.updatedAgentNodeIds, ['agent-1'])
  assert.deepEqual(result.nodes.find((node) => node.id === 'agent-1')?.data?.toolNames, ['complete', 'new_name'])
  assert.deepEqual(result.nodes.find((node) => node.id === 'agent-2')?.data?.toolNames, ['complete', 'old_name'])
})

test('node type contracts return one raw operation schema without runtime metadata', () => {
  const details = nodeTypeFieldsContract('agent', 'update')
  assert.equal(details.success, true)
  if (!details.success) return
  assert.deepEqual(Object.keys(details), ['success', 'fields_schema'])
  assert.deepEqual(Object.keys(details.fields_schema?.properties ?? {}), [
    'label',
    'description',
    'objective',
    'systemPrompt',
    'systemPromptPatch',
    'model',
    'toolNames',
    'maxTokens',
    'exposeInHarness'
  ])
  assert.deepEqual(details.fields_schema?.properties.systemPromptPatch, {
    type: 'object',
    properties: {
      find: { type: 'string', minLength: 1 },
      replace: { type: 'string' }
    },
    required: ['find', 'replace'],
    additionalProperties: false,
    description: 'Exact replacement patch for systemPrompt: { find, replace }. Use for small prompt edits instead of replacing the full prompt.'
  })
})

test('Document edits expose one discriminated contentPatch contract only during edits', () => {
  const create = nodeTypeFieldsContract('note', 'create')
  const update = nodeTypeFieldsContract('note', 'update')
  assert.equal(create.success, true)
  assert.equal(update.success, true)
  if (!create.success || !update.success) return
  assert.deepEqual(Object.keys(create.fields_schema?.properties ?? {}), ['content'])
  assert.deepEqual(update.fields_schema?.properties.contentPatch, {
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
  })
  assert.equal(Object.prototype.hasOwnProperty.call(update.fields_schema?.properties ?? {}, 'contentAppend'), false)
})

test('node type contracts report unsupported edits without exposing unrelated fields', () => {
  assert.deepEqual(nodeTypeFieldsContract('browser', 'update'), {
    success: false,
    error: 'browser nodes do not support update.'
  })
  assert.deepEqual(nodeTypeFieldsContract('browser', 'create'), {
    success: true,
    fields_schema: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'Initial or current browser URL.' },
        browserProfileChannel: {
          type: 'string',
          enum: ['conexus', 'user'],
          description: 'Browser channel. Use conexus for the persistent Conexus browser profile; use user for the main signed-in Chrome/Edge profile.'
        }
      },
      additionalProperties: false
    }
  })
})

test('portable observation parsing applies one deep ABI with partial item errors', () => {
  const parsed = parsePortableObservationArgs({
    requests: [
      { node_id: ' note-1 ', detail: 'values', fields: [' content ', 'content'], start_line: 2, end_line: 3 },
      { node_id: 'note-2', detail: 'verbose' },
      { node_id: 'note-3', fields: ['ok'], legacy: true }
    ]
  })
  assert.equal(parsed.ok, true)
  if (!parsed.ok) return
  assert.deepEqual(parsed.value.entries[0], {
    ok: true,
    requestIndex: 0,
    request: {
      nodeId: 'note-1',
      detail: 'values',
      fields: ['content'],
      includeContracts: false,
      startLine: 2,
      endLine: 3
    }
  })
  assert.equal(parsed.value.entries[1]?.ok, false)
  assert.match(parsed.value.entries[1]?.ok === false ? parsed.value.entries[1].result.error : '', /values or full/)
  assert.equal(parsed.value.entries[1]?.ok === false ? parsed.value.entries[1].result.at : '', 'requests[1].detail')
  assert.match(parsed.value.entries[2]?.ok === false ? parsed.value.entries[2].result.error : '', /unsupported fields/)

  const removedShape = parsePortableObservationArgs({ node_ids: ['note-1'] })
  assert.equal(removedShape.ok, false)
  assert.equal(removedShape.ok ? '' : removedShape.result.at, 'node_ids')
})

test('portable observations share selection, line ranges, and recursive protection', () => {
  const built = buildPortableNodeObservation({
    id: 'custom-1',
    type: 'custom',
    data: {
      label: 'Worker',
      description: 'Portable worker'
    }
  }, {
    nodeId: 'custom-1',
    detail: 'full',
    fields: [],
    startLine: 2,
    endLine: 3
  }, {
    values: {
      content: 'one\ntwo\nthree\nfour',
      nested: { apiKey: 'secret', safe: true },
      endpoint: 'https://user:password@example.com/path'
    }
  })
  assert.equal(built.ok, true)
  if (!built.ok) return
  assert.deepEqual(built.observation, {
    node_id: 'custom-1',
    type: 'custom',
    values: {
      content: 'two\nthree',
      nested: { safe: true },
      endpoint: ''
    },
    line_ranges: {
      content: {
        start_line: 2,
        end_line: 3,
        total_lines: 4
      },
      endpoint: {
        start_line: 2,
        end_line: 1,
        total_lines: 1
      }
    }
  })

  const explicitAgentConfiguration = buildPortableNodeObservation({
    id: 'agent-1', type: 'agent', data: { systemPrompt: 'Inspect only when requested.' }
  }, { nodeId: 'agent-1', detail: 'values', fields: ['systemPrompt'] })
  assert.equal(explicitAgentConfiguration.ok, true)
  if (!explicitAgentConfiguration.ok) return
  assert.deepEqual(explicitAgentConfiguration.observation.values, {
    systemPrompt: 'Inspect only when requested.'
  })
})

test('CLI observations can expose host runtime values without placing them in the system prompt', () => {
  const built = buildPortableNodeObservation({
    id: 'cli-1',
    type: 'cli',
    data: { label: 'Terminal' }
  }, {
    nodeId: 'cli-1',
    detail: 'values',
    fields: []
  }, {
    values: {
      shell: 'powershell',
      cwd: 'E:\\workspace',
      platform: 'Windows',
      timeoutMs: 120_000,
      commandSyntax: 'Use PowerShell syntax.'
    }
  })

  assert.equal(built.ok, true)
  if (!built.ok) return
  assert.deepEqual(built.observation.values, {
    shell: 'powershell',
    cwd: 'E:\\workspace',
    platform: 'Windows',
    timeoutMs: 120_000,
    commandSyntax: 'Use PowerShell syntax.'
  })
})

test('portable observations expose concise type-specific values instead of raw persistence metadata', () => {
  const harness = buildPortableNodeObservation({
    id: 'harness-1',
    type: 'harness',
    data: {
      label: 'Production Harness',
      description: 'Produces reports.',
      summary: 'Current workflow summary.',
      purpose: 'primary',
      template: { name: 'Production Harness' },
      defaultExposureId: 'report',
      backingPath: 'workspace/harnesses/internal/harness.json',
      createdByAgentNodeId: 'agent-owner',
      publicationSlug: 'legacy-publication',
      publicationVersion: 3,
      hostingSlug: 'production-harness',
      hostingReleaseChecksum: 'checksum',
      invocations: [{ hostingRevision: 1 }, { hostingRevision: 1 }]
    }
  }, {
    nodeId: 'harness-1', detail: 'full', fields: []
  })
  assert.equal(harness.ok, true)
  if (!harness.ok) return
  assert.deepEqual(harness.observation.values, {
    summary: 'Current workflow summary.',
    purpose: 'primary',
    defaultExposureId: 'report',
    template: { name: 'Production Harness' }
  })

  const internalField = buildPortableNodeObservation({
    id: 'harness-1',
    type: 'harness',
    data: { publicationSlug: 'legacy-publication' }
  }, {
    nodeId: 'harness-1', detail: 'values', fields: ['publicationSlug']
  })
  assert.equal(internalField.ok, false)
  assert.equal(internalField.ok ? '' : internalField.issue.code, 'unknown_field')
})

test('portable Agent full detail returns every safe field without raw messages or session internals', () => {
  const full = buildPortableNodeObservation({
    id: 'agent-1',
    type: 'agent',
    data: {
      label: 'Report Agent',
      objective: 'Produce a report.',
      model: 'openai/gpt-5',
      toolNames: ['observe_nodes', 'complete'],
      maxTokens: 8_192,
      exposeInHarness: true,
      systemPrompt: 'Private node instructions.',
      historySessionId: 'session-internal',
      backingPath: 'workspace/agents/internal.json',
      status: 'running',
      summary: 'Drafting the report.',
      completionGaps: ['Verify the output node.'],
      toolCounts: { run_node: 2 },
      lastError: '',
      messages: [
        { role: 'user', content: 'Create the report.' },
        { role: 'assistant', content: 'The report draft is ready.' }
      ]
    }
  }, {
    nodeId: 'agent-1', detail: 'full', fields: []
  })
  assert.equal(full.ok, true)
  if (!full.ok) return
  assert.deepEqual(full.observation.values, {
    objective: 'Produce a report.',
    systemPrompt: 'Private node instructions.',
    model: 'openai/gpt-5',
    toolNames: ['observe_nodes', 'complete'],
    maxTokens: 8_192,
    exposeInHarness: true,
    status: 'running',
    summary: 'Drafting the report.',
    completionGaps: ['Verify the output node.'],
    lastMessage: 'The report draft is ready.',
    toolCounts: { run_node: 2 }
  })

  const values = buildPortableNodeObservation({
    id: 'agent-1',
    type: 'agent',
    data: {
      objective: 'Produce a report.',
      model: 'openai/gpt-5',
      toolNames: ['complete'],
      maxTokens: 4_096,
      exposeInHarness: true,
      status: 'running',
      summary: 'Drafting the report.'
    }
  }, {
    nodeId: 'agent-1', detail: 'values', fields: []
  })
  assert.equal(values.ok, true)
  if (!values.ok) return
  assert.deepEqual(values.observation.values, {
    objective: 'Produce a report.',
    model: 'openai/gpt-5',
    toolNames: ['complete'],
    maxTokens: 4_096,
    exposeInHarness: true
  })

  const longMessage = 'x'.repeat(5_000)
  const unbounded = buildPortableNodeObservation({
    id: 'agent-2',
    type: 'agent',
    data: {
      messages: [{ role: 'assistant', content: longMessage }]
    }
  }, {
    nodeId: 'agent-2', detail: 'full', fields: []
  })
  assert.equal(unbounded.ok, true)
  if (!unbounded.ok) return
  assert.equal(unbounded.observation.values?.lastMessage, longMessage)
  assert.equal('lastMessageTruncated' in (unbounded.observation.values ?? {}), false)
})

test('portable Tool full observations omit duplicate results and timing telemetry', () => {
  const node = {
    id: 'tool-1',
    type: 'tool',
    data: {
      label: 'Report Tool',
      toolName: 'build_report',
      runtime: 'python',
      exposeAsTool: true,
      inputSchema: { type: 'object', properties: { query: { type: 'string' } } },
      outputSchema: { type: 'object', properties: { markdown: { type: 'string' } } },
      code: 'def run(input, context):\n    return {"markdown": "large"}',
      output: { markdown: 'large report body' },
      status: 'ready',
      lastArgs: { query: 'test' },
      lastDurationMs: 42,
      lastResult: { markdown: 'large report body' },
      lastStdout: 'large stdout',
      lastStderr: ''
    }
  }
  const full = buildPortableNodeObservation(node, {
    nodeId: 'tool-1', detail: 'full', fields: []
  })
  assert.equal(full.ok, true)
  if (!full.ok) return
  assert.deepEqual(full.observation.values, {
    toolName: 'build_report',
    runtime: 'python',
    exposeAsTool: true,
    inputSchema: { type: 'object', properties: { query: { type: 'string' } } },
    outputSchema: { type: 'object', properties: { markdown: { type: 'string' } } },
    output: { markdown: 'large report body' },
    code: 'def run(input, context):\n    return {"markdown": "large"}',
    status: 'ready',
    lastArgs: { query: 'test' },
    lastStdout: 'large stdout'
  })

  const selected = buildPortableNodeObservation(node, {
    nodeId: 'tool-1', detail: 'values', fields: ['code', 'output']
  })
  assert.equal(selected.ok, true)
  if (!selected.ok) return
  assert.deepEqual(selected.observation.values, {
    code: 'def run(input, context):\n    return {"markdown": "large"}',
    output: { markdown: 'large report body' }
  })
})

test('portable App and CLI observations omit stored interaction and command histories', () => {
  const app = {
    id: 'app-1',
    type: 'app',
    data: {
      label: 'Dashboard',
      code: 'export default function App() { return null }',
      props: { title: 'Dashboard' },
      lastSubmission: { payload: 'latest submission' },
      submissions: [{ payload: 'large submission history' }],
      lastSdkResult: { payload: 'large SDK result' },
      lastError: 'Preview failed.'
    }
  }
  const appFull = buildPortableNodeObservation(app, {
    nodeId: 'app-1', detail: 'full', fields: []
  })
  assert.equal(appFull.ok, true)
  if (!appFull.ok) return
  assert.deepEqual(appFull.observation.values, {
    code: 'export default function App() { return null }',
    props: { title: 'Dashboard' },
    lastSubmission: { payload: 'latest submission' },
    lastSdkResult: { payload: 'large SDK result' },
    lastError: 'Preview failed.'
  })
  const appSelected = buildPortableNodeObservation(app, {
    nodeId: 'app-1', detail: 'values', fields: ['code', 'lastSubmission', 'lastSdkResult']
  })
  assert.equal(appSelected.ok, true)
  if (!appSelected.ok) return
  assert.deepEqual(appSelected.observation.values, {
    code: 'export default function App() { return null }',
    lastSubmission: { payload: 'latest submission' },
    lastSdkResult: { payload: 'large SDK result' }
  })

  const cli = {
    id: 'cli-1',
    type: 'cli',
    data: {
      shell: 'powershell',
      cwd: 'E:\\Conexus',
      status: 'ready',
      history: [{ command: 'build', stdout: 'large output' }]
    }
  }
  const cliFull = buildPortableNodeObservation(cli, {
    nodeId: 'cli-1', detail: 'full', fields: []
  })
  assert.equal(cliFull.ok, true)
  if (!cliFull.ok) return
  assert.deepEqual(cliFull.observation.values, {
    shell: 'powershell',
    cwd: 'E:\\Conexus',
    status: 'ready'
  })
  const cliSelected = buildPortableNodeObservation(cli, {
    nodeId: 'cli-1', detail: 'values', fields: ['history']
  })
  assert.equal(cliSelected.ok, false)
})

test('portable update parsing is ordered, atomic, capability-scoped, and rejects old shapes', () => {
  const unsupported = parsePortableNodeUpdates({
    updates: [{ node_id: 'note-1', data: { content: 'legacy' }, placement: { type: 'root' } }]
  })
  assert.equal(unsupported.ok, false)
  if (!unsupported.ok) {
    assert.match(unsupported.result.errors[0]?.error ?? '', /unsupported fields/)
    assert.equal(unsupported.result.errors[0]?.at, 'updates[0].data')
  }

  const desktop = parsePortableNodeUpdates({
    updates: [
      {
        node_id: ' note-1 ',
        set: { contentPatch: { operation: 'replace', find: 'old', replace: 'new' } },
        placement: { type: 'harness', harness_node_id: 'flow-1' }
      }
    ]
  }, { updateNodePlacement: true })
  assert.equal(desktop.ok, true)
  if (!desktop.ok) return
  assert.deepEqual(desktop.value.updates.map((update) => ({
    nodeId: update.nodeId,
    placement: update.placement
  })), [
    { nodeId: 'note-1', placement: { type: 'harness', harnessNodeId: 'flow-1' } }
  ])

  const removedHistoryMutation = parsePortableNodeUpdates({
    updates: [{ node_id: 'agent-2', clear_history: true }]
  }, { updateNodePlacement: true })
  assert.equal(removedHistoryMutation.ok, false)
  assert.equal(removedHistoryMutation.ok ? '' : removedHistoryMutation.result.errors[0]?.at, 'updates[0].clear_history')

  const repeated = parsePortableNodeUpdates({
    updates: [
      { node_id: 'same', set: { contentPatch: { operation: 'replace', find: 'old', replace: 'middle' } } },
      { node_id: 'same', set: { contentPatch: { operation: 'replace', find: 'middle', replace: 'new' } } }
    ]
  })
  assert.equal(repeated.ok, true)
  if (!repeated.ok) return
  assert.deepEqual(repeated.value.updates.map((update) => ({
    updateIndex: update.updateIndex,
    nodeId: update.nodeId
  })), [
    { updateIndex: 0, nodeId: 'same' },
    { updateIndex: 1, nodeId: 'same' }
  ])

  let staged = { id: 'same', type: 'note', data: { content: 'old value' } }
  for (const update of repeated.value.updates) {
    const result = applyPortableNodeDataUpdate(staged, update)
    assert.equal(result.ok, true)
    if (!result.ok) return
    staged = { ...staged, data: { ...staged.data, ...result.data } }
  }
  assert.equal(staged.data.content, 'new value')
})

test('portable update protection is recursive and identical for all hosts', () => {
  const parsed = parsePortableNodeUpdates({
    updates: [{
      node_id: 'agent-1',
      set: {
        safe: { authorization: 'Bearer secret' },
        endpoint: 'https://user:password@example.com',
        status: 'done'
      }
    }]
  })
  assert.equal(parsed.ok, true)
  if (!parsed.ok) return
  const issue = validatePortableNodeUpdateProtection(parsed.value.updates[0]!, 'agent')
  assert.equal(issue?.code, 'protected_field')
  assert.deepEqual(issue?.fields, [
    'updates[0].set.safe.authorization',
    'updates[0].set.endpoint',
    'updates[0].set.status'
  ])

  const nestedRuntimeOutput = parsePortableNodeUpdates({
    updates: [{
      node_id: 'agent-1',
      set: { output: { summary: 'Deliverable summary', status: 'ready' } }
    }]
  })
  assert.equal(nestedRuntimeOutput.ok, true)
  if (!nestedRuntimeOutput.ok) return
  const nestedRuntimeIssue = validatePortableNodeUpdateProtection(nestedRuntimeOutput.value.updates[0]!, 'agent')
  assert.equal(nestedRuntimeIssue?.code, 'protected_field')
  assert.deepEqual(nestedRuntimeIssue?.fields, [
    'updates[0].set.output.summary',
    'updates[0].set.output.status'
  ])

  assert.deepEqual(redactPortableGraphData({
    safe: 1,
    nested: { password: 'hidden', keep: true },
    _runtime: 'hidden'
  }), { safe: 1, nested: { keep: true } })
})

test('portable data updates apply identical patches before either host commits them', () => {
  const parsed = parsePortableNodeUpdates({
    updates: [{
      node_id: 'note-1',
      set: { label: ' Updated ', contentPatch: { operation: 'replace', find: 'old', replace: 'new' } }
    }]
  })
  assert.equal(parsed.ok, true)
  if (!parsed.ok) return
  assert.deepEqual(applyPortableNodeDataUpdate({
    id: 'note-1', type: 'note', data: { content: 'old value' }
  }, parsed.value.updates[0]!), {
    ok: true,
    data: { content: 'new value', label: 'Updated' }
  })

  const append = parsePortableNodeUpdates({
    updates: [{ node_id: 'note-1', set: { contentPatch: { operation: 'append', text: '\nsecond chunk' } } }]
  })
  assert.equal(append.ok, true)
  if (!append.ok) return
  assert.deepEqual(applyPortableNodeDataUpdate({
    id: 'note-1', type: 'note', data: { content: 'first chunk' }
  }, append.value.updates[0]!), {
    ok: true,
    data: { content: 'first chunk\nsecond chunk' }
  })

  const conflictingAppend = parsePortableNodeUpdates({
    updates: [{ node_id: 'note-1', set: { content: 'replace', contentPatch: { operation: 'append', text: 'append' } } }]
  })
  assert.equal(conflictingAppend.ok, true)
  if (!conflictingAppend.ok) return
  const conflictingResult = applyPortableNodeDataUpdate({
    id: 'note-1', type: 'note', data: { content: 'first chunk' }
  }, conflictingAppend.value.updates[0]!)
  assert.equal(conflictingResult.ok, false)
  if (!conflictingResult.ok) assert.match(conflictingResult.issue.error, /cannot be used with content/)

  const obsoleteAppend = parsePortableNodeUpdates({
    updates: [{ node_id: 'note-1', set: { contentAppend: 'obsolete' } }]
  })
  assert.equal(obsoleteAppend.ok, true)
  if (!obsoleteAppend.ok) return
  const obsoleteAppendResult = applyPortableNodeDataUpdate(
    { id: 'note-1', type: 'note', data: { content: 'unchanged' } },
    obsoleteAppend.value.updates[0]!
  )
  assert.equal(obsoleteAppendResult.ok, false)
  if (!obsoleteAppendResult.ok) assert.equal(obsoleteAppendResult.issue.code, 'unsupported_field')

  const obsoletePatch = parsePortableNodeUpdates({
    updates: [{ node_id: 'note-1', set: { contentPatch: { find: 'unchanged', replace: 'legacy' } } }]
  })
  assert.equal(obsoletePatch.ok, true)
  if (!obsoletePatch.ok) return
  const obsoletePatchResult = applyPortableNodeDataUpdate(
    { id: 'note-1', type: 'note', data: { content: 'unchanged' } },
    obsoletePatch.value.updates[0]!
  )
  assert.equal(obsoletePatchResult.ok, false)
  if (!obsoletePatchResult.ok) assert.match(obsoletePatchResult.issue.error, /operation must be replace or append/)

  const unsupportedNoteProp = parsePortableNodeUpdates({
    updates: [{ node_id: 'note-1', set: { output: { ok: true } } }]
  })
  assert.equal(unsupportedNoteProp.ok, true)
  if (!unsupportedNoteProp.ok) return
  const unsupportedNoteResult = applyPortableNodeDataUpdate(
    { id: 'note-1', type: 'note', data: { content: 'unchanged' } },
    unsupportedNoteProp.value.updates[0]!
  )
  assert.equal(unsupportedNoteResult.ok, false)
  if (!unsupportedNoteResult.ok) {
    assert.equal(unsupportedNoteResult.issue.code, 'unsupported_field')
    assert.deepEqual(unsupportedNoteResult.issue.fields, ['updates[0].set.output'])
    assert.match(unsupportedNoteResult.issue.error, /Allowed fields: label, description, content, contentPatch/)
  }

  const agentRuntimePatch = parsePortableNodeUpdates({
    updates: [{ node_id: 'agent-1', set: { status: 'done' } }]
  })
  assert.equal(agentRuntimePatch.ok, true)
  if (!agentRuntimePatch.ok) return
  const rejected = applyPortableNodeDataUpdate(
    { id: 'agent-1', type: 'agent', data: {} },
    agentRuntimePatch.value.updates[0]!
  )
  assert.equal(rejected.ok, false)
  assert.equal(rejected.ok ? '' : rejected.issue.code, 'protected_field')

  const agentOutputPatch = parsePortableNodeUpdates({
    updates: [{ node_id: 'agent-1', set: { output: 'Obsolete Agent output.' } }]
  })
  assert.equal(agentOutputPatch.ok, true)
  if (!agentOutputPatch.ok) return
  const rejectedOutput = applyPortableNodeDataUpdate(
    { id: 'agent-1', type: 'agent', data: {} },
    agentOutputPatch.value.updates[0]!
  )
  assert.equal(rejectedOutput.ok, false)
  if (!rejectedOutput.ok) {
    assert.equal(rejectedOutput.issue.code, 'unsupported_field')
    assert.deepEqual(rejectedOutput.issue.fields, ['updates[0].set.output'])
  }

  const agentConfigurationPatch = parsePortableNodeUpdates({
    updates: [{
      node_id: 'agent-1',
      set: {
        objective: 'Updated objective',
        systemPrompt: 'Updated node instructions.',
        model: 'openai/gpt-5',
        toolNames: ['observe_nodes', 'complete'],
        maxTokens: 8_192,
        exposeInHarness: true
      }
    }]
  })
  assert.equal(agentConfigurationPatch.ok, true)
  if (!agentConfigurationPatch.ok) return
  assert.deepEqual(applyPortableNodeDataUpdate(
    { id: 'agent-1', type: 'agent', data: {} },
    agentConfigurationPatch.value.updates[0]!
  ), {
    ok: true,
    data: {
      objective: 'Updated objective',
      systemPrompt: 'Updated node instructions.',
      model: 'openai/gpt-5',
      toolNames: ['observe_nodes', 'complete'],
      maxTokens: 8_192,
      exposeInHarness: true
    }
  })

  const agentPromptPatch = parsePortableNodeUpdates({
    updates: [{
      node_id: 'agent-1',
      set: { systemPromptPatch: { find: 'old rule', replace: 'new rule' } }
    }]
  })
  assert.equal(agentPromptPatch.ok, true)
  if (!agentPromptPatch.ok) return
  assert.deepEqual(applyPortableNodeDataUpdate(
    { id: 'agent-1', type: 'agent', data: { systemPrompt: 'Keep the old rule here.' } },
    agentPromptPatch.value.updates[0]!
  ), {
    ok: true,
    data: { systemPrompt: 'Keep the new rule here.' }
  })

  const unknownAgentProp = parsePortableNodeUpdates({
    updates: [{ node_id: 'agent-1', set: { imaginaryPatch: { find: 'a', replace: 'b' } } }]
  })
  assert.equal(unknownAgentProp.ok, true)
  if (!unknownAgentProp.ok) return
  const unknownAgentResult = applyPortableNodeDataUpdate(
    { id: 'agent-1', type: 'agent', data: { systemPrompt: 'a' } },
    unknownAgentProp.value.updates[0]!
  )
  assert.equal(unknownAgentResult.ok, false)
  if (!unknownAgentResult.ok) {
    assert.equal(unknownAgentResult.issue.code, 'unsupported_field')
    assert.deepEqual(unknownAgentResult.issue.fields, ['updates[0].set.imaginaryPatch'])
    assert.match(unknownAgentResult.issue.error, /systemPromptPatch/)
  }

  const invalidAgentConfiguration = parsePortableNodeUpdates({
    updates: [{ node_id: 'agent-1', set: { maxTokens: 1.5 } }]
  })
  assert.equal(invalidAgentConfiguration.ok, true)
  if (!invalidAgentConfiguration.ok) return
  const invalidResult = applyPortableNodeDataUpdate(
    { id: 'agent-1', type: 'agent', data: {} },
    invalidAgentConfiguration.value.updates[0]!
  )
  assert.equal(invalidResult.ok, false)
  assert.equal(invalidResult.ok ? '' : invalidResult.issue.code, 'invalid_update')
})

test('portable list and batch envelopes keep the canonical cross-host result shape', () => {
  assert.deepEqual(createPortableListNodesResult([
    { id: 'note-1', type: 'note', parentId: 'flow', data: { label: 'Brief', summary: 'Summary' } }
  ], [
    {
      id: 'edge-1',
      source: 'note-1',
      target: 'agent-1',
      relation: 'The note provides context to the Agent.'
    }
  ]), {
    success: true,
    nodes: [{ id: 'note-1', type: 'note', label: 'Brief', description: 'Summary', parent_id: 'flow' }],
    edges: [{
      node_ids: ['note-1', 'agent-1'],
      relation: 'The note provides context to the Agent.'
    }]
  })
  const observation: PortableNodeObservation & {
    runtime: { host: string }
    recent_messages: unknown[]
    toolCounts: Record<string, number>
  } = {
    node_id: 'agent-1',
    type: 'agent',
    runtime: { host: 'desktop' },
    recent_messages: [{ role: 'user', content: 'protected' }],
    toolCounts: { run_node: 1 }
  }
  assert.deepEqual(createPortableObservationEnvelope([
    createPortableObservationSuccess(observation),
    portableObservationResultFailure(1, portableNodeNotFoundIssue('missing'), 'missing')
  ]), {
    success: false,
    results: [
      {
        node_id: 'agent-1',
        type: 'agent'
      },
      {
        node_id: 'missing',
        error: 'Node was not found: missing.',
        at: 'requests[1]'
      }
    ]
  })

  assert.deepEqual(createPortableUpdateSuccessEnvelope(), { success: true })
})

test('portable list scope is selected once in shared core', () => {
  const nodes = [
    { id: 'root-agent', type: 'agent' },
    { id: 'root-note', type: 'note' },
    { id: 'flow', type: 'harness' },
    { id: 'inner-agent', type: 'agent', parentId: 'flow' },
    { id: 'inner-note', type: 'note', data: { harnessNodeId: 'flow' } },
    { id: 'nested-flow', type: 'harness', parentId: 'flow' },
    { id: 'nested-note', type: 'note', parentId: 'nested-flow' },
    { id: 'other-flow', type: 'harness', parentId: 'other-scope' }
  ]
  const edges = [
    { id: 'root-edge', source: 'root-agent', target: 'root-note', relation: 'The Agent uses the note as context.' },
    { id: 'inner-edge', source: 'inner-agent', target: 'inner-note', relation: 'The Agent uses the note as context.' },
    { id: 'nested-edge', source: 'nested-flow', target: 'inner-note', relation: 'The nested Harness uses the note.' },
    { id: 'cross-edge', source: 'root-note', target: 'inner-note', relation: 'These notes are related.' }
  ]
  const result = createPortableListNodesResult(nodes, edges, { ownerNodeId: 'inner-agent' })

  assert.equal(result.success, true)
  if (!result.success) return
  assert.deepEqual(result.nodes.map((node) => node.id), ['inner-agent', 'inner-note', 'nested-flow'])
  assert.deepEqual(result.edges.map((edge) => edge.node_ids), [
    ['inner-agent', 'inner-note'],
    ['nested-flow', 'inner-note']
  ])

  const harnessResult = createPortableListNodesResult(nodes, edges, {
    ownerNodeId: 'root-agent',
    harnessNodeId: 'flow'
  })
  assert.equal(harnessResult.success, true)
  if (!harnessResult.success) return
  assert.deepEqual(harnessResult.nodes.map((node) => ({ id: node.id, parent_id: node.parent_id })), [
    { id: 'inner-agent', parent_id: 'flow' },
    { id: 'inner-note', parent_id: 'flow' },
    { id: 'nested-flow', parent_id: 'flow' }
  ])
  assert.equal(harnessResult.edges.length, 2)
  assert.equal(harnessResult.nodes.some((node) => node.id === 'nested-note'), false)

  const ownHarnessResult = createPortableListNodesResult(nodes, edges, {
    ownerNodeId: 'inner-agent',
    harnessNodeId: 'flow'
  })
  assert.equal(ownHarnessResult.success, true)

  const inaccessible = createPortableListNodesResult(nodes, edges, {
    ownerNodeId: 'inner-agent',
    harnessNodeId: 'other-flow'
  })
  assert.equal(inaccessible.success, false)
  assert.match(inaccessible.success ? '' : inaccessible.error, /outside the current Agent scope/)
})

test('portable node-not-found errors have one canonical message', () => {
  assert.deepEqual(portableNodeNotFoundIssue('node-42', { update_index: 3 }), {
    code: 'node_not_found',
    error: 'Node was not found: node-42.',
    update_index: 3,
    node_id: 'node-42'
  })
})
