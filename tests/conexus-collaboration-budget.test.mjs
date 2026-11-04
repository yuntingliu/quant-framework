import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../', import.meta.url))
const pythonExecutable = [process.env.COLLABORATION_TEST_PYTHON, 'build/member1/venv/Scripts/python.exe', '.venv/Scripts/python.exe', '.venv/bin/python']
  .filter(Boolean).map(path => resolve(root, path)).find(path => existsSync(path))
const built = existsSync(new URL('../build/conexus/packages/runtime-core/dist/index.js', import.meta.url)) && Boolean(pythonExecutable)
const runner = built ? await import('../scripts/check_collaboration_budget.mjs') : null
test('pinned provider accounts for DeepSeek cache receipts without adding cached tokens twice', { skip: !built }, async () => {
  const { createPinnedModelProvider } = await import('../integrations/conexus/collaboration/model-provider.mjs')
  for (const usage of [
    { prompt_tokens: 10, completion_tokens: 3, total_tokens: 13, prompt_cache_hit_tokens: 7 },
    { prompt_tokens: 10, completion_tokens: 3, total_tokens: 13, prompt_cache_hit_tokens: 9, prompt_tokens_details: { cached_tokens: 7 } },
  ]) {
    const provider = createPinnedModelProvider({ profile: { model: 'test', base_url: 'https://model.example', api_key: 'dummy' },
      fetchImpl: async () => new Response(JSON.stringify({ choices: [{ message: { role: 'assistant', content: 'OK' }, finish_reason: 'stop' }], usage })) })
    const result = await provider.complete({ messages: [{ role: 'user', content: 'OK' }], maxTokens: 20, signal: new AbortController().signal })
    assert.equal(result.usage.cached_input_tokens, 7)
    assert.equal(result.usage.input_tokens, 10)
    assert.equal(result.usage.total_tokens, 13)
    assert.equal(result.usage.cost_micros, undefined)
  }
})
for (const scenario of runner?.budgetScenarios ?? ['runtime unavailable']) {
  test(`collaboration shared budget: ${scenario}`, { skip: !built, timeout: 240000 }, async () => {
    await runner.runBudgetScenario({ scenario, pythonExecutable,
      output: join(root, 'build/member1/budget-tests', `${scenario}-${randomUUID()}`) })
  })
}
