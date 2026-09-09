import assert from 'node:assert/strict'
import test from 'node:test'
import type { HarnessReleaseArtifact, RuntimeWorkspace } from '../index.js'
import {
  HARNESS_RELEASE_SCHEMA,
  HARNESS_RELEASE_SCHEMA_VERSION,
  HARNESS_RUNTIME_VERSION,
  HarnessContractError,
  bindHarnessReleaseExposure,
  compileHarnessRelease,
  harnessReleaseChecksum,
  instantiateHarnessWorkspace,
  resolveHarnessExposure,
  validateHarnessRelease
} from './release.js'
import { publicHarnessExposure } from '../harness/harness-exposures.js'

function releaseSource(): Record<string, unknown> {
  return {
    schema: 'conexus.harness',
    manifest: {
      id: 'test.node-exposure.v1',
      name: 'Node Exposure',
      description: 'Exercises the immutable release contract.',
      version: 1,
      exposures: [{
        id: 'agent',
        name: 'Agent',
        nodeId: 'agent',
        nodeType: 'agent',
        surfaces: ['agent_tool', 'api']
      }],
      permissions: [{ kind: 'model', required: true }],
      dependencies: [{ kind: 'model', name: 'test-model' }],
      capabilities: ['test'],
      triggers: ['test release'],
      tags: ['runtime-core']
    },
    graph: {
      nodes: [
        {
          id: 'instructions',
          type: 'note',
          position: { x: 40, y: 80 },
          width: 480,
          style: { selected: true },
          data: {
            label: 'Instructions',
            content: 'Explain the requested topic.',
            status: 'running',
            messages: [{ role: 'assistant', content: 'stale' }],
            _editorSelection: true,
            nested: {
              keep: true,
              lastError: 'static business value',
              inputSchema: { type: 'object', properties: { value: { type: 'string' } } }
            }
          }
        },
        {
          id: 'agent',
          type: 'agent',
          position: { x: 600, y: 80 },
          data: {
            label: 'Agent',
            exposeInHarness: true,
            systemPrompt: 'Process the current request.',
            task: 'Produce the requested result.',
            credentials: { secretRef: 'MODEL_CREDENTIALS' },
            authToken: '{{runtime_token}}',
            summary: 'stale summary',
            toolCounts: { complete: 2 }
          }
        },
        { id: 'result', type: 'custom', data: { label: 'Structured result' } }
      ],
      edges: [{
        id: 'instructions-to-agent',
        source: 'instructions',
        target: 'agent',
        selected: true,
        animated: true,
        data: { relationship: 'context', status: 'stale' }
      }]
    },
    schemas: { definitions: { shared: { type: 'string' } } },
    runtime: { policy: { maxAttempts: 1 }, timeoutMs: 30_000, isolation: 'none' },
    verification: { successCriteria: ['A result is produced.'] }
  }
}

function reverseObjectKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(reverseObjectKeys)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .reverse()
    .map(([key, nested]) => [key, reverseObjectKeys(nested)]))
}

function errorCode(error: unknown, code: string): boolean {
  return error instanceof HarnessContractError && error.code === code
}

test('compileHarnessRelease creates a canonical immutable artifact and strips mutable state', () => {
  const source = releaseSource()
  const release = compileHarnessRelease(source)
  assert.equal(release.schema, HARNESS_RELEASE_SCHEMA)
  assert.equal(release.schemaVersion, HARNESS_RELEASE_SCHEMA_VERSION)
  assert.equal(release.runtimeVersion, HARNESS_RUNTIME_VERSION)
  assert.equal(release.manifest.defaultExposureId, 'agent')
  assert.deepEqual(release.manifest.exposures[0], {
    id: 'agent', name: 'Agent', nodeId: 'agent', nodeType: 'agent', surfaces: ['agent_tool', 'api']
  })

  const instructions = release.graph.nodes.find((node) => node.id === 'instructions')!
  const agent = release.graph.nodes.find((node) => node.id === 'agent')!
  assert.equal('position' in instructions, false)
  assert.equal('status' in instructions.data, false)
  assert.equal('messages' in instructions.data, false)
  assert.equal('_editorSelection' in instructions.data, false)
  assert.equal('summary' in agent.data, false)
  assert.equal('toolCounts' in agent.data, false)
  assert.deepEqual(agent.data.credentials, { secretRef: 'MODEL_CREDENTIALS' })
  assert.equal(agent.data.authToken, '{{runtime_token}}')
  assert.equal('selected' in release.graph.edges[0]!, false)
  assert.equal('state' in release, false)
  assert.deepEqual(release.requirements.map(({ kind, name }) => `${kind}:${name}`), [
    'model:test-model', 'node:agent', 'node:custom', 'node:note'
  ])

  assert.equal(
    harnessReleaseChecksum(reverseObjectKeys(release) as HarnessReleaseArtifact),
    harnessReleaseChecksum(release)
  )
  const stateOnlyChange = releaseSource()
  const nodes = (stateOnlyChange.graph as { nodes: Array<{ data: Record<string, unknown> }> }).nodes
  nodes[0]!.data.status = 'failed'
  assert.equal(harnessReleaseChecksum(compileHarnessRelease(stateOnlyChange)), harnessReleaseChecksum(release))
  nodes[1]!.data.task = 'Different behavior.'
  assert.notEqual(harnessReleaseChecksum(compileHarnessRelease(stateOnlyChange)), harnessReleaseChecksum(release))
})

test('multiple capabilities require exposure selection and public descriptors hide internal node ids', () => {
  const source = releaseSource()
  const manifest = source.manifest as { exposures: Array<Record<string, unknown>> }
  const graph = source.graph as { nodes: Array<{ id: string; type: string; data: Record<string, unknown> }> }
  graph.nodes.push({ id: 'reviewer', type: 'agent', data: { label: 'Reviewer', exposeInHarness: true } })
  manifest.exposures.push({
    id: 'review', name: 'Review', nodeId: 'reviewer', nodeType: 'agent', surfaces: ['agent_tool', 'api']
  })
  const release = compileHarnessRelease(source)
  assert.throws(() => resolveHarnessExposure(release), (error) => errorCode(error, 'exposure_id_required'))
  const selected = bindHarnessReleaseExposure(release, 'review', 'agent_tool')
  assert.equal(selected.exposure.nodeId, 'reviewer')
  assert.equal(selected.release.manifest.exposures.length, 2)
  const descriptor = publicHarnessExposure(selected.exposure)
  assert.equal('nodeId' in descriptor, false)
  assert.deepEqual(descriptor, {
    id: 'review', name: 'Review', nodeType: 'agent', surfaces: ['agent_tool', 'api']
  })
})

test('instantiateHarnessWorkspace clones release or durable workspace without binding inputs', () => {
  const release = compileHarnessRelease(releaseSource())
  const workspace = instantiateHarnessWorkspace(release, 'agent')
  workspace.nodes.find((node) => node.id === 'agent')!.data.task = 'mutated'
  assert.equal(release.graph.nodes.find((node) => node.id === 'agent')!.data.task, 'Produce the requested result.')

  const durable: RuntimeWorkspace = structuredClone(workspace)
  durable.nodes.find((node) => node.id === 'result')!.data.content = { done: true }
  const resumed = instantiateHarnessWorkspace(release, 'agent', durable)
  assert.deepEqual(resumed.nodes.find((node) => node.id === 'result')!.data.content, { done: true })
  ;(resumed.nodes.find((node) => node.id === 'result')!.data.content as Record<string, unknown>).done = false
  assert.deepEqual(durable.nodes.find((node) => node.id === 'result')!.data.content, { done: true })

  const invalid = structuredClone(durable)
  invalid.nodes = invalid.nodes.filter((node) => node.id !== 'agent')
  assert.throws(
    () => instantiateHarnessWorkspace(release, 'agent', invalid),
    (error) => errorCode(error, 'invalid_instance_workspace')
  )
})

test('release compilation rejects obsolete Harness-owned IO without migration', () => {
  for (const field of ['inputs', 'outputs', 'inputSchema', 'outputSchema']) {
    const source = releaseSource()
    const exposure = (source.manifest as { exposures: Array<Record<string, unknown>> }).exposures[0]!
    exposure[field] = field.endsWith('Schema') ? { type: 'object' } : []
    assert.throws(
      () => compileHarnessRelease(source),
      (error) => errorCode(error, 'obsolete_harness_exposure_io')
    )
  }
  const source = releaseSource()
  ;(source.manifest as Record<string, unknown>).inputs = []
  assert.throws(() => compileHarnessRelease(source), (error) => errorCode(error, 'obsolete_harness_contract'))

  for (const field of ['artifacts', 'traces']) {
    const obsolete = releaseSource()
    obsolete[field] = field === 'artifacts' ? [] : { runs: [] }
    assert.throws(() => compileHarnessRelease(obsolete), (error) => errorCode(error, 'obsolete_harness_contract'))
  }
})

test('release compilation rejects embedded node secrets', () => {
  const source = releaseSource()
  const graph = source.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
  graph.nodes.find((node) => node.id === 'agent')!.data.provider = { apiKey: 'secret' }
  assert.throws(() => compileHarnessRelease(source), (error) => errorCode(error, 'embedded_secret'))
})

test('release validation derives requirements instead of trusting packaged metadata', () => {
  const release = compileHarnessRelease(releaseSource())
  const tampered = structuredClone(release)
  tampered.requirements = [{ kind: 'runtime', name: 'untrusted-runtime' }]
  assert.deepEqual(validateHarnessRelease(tampered).requirements, release.requirements)
})

test('release compilation rejects duplicate graph ids and unpinned exposure targets', () => {
  const duplicate = releaseSource()
  const graph = duplicate.graph as { nodes: Array<Record<string, unknown>> }
  graph.nodes.push(structuredClone(graph.nodes[0]!))
  assert.throws(() => compileHarnessRelease(duplicate), (error) => errorCode(error, 'duplicate_node_id'))

  const unpinned = releaseSource()
  const unpinnedGraph = unpinned.graph as { nodes: Array<{ id: string; data: Record<string, unknown> }> }
  delete unpinnedGraph.nodes.find((node) => node.id === 'agent')!.data.exposeInHarness
  assert.throws(() => compileHarnessRelease(unpinned), (error) => errorCode(error, 'unexposed_harness_node'))
})

test('every pinned node becomes one exposure and declarations only enrich their target', () => {
  const source = releaseSource()
  const graph = source.graph as { nodes: Array<{ id: string; type: string; data: Record<string, unknown> }> }
  graph.nodes.find((node) => node.id === 'instructions')!.data.exposeInHarness = true
  graph.nodes.push({
    id: 'app',
    type: 'app',
    data: { label: 'App', exposeInHarness: true, props: { agentNodeId: 'agent' } }
  })
  const release = compileHarnessRelease(source)
  assert.deepEqual(release.manifest.exposures.map((exposure) => exposure.nodeId).sort(), ['agent', 'app', 'instructions'])
  assert.deepEqual(release.manifest.exposures.find((exposure) => exposure.nodeId === 'app')?.surfaces, ['page'])
  assert.deepEqual(release.manifest.exposures.find((exposure) => exposure.nodeId === 'instructions')?.surfaces, ['page'])
})
