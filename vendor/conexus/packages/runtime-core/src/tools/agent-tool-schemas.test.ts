import assert from 'node:assert/strict'
import test from 'node:test'
import AjvModule, { type ValidateFunction } from 'ajv'
import { AGENT_COMPLETION_TOOL, executeAgentCompletionTool } from '../agent/agent-completion.js'
import { getNodeCapabilityContract } from '../capabilities/node-capability-contracts.js'
import { parseBrowserToolAction } from './browser.js'
import { NODE_AGENT_TOOL_SCHEMAS } from './node-agent-tools.js'

const AjvConstructor = (
  (AjvModule as unknown as { default?: unknown }).default ?? AjvModule
) as new (options: { allErrors: boolean; strict: boolean }) => {
  compile: (schema: unknown) => ValidateFunction<unknown>
}

function validator(schema: unknown): ValidateFunction<unknown> {
  return new AjvConstructor({ allErrors: true, strict: false }).compile(schema)
}

test('all model-visible node Agent schemas use provider-portable object roots', () => {
  const forbiddenRootKeywords = ['oneOf', 'anyOf', 'allOf', 'enum', 'const', 'not']
  for (const tool of Object.values(NODE_AGENT_TOOL_SCHEMAS)) {
    assert.equal(tool.function.parameters.type, 'object', tool.function.name)
    for (const keyword of forbiddenRootKeywords) {
      assert.equal(keyword in tool.function.parameters, false, `${tool.function.name}: ${keyword}`)
    }
  }
})

test('complete keeps conditional validation in the runtime', () => {
  const validate = validator(AGENT_COMPLETION_TOOL.function.parameters)
  assert.equal(validate({ status: 'done', summary: 'Finished.' }), true)
  assert.equal(validate({ status: 'blocked', summary: 'Cannot continue.', blocker: 'Missing access.' }), true)
  assert.equal(validate({ status: 'done', summary: 'Finished.', verification: 'Obsolete.' }), false)
  assert.equal(JSON.parse(executeAgentCompletionTool({ status: 'done', summary: 'Finished.', blocker: 'Not blocked.' })).success, false)
  assert.equal(JSON.parse(executeAgentCompletionTool({ status: 'blocked', summary: 'Cannot continue.' })).success, false)
})

test('edit validates its five tagged atomic operation shapes', () => {
  const validate = validator(NODE_AGENT_TOOL_SCHEMAS.edit.function.parameters)
  assert.equal(validate({ operations: [{ kind: 'patch', node_id: 'note-1', set: { content: 'Updated' } }] }), true)
  assert.equal(validate({ operations: [{ kind: 'move', node_id: 'note-1', scope: 'root' }] }), true)
  assert.equal(validate({ operations: [{ kind: 'connect', node_a_id: 'a', node_b_id: 'b', relation: 'A supports B.' }] }), true)
  assert.equal(validate({ operations: [{ kind: 'disconnect', edge_id: 'edge-1' }] }), true)
  assert.equal(validate({ operations: [{ kind: 'delete', node_id: 'note-1' }] }), true)
  assert.equal(validate({ operations: [] }), false)
  assert.equal(validate({ operations: [{ kind: 'patch', node_id: 'note-1', data: {} }] }), false)
  assert.equal(validate({ operations: [{ kind: 'patch', node_id: 'note-1', props: { content: 'Legacy' } }] }), false)
  assert.equal(validate({ operations: [{ kind: 'patch', node_id: 'note-1', set: {} }] }), false)
  assert.equal(validate({ operations: [{ kind: 'patch', node_id: 'note-1', set: { unknownField: 'No' } }] }), false)
})

test('edit declares satisfiable patch fields and preserves different node patch formats', () => {
  const validate = validator(NODE_AGENT_TOOL_SCHEMAS.edit.function.parameters)
  for (const set of [
    { label: 'Revised brief', description: '' },
    { contentPatch: { operation: 'append', text: '\nNext steps' } },
    { contentPatch: { operation: 'replace', find: 'Draft', replace: 'Final' } },
    { contentPatch: { find: 'Draft', replace: 'Final' } },
    { systemPromptPatch: { find: 'old', replace: 'new' } },
    { props: { title: 'Tour', nested: { count: 1 } }, state: { step: 'intro' } }
  ]) {
    assert.equal(validate({ operations: [{ kind: 'patch', node_id: 'node-1', set }] }), true, JSON.stringify(validate.errors))
  }

  const inspect = (value: unknown, path: string): void => {
    if (!value || typeof value !== 'object') return
    const schema = value as Record<string, unknown>
    if (schema.type === 'object' && typeof schema.minProperties === 'number' && schema.minProperties > 0) {
      // Reproduce the provider's closed-object constraint from the reported 400.
      assert.ok(Object.keys(schema.properties ?? {}).length >= schema.minProperties, path)
    }
    for (const [key, child] of Object.entries(value)) inspect(child, `${path}/${key}`)
  }
  for (const tool of Object.values(NODE_AGENT_TOOL_SCHEMAS)) inspect(tool.function.parameters, tool.function.name)
})

test('browser capability contracts expose exact inputs while the parser enforces action semantics', () => {
  const browser = { id: 'browser-1', type: 'browser', data: {} }
  const click = getNodeCapabilityContract(browser, 'browser.click')
  assert.ok(click)
  const validate = validator(click!.input_schema)
  assert.equal(validate({ element_id: 'button-1' }), true)
  assert.equal(validate({ elementId: 'button-1' }), false)
  assert.deepEqual(parseBrowserToolAction({ kind: 'click', element_id: ' button-1 ' }), {
    kind: 'click',
    elementId: 'button-1'
  })
})
