import assert from "node:assert/strict"
import { test } from "node:test"

import { reportEvidence, reportFilterStatus, reportReferenceKey, reportReferenceLabel } from "../src/workspace/reportEvidence.ts"
import { parseWorkspaceReportNode, type AgentResearchResult } from "../src/workspace/researchResults.ts"

const result: AgentResearchResult = {
  id: "report-1", version: 1, reportId: "report-1", kind: "document", title: "Momentum",
  markdown: "# Momentum", sources: [], provenance: {
    projectId: "research",
    references: [
      { kind: "strategy_package", revision: 3 },
      { kind: "factor_evidence", id: "evidence-1" },
      { kind: "data_manifest" },
    ],
  },
}

test("report evidence parses canonical references and stable labels", () => {
  const parsed = reportEvidence(result)
  assert.equal(parsed.projectId, "research")
  assert.equal(parsed.references.length, 3)
  assert.equal(reportReferenceLabel(parsed.references[0], true), "策略包 v3")
  assert.equal(reportReferenceKey(parsed.references[1]), "factor_evidence:evidence-1:")
  assert.equal(reportFilterStatus(result), "manifest_unavailable")
})

test("missing references remain unlinked rather than verified", () => {
  assert.equal(reportFilterStatus({ ...result, provenance: {} }), "unlinked")
})

test("workspace report nodes preserve evidence metadata", () => {
  const parsed = parseWorkspaceReportNode({
    id: "report-1", type: "document", description: "AlphaLab quantitative report",
    label: "Momentum", updatedAt: "2026-01-01T00:00:00Z",
    values: { content: "# Momentum", data: { provenance: result.provenance } },
  })
  assert.ok(parsed)
  assert.equal(reportEvidence(parsed).projectId, "research")
  assert.equal(reportEvidence(parsed).references.length, 3)
})
