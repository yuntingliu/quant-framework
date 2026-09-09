import { readFile, readdir } from 'node:fs/promises'
import { resolve } from 'node:path'

export async function researchCanvas(bundle, { model, slug = 'alphalab-research-agent' }) {
  const load = async (path) => JSON.parse(await readFile(resolve(bundle, path), 'utf8'))
  const harness = await load('harness.json')
  const harnessId = 'alphalab-research-harness-v1'
  const agentId = 'alphalab-research-agent-v1'
  const agent = await load('agents/AlphaLab-Research-Agent.agent.json')
  const nodes = [{ id: harnessId, type: 'harness', position: { x: 0, y: 0 }, data: {
    label: 'AlphaLab Research Agent', summary: harness.summary, template: harness.template,
    hostingSlug: slug, defaultExposureId: 'alphalab-research-agent',
  } }]
  const child = (id, type, data) => ({ id, type, parentId: harnessId, position: { x: 20, y: 100 }, data })
  nodes.push(child(agentId, 'agent', {
    ...agent, model, label: 'AlphaLab Research Agent',
    exposeInHarness: true, exposureId: 'alphalab-research-agent',
  }))
  for (const [id, label, file] of [
    ['alphalab-decision-notebook-v1', 'Decision Notebook', 'Decision-Notebook'],
    ['alphalab-workspace-result-v1', 'Workspace Result', 'Workspace-Result'],
    ['alphalab-workspace-commands-v1', 'Workspace Commands', 'Workspace-Commands'],
  ]) nodes.push(child(id, 'custom', { label, ...await load(`data/${file}.custom.json`) }))
  for (const file of (await readdir(resolve(bundle, 'tools'))).filter(file => file.endsWith('.tool.json')).sort()) {
    const tool = await load(`tools/${file}`)
    nodes.push(child(tool.toolName.replaceAll('_', '-'), 'tool', { label: tool.toolName, ...tool }))
  }
  return { nodes, edges: nodes.filter(node => ![harnessId, agentId].includes(node.id)).map((node, i) => ({
    id: `alphalab-edge-${i}`, source: agentId, target: node.id,
  })) }
}
