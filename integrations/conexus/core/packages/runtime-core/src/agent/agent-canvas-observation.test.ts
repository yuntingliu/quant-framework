import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildCanvasAgentChangeContextSystemSection,
  captureCanvasAgentObservation,
  diffCanvasAgentObservations
} from './agent-canvas-observation.js'

const edges = [{ id: 'edge-1', source: 'agent-1', target: 'note-1', relation: 'uses' }]

test('Canvas observations report semantic changes and ignore runtime-only Agent state', () => {
  const previous = captureCanvasAgentObservation([
    { id: 'agent-1', type: 'agent', data: { label: 'Worker', status: 'idle', messages: [] } },
    { id: 'note-1', type: 'note', data: { label: 'Brief', content: 'old' } }
  ], edges, 'agent-1')
  const current = captureCanvasAgentObservation([
    { id: 'agent-1', type: 'agent', data: { label: 'Worker', status: 'running', messages: [{ role: 'user' }] } },
    { id: 'note-1', type: 'note', data: { label: 'Brief', content: 'new' } },
    { id: 'note-2', type: 'note', data: { label: 'Result', content: 'created' } }
  ], [
    { id: 'edge-1', source: 'agent-1', target: 'note-1', relation: 'reads' },
    { id: 'edge-2', source: 'agent-1', target: 'note-2', relation: 'writes' }
  ], 'agent-1')

  const changes = diffCanvasAgentObservations(previous, current)
  assert.ok(changes)
  assert.deepEqual(changes.createdNodes.map((node) => node.id), ['note-2'])
  assert.deepEqual(changes.updatedNodes.map(({ node, changedFields }) => ({
    id: node.id,
    changedFields
  })), [{ id: 'note-1', changedFields: ['content'] }])
  assert.deepEqual(changes.updatedEdges.map(({ edge, changedFields }) => ({
    id: edge.id,
    changedFields
  })), [{ id: 'edge-1', changedFields: ['relation'] }])
  assert.deepEqual(changes.createdEdges.map((edge) => edge.id), ['edge-2'])

  const section = buildCanvasAgentChangeContextSystemSection(previous, current)
  assert.match(String(section), /Canvas Changes Since Previous Run/)
  assert.match(String(section), /id="note-1".*changed_fields=\["content"\]/)
  assert.doesNotMatch(String(section), /status|messages/)
})

test('Canvas change context is absent for a first observation or an unchanged Canvas', () => {
  const observation = captureCanvasAgentObservation([
    { id: 'agent-1', type: 'agent', data: { label: 'Worker' } }
  ], [], 'agent-1')

  assert.equal(buildCanvasAgentChangeContextSystemSection(undefined, observation), undefined)
  assert.equal(buildCanvasAgentChangeContextSystemSection(observation, observation), undefined)
})
