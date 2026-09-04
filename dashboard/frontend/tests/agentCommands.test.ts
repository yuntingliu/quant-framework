import assert from "node:assert/strict"
import test from "node:test"

import { parseAgentWorkspaceCommandBatch } from "../src/workspace/agentCommands.ts"

test("parses the project focus and strategy workbench command sequence", () => {
  assert.deepEqual(parseAgentWorkspaceCommandBatch({
    version: 1,
    requestId: "request-1",
    commands: [
      { type: "set_focus", projectId: "new-project" },
      { type: "switch_mode", mode: "strategy" },
      { type: "open_widget", widgetId: "strategy.workbench", mode: "strategy" },
    ],
  }), {
    version: 1,
    requestId: "request-1",
    commands: [
      { type: "set_focus", projectId: "new-project" },
      { type: "switch_mode", mode: "strategy" },
      { type: "open_widget", widgetId: "strategy.workbench", mode: "strategy" },
    ],
  })
})

test("rejects workspace command batches with the omitted version or widget alias", () => {
  assert.equal(parseAgentWorkspaceCommandBatch({
    requestId: "request-1",
    commands: [{ type: "set_focus", projectId: "new-project" }],
  }), null)
  assert.equal(parseAgentWorkspaceCommandBatch({
    version: 1,
    requestId: "request-1",
    commands: [{ type: "open_widget", widget: "strategy.workbench" }],
  }), null)
})
