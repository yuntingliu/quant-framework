import assert from "node:assert/strict"
import test from "node:test"

import {
  parseAgentWorkspaceCommandBatch,
  workspaceResultMatchesCommands,
} from "../src/workspace/agentCommands.ts"

test("parses the project focus and strategy workbench command sequence", () => {
  assert.deepEqual(parseAgentWorkspaceCommandBatch({
    version: 1,
    requestId: "request-1",
    commands: [
      { type: "set_focus", projectId: "new-project", strategyId: "momentum" },
      { type: "switch_mode", mode: "strategy" },
      { type: "open_widget", widgetId: "strategy.workbench", mode: "strategy" },
    ],
  }), {
    version: 1,
    requestId: "request-1",
    commands: [
      { type: "set_focus", projectId: "new-project", strategyId: "momentum" },
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

test("opens a report only when the atomic result descriptor matches the request and node", () => {
  const batch = parseAgentWorkspaceCommandBatch({
    version: 1,
    requestId: "request-1",
    commands: [{ type: "open_result", resultId: "report-1", mode: "report" }],
  })
  assert.ok(batch)
  assert.equal(workspaceResultMatchesCommands(batch, undefined), false)
  assert.equal(workspaceResultMatchesCommands(batch, {
    id: "report-1",
    version: 1,
    reportId: "report-1",
    requestId: "request-1",
    kind: "document",
    title: "Current report",
    markdown: "# Current report",
    sources: [],
  }), true)
  assert.equal(workspaceResultMatchesCommands(batch, {
    id: "old-report",
    version: 1,
    reportId: "old-report",
    requestId: "request-1",
    kind: "document",
    title: "Stale report",
    markdown: "# Stale report",
    sources: [],
  }), false)
})
