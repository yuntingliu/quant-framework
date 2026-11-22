import assert from 'node:assert/strict'
import { test } from 'node:test'
import { compactToolResult, compactedCompletion } from '../integrations/conexus/context-compaction.mjs'
import { configuredCompletion } from '../integrations/conexus/model-providers.mjs'

const equity = Array.from({ length: 480 }, (_, day) => ({ date: `2024-${day}`, value: 1 + day / 1000 }))
const backtest = (runId = 'a906315bcd66') => JSON.stringify({ success: true, output: {
  run_id: runId, status: 'succeeded', as_of: '2025-12-31', signal_date: '2025-11-28',
  metrics: { sharpe: -1.136, max_drawdown: -0.7736, total_return: -0.6766, trades_attempted: 393, trades_successful: 339 },
  execution_reliable: true, research_valid: true, warnings: ['LOW_EXECUTION_FIDELITY'],
  pending_confirmation: { confirm_token: 'tok-1', action: 'delete' },
  equity_curve: equity,
  notes: 'x'.repeat(2000),
} })
const call = (id, name, command) => ({ id, type: 'function', function: { name,
  arguments: JSON.stringify(command ? { input: { command } } : {}) } })
const turn = (id, name, content, command) => [
  { role: 'assistant', content: null, tool_calls: [call(id, name, command)] },
  { role: 'tool', tool_call_id: id, name, content },
]
const conversation = (old, recent = 6) => [
  { role: 'system', content: 'sys' },
  { role: 'user', content: 'request' },
  ...old,
  ...Array.from({ length: recent }, (_, n) => turn(`recent-${n}`, 'find', backtest(`recent-${n}`))).flat(),
]

test('compaction keeps numbers, PIT dates, statuses, authorization and output ids', () => {
  const compacted = JSON.parse(compactToolResult(backtest()))
  const output = compacted.result.output
  assert.match(compacted._compacted, /Repeat the read/)
  assert.deepEqual(output.metrics, { sharpe: -1.136, max_drawdown: -0.7736, total_return: -0.6766, trades_attempted: 393, trades_successful: 339 })
  assert.equal(output.run_id, 'a906315bcd66')
  assert.equal(output.status, 'succeeded')
  assert.equal(output.as_of, '2025-12-31')
  assert.equal(output.signal_date, '2025-11-28')
  assert.equal(output.execution_reliable, true)
  assert.equal(output.research_valid, true)
  assert.deepEqual(output.warnings, ['LOW_EXECUTION_FIDELITY'])
  assert.deepEqual(output.pending_confirmation, { confirm_token: 'tok-1', action: 'delete' })
  assert.equal(output.equity_curve._items, 480)
  assert.deepEqual(output.equity_curve._last.at(-1), equity.at(-1))
  assert.match(output.notes, /\[\+1700 chars\]$/)
})

test('failed, small and non-JSON results stay whole', () => {
  const failed = JSON.stringify({ success: false, error: 'x'.repeat(5000) })
  assert.equal(compactToolResult(failed), null)
  assert.equal(compactToolResult(JSON.stringify({ success: true, output: { status: 'failed', log: 'y'.repeat(5000) } })), null)
  assert.equal(compactToolResult('{"success":true}'), null)
  assert.equal(compactToolResult('z'.repeat(5000)), null)
})

test('only older results are compacted; deliverables and the stored conversation are untouched', async () => {
  const seen = []
  const complete = compactedCompletion(async input => { seen.push(input); return { content: 'ok' } })
  const messages = conversation([
    ...turn('old-read', 'use', backtest(), 'backtest.summary'),
    ...turn('old-outputs', 'use', backtest(), 'workspace.outputs.prepare'),
    ...turn('old-edit', 'edit', backtest()),
    ...turn('old-ask', 'request_user_input', backtest()),
  ])
  const stored = JSON.stringify(messages)
  await complete({ messages })

  const sent = Object.fromEntries(seen[0].messages.filter(m => m.role === 'tool').map(m => [m.tool_call_id, m.content]))
  assert.match(sent['old-read'], /_compacted/)
  for (const id of ['old-outputs', 'old-edit', 'old-ask', 'recent-0', 'recent-5']) assert.equal(sent[id], backtest(id.startsWith('recent') ? id : undefined))
  assert.equal(seen[0].compaction.messages, 1)
  assert.ok(seen[0].compaction.saved_chars > 10_000)
  assert.equal(JSON.stringify(messages), stored, 'the stored conversation is not changed')
})

test('a compacted result is reused verbatim and recomputed when its content changes', async () => {
  const seen = []
  const complete = compactedCompletion(async input => { seen.push(input); return { content: 'ok' } })
  const messages = conversation(turn('old-read', 'use', backtest(), 'backtest.summary'))
  await complete({ messages })
  await complete({ messages })
  assert.deepEqual(complete.stats, { computed: 1, reused: 1 })
  assert.equal(seen[0].messages[3].content, seen[1].messages[3].content, 'stable text keeps the provider prompt cache')

  messages[3] = { ...messages[3], content: backtest('rerun-77') }
  await complete({ messages })
  assert.deepEqual(complete.stats, { computed: 2, reused: 1 })
  assert.equal(JSON.parse(seen[2].messages[3].content).result.output.run_id, 'rerun-77')
})

test('compaction is off by default and its cache is not shared across runs', async () => {
  const env = { CONEXUS_MODEL_BASE_URL: 'http://model.test', CONEXUS_MODEL_ID: 'm' }
  const sent = []
  const factory = () => async input => { sent.push(input); return { content: 'ok' } }
  const messages = conversation(turn('old-read', 'use', backtest(), 'backtest.summary'))

  const off = configuredCompletion('missing-settings.json', factory, env)
  await off({ messages, signal: new AbortController().signal })
  assert.equal(sent[0].messages[3].content, backtest())

  const on = configuredCompletion('missing-settings.json', factory, { ...env, ALPHALAB_AGENT_CONTEXT_COMPACTION: '1' })
  const other = conversation(turn('old-read', 'use', backtest('other-task'), 'backtest.summary'))
  await on({ messages, signal: new AbortController().signal })
  await on({ messages: other, signal: new AbortController().signal })
  assert.equal(JSON.parse(sent[1].messages[3].content).result.output.run_id, 'a906315bcd66')
  assert.equal(JSON.parse(sent[2].messages[3].content).result.output.run_id, 'other-task',
    'the same tool_call_id in another run never reuses this run\'s compacted text')
  assert.ok(sent.every(input => input.compaction === undefined), 'bookkeeping is not sent to the provider')
})
