import assert from 'node:assert/strict'
import { promises as fsp } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { PROJECT_NODE_HISTORY_WORKSPACE_ID } from '@conexus/runtime-protocol'
import { captureCanvasAgentObservation } from '@conexus/runtime-core'
import { JsonNodeHistoryStore, stripNodeHistoryProjections } from './node-history-store.js'

const WORKSPACE_ID = 'workspace-a'

async function temporaryProject(): Promise<string> {
  return fsp.mkdtemp(join(tmpdir(), 'conexus-node-history-'))
}

test('stores sessions and immutable run facts outside the Canvas projection', async (context) => {
  const projectRoot = await temporaryProject()
  context.after(() => fsp.rm(projectRoot, { recursive: true, force: true }))
  const dates = [
    '2026-07-30T01:00:00.000Z',
    '2026-07-30T01:01:00.000Z',
    '2026-07-30T01:02:00.000Z'
  ]
  const store = new JsonNodeHistoryStore(
    projectRoot,
    () => new Date(dates.shift() ?? '2026-07-30T01:03:00.000Z')
  )

  const session = await store.createSession({
    workspaceId: WORKSPACE_ID,
    nodeId: 'agent-1',
    nodeType: 'agent',
    title: 'First chat'
  })
  const canvasObservation = captureCanvasAgentObservation([
    { id: 'agent-1', type: 'agent', data: { label: 'Agent' } },
    { id: 'note-1', type: 'note', data: { label: 'Brief', content: 'Observed' } }
  ], [{ id: 'edge-1', source: 'agent-1', target: 'note-1', relation: 'reads' }], 'agent-1')
  await store.saveSession({
    workspaceId: WORKSPACE_ID,
    nodeId: 'agent-1',
    nodeType: 'agent',
    sessionId: session.id,
    canvasObservation,
    messages: [
      { role: 'user', content: 'Build it' },
      { role: 'assistant', content: 'Done' }
    ],
    run: {
      id: 'run-1',
      status: 'done',
      startedAt: '2026-07-30T01:00:10.000Z',
      completedAt: '2026-07-30T01:00:20.000Z',
      input: { prompt: 'Build it' },
      output: { summary: 'Done' }
    }
  })
  await store.saveSession({
    workspaceId: WORKSPACE_ID,
    nodeId: 'agent-1',
    nodeType: 'agent',
    sessionId: session.id,
    messages: [
      { role: 'user', content: 'Build it' },
      { role: 'assistant', content: 'Done' }
    ]
  })

  const saved = await store.getSession(WORKSPACE_ID, 'agent-1', session.id)
  assert.equal(saved?.title, 'First chat')
  assert.equal(saved?.messageCount, 2)
  assert.equal(saved?.messages[0]?.content, 'Build it')
  assert.equal(saved?.messages[0]?.visibility, 'public')
  assert.deepEqual(saved?.canvasObservation, canvasObservation)
  assert.equal('canvasObservation' in (await store.snapshot(WORKSPACE_ID, 'agent-1')).sessions[0]!, false)
  assert.equal((await store.snapshot(WORKSPACE_ID, 'agent-1')).runs[0]?.id, 'run-1')

  await store.saveRun({
    id: 'tool-run-1',
    workspaceId: WORKSPACE_ID,
    nodeId: 'tool-1',
    nodeType: 'tool',
    status: 'running',
    startedAt: '2026-07-30T01:00:30.000Z',
    input: { value: 1 }
  })
  await store.saveRun({
    id: 'tool-run-1',
    workspaceId: WORKSPACE_ID,
    nodeId: 'tool-1',
    nodeType: 'tool',
    status: 'done',
    completedAt: '2026-07-30T01:00:31.000Z',
    output: { value: 2 }
  })
  const toolRun = (await store.snapshot(WORKSPACE_ID, 'tool-1')).runs[0]
  assert.equal(toolRun?.status, 'done')
  assert.deepEqual(toolRun?.input, { value: 1 })
  assert.deepEqual(toolRun?.output, { value: 2 })

  const canvas = [{
    id: 'agent-1',
    type: 'agent',
    data: { label: 'Agent', messages: [{ role: 'user', content: 'transient' }], historySessionId: session.id }
  }]
  assert.deepEqual(stripNodeHistoryProjections(canvas), [{
    id: 'agent-1',
    type: 'agent',
    data: { label: 'Agent' }
  }])
  assert.equal(await store.deleteNode(WORKSPACE_ID, 'agent-1'), true)
  assert.equal((await store.snapshot(WORKSPACE_ID, 'agent-1')).sessions.length, 0)
})

test('infers only default conversation titles and preserves explicit renames', async (context) => {
  const projectRoot = await temporaryProject()
  context.after(() => fsp.rm(projectRoot, { recursive: true, force: true }))
  const store = new JsonNodeHistoryStore(projectRoot, () => new Date('2026-07-30T01:00:00.000Z'))
  const session = await store.createSession({
    workspaceId: WORKSPACE_ID,
    nodeId: 'agent-1',
    nodeType: 'agent',
    title: 'New conversation'
  })

  const inferred = await store.saveSession({
    workspaceId: WORKSPACE_ID,
    nodeId: 'agent-1',
    nodeType: 'agent',
    sessionId: session.id,
    messages: [{ role: 'user', content: 'Design the release plan' }]
  })
  assert.equal(inferred.title, 'Design the release plan')

  const renamed = await store.saveSession({
    workspaceId: WORKSPACE_ID,
    nodeId: 'agent-1',
    nodeType: 'agent',
    sessionId: session.id,
    title: 'Launch plan',
    messages: [{ role: 'user', content: 'Design the release plan' }]
  })
  const preserved = await store.saveSession({
    workspaceId: WORKSPACE_ID,
    nodeId: 'agent-1',
    nodeType: 'agent',
    sessionId: session.id,
    messages: [
      { role: 'user', content: 'Design the release plan' },
      { role: 'assistant', content: 'Ready' }
    ]
  })

  assert.equal(renamed.title, 'Launch plan')
  assert.equal(preserved.title, 'Launch plan')
})

test('imports legacy projections once and projects the selected latest session', async (context) => {
  const projectRoot = await temporaryProject()
  context.after(() => fsp.rm(projectRoot, { recursive: true, force: true }))
  const store = new JsonNodeHistoryStore(projectRoot, () => new Date('2026-07-30T02:00:00.000Z'))
  const legacy = [
    {
      id: 'agent-legacy',
      type: 'agent',
      data: {
        label: 'Legacy',
        messages: [
          { role: 'user', content: 'Old request' },
          { role: 'assistant', content: 'Old answer' }
        ]
      }
    },
    {
      id: 'cli-legacy',
      type: 'cli',
      data: {
        history: [{
          id: 'command-1',
          command: 'npm test',
          stdout: 'ok',
          exitCode: 0,
          startedAt: '2026-07-30T01:59:00.000Z',
          status: 'completed'
        }]
      }
    }
  ]

  await store.captureCanvasNodes(legacy)
  await store.captureCanvasNodes(legacy)

  const agentSnapshot = await store.snapshot(PROJECT_NODE_HISTORY_WORKSPACE_ID, 'agent-legacy')
  const cliSnapshot = await store.snapshot(PROJECT_NODE_HISTORY_WORKSPACE_ID, 'cli-legacy')
  assert.equal(agentSnapshot.sessions.length, 1)
  assert.equal(agentSnapshot.sessions[0]?.messageCount, 2)
  assert.equal(cliSnapshot.runs.length, 1)

  await store.saveRun({
    id: 'projection-not-a-command',
    workspaceId: PROJECT_NODE_HISTORY_WORKSPACE_ID,
    nodeId: 'cli-legacy',
    nodeType: 'cli',
    status: 'done',
    startedAt: '2026-07-30T01:59:30.000Z',
    output: { label: 'CLI node snapshot', cwd: '/workspace' }
  })

  const projected = await store.projectCanvasNodes(stripNodeHistoryProjections(legacy)) as Array<{
    id: string
    type: string
    data: Record<string, any>
  }>
  assert.equal(projected[0]?.data.historySessionId, agentSnapshot.sessions[0]?.id)
  assert.equal(projected[0]?.data.messages?.[1]?.content, 'Old answer')
  assert.equal(projected[1]?.data.history?.length, 1)
  assert.equal(projected[1]?.data.history?.[0]?.stdout, 'ok')
  assert.equal(projected[1]?.data.history?.[0]?.cwd, '')
  assert.equal(projected[1]?.data.history?.[0]?.stderr, '')
  assert.equal(projected[1]?.data.history?.[0]?.exitCode, 0)
})

test('projects only complete CLI command records into Canvas history', async (context) => {
  const projectRoot = await temporaryProject()
  context.after(() => fsp.rm(projectRoot, { recursive: true, force: true }))
  const store = new JsonNodeHistoryStore(projectRoot, () => new Date('2026-07-30T02:00:00.000Z'))
  const cliNode = {
    id: 'cli-1',
    type: 'cli',
    data: {
      label: 'Terminal',
      shell: 'bash',
      status: 'idle',
      lastExitCode: 0,
      cwd: '/project'
    }
  }

  await store.captureCanvasNodes([cliNode])
  assert.equal((await store.snapshot(PROJECT_NODE_HISTORY_WORKSPACE_ID, cliNode.id)).runs.length, 0)

  await store.saveRun({
    id: 'invalid-cli-state',
    workspaceId: PROJECT_NODE_HISTORY_WORKSPACE_ID,
    nodeId: cliNode.id,
    nodeType: 'cli',
    status: 'done',
    startedAt: '2026-07-30T01:58:00.000Z',
    output: cliNode.data
  })
  await store.captureCanvasNodes([{
    ...cliNode,
    data: {
      ...cliNode.data,
      history: [{
        id: 'command-1',
        command: 'pwd',
        shell: 'bash',
        cwd: '/project',
        stdout: '/project',
        stderr: '',
        exitCode: 0
      }]
    }
  }])

  const projected = await store.projectCanvasNodes([cliNode]) as Array<{
    data: { history?: Array<Record<string, unknown>> }
  }>
  assert.deepEqual(projected[0]?.data.history, [{
    id: 'command-1',
    command: 'pwd',
    shell: 'bash',
    cwd: '/project',
    stdout: '/project',
    stderr: '',
    exitCode: 0
  }])
})

test('records content revisions only when content changes', async (context) => {
  const projectRoot = await temporaryProject()
  context.after(() => fsp.rm(projectRoot, { recursive: true, force: true }))
  let minute = 0
  const store = new JsonNodeHistoryStore(
    projectRoot,
    () => new Date(`2026-07-30T03:${String(minute++).padStart(2, '0')}:00.000Z`)
  )

  await store.captureCanvasNodes([{
    id: 'note-1',
    type: 'note',
    data: {
      snapshots: [{
        id: 'legacy-revision',
        content: 'Legacy version',
        createdAt: '2026-07-30T02:30:00.000Z'
      }],
      content: 'Version one'
    }
  }])
  await store.captureCanvasNodes([{ id: 'note-1', type: 'note', data: { content: 'Version one' } }])
  await store.captureCanvasNodes([{ id: 'note-1', type: 'note', data: { content: 'Version two' } }])

  const revisions = (await store.snapshot(PROJECT_NODE_HISTORY_WORKSPACE_ID, 'note-1')).revisions
  assert.equal(revisions.length, 3)
  assert.equal(revisions[0]?.value, 'Version two')
  assert.equal(revisions[0]?.parentRevisionId, revisions[1]?.id)
  assert.equal(revisions[2]?.id, 'legacy-revision')
})

test('scopes identical node ids to independent Harness workspaces', async (context) => {
  const projectRoot = await temporaryProject()
  context.after(() => fsp.rm(projectRoot, { recursive: true, force: true }))
  const store = new JsonNodeHistoryStore(projectRoot)
  const first = await store.createSession({
    workspaceId: 'workspace-one',
    nodeId: 'shared-agent',
    nodeType: 'agent',
    title: 'First workspace'
  })
  await store.saveSession({
    workspaceId: 'workspace-one',
    nodeId: 'shared-agent',
    nodeType: 'agent',
    sessionId: first.id,
    messages: [{ role: 'user', content: 'Private to workspace one.' }]
  })
  const second = await store.createSession({
    workspaceId: 'workspace-two',
    nodeId: 'shared-agent',
    nodeType: 'agent',
    title: 'Second workspace'
  })
  await store.saveSession({
    workspaceId: 'workspace-two',
    nodeId: 'shared-agent',
    nodeType: 'agent',
    sessionId: second.id,
    messages: [{ role: 'user', content: 'Private to workspace two.' }]
  })

  const firstSnapshot = await store.snapshot('workspace-one', 'shared-agent')
  const secondSnapshot = await store.snapshot('workspace-two', 'shared-agent')
  assert.deepEqual(firstSnapshot.sessions.map((session) => session.title), ['First workspace'])
  assert.deepEqual(secondSnapshot.sessions.map((session) => session.title), ['Second workspace'])
})

test('migrates the v1 project node history into the explicit project workspace', async (context) => {
  const projectRoot = await temporaryProject()
  context.after(() => fsp.rm(projectRoot, { recursive: true, force: true }))
  const historyDirectory = join(projectRoot, '.conexus', 'history')
  await fsp.mkdir(historyDirectory, { recursive: true })
  await fsp.writeFile(join(historyDirectory, 'node-history.v1.json'), JSON.stringify({
    schema: 'conexus.node-history',
    version: 1,
    sessions: [{
      id: 'legacy-session',
      nodeId: 'legacy-agent',
      nodeType: 'agent',
      title: 'Legacy session',
      createdAt: '2026-07-30T00:00:00.000Z',
      updatedAt: '2026-07-30T00:00:00.000Z',
      messageCount: 1,
      messages: [{
        id: 'legacy-message',
        nodeId: 'legacy-agent',
        sessionId: 'legacy-session',
        visibility: 'public',
        createdAt: '2026-07-30T00:00:00.000Z',
        role: 'user',
        content: 'Preserve me.'
      }]
    }],
    runs: [],
    revisions: []
  }))

  const store = new JsonNodeHistoryStore(projectRoot)
  await store.initialize()
  const snapshot = await store.snapshot(PROJECT_NODE_HISTORY_WORKSPACE_ID, 'legacy-agent')
  assert.equal(snapshot.sessions[0]?.workspaceId, PROJECT_NODE_HISTORY_WORKSPACE_ID)
  assert.equal((await store.getSession(
    PROJECT_NODE_HISTORY_WORKSPACE_ID,
    'legacy-agent',
    'legacy-session'
  ))?.messages[0]?.workspaceId, PROJECT_NODE_HISTORY_WORKSPACE_ID)
  const migrated = JSON.parse(await fsp.readFile(
    join(historyDirectory, 'node-history.v2.json'),
    'utf8'
  )) as { version: number }
  assert.equal(migrated.version, 2)
})
