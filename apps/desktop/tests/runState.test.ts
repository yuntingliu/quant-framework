import assert from "node:assert/strict"
import { describe, it } from "node:test"

import {
  changedWorkspaceNodesForRun,
  isTerminalRun,
  mergeRunSnapshot,
  parseRunEventBlock,
} from "../src/lib/conexus/runState.ts"
import type {
  PublishedHarnessRun,
  PublishedHarnessWorkspaceSnapshot,
} from "../src/lib/conexus/types.ts"

function run(status: PublishedHarnessRun["status"]): PublishedHarnessRun {
  return {
    id: "run-1",
    slug: "alphalab-research-agent",
    version: 1,
    status,
    createdAt: "2026-09-03T00:00:00Z",
  }
}

describe("published Harness run state", () => {
  it("never lets a stale active snapshot replace a terminal state", () => {
    assert.equal(mergeRunSnapshot(run("completed"), run("running")).status, "completed")
    assert.equal(mergeRunSnapshot(run("running"), run("failed")).status, "failed")
  })

  it("recognizes every terminal state", () => {
    for (const status of ["completed", "blocked", "failed", "cancelled"] as const) {
      assert.equal(isTerminalRun(run(status)), true)
    }
    assert.equal(isTerminalRun(run("running")), false)
  })

  it("parses an unterminated trailing SSE data block", () => {
    const event = parseRunEventBlock(
      'event: message\ndata: {"sequence":3,"runId":"run-1","at":"now","type":"run.completed","status":"completed"}',
    )
    assert.equal(event?.type, "run.completed")
    assert.equal(event?.status, "completed")
  })

  it("recovers only this run's changed workspace outputs", () => {
    const completed = {
      ...run("completed"),
      nodeChanges: {
        created: [],
        updated: ["workspace-commands", "decision-notebook"],
        deleted: [],
      },
    }
    const workspace: PublishedHarnessWorkspaceSnapshot = {
      workspaceId: "workspace-1",
      slug: "alphalab-research-agent",
      revision: 3,
      nodes: [
        {
          id: "workspace-commands",
          type: "custom",
          label: "Workspace Commands",
          values: { data: { version: 1 } },
          createdAt: "2026-09-03T00:00:00Z",
          updatedAt: "2026-09-03T00:01:00Z",
          createdByRunId: "run-0",
          updatedByRunId: "run-1",
        },
        {
          id: "decision-notebook",
          type: "custom",
          label: "Decision Notebook",
          values: { data: { classification: "strategy" } },
          createdAt: "2026-09-03T00:00:00Z",
          updatedAt: "2026-09-03T00:01:00Z",
          createdByRunId: "run-0",
          updatedByRunId: "run-2",
        },
        {
          id: "unrelated",
          type: "note",
          label: "Unrelated",
          values: { content: "ignored" },
          createdAt: "2026-09-03T00:00:00Z",
          updatedAt: "2026-09-03T00:01:00Z",
          createdByRunId: "run-1",
          updatedByRunId: "run-1",
        },
      ],
    }

    assert.deepEqual(changedWorkspaceNodesForRun(completed, workspace), [{
      id: "workspace-commands",
      type: "custom",
      label: "Workspace Commands",
      values: { data: { version: 1 } },
    }])
  })
})
