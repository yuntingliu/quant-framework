// Runnable end-to-end demo over real HTTP.
//
//   node examples/conexus/minimal-agent/main.mjs
//
// Starts a scripted OpenAI-compatible endpoint on loopback, starts the local
// Host, creates a run, follows its SSE stream, and prints the note the Agent
// saved. No account, no credential, no network egress.
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { startMinimalHost, SLUG } from './host.mjs'
import { startModelEndpoint, toolCall } from './doubles/scripted-model.mjs'
import { TOOL_NODE_ID, EXPOSURE_ID } from './harness.mjs'

const VALUES = [0.021, -0.004, 0.013, 0.008, -0.011]

// The script is the whole "model". Turn 3 reads the tool result the Host fed
// back, so the saved note contains real computed numbers, not invented ones.
const script = [
  () => toolCall('use', { node_id: TOOL_NODE_ID, capability: 'tool.invoke', input: { values: VALUES } }),
  (request) => {
    const observed = request.messages.findLast((message) => message.role === 'tool')
    const stats = JSON.parse(observed.content).output
    return toolCall('create', { nodes: [{
      type: 'note',
      label: 'Series summary',
      description: 'Minimal example output',
      content: `# Series summary\n\ncount=${stats.count} mean=${stats.mean} min=${stats.min} max=${stats.max}`,
    }] })
  },
  () => toolCall('complete', { status: 'done', summary: 'Summarized the series and saved a note.' }),
]

const projectRoot = await mkdtemp(join(tmpdir(), 'conexus-minimal-'))
const model = await startModelEndpoint(script)
let host

try {
  host = await startMinimalHost({ projectRoot, model: model.model, baseUrl: model.baseUrl })
  await host.app.listen({ host: '127.0.0.1', port: 0 })
  const origin = host.app.listeningOrigin
  const workspaceAuth = { authorization: `Bearer ${host.token}` }
  console.log(`local Host: ${origin}`)

  const created = await fetch(`${origin}/api/public/harnesses/${SLUG}/runs`, {
    method: 'POST',
    headers: { ...workspaceAuth, 'content-type': 'application/json' },
    body: JSON.stringify({ exposureId: EXPOSURE_ID, input: { request: 'Summarize the sample return series.' } }),
  })
  if (created.status !== 202) throw new Error(`run rejected: ${created.status} ${await created.text()}`)
  const { run, accessToken } = await created.json()
  console.log(`run ${run.id} queued`)

  // The run token is separate from the workspace token and is issued per run.
  const events = await fetch(`${origin}/api/public/runs/${run.id}/events`, {
    headers: { authorization: `Bearer ${accessToken}`, accept: 'text/event-stream' },
  })
  for await (const chunk of events.body) {
    for (const line of Buffer.from(chunk).toString('utf8').split('\n')) {
      if (!line.startsWith('data: ')) continue
      const event = JSON.parse(line.slice(6))
      console.log(`  [${event.sequence}] ${event.type} (${event.status})`)
    }
  }

  const finished = await (await fetch(`${origin}/api/public/runs/${run.id}`, {
    headers: { authorization: `Bearer ${accessToken}` },
  })).json()
  console.log(`status: ${finished.run.status}`)
  console.log(`summary: ${finished.run.summary}`)

  const { workspace } = await (await fetch(`${origin}/api/public/harnesses/${SLUG}/workspace`, {
    headers: workspaceAuth,
  })).json()
  const note = workspace.nodes.find((node) => node.label === 'Series summary')
  console.log(`\n${note.values.content}`)
  if (finished.run.status !== 'completed') process.exitCode = 1
} finally {
  await host?.close()
  await model.close()
  await rm(projectRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
}
