import { access, cp, mkdir, readFile, rm, writeFile } from "node:fs/promises"
import { relative, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"

const repositoryRoot = resolve(fileURLToPath(new URL("..", import.meta.url)))
const sourceRoot = resolve(process.env.ALPHALAB_SOURCE_ROOT || repositoryRoot)
const projectRoot = resolve(process.env.CONEXUS_PROJECT_ROOT || sourceRoot)
const canvasPath = resolve(projectRoot, ".conexus", "canvas.json")
const bundlePath = "integrations/conexus/alphalab-research-agent"
const stagedBundlePath = "workspace/harnesses/AlphaLab-Research-Agent-v1"
const sourceBundlePath = resolve(sourceRoot, bundlePath)
const stagedBundleAbsolutePath = resolve(projectRoot, stagedBundlePath)
const harnessId = "alphalab-research-harness-v1"
const agentId = "alphalab-research-agent-v1"
const notebookId = "alphalab-decision-notebook-v1"
const commandsId = "alphalab-workspace-commands-v1"
const resultId = "alphalab-workspace-result-v1"
const sdkSkillRelativePath = "skills/alphalab-sdk-v1/SKILL.md"
const agentRelativePath = "agents/AlphaLab-Research-Agent.agent.json"
const sdkSkillPlaceholder = "{{ALPHALAB_SDK_SKILL}}"

const toolNodes = [
  ["alphalab-tool-project-files-v1", "AlphaLab Project Files", "Project-Files.tool.json"],
  ["alphalab-tool-project-run-v1", "AlphaLab Project Run", "Project-Run.tool.json"],
]

await mkdir(resolve(projectRoot, ".conexus"), { recursive: true })
await access(canvasPath).catch(async () => {
  await writeFile(
    canvasPath,
    `${JSON.stringify({ nodes: [], edges: [], metadata: { savedAt: new Date().toISOString() } }, null, 2)}\n`,
    "utf8",
  )
})
await access(resolve(sourceRoot, bundlePath, "harness.json"))
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
const stagedAgentPath = resolve(stagedBundleAbsolutePath, agentRelativePath)
const stagedSkillPath = resolve(stagedBundleAbsolutePath, sdkSkillRelativePath)
const [agentSource, sdkSkill] = await Promise.all([
  readFile(stagedAgentPath, "utf8"),
  readFile(stagedSkillPath, "utf8"),
])
const agent = JSON.parse(agentSource)
if (!agent.systemPrompt?.includes(sdkSkillPlaceholder)) {
  throw new Error(`AlphaLab Agent prompt is missing ${sdkSkillPlaceholder}`)
}
agent.systemPrompt = agent.systemPrompt.replace(sdkSkillPlaceholder, sdkSkill.trim())
await writeFile(stagedAgentPath, `${JSON.stringify(agent, null, 2)}\n`, "utf8")

const ownedNodeIds = new Set([
  harnessId,
  agentId,
  notebookId,
  commandsId,
  resultId,
  ...toolNodes.map(([id]) => id),
])
const obsoleteNodeIds = new Set([
  "alphalab-research-context-v1",
  "alphalab-tool-workspace-context-v1",
  "alphalab-tool-research-project-v1",
  "alphalab-tool-data-recipe-v1",
  "alphalab-tool-data-sync-job-v1",
  "alphalab-tool-data-query-v1",
  "alphalab-tool-edit-strategy-source-v1",
  "alphalab-tool-evaluate-strategy-factor-v1",
  "alphalab-tool-preview-strategy-v1",
  "alphalab-tool-validation-source-v1",
  "alphalab-tool-backtest-v1",
  "alphalab-tool-analyze-backtest-v1",
  "alphalab-tool-strategy-v1",
  "alphalab-tool-research-strategy-v1",
  "alphalab-tool-manage-strategy-v1",
  "alphalab-tool-manage-research-run-v1",
  "alphalab-tool-generate-signal-v1",
  "alphalab-tool-pipeline-project-v1",
  "alphalab-tool-manage-pipeline-v1",
  "alphalab-tool-preview-pipeline-v1",
  "alphalab-tool-run-python-lab-v1",
  "alphalab-tool-promote-python-lab-v1",
  "alphalab-tool-factor-library-v1",
  "alphalab-tool-evaluate-factor-v1",
  "alphalab-tool-strategy-project-v1",
  "alphalab-tool-data-plan-sync-v1",
  "alphalab-tool-data-run-sync-v1",
  "alphalab-tool-paper-state-v1",
  "alphalab-tool-preview-paper-rebalance-v1",
  "alphalab-tool-execute-paper-rebalance-v1",
  "alphalab-tool-submit-paper-order-v1",
  "alphalab-tool-manage-research-project-v1",
  "alphalab-tool-market-bars-v1",
  "alphalab-tool-fundamentals-v1",
  "alphalab-tool-factor-returns-v1",
  "alphalab-tool-evaluate-market-risk-factor-v1",
  "alphalab-tool-run-backtest-v1",
  "alphalab-tool-save-report-v1",
  "alphalab-tool-reports-v1",
  "alphalab-research-document-v1",
  "alphalab-tool-data-catalog-v1",
  "alphalab-tool-data-status-v1",
  "alphalab-tool-data-validate-v1",
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
    height: 1450,
    style: { width: 1600, height: 1450 },
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
    description: "Descriptor for the report Document changed by the current run and optional attachments.",
    backingPath: `${stagedBundlePath}/data/Workspace-Result.custom.json`,
  }),
  child(commandsId, "custom", { x: 1208, y: 280 }, {
    label: "Workspace Commands",
    description: "Allowlisted commands consumed by the AlphaLab frontend.",
    backingPath: `${stagedBundlePath}/data/Workspace-Commands.custom.json`,
  }, { width: 344, height: 104 }),
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
