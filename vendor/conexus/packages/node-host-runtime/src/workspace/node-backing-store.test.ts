import assert from 'node:assert/strict'
import { promises as fsp } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { NodeBackingStore, type BackedWorkspaceNode } from './node-backing-store.js'

async function temporaryRoot(prefix: string): Promise<string> {
  return fsp.mkdtemp(join(tmpdir(), prefix))
}

test('NodeBackingStore materializes and hydrates the shared node content format', async () => {
  const root = await temporaryRoot('conexus-node-backings-')
  try {
    const store = new NodeBackingStore({ root })
    const nodes: BackedWorkspaceNode[] = [{
      id: 'harness-1',
      type: 'harness',
      data: { label: 'Research', summary: 'Nested workspace' }
    }, {
      id: 'agent-1',
      type: 'agent',
      parentId: 'harness-1',
      data: {
        label: 'Writer',
        objective: 'Create a report',
        systemPrompt: 'Use the available evidence.',
        toolNames: ['web_search'],
        _connectedNodeRevisions: { version: 1, entries: { obsolete: true } },
        messages: [{ role: 'user', content: 'Keep this projection inline.' }]
      }
    }, {
      id: 'document-1',
      type: 'document',
      parentId: 'harness-1',
      data: { label: 'Report', content: '# Result' }
    }, {
      id: 'tool-1',
      type: 'tool',
      data: { label: 'Lookup', runtime: 'javascript', code: 'return { ok: true }' }
    }]

    const materialized = await store.materializeNodes(nodes) as BackedWorkspaceNode[]
    const agent = materialized.find((node) => node.id === 'agent-1')!
    const document = materialized.find((node) => node.id === 'document-1')!
    const tool = materialized.find((node) => node.id === 'tool-1')!
    assert.equal(agent.data?.objective, undefined)
    assert.equal(agent.data?._connectedNodeRevisions, undefined)
    assert.deepEqual(agent.data?.messages, nodes[1]?.data?.messages)
    assert.match(String(agent.data?.backingPath), /^workspace\/harnesses\/.+\/agents\/.+\.agent\.json$/)
    assert.equal(document.data?.content, undefined)
    assert.match(String(document.data?.backingPath), /^workspace\/harnesses\/.+\/documents\/.+\.md$/)
    assert.equal(tool.data?.code, undefined)
    assert.match(String(tool.data?.backingPath), /^workspace\/tools\/.+\.tool\.json$/)

    const hydrated = await store.hydrateNodes(materialized) as BackedWorkspaceNode[]
    assert.equal(hydrated.find((node) => node.id === 'agent-1')?.data?.objective, 'Create a report')
    assert.equal(hydrated.find((node) => node.id === 'agent-1')?.data?._connectedNodeRevisions, undefined)
    assert.equal(hydrated.find((node) => node.id === 'document-1')?.data?.content, '# Result')
    assert.equal(hydrated.find((node) => node.id === 'tool-1')?.data?.code, 'return { ok: true }')

    const documentPath = join(root, String(document.data?.backingPath))
    await fsp.writeFile(documentPath, '# Edited outside the Canvas', 'utf8')
    const authoritative = await store.hydrateNode({
      ...document,
      data: { ...document.data, content: '# Stale inline value' }
    })
    assert.equal(authoritative.data?.content, '# Edited outside the Canvas')
  } finally {
    await fsp.rm(root, { recursive: true, force: true })
  }
})

test('NodeBackingStore prunes a replaced backing and confines imported assets to its isolated root', async () => {
  const projectRoot = await temporaryRoot('conexus-node-backing-source-')
  try {
    await fsp.writeFile(join(projectRoot, 'source.png'), Buffer.from([1, 2, 3]))
    const accountRoot = join(projectRoot, '.conexus', 'history', 'accounts', 'account', 'app', 'workspace')
    const store = new NodeBackingStore({
      root: accountRoot,
      directory: 'content',
      sourceRoot: projectRoot,
      logicalSourceRoot: '/project'
    })
    const first = await store.materializeNodes([{
      id: 'shared-id',
      type: 'note',
      data: { label: 'Draft', content: 'old content' }
    }, {
      id: 'image-1',
      type: 'image',
      data: { label: 'Image', path: '/project/source.png' }
    }]) as BackedWorkspaceNode[]
    const oldPath = join(accountRoot, String(first[0]?.data?.backingPath))
    assert.equal(await fsp.readFile(oldPath, 'utf8'), 'old content')
    assert.equal(await fsp.readFile(
      join(accountRoot, String(first[1]?.data?.backingPath))
    ).then((value) => value.toString('hex')), '010203')

    const next = await store.materializeNodes([{
      id: 'shared-id',
      type: 'agent',
      data: { label: 'Draft', objective: 'replacement' }
    }]) as BackedWorkspaceNode[]
    await store.prune(first, next)
    await assert.rejects(fsp.access(oldPath), (error: unknown) =>
      (error as NodeJS.ErrnoException).code === 'ENOENT')
    assert.match(String(next[0]?.data?.backingPath), /^content\/agents\/.+\.agent\.json$/)
  } finally {
    await fsp.rm(projectRoot, { recursive: true, force: true })
  }
})
