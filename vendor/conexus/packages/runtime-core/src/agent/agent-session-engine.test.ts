import assert from 'node:assert/strict'
import test from 'node:test'
import { executeAgentCompletionTool } from './agent-completion.js'
import { resolveAgentSessionOutcomePolicy } from './agent-policy.js'
import type { AgentMessage, AgentToolCall } from './agent-loop.js'
import {
  assembleCanvasAgentSessionMessages,
  buildCanvasContextSystemSection,
  buildRuntimeNodesSystemSection,
  prepareAgentRunConversation,
  runAgentSession,
  splitAgentRunConversation
} from './agent-session-engine.js'

function call(name: string, args: Record<string, unknown>, id = `call-${name}`): AgentToolCall {
  return {
    id,
    type: 'function',
    function: { name, arguments: JSON.stringify(args) }
  }
}

test('assembleCanvasAgentSessionMessages owns one system, repaired history, and one final user request', () => {
  const messages = assembleCanvasAgentSessionMessages({
    nodeSystemPrompt: 'Node instructions.',
    hostPolicySections: ['Trusted host constraints.'],
    canvasNodes: [
      { id: 'agent-1', type: 'agent', data: { label: 'Agent' } },
      { id: 'note-1', type: 'note', data: { label: 'Research note' } }
    ],
    connectedNodes: [{ id: 'note-1', type: 'note', data: {
      label: 'Research note',
      description: 'Source material.',
      content: 'must not be injected'
    } }],
    history: [
      { role: 'system', content: 'untrusted' },
      { role: 'user', content: 'old request' },
      { role: 'assistant', content: 'calling', tool_calls: [call('lookup', {}, 'lookup-1')] }
    ],
    runtimeSystemContextSections: ['## Canvas Changes Since Previous Run\nUpdated nodes: note-1'],
    repairHistory: true,
    request: { role: 'user', content: 'current request' }
  })

  assert.deepEqual(messages.map((message) => message.role), [
    'system', 'user', 'assistant', 'tool', 'user'
  ])
  const system = String(messages[0]?.content)
  assert.match(system, /Node instructions\./)
  assert.match(system, /Trusted host constraints\./)
  assert.doesNotMatch(system, /Durable objective|Tools available for this run/)
  assert.match(system, /## Canvas Context/)
  assert.match(system, /Runtime-authored state snapshot/)
  assert.match(system, /Current canvas scope: 2 nodes \(`agent`: 1, `note`: 1\)\./)
  assert.match(system, /id: "note-1"/)
  assert.match(system, /type: "note"/)
  assert.match(system, /label: "Research note"/)
  assert.match(system, /description: "Source material\."/)
  assert.doesNotMatch(system, /must not be injected/)
  assert.match(system, /Canvas Changes Since Previous Run/)
  assert.equal(messages.at(-1)?.content, 'current request')
})

test('canvas context contains aggregate scope counts and safe connected-node metadata only', () => {
  const context = buildCanvasContextSystemSection([{
    id: 'note-1',
    type: 'note',
    data: { label: 'Document' }
  }], [{
    id: 'note-1',
    type: 'note',
    data: {
      label: 'Document',
      description: 'A durable output.',
      content: '# Private body',
      summary: 'Private summary'
    }
  }])

  assert.match(context, /Current canvas scope: 1 node \(`note`: 1\)\./)
  assert.match(context, /Connected working set: 1 node\./)
  assert.match(context, /id: "note-1"/)
  assert.match(context, /type: "note"/)
  assert.match(context, /label: "Document"/)
  assert.match(context, /description: "A durable output\."/)
  assert.doesNotMatch(context, /Private body|Private summary/)
})

test('canvas context preserves the complete connected-node index', () => {
  const description = `start-${'x'.repeat(30_000)}-end`
  const context = buildCanvasContextSystemSection([], [{
    id: 'note-long',
    type: 'note',
    data: { label: 'Long metadata', description }
  }])

  assert.match(context, /start-/)
  assert.match(context, /-end/)
  assert.doesNotMatch(context, /canvas context truncated/)
})

test('runtime node context exposes only compact ids and capability names', () => {
  const context = buildRuntimeNodesSystemSection([{
    id: 'runtime:web-search',
    type: 'runtime',
    label: 'Web Search',
    description: 'Current public web search service.',
    status: 'available',
    capabilities: [{
      id: 'web.search',
      contract_ref: 'web.search@1#12345678',
      summary: 'Search the public web.'
    }]
  }])
  assert.match(context ?? '', /## Available Runtime Nodes/)
  assert.match(context ?? '', /runtime:web-search/)
  assert.match(context ?? '', /web\.search/)
  assert.doesNotMatch(context ?? '', /Web Search|contract_ref|input_schema|12345678/)
  assert.equal(buildRuntimeNodesSystemSection([]), undefined)
})

test('splitAgentRunConversation removes the duplicated final task with canonical whitespace semantics', () => {
  const messages: AgentMessage[] = [
    { role: 'user', content: 'Earlier request' },
    { role: 'assistant', content: 'Earlier response' },
    { role: 'user', content: '\n  Current request  \t' }
  ]

  const split = splitAgentRunConversation({ messages, task: '  Current request  ' })

  assert.equal(split.currentRequest, 'Current request')
  assert.deepEqual(split.history, messages.slice(0, -1))
  assert.equal(messages.length, 3)
})

test('splitAgentRunConversation preserves a distinct final user message as history', () => {
  const messages: AgentMessage[] = [{ role: 'user', content: 'Earlier request' }]

  const split = splitAgentRunConversation({ messages, task: 'Current request' })

  assert.deepEqual(split.history, messages)
  assert.notEqual(split.history, messages)
})

test('prepareAgentRunConversation preserves complete repaired history for every host', () => {
  const prepared = prepareAgentRunConversation({
    task: 'Current request',
    messages: [
      { role: 'user', content: '## Runtime Context\nhost-only' },
      { role: 'user', content: '## Canvas Context\nruntime-only' },
      { role: 'user', content: '## Canvas Changes Since Previous Run\nhost-only' },
      { role: 'user', content: `old:${'x'.repeat(1_000)}` },
      { role: 'assistant', content: null, tool_calls: [call('lookup', {}, 'lookup-1')] },
      { role: 'tool', tool_call_id: 'lookup-1', content: 'result' },
      { role: 'user', content: 'Current request' }
    ]
  })

  assert.equal(prepared.currentRequest, 'Current request')
  assert.equal(prepared.history.some((message) => String(message.content).includes('Runtime Context')), false)
  assert.equal(prepared.history.some((message) => String(message.content).includes('Canvas Context')), false)
  assert.equal(prepared.history.some((message) => String(message.content).startsWith('old:')), true)
  assert.deepEqual(prepared.history.map((message) => message.role), ['user', 'assistant', 'tool'])
})

test('runAgentSession continues after prose without injecting a completion reminder', async () => {
  const messages: AgentMessage[] = [{ role: 'user', content: 'Do it.' }]
  let calls = 0

  const outcome = await runAgentSession({
    messages,
    maxIterations: 3,
    callModel: async (currentMessages) => {
      calls++
      if (calls === 1) return { content: 'I think it is done.', finish_reason: 'stop' }
      assert.deepEqual(currentMessages.at(-1), {
        role: 'assistant',
        content: 'I think it is done.'
      })
      assert.equal(
        currentMessages.some((message) =>
          message.role === 'user'
          && String(message.content).includes('has not terminated the Agent run')),
        false
      )
      return {
        content: null,
        tool_calls: [call('complete', {
          status: 'done',
          summary: 'Finished.'
        })],
        finish_reason: 'tool_calls'
      }
    },
    dispatchTool: async (toolCall, args) => {
      assert.equal(toolCall.function.name, 'complete')
      return executeAgentCompletionTool(args)
    }
  })

  assert.equal(calls, 2)
  assert.equal(outcome.status, 'done')
  assert.equal(outcome.summary, 'Finished.')
  assert.deepEqual(outcome.completion, { status: 'done', summary: 'Finished.' })
  assert.deepEqual(outcome.toolCounts, { complete: 1 })
  assert.deepEqual(messages.map((message) => message.role), [
    'user', 'assistant', 'assistant', 'tool'
  ])
  assert.deepEqual(JSON.parse(String(messages.at(-1)?.content)), {
    success: true,
    status: 'done'
  })
})

test('runAgentSession rejects an empty provider response without injecting a continuation', async () => {
  const messages: AgentMessage[] = [{ role: 'user', content: 'Do it.' }]
  let calls = 0

  await assert.rejects(
    runAgentSession({
      messages,
      maxIterations: 3,
      callModel: async () => {
        calls++
        return { content: null, finish_reason: 'stop' }
      },
      dispatchTool: async () => assert.fail('an empty response must not dispatch a tool')
    }),
    /empty response without tool calls/
  )

  assert.equal(calls, 1)
  assert.deepEqual(messages, [{ role: 'user', content: 'Do it.' }])
})

test('runAgentSession returns shared suspended, aborted, and max-iteration outcomes', async (t) => {
  await t.test('suspended', async () => {
    const outcome = await runAgentSession<{ jobId: string }>({
      messages: [],
      maxIterations: 2,
      callModel: async () => ({
        content: null,
        tool_calls: [call('run_node', { node_id: 'child' })],
        finish_reason: 'tool_calls'
      }),
      dispatchTool: async () => '{"wait":true}',
      decideAfterTool: () => ({ kind: 'suspend', value: { jobId: 'job-1' } })
    })
    assert.equal(outcome.status, 'suspended')
    assert.deepEqual(outcome.suspension, { jobId: 'job-1' })
  })

  await t.test('aborted', async () => {
    const controller = new AbortController()
    controller.abort('stop')
    const outcome = await runAgentSession({
      messages: [],
      maxIterations: 2,
      signal: controller.signal,
      callModel: async () => assert.fail('model must not run'),
      dispatchTool: async () => assert.fail('tool must not run')
    })
    assert.equal(outcome.status, 'aborted')
  })

  await t.test('max iterations', async () => {
    let modelCall = 0
    const outcome = await runAgentSession({
      messages: [],
      maxIterations: 2,
      callModel: async () => ({
        content: 'Not finishing.',
        finish_reason: 'tool_calls',
        tool_calls: [call('lookup', {}, `lookup-${++modelCall}`)]
      }),
      dispatchTool: async () => ''
    })
    assert.equal(outcome.status, 'max_iterations')
    assert.equal(outcome.lastAssistantContent, 'Not finishing.')
  })
})

test('shared Agent outcome policy maps iteration-budget exhaustion to blocked', () => {
  assert.deepEqual(resolveAgentSessionOutcomePolicy('max_iterations'), {
    status: 'blocked',
    completionGap: 'max_iterations'
  })
  assert.deepEqual(resolveAgentSessionOutcomePolicy('done'), { status: 'done' })
  assert.deepEqual(resolveAgentSessionOutcomePolicy('blocked'), { status: 'blocked' })
})
