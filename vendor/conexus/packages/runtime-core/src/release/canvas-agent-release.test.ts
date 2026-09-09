import assert from 'node:assert/strict'
import test from 'node:test'
import type { TextOnlyAgentRunCommand } from '@conexus/runtime-protocol'
import {
  compileCanvasAgentRelease,
  prepareCanvasAgentRun
} from './canvas-agent-release.js'
import { createHostedAgentToolProfile, HostedRuntimeError } from '../hosted/hosted-runtime.js'

function canvasRun(): TextOnlyAgentRunCommand {
  return {
    nodeId: 'writer',
    sessionId: 'session-1',
    task: 'Write and review the answer.',
    messages: [],
    canvasState: {
      nodes: [
        {
          id: 'writer',
          type: 'agent',
          position: { x: 0, y: 0 },
          data: { label: 'Writer', model: 'test/model', toolNames: ['use'] }
        },
        {
          id: 'review',
          type: 'harness',
          position: { x: 200, y: 0 },
          data: { label: 'Review', entryNodeId: 'reviewer' }
        },
        {
          id: 'reviewer',
          type: 'agent',
          parentId: 'review',
          position: { x: 220, y: 40 },
          data: { label: 'Reviewer', model: 'test/model', toolNames: ['complete'] }
        },
        {
          id: 'unrelated-browser',
          type: 'browser',
          position: { x: 500, y: 0 },
          data: { url: 'https://example.com' }
        }
      ],
      edges: [{
        id: 'writer-review',
        source: 'writer',
        target: 'review',
        relation: 'The Writer runs the Review Harness.'
      }]
    }
  }
}

const options = {
  hostLabel: 'Test runtime',
  releaseIdPrefix: 'test.canvas-agent',
  defaultModel: 'test/model',
  allowedModels: new Set(['test/model']),
  tags: ['test']
} as const

test('Canvas Agent compilation packages the reachable graph and nested Harness contents', () => {
  const release = compileCanvasAgentRelease(canvasRun(), options)
  assert.deepEqual(release.graph.nodes.map((node) => node.id), ['writer', 'review', 'reviewer'])
  assert.equal(release.graph.nodes.find((node) => node.id === 'reviewer')?.parentId, 'review')
  assert.deepEqual(release.graph.edges.map((edge) => edge.id), ['writer-review'])
  assert.deepEqual(release.graph.nodes.find((node) => node.id === 'writer')?.data.toolNames, ['use', 'complete'])
  assert.equal(release.graph.nodes.find((node) => node.id === 'writer')?.data.task, '{{request}}')
  assert.deepEqual(release.manifest.tags, ['canvas', 'ephemeral', 'test'])
})

test('Canvas Agent compilation rejects connected nodes outside the host capability profile', () => {
  const request = canvasRun()
  request.canvasState.edges.push({
    id: 'writer-browser',
    source: 'writer',
    target: 'unrelated-browser',
    relation: 'The Writer uses this Browser.'
  })
  assert.throws(
    () => compileCanvasAgentRelease(request, options),
    (error) => error instanceof HostedRuntimeError && error.code === 'unsupported_agent_context'
  )
})

test('Canvas Agent compilation keeps packaged Tool names below the use capability boundary', () => {
  const request = canvasRun()
  request.canvasState.nodes.push({
    id: 'conflicting-tool',
    type: 'tool',
    position: { x: 100, y: 100 },
    data: {
      label: 'Conflicting Tool',
      toolName: 'complete',
      runtime: 'node',
      code: 'async function run() { return { ok: true } }'
    }
  })
  request.canvasState.edges.push({
    id: 'writer-conflicting-tool',
    source: 'writer',
    target: 'conflicting-tool',
    relation: 'The Writer can run this Tool.'
  })

  const release = compileCanvasAgentRelease(request, {
    ...options,
    supportedNodeTypes: ['tool']
  })
  assert.equal(release.tools.some((tool) => tool.nodeId === 'conflicting-tool' && tool.name === 'complete'), true)
  const writerToolNames = release.graph.nodes.find((node) => node.id === 'writer')?.data.toolNames as string[]
  assert.equal(writerToolNames.includes('complete'), true)
})

test('Canvas Agent compilation derives timeout and host-specific node capability from options', () => {
  const request = canvasRun()
  request.timeoutMs = 2_500
  request.canvasState.edges.push({
    id: 'writer-browser',
    source: 'writer',
    target: 'unrelated-browser',
    relation: 'The Writer uses this Browser.'
  })
  const release = compileCanvasAgentRelease(request, {
    ...options,
    supportedNodeTypes: ['browser'],
    defaultTimeoutMs: 5_000,
    maximumTimeoutMs: 5_000
  })
  assert.equal(release.runtime.timeoutMs, 2_500)
  assert.ok(release.graph.nodes.some((node) => node.id === 'unrelated-browser'))
})

test('Canvas Agent run preparation owns release, conversation, configuration, and timeout policy', () => {
  const request = canvasRun()
  request.timeoutMs = 2_500
  request.messages = [
    { role: 'user', content: 'Earlier request' },
    { role: 'assistant', content: 'Earlier response' },
    { role: 'user', content: '  Write and review the answer.  ' }
  ]

  const plan = prepareCanvasAgentRun(request, {
    ...options,
    defaultTimeoutMs: 5_000,
    maximumTimeoutMs: 5_000
  })

  assert.equal(plan.conversation.currentRequest, 'Write and review the answer.')
  assert.deepEqual(plan.conversation.history, request.messages.slice(0, -1))
  assert.deepEqual(plan.runtimeConfiguration, {
    model: 'test/model',
    toolNames: ['use', 'complete'],
    explicitToolNames: ['use']
  })
  assert.equal(plan.timeoutMs, 2_500)
  assert.equal(plan.release.runtime.timeoutMs, plan.timeoutMs)
})

test('Canvas Agent compilation resolves historical automatic tool lists from the active host', () => {
  const request = canvasRun()
  request.canvasState.nodes[0]!.data.toolNames = [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_node', 'connect_nodes',
    'delete_node', 'list_nodes', 'attach_node', 'web_search',
    'search_subharness_library', 'complete', 'retired_host_tool'
  ]

  const plan = prepareCanvasAgentRun(request, options)
  const hostDefaults = createHostedAgentToolProfile().defaultToolNames
  const releaseToolNames = plan.release.graph.nodes.find((node) => node.id === 'writer')?.data.toolNames

  assert.equal(plan.runtimeConfiguration.explicitToolNames, undefined)
  assert.deepEqual(new Set(plan.runtimeConfiguration.toolNames), new Set(hostDefaults))
  assert.deepEqual(new Set(releaseToolNames as string[]), new Set(hostDefaults))
  assert.equal(plan.runtimeConfiguration.toolNames.includes('retired_host_tool'), false)
})
