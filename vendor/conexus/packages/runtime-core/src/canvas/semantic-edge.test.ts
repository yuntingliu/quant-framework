import assert from 'node:assert/strict'
import test from 'node:test'
import {
  semanticEdgeFields,
  semanticEdgeSentence
} from './semantic-edge.js'
import { NODE_AGENT_TOOL_SCHEMAS } from '../tools/node-agent-tools.js'

test('semantic edges preserve current fields and migrate persisted labels narrowly', () => {
  assert.deepEqual(
    semanticEdgeFields({ kind: 'reads', relation: 'uses as evidence' }),
    { relation: 'uses as evidence' }
  )
  assert.deepEqual(
    semanticEdgeFields({ label: 'output' }),
    { relation: 'One node stores output in the other.' }
  )
  assert.deepEqual(
    semanticEdgeFields({ label: 'controlled shell', data: { contracts: ['context', 'runtime'] } }),
    { relation: 'controlled shell' }
  )
  assert.deepEqual(
    semanticEdgeFields({}),
    { relation: 'These nodes are related.' }
  )
})

test('Agent authoring writes one undirected relation without machine kinds or directed argument names', () => {
  const edit = NODE_AGENT_TOOL_SCHEMAS.edit.function.parameters as {
    properties: {
      operations: {
        items: {
          oneOf: Array<{ properties: { kind: { const: string } }; required: string[] }>
        }
      }
    }
  }
  const connect = edit.properties.operations.items.oneOf.find((operation) => operation.properties.kind.const === 'connect')!
  assert.deepEqual(connect.required, ['kind', 'node_a_id', 'node_b_id', 'relation'])
  assert.deepEqual(Object.keys(connect.properties), ['kind', 'node_a_id', 'node_b_id', 'relation'])

  const create = NODE_AGENT_TOOL_SCHEMAS.create.function.parameters as {
    properties: {
      relations: {
        items: {
          properties: Record<string, unknown>
          required: string[]
        }
      }
    }
  }
  assert.deepEqual(create.properties.relations.items.required, ['node_a_ref', 'node_b_ref', 'relation'])
  assert.deepEqual(Object.keys(create.properties.relations.items.properties), ['node_a_ref', 'node_b_ref', 'relation'])
})

test('semantic edge sentences present an unordered node pair and its natural-language statement', () => {
  assert.equal(
    semanticEdgeSentence(
      { relation: 'The Writer delegates review to the Reviewer.' },
      'Writer',
      'Reviewer'
    ),
    'Writer ↔ Reviewer: The Writer delegates review to the Reviewer.'
  )
})
