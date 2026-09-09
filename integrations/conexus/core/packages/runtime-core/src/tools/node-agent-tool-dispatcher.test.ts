import assert from 'node:assert/strict'
import test from 'node:test'
import { AgentRunController } from '../agent/agent-run-controller.js'
import {
  dispatchNodeAgentTool,
  requestAgentUserInput,
  type NodeAgentToolHandlers
} from './node-agent-tool-dispatcher.js'

function handlers(calls: string[]): NodeAgentToolHandlers<string> {
  return {
    findNodes: (request, context) => ({ success: true, route: `find:${context}`, scope: request.scope }),
    observeNodes: () => ({ success: true, route: 'observe' }),
    createNodes: () => ({ success: true, route: 'create' }),
    editNodes: () => ({ success: true, route: 'edit' }),
    useNode: (request) => ({ success: true, route: 'use', capability: request.capability }),
    requestUserInput: (request, context) => {
      calls.push(`input:${request.prompt}:${context}`)
      return { success: true, value: 'yes' }
    }
  }
}

test('dispatcher owns completion and routes the five node operations', async () => {
  const calls: string[] = []
  const complete = await dispatchNodeAgentTool({
    name: 'complete', args: { status: 'done', summary: 'Done.' }, context: 'desktop', handlers: handlers(calls)
  })
  assert.equal(JSON.parse(complete.handled ? complete.result : '{}').success, true)

  const find = await dispatchNodeAgentTool({
    name: 'find', args: {}, context: 'hosted', handlers: handlers(calls)
  })
  assert.deepEqual(JSON.parse(find.handled ? find.result : '{}'), {
    success: true,
    route: 'find:hosted',
    scope: 'current'
  })

  const use = await dispatchNodeAgentTool({
    name: 'use',
    args: { node_id: 'runtime:web-search', capability: 'web.search', input: { query: 'Conexus' } },
    context: 'hosted',
    handlers: handlers(calls)
  })
  assert.equal(JSON.parse(use.handled ? use.result : '{}').capability, 'web.search')

  const unknown = await dispatchNodeAgentTool({
    name: 'web_search', args: {}, context: 'desktop', handlers: handlers(calls)
  })
  assert.deepEqual(unknown, { handled: false })
})

test('dispatcher rejects malformed operations before invoking a host', async () => {
  const calls: string[] = []
  const invalidObserve = await dispatchNodeAgentTool({
    name: 'observe', args: { requests: [] }, context: 'hosted', handlers: handlers(calls)
  })
  assert.match(invalidObserve.handled ? invalidObserve.result : '', /1 through 20/)

  const invalidCreate = await dispatchNodeAgentTool({
    name: 'create', args: { nodes: [] }, context: 'desktop', handlers: handlers(calls)
  })
  assert.match(invalidCreate.handled ? invalidCreate.result : '', /1 through 50/)

  const invalidEdit = await dispatchNodeAgentTool({
    name: 'edit',
    args: { operations: [{ kind: 'patch', node_id: 'note-1' }] },
    context: 'desktop',
    handlers: handlers(calls)
  })
  assert.match(invalidEdit.handled ? invalidEdit.result : '', /requires a non-empty set/)

  const invalidUse = await dispatchNodeAgentTool({
    name: 'use', args: { node_id: 'runtime:web-search', input: {} }, context: 'desktop', handlers: handlers(calls)
  })
  assert.match(invalidUse.handled ? invalidUse.result : '', /requires capability/)
  assert.deepEqual(calls, [])
})

test('observe includes full contracts only when explicitly requested', async () => {
  const observed: boolean[] = []
  const base = handlers([])
  const observingHandlers: NodeAgentToolHandlers<string> = {
    ...base,
    observeNodes: (request) => {
      observed.push(...request.requests.map((item) => item.includeContracts))
      return { success: true }
    }
  }
  await dispatchNodeAgentTool({
    name: 'observe',
    args: { requests: [{ node_id: 'note-1' }, { node_id: 'tool-1', include_contracts: true }] },
    context: 'hosted',
    handlers: observingHandlers
  })
  assert.deepEqual(observed, [false, true])
})

test('request_user_input validates modes and registers before host emission', async () => {
  const calls: string[] = []
  const invalid = await dispatchNodeAgentTool({
    name: 'request_user_input',
    args: { prompt: 'Choose.', choices: ['Yes'], response_schema: { type: 'object' } },
    context: 'hosted',
    handlers: handlers(calls)
  })
  assert.match(invalid.handled ? invalid.result : '', /either choices or response_schema/)
  assert.deepEqual(calls, [])

  const controller = new AgentRunController()
  let registeredDuringEmission = false
  const answer = requestAgentUserInput({
    controller,
    ownerNodeId: 'nested-agent',
    interactionId: 'input-1',
    prompt: 'Provide details.',
    choices: [],
    responseSchema: { type: 'object', properties: { approved: { type: 'boolean' } } },
    emit: ({ interactionId, ownerNodeId, kind }) => {
      registeredDuringEmission = kind === 'form' && controller.pendingInteractions.some((interaction) =>
        interaction.id === interactionId && interaction.ownerNodeId === ownerNodeId
      )
    }
  })
  assert.equal(registeredDuringEmission, true)
  assert.equal(controller.resolveInteraction('input-1', { approved: true }), true)
  assert.deepEqual(JSON.parse(await answer), { success: true, value: { approved: true } })
})
