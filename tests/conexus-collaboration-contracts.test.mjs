import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

const built = existsSync(new URL('../build/conexus/node_modules/ajv', import.meta.url))
const api = built ? await import('../integrations/conexus/collaboration/contracts.mjs') : null
const examples = JSON.parse(readFileSync(new URL('./fixtures/collaboration/contracts/examples.json', import.meta.url)))
const cases = JSON.parse(readFileSync(new URL('./fixtures/collaboration/contracts/cases.json', import.meta.url)))
for (const item of cases) {
  test(`collaboration contract: ${item.name}`, { skip: !built }, () => {
    const payload = structuredClone(examples.messages[item.kind])
    const context = structuredClone(examples[item.kind === 'correction_response' ? 'correction_context' : 'context'])
    for (const patch of item.patches) {
      let parent = patch.scope === 'payload' ? payload : context
      for (const key of patch.path.slice(0, -1)) parent = parent[key]
      const key = patch.path.at(-1)
      if (patch.operation === 'delete') delete parent[key]
      else parent[key] = structuredClone(patch.value)
    }
    if (item.expected_code) assert.throws(() => api.validateContract(item.kind, payload, context), error => error.code === item.expected_code)
    else api.validateContract(item.kind, payload, context)
  })
}
test('review submission is bound to trusted identity and acknowledges only persisted results', { skip: !built }, async () => {
  const review = structuredClone(examples.messages.review_result)
  const binding = { role: 'reviewer', task_id: 'task-1', node_id: 'reviewer-1', child_task_id: review.child_task_id, attempt_id: review.attempt_id }
  const receipts = []
  const handler = api.createReviewSubmissionHandler({ binding, context: examples.context, accept: async receipt => {
    receipts.push(receipt)
    return { submission_id: receipt.submission_id, sha256: receipt.sha256 }
  } })
  await assert.rejects(handler({ input: { review }, identity: { ...binding, node_id: 'researcher-1' } }), error => error.code === 'TASK_MISMATCH')
  assert.equal(receipts.length, 0)
  const result = await handler({ input: { review }, identity: binding })
  assert.equal(result.status, 'accepted')
  assert.equal(receipts.length, 1)
  assert.deepEqual(JSON.parse(receipts[0].payload_json), review)
})
test('a failed persistence callback cannot produce a review acknowledgment', { skip: !built }, async () => {
  const review = examples.messages.review_result
  const binding = { role: 'reviewer', task_id: 'task-1', node_id: 'reviewer-1', child_task_id: review.child_task_id, attempt_id: review.attempt_id }
  const handler = api.createReviewSubmissionHandler({ binding, context: examples.context, accept: async () => { throw new Error('private-path-must-not-leak') } })
  await assert.rejects(handler({ input: { review }, identity: binding }), error => error.code === 'STORAGE_UNAVAILABLE' && !error.message.includes('private-path'))
})
test('JavaScript also rejects nonfinite metric values', { skip: !built }, () => {
  for (const value of [NaN, Infinity, -Infinity]) {
    const payload = structuredClone(examples.messages.research_package)
    payload.metrics[0].value = value
    assert.throws(() => api.validateContract('research_package', payload, examples.context), error => error.code === 'INVALID_CONTRACT')
  }
})
test('text evidence uses the same Unicode line boundaries as the Python reader', { skip: !built }, () => {
  for (const separator of ['\n', '\r\n', '\r', '\v', '\f', '\u001c', '\u001d', '\u001e', '\u0085', '\u2028', '\u2029']) {
    assert.equal(api.resolveLocator(`first${separator}second`, 'lines:2:2', 'text/plain'), 'second')
  }
})
