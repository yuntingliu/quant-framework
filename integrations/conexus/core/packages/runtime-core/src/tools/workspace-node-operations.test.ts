import assert from 'node:assert/strict'
import test from 'node:test'
import type { RuntimeWorkspace } from '../contracts.js'
import { createWorkspaceNodes } from './workspace-node-operations.js'

function workspace(nodes: RuntimeWorkspace['nodes'] = []): RuntimeWorkspace {
  return { nodes, edges: [] }
}

function note(label: string, clientRef?: string): Record<string, unknown> {
  return {
    ...(clientRef ? { client_ref: clientRef } : {}),
    type: 'note',
    label,
    description: `${label} description`,
    content: label
  }
}

test('create relations resolve exact labels across new and existing Canvas nodes', () => {
  let id = 0
  const result = createWorkspaceNodes(
    workspace([{
      id: 'existing-preview',
      type: 'note',
      data: { label: 'Existing Preview', content: '' }
    }]),
    {
      nodes: [note('Presentation'), note('Generated Preview')],
      relations: [
        {
          node_a_ref: 'Presentation',
          node_b_ref: 'Generated Preview',
          relation: 'Presentation has this generated preview.'
        },
        {
          node_a_ref: 'Generated Preview',
          node_b_ref: 'Existing Preview',
          relation: 'Generated Preview supersedes Existing Preview.'
        }
      ]
    },
    'agent-1',
    () => String(++id)
  )

  assert.equal(result.result.success, true)
  assert.equal(result.createdEdges?.[0]?.source, 'note-1')
  assert.equal(result.createdEdges?.[0]?.target, 'note-2')
  assert.equal(result.createdEdges?.[1]?.source, 'note-2')
  assert.equal(result.createdEdges?.[1]?.target, 'existing-preview')
})

test('create relations reject ambiguous labels atomically', () => {
  const result = createWorkspaceNodes(
    workspace([
      { id: 'duplicate-a', type: 'note', data: { label: 'Duplicate' } },
      { id: 'duplicate-b', type: 'note', data: { label: 'Duplicate' } }
    ]),
    {
      nodes: [note('Presentation')],
      relations: [{
        node_a_ref: 'Presentation',
        node_b_ref: 'Duplicate',
        relation: 'Presentation references Duplicate.'
      }]
    },
    'agent-1',
    () => 'created'
  )

  assert.equal(result.result.success, false)
  assert.match(String(result.result.error), /ambiguous/)
  assert.match(String(result.result.error), /duplicate-a, duplicate-b/)
  assert.equal(result.createdNodes, undefined)
  assert.equal(result.createdEdges, undefined)
})

test('client_ref takes precedence over an identical Canvas label', () => {
  const result = createWorkspaceNodes(
    workspace([{
      id: 'existing-ref-name',
      type: 'note',
      data: { label: 'presentation' }
    }]),
    {
      nodes: [note('New Presentation', 'presentation'), note('Preview', 'preview')],
      relations: [{
        node_a_ref: 'presentation',
        node_b_ref: 'preview',
        relation: 'Presentation has this preview.'
      }]
    },
    'agent-1',
    (() => {
      let index = 0
      return () => String(++index)
    })()
  )

  assert.equal(result.result.success, true)
  assert.equal(result.createdEdges?.[0]?.source, 'note-1')
  assert.equal(result.createdEdges?.[0]?.target, 'note-2')
})
