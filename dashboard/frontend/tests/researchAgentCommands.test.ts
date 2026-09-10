import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"

const tool = JSON.parse(readFileSync(new URL("../../../integrations/conexus/alphalab-research-agent/tools/Project-Run.tool.json", import.meta.url), "utf8"))

function harness() {
  const calls: Array<{ path: string; body: Record<string, unknown> | null }> = []
  const context = vm.createContext({
    process: { env: {} }, AbortSignal, URLSearchParams,
    fetch: async (url: string, options: { body?: string }) => {
      calls.push({ path: new URL(url).pathname, body: options.body ? JSON.parse(options.body) : null })
      return { ok: true, json: async () => url.endsWith("/projects/p")
        ? { dirty: false, current_revision: 7 }
        : { outputs: { risk: { status: "insufficient" } }, status: "insufficient", rows: Array.from({ length: 50 }, (_, id) => ({ id })) } }
    },
  })
  vm.runInContext(tool.code, context)
  return { calls, run: (input: unknown) => context.run(input) }
}

test("research command preserves saved revision, parameters and compact evidence", async () => {
  const { run, calls } = harness()
  const result = await run({ command: "factor.research", confirm_python_execution: true,
    args: { project_id: "p", factor_id: "value", start_date: "2024-01-01", end_date: "2025-01-01", quantiles: 3, horizons: [1, 6] } })
  assert.equal(result.status, "succeeded")
  assert.equal(calls[1].path, "/api/strategy/projects/p/factors/value/research")
  assert.equal(calls[1].body?.revision, 7)
  assert.equal(calls[1].body?.quantiles, 3)
  assert.deepEqual(calls[1].body?.horizons, [1, 6])
  assert.equal(result.result.rows.truncated, true)
})

test("research execution requires consent while frozen validation is read only", async () => {
  const { run, calls } = harness()
  assert.equal((await run({ command: "factor.research", args: {} })).status, "failed")
  assert.equal(calls.length, 0)
  const result = await run({ command: "backtest.validation", args: { backtest_id: "bt-1" } })
  assert.equal(calls[0].path, "/api/backtests/bt-1/validation")
  assert.equal(calls[0].body, null)
  assert.equal(result.result.outputs.risk.status, "insufficient")
})
