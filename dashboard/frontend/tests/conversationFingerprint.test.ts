import assert from "node:assert/strict"
import { test } from "node:test"
import { conversationFingerprint } from "../src/lib/conexus/conversationFingerprint.ts"

test("a server round trip does not trigger another conversation write", () => {
  const local = { id: "conversation", updatedAt: "2026-09-11T02:00:00.000Z", messages: [
    { content: "result", createdAt: "2026-09-11T02:00:00Z", artifacts: [{ content: { a: 1, b: 2 } }] },
  ] }
  const server = { messages: [
    { artifacts: [{ content: { b: 2, a: 1 } }], createdAt: "2026-09-11T02:00:00+00:00", content: "result" },
  ], updatedAt: "2026-09-11T02:00:00+00:00", id: "conversation" }
  assert.equal(conversationFingerprint(local), conversationFingerprint(server))
  assert.notEqual(conversationFingerprint(local), conversationFingerprint({ ...local, messages: [] }))
  assert.notEqual(conversationFingerprint(local), conversationFingerprint({ ...local, messages: [{ content: "new result" }] }))
})
