import { promises as fs } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = resolve(fileURLToPath(new URL('..', import.meta.url)))
const canvasPath = resolve(projectRoot, '.conexus', 'canvas.json')
const harnessId = 'alphalab-research-harness-v1'
const agentId = 'alphalab-research-agent-v1'
const contextId = 'alphalab-research-context-v1'
const notebookId = 'alphalab-decision-notebook-v1'
const commandsId = 'alphalab-workspace-commands-v1'
const resultId = 'alphalab-workspace-result-v1'
const documentId = 'alphalab-research-document-v1'
const harnessPath = 'workspace/harnesses/AlphaLab-Research-Agent-v1'

const toolNodes = [
  ['alphalab-tool-workspace-context-v1', 'AlphaLab Workspace Context', 'Get-Workspace-Context.tool.json'],
  ['alphalab-tool-strategy-v1', 'AlphaLab Strategy', 'Get-Strategy.tool.json'],
  ['alphalab-tool-market-bars-v1', 'AlphaLab Market Bars', 'Get-Market-Bars.tool.json'],
  ['alphalab-tool-backtest-v1', 'AlphaLab Backtest', 'Get-Backtest.tool.json'],
  ['alphalab-tool-run-backtest-v1', 'Run AlphaLab Backtest', 'Run-Backtest.tool.json'],
  ['alphalab-tool-generate-signal-v1', 'Generate AlphaLab Paper Signal', 'Generate-Signal.tool.json'],
]

const ownedNodeIds = new Set([harnessId, agentId, contextId, notebookId, commandsId, resultId, documentId, ...toolNodes.map(([id]) => id)])
const ownedEdgePrefix = 'edge-alphalab-research-v1-'
const canvas = JSON.parse(await fs.readFile(canvasPath, 'utf8'))

const child = (id, type, position, data, size = { width: 344, height: 192 }) => ({
  id,
  type,
  position,
  data: { ...data, harnessNodeId: harnessId },
  parentId: harnessId,
  extent: 'parent',
  width: size.width,
  height: size.height,
  style: { width: size.width, height: size.height },
})

const nodes = [
  {
    id: harnessId,
    type: 'harness',
    position: { x: 3600, y: 96 },
    data: {
      label: 'AlphaLab Research Agent',
      description: 'Published conversational research Harness backed by AlphaLab FastAPI.',
      purpose: 'primary',
      publicationSlug: 'alphalab-research-agent',
      publicationAccessPolicy: 'anonymous',
      publicationBillingPolicy: 'publisher',
      backingPath: `${harnessPath}/harness.json`,
    },
    width: 1600,
    height: 760,
    style: { width: 1600, height: 760 },
  },
  child(agentId, 'agent', { x: 32, y: 72 }, {
    label: 'AlphaLab Research Agent',
    description: 'Published entry Agent for AlphaLab research conversations.',
    showOnHarnessPreview: true,
    backingPath: `${harnessPath}/agents/AlphaLab-Research-Agent.agent.json`,
  }, { width: 360, height: 264 }),
  child(contextId, 'custom', { x: 424, y: 72 }, {
    label: 'Workspace Context',
    description: 'Structured context supplied by the AlphaLab workstation.',
    backingPath: `${harnessPath}/data/Workspace-Context.custom.json`,
  }),
  child(notebookId, 'custom', { x: 816, y: 72 }, {
    label: 'Decision Notebook',
    description: 'Structured output consumed by the AlphaLab right rail.',
    backingPath: `${harnessPath}/data/Decision-Notebook.custom.json`,
  }),
  child(resultId, 'custom', { x: 1208, y: 72 }, {
    label: 'Workspace Result',
    description: 'Request-bound descriptor and optional table attachment for the research document.',
    backingPath: `${harnessPath}/data/Workspace-Result.custom.json`,
  }),
  child(commandsId, 'custom', { x: 1208, y: 280 }, {
    label: 'Workspace Commands',
    description: 'Validated commands consumed by the AlphaLab frontend.',
    backingPath: `${harnessPath}/data/Workspace-Commands.custom.json`,
  }, { width: 344, height: 104 }),
  child(documentId, 'note', { x: 1208, y: 416 }, {
    label: 'Research Result Document',
    description: 'Durable Markdown report with safe static HTML rendered in the AlphaLab research workspace.',
    format: 'markdown',
    backingPath: `${harnessPath}/documents/Research-Result.md`,
  }, { width: 344, height: 320 }),
  ...toolNodes.map(([id, label, file], index) => child(id, 'tool', {
    x: 32 + (index % 3) * 392,
    y: 400 + Math.floor(index / 3) * 232,
  }, {
    label,
    backingPath: `${harnessPath}/tools/${file}`,
  })),
]

const targets = [contextId, notebookId, resultId, commandsId, documentId, ...toolNodes.map(([id]) => id)]
const edges = targets.map((target, index) => ({
  id: `${ownedEdgePrefix}${index + 1}`,
  source: agentId,
  target,
}))

canvas.nodes = [...canvas.nodes.filter((node) => !ownedNodeIds.has(node.id)), ...nodes]
canvas.edges = [...canvas.edges.filter((edge) => !edge.id.startsWith(ownedEdgePrefix)), ...edges]
canvas.metadata = { ...(canvas.metadata ?? {}), savedAt: new Date().toISOString() }
await fs.writeFile(canvasPath, `${JSON.stringify(canvas, null, 2)}\n`, 'utf8')

console.log(JSON.stringify({ canvasPath, harnessId, nodes: nodes.length, edges: edges.length }))
