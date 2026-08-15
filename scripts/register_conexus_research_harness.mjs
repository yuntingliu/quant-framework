import { access, cp, mkdir, readFile, rm, writeFile } from "node:fs/promises"
import { relative, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"

const projectRoot = resolve(fileURLToPath(new URL("..", import.meta.url)))
const canvasPath = resolve(projectRoot, ".conexus", "canvas.json")
const bundlePath = "integrations/conexus/alphalab-research-agent"
const stagedBundlePath = "workspace/harnesses/AlphaLab-Research-Agent-v1"
const sourceBundlePath = resolve(projectRoot, bundlePath)
const stagedBundleAbsolutePath = resolve(projectRoot, stagedBundlePath)
const harnessId = "alphalab-research-harness-v1"
const agentId = "alphalab-research-agent-v1"
const notebookId = "alphalab-decision-notebook-v1"
const commandsId = "alphalab-workspace-commands-v1"
const resultId = "alphalab-workspace-result-v1"
const documentId = "alphalab-research-document-v1"

const toolNodes = [
  ["alphalab-tool-workspace-context-v1", "AlphaLab Workspace Context", "Get-Workspace-Context.tool.json"],
  ["alphalab-tool-pipeline-project-v1", "AlphaLab Pipeline Project", "Get-Pipeline-Project.tool.json"],
  ["alphalab-tool-manage-pipeline-v1", "Manage AlphaLab Pipeline", "Manage-Pipeline.tool.json"],
  ["alphalab-tool-preview-pipeline-v1", "Preview AlphaLab Pipeline", "Preview-Pipeline.tool.json"],
  ["alphalab-tool-market-bars-v1", "AlphaLab Market Bars", "Get-Market-Bars.tool.json"],
  ["alphalab-tool-fundamentals-v1", "AlphaLab Fundamentals", "Get-Fundamentals.tool.json"],
  ["alphalab-tool-factor-returns-v1", "AlphaLab Factor Returns", "Get-Factor-Returns.tool.json"],
  ["alphalab-tool-factor-library-v1", "AlphaLab Factor Library", "Get-Factor-Library.tool.json"],
  ["alphalab-tool-evaluate-factor-v1", "Evaluate AlphaLab Factor", "Evaluate-Factor.tool.json"],
  ["alphalab-tool-evaluate-market-risk-factor-v1", "Evaluate AlphaLab Market Risk Factor", "Evaluate-Market-Risk-Factor.tool.json"],
  ["alphalab-tool-backtest-v1", "AlphaLab Backtest", "Get-Backtest.tool.json"],
  ["alphalab-tool-analyze-backtest-v1", "Analyze AlphaLab Backtest", "Analyze-Backtest.tool.json"],
  ["alphalab-tool-run-backtest-v1", "Run AlphaLab Backtest", "Run-Backtest.tool.json"],
  ["alphalab-tool-reports-v1", "AlphaLab Research Reports", "Get-Reports.tool.json"],
  ["alphalab-tool-paper-state-v1", "AlphaLab Paper State", "Get-Paper-State.tool.json"],
  ["alphalab-tool-preview-paper-rebalance-v1", "Preview AlphaLab Paper Rebalance", "Preview-Paper-Rebalance.tool.json"],
  ["alphalab-tool-execute-paper-rebalance-v1", "Execute AlphaLab Paper Rebalance", "Execute-Paper-Rebalance.tool.json"],
  ["alphalab-tool-submit-paper-order-v1", "Submit AlphaLab Paper Order", "Submit-Paper-Order.tool.json"],
  ["alphalab-tool-data-catalog-v1", "AlphaLab Data Catalog", "Data-Catalog.tool.json"],
  ["alphalab-tool-data-status-v1", "AlphaLab Data Status", "Data-Status.tool.json"],
  ["alphalab-tool-data-plan-sync-v1", "Plan AlphaLab Data Sync", "Plan-Data-Sync.tool.json"],
  ["alphalab-tool-data-run-sync-v1", "Run AlphaLab Data Sync", "Run-Data-Sync.tool.json"],
  ["alphalab-tool-data-validate-v1", "Validate AlphaLab Data", "Validate-Data.tool.json"],
  ["alphalab-tool-data-query-v1", "Query AlphaLab Runtime Data", "Query-Runtime-Data.tool.json"],
  ["alphalab-tool-data-sync-job-v1", "Manage AlphaLab Data Sync Job", "Manage-Data-Sync-Job.tool.json"],
]

await mkdir(resolve(projectRoot, ".conexus"), { recursive: true })
await access(canvasPath).catch(async () => {
  await writeFile(
    canvasPath,
    `${JSON.stringify({ nodes: [], edges: [], metadata: { savedAt: new Date().toISOString() } }, null, 2)}\n`,
    "utf8",
  )
})
await access(resolve(projectRoot, bundlePath, "harness.json"))
const workspaceRoot = resolve(projectRoot, "workspace")
const stagedRelative = relative(workspaceRoot, stagedBundleAbsolutePath)
if (
  !stagedRelative
  || stagedRelative === ".."
  || stagedRelative.startsWith(`..${sep}`)
) {
  throw new Error(`Conexus staging path escapes the project workspace: ${stagedBundleAbsolutePath}`)
}
await mkdir(resolve(stagedBundleAbsolutePath, ".."), { recursive: true })
await rm(stagedBundleAbsolutePath, { recursive: true, force: true })
await cp(sourceBundlePath, stagedBundleAbsolutePath, { recursive: true, force: true })

const ownedNodeIds = new Set([
  harnessId,
  agentId,
  notebookId,
  commandsId,
  resultId,
  documentId,
  ...toolNodes.map(([id]) => id),
])
const obsoleteNodeIds = new Set([
  "alphalab-research-context-v1",
  "alphalab-tool-strategy-v1",
  "alphalab-tool-research-strategy-v1",
  "alphalab-tool-manage-strategy-v1",
  "alphalab-tool-manage-research-run-v1",
  "alphalab-tool-generate-signal-v1",
])
const ownedEdgePrefix = "edge-alphalab-research-v1-"
const canvas = JSON.parse(await readFile(canvasPath, "utf8"))

const child = (id, type, position, data, size = { width: 344, height: 192 }) => ({
  id,
  type,
  position,
  data: { ...data, harnessNodeId: harnessId },
  parentId: harnessId,
  extent: "parent",
  width: size.width,
  height: size.height,
  style: { width: size.width, height: size.height },
})

const nodes = [
  {
    id: harnessId,
    type: "harness",
    position: { x: 3600, y: 96 },
    data: {
      label: "AlphaLab Research Agent",
      description: "Optional published research Harness backed by AlphaLab FastAPI.",
      purpose: "primary",
      hostingSlug: "alphalab-research-agent",
      hostingAccessPolicy: "anonymous",
      hostingBillingPolicy: "publisher",
      backingPath: `${stagedBundlePath}/harness.json`,
    },
    width: 1600,
    height: 2600,
    style: { width: 1600, height: 2600 },
  },
  child(agentId, "agent", { x: 32, y: 72 }, {
    label: "AlphaLab Research Agent",
    description: "Published entry Agent for AlphaLab research conversations.",
    exposeInHarness: true,
    backingPath: `${stagedBundlePath}/agents/AlphaLab-Research-Agent.agent.json`,
  }, { width: 360, height: 264 }),
  child(notebookId, "custom", { x: 816, y: 72 }, {
    label: "Decision Notebook",
    description: "Structured output consumed by the AlphaLab right rail.",
    backingPath: `${stagedBundlePath}/data/Decision-Notebook.custom.json`,
  }),
  child(resultId, "custom", { x: 1208, y: 72 }, {
    label: "Workspace Result",
    description: "Request-bound descriptor and optional table attachment.",
    backingPath: `${stagedBundlePath}/data/Workspace-Result.custom.json`,
  }),
  child(commandsId, "custom", { x: 1208, y: 280 }, {
    label: "Workspace Commands",
    description: "Allowlisted commands consumed by the AlphaLab frontend.",
    backingPath: `${stagedBundlePath}/data/Workspace-Commands.custom.json`,
  }, { width: 344, height: 104 }),
  child(documentId, "note", { x: 1208, y: 416 }, {
    label: "Research Result Document",
    description: "Durable Markdown report rendered by the AlphaLab workspace.",
    format: "markdown",
    backingPath: `${stagedBundlePath}/documents/Research-Result.md`,
  }, { width: 344, height: 320 }),
  ...toolNodes.map(([id, label, file], index) => child(id, "tool", {
    x: 32 + (index % 3) * 392,
    y: 400 + Math.floor(index / 3) * 232,
  }, {
    label,
    backingPath: `${stagedBundlePath}/tools/${file}`,
  })),
]

const targets = [
  notebookId,
  resultId,
  commandsId,
  documentId,
  ...toolNodes.map(([id]) => id),
]
const edges = targets.map((target, index) => ({
  id: `${ownedEdgePrefix}${index + 1}`,
  source: agentId,
  target,
}))

canvas.nodes = [
  ...canvas.nodes.filter((node) => !ownedNodeIds.has(node.id) && !obsoleteNodeIds.has(node.id)),
  ...nodes,
]
canvas.edges = [...canvas.edges.filter((edge) => !edge.id.startsWith(ownedEdgePrefix)), ...edges]
canvas.metadata = { ...(canvas.metadata ?? {}), savedAt: new Date().toISOString() }
await writeFile(canvasPath, `${JSON.stringify(canvas, null, 2)}\n`, "utf8")

console.log(JSON.stringify({ canvasPath, harnessId, nodes: nodes.length, edges: edges.length }))
