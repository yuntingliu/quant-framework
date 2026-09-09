import assert from 'node:assert/strict'
import test from 'node:test'
import {
  canvasNodeBelongsToHarness,
  canvasNodeScope,
  collectCanvasDescendantNodes,
  collectReachableCanvasNodes
} from './canvas-graph-scope.js'

const nodes = [
  { id: 'entry', type: 'agent', data: {} },
  { id: 'note', type: 'note', data: {} },
  { id: 'harness', type: 'harness', data: { entryNodeId: 'worker' } },
  { id: 'worker', type: 'agent', parentId: 'harness', data: {} },
  { id: 'worker-note', type: 'note', parentId: 'worker', data: {} },
  { id: 'nested', type: 'harness', parentId: 'harness', data: { entryNodeId: 'nested-worker' } },
  { id: 'nested-worker', type: 'agent', data: { harnessNodeId: 'nested' } },
  { id: 'unrelated', type: 'note', data: {} }
]

test('Canvas graph scope uses one parent/Harness identity contract', () => {
  assert.equal(canvasNodeScope(nodes[3]!), 'harness')
  assert.equal(canvasNodeScope(nodes[6]!), 'nested')
  assert.equal(canvasNodeBelongsToHarness(nodes[6]!, 'nested'), true)
})

test('Harness descendants and reachable Canvas scope include recursively nested contents once', () => {
  assert.deepEqual(
    collectCanvasDescendantNodes(nodes, 'harness').map((node) => node.id),
    ['worker', 'worker-note', 'nested', 'nested-worker']
  )
  assert.deepEqual(
    collectReachableCanvasNodes(nodes, [
      { source: 'entry', target: 'note' },
      { source: 'entry', target: 'harness' }
    ], 'entry').map((node) => node.id),
    ['entry', 'note', 'harness', 'worker', 'worker-note', 'nested', 'nested-worker']
  )
})
