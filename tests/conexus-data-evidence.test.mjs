import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const tool = JSON.parse(await readFile(new URL('../integrations/conexus/alphalab-research-agent/tools/Project-Run.tool.json', import.meta.url)))
async function query(payload, args) {
  const requests = []
  const context = vm.createContext({ process: { env: {} }, AbortSignal,
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) })
      return { ok: true, json: async () => ({ result: payload }) }
    },
  })
  vm.runInContext(tool.code, context)
  const result = await vm.runInContext(`run(${JSON.stringify({ command: 'data.query', args: { dataset: 'example', ...args } })})`, context)
  return { result: JSON.parse(JSON.stringify(result)), requests }
}
const records = count => Array.from({ length: count }, (_, i) => ({ key: i, available_date: '2024-01-01', disclosed_date: i === 23 ? '2024-02-01' : '2024-01-01' }))

test('a bounded 40-row query preserves middle evidence and column projection', async () => {
  const rows = records(40)
  const { result, requests } = await query({ rows, matched_rows: 40, returned_rows: 40, truncated: false }, { limit: 40, columns: ['key', 'available_date', 'disclosed_date'] })
  assert.deepEqual(result.rows, rows)
  assert.equal(result.rows[23].disclosed_date, '2024-02-01')
  assert.equal(result.rows_count, 40)
  assert.equal(result.rows_truncated, false)
  assert.deepEqual(requests[0].body.input.columns, ['key', 'available_date', 'disclosed_date'])
})
test('API truncation remains visible even when the returned page fits the tool limit', async () => {
  for (const metadata of [{ truncated: true }, { matched_rows: 80 }, { rows_truncated: true }]) {
    const { result } = await query({ rows: records(10), returned_rows: 10, ...metadata }, { limit: 10 })
    assert.equal(result.rows.length, 10)
    assert.equal(result.rows_truncated, true)
  }
})
test('requested row count stays bounded at 50 and is not silently reduced to six', async () => {
  const rows = records(80)
  const { result, requests } = await query({ rows }, { limit: 500 })
  assert.equal(requests[0].body.input.limit, 50)
  assert.deepEqual(result.rows, [...rows.slice(0, 25), ...rows.slice(-25)])
  assert.equal(result.rows_count, 80)
  assert.equal(result.rows_truncated, true)
})
test('row preservation does not bypass nested bounds or internal-field removal', async () => {
  const rows = records(40).map(row => ({ ...row, source: 'private code', stdout: 'private log', nested: Array.from({ length: 20 }, (_, i) => i) }))
  const { result } = await query({ rows }, { limit: 40 })
  assert.equal(result.rows.length, 40)
  assert.ok(result.rows.every(row => !('source' in row) && !('stdout' in row)))
  assert.deepEqual(result.rows[23].nested, { count: 20, head: [0, 1, 2], tail: [17, 18, 19], truncated: true })
})
