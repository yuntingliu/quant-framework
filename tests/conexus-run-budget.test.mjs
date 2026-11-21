import assert from 'node:assert/strict'
import { test } from 'node:test'
import { budgetPhase, budgetedCompletion, readRunBudget } from '../integrations/conexus/run-budget.mjs'

const request = budget => ({ role: 'user', content: JSON.stringify({
  version: 1, userRequest: 'Find the best of five factors.', workspaceContext: { requestId: 'r1', ...(budget ? { budget } : {}) },
}) })
const tools = ['find', 'use', 'edit', 'complete'].map(name => ({ type: 'function', function: { name, parameters: { type: 'object' } } }))

test('the budget is read from the latest user request that carries one', () => {
  assert.deepEqual(readRunBudget([{ role: 'system', content: 'x' }, request({ max_model_calls: 25, max_seconds: 1200 })]),
    { max_model_calls: 25, max_seconds: 1200 })
  assert.equal(readRunBudget([request()]), null)
  assert.equal(readRunBudget([{ role: 'user', content: 'plain text' }]), null)
  assert.equal(readRunBudget([request({ max_model_calls: -1, max_seconds: 'soon' })]), null)
})

test('phases leave calls and time for delivery, then force a final call', () => {
  const budget = { max_model_calls: 25, max_seconds: 1200 }
  assert.equal(budgetPhase(budget, 21, 100), 'normal')
  assert.equal(budgetPhase(budget, 22, 100), 'wrap_up')
  assert.equal(budgetPhase(budget, 25, 100), 'final')
  assert.equal(budgetPhase(budget, 5, 1080), 'wrap_up')
  assert.equal(budgetPhase(budget, 5, 1155), 'final')
  // Short tasks keep most of their time for work.
  assert.equal(budgetPhase({ max_model_calls: 12, max_seconds: 180 }, 3, 120), 'normal')
  assert.equal(budgetPhase({ max_model_calls: 12, max_seconds: 180 }, 3, 135), 'wrap_up')
})

test('budgeted runs get a status note per call and only complete on the last call', async () => {
  const seen = []
  let now = 0
  const complete = budgetedCompletion(async input => { seen.push(input); return { content: 'ok' } }, { clock: () => now })
  const messages = [{ role: 'system', content: 'sys' }, request({ max_model_calls: 5, max_seconds: 600 })]
  for (let call = 0; call < 5; call++) { await complete({ messages, tools }); now += 10_000 }

  assert.deepEqual(seen.map(input => input.runBudget.phase), ['normal', 'wrap_up', 'wrap_up', 'wrap_up', 'final'])
  assert.ok(seen.every(input => input.messages.length === messages.length + 1))
  assert.match(seen[0].messages.at(-1).content, /model call 1 of 5, 0s of 600s/)
  assert.match(seen[1].messages.at(-1).content, /Start no new research/)
  assert.match(seen[4].messages.at(-1).content, /last model call/)
  assert.deepEqual(seen[3].tools.map(tool => tool.function.name), ['find', 'use', 'edit', 'complete'])
  assert.deepEqual(seen[4].tools.map(tool => tool.function.name), ['complete'])
  assert.ok(seen.every(input => input.toolChoice === undefined), 'thinking-mode providers reject a forced tool_choice')
  assert.equal(messages.length, 2, 'the stored conversation is not changed')
})

test('runs without a budget pass through unchanged', async () => {
  let seen
  const complete = budgetedCompletion(async input => { seen = input; return { content: 'ok' } })
  const input = { messages: [request()], tools }
  await complete(input)
  assert.equal(seen, input)
})
