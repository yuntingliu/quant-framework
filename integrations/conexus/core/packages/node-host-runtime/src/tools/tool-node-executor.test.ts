import assert from 'node:assert/strict'
import test from 'node:test'
import { createNodeShellRuntime } from '../shell/node-shell-runtime.js'
import { executeToolNode } from './tool-node-executor.js'

test('executes a Node Tool with structured input, context, and nested node calls', async () => {
  const runtime = createNodeShellRuntime()
  try {
    const result = await executeToolNode({
      shellRuntime: runtime,
      nodeId: 'tool-test',
      runtime: 'node',
      code: `
async function run(input, context) {
  const nested = await context.runNode('nested-tool', { value: input.value })
  return { value: input.value, project: context.projectPath, nested }
}`,
      args: { value: 7 },
      context: { projectPath: '/project' },
      callNode: async ({ nodeId, args }) => ({ nodeId, value: args?.value })
    })

    assert.equal(result.success, true)
    assert.equal(result.exit_code, 0)
    assert.deepEqual(result.result, {
      value: 7,
      project: '/project',
      nested: { nodeId: 'nested-tool', value: 7 }
    })
  } finally {
    runtime.disposeAll()
  }
})
