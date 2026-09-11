import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import vm from "node:vm"
import { bindToolToInstance } from "../scripts/lib/conexus-instance.mjs"

for (const file of ["Project-Files", "Project-Run"]) {
  const tool = JSON.parse(await readFile(new URL(`../integrations/conexus/alphalab-research-agent/tools/${file}.tool.json`, import.meta.url)))
  test(`${file}: wrong instance cannot read or mutate another deployment`, async () => {
    const bound = bindToolToInstance(tool, "http://127.0.0.1:18003", "dev3")
    const calls = []
    const context = vm.createContext({ AbortSignal, process: { env: { ALPHALAB_API_ORIGIN: "http://wrong-host" } },
      fetch: async (url) => { calls.push(url); return { ok: true, json: async () => ({ instance_id: "dev2" }) } } })
    vm.runInContext(bound.code, context)
    const result = await vm.runInContext('run({command:"projects.delete",args:{project_id:"example"},confirm_delete:true})', context)
    assert.equal(result.status, "failed")
    assert.match(result.error_summary, /instance mismatch/)
    assert.deepEqual(calls, ["http://127.0.0.1:18003/api/agent/identity"])
  })
  test(`${file}: verified tool ignores host-wide origin and preserves its API contract`, async () => {
    const bound = bindToolToInstance(tool, "http://127.0.0.1:18003", "dev3")
    const calls = []
    const command = file === "Project-Files" ? "projects.list" : "workspace.context"
    const context = vm.createContext({ AbortSignal, process: { env: { ALPHALAB_API_ORIGIN: "http://wrong-host" } },
      fetch: async (url) => { calls.push(url); return { ok: true, status: 200, json: async () => url.endsWith("/identity") ? { instance_id: "dev3" } : file === "Project-Files" ? [] : {} } } })
    vm.runInContext(bound.code, context)
    const result = await vm.runInContext(`run({command:${JSON.stringify(command)}})`, context)
    if (file === "Project-Files") assert.equal(result.status, "succeeded")
    else assert.equal(JSON.stringify(result), "{}")
    assert.ok(calls.length >= 2)
    assert.ok(calls.every((url) => url.startsWith("http://127.0.0.1:18003/")))
  })
}
test("deployment binding rejects credential-bearing, remote and malformed origins", () => {
  for (const origin of ["https://example.com", "http://user:secret@127.0.0.1:8000", "http://127.0.0.1/api"])
    assert.throws(() => bindToolToInstance({}, origin, "dev3"))
  assert.throws(() => bindToolToInstance({}, "http://127.0.0.1:18003", ""))
})
