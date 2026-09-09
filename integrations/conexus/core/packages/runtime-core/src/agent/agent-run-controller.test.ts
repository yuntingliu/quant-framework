import assert from 'node:assert/strict'
import test from 'node:test'
import { AgentRunController, normalizeAgentContinuation } from './agent-run-controller.js'

test('AgentRunController normalizes accepted input and drains it in FIFO order', () => {
  const controller = new AgentRunController()

  assert.equal(normalizeAgentContinuation('  first  '), 'first')
  assert.equal(normalizeAgentContinuation({ text: '  second  ' }), 'second')
  assert.equal(controller.continue('  first  '), true)
  assert.equal(controller.continue({ text: '  second  ' }), true)
  assert.equal(controller.pendingContinuationCount, 2)
  assert.deepEqual(controller.drainContinuations(), ['first', 'second'])
  assert.equal(controller.pendingContinuationCount, 0)
  assert.deepEqual(controller.drainContinuations(), [])
})

test('AgentRunController rejects empty and invalid continuation values', () => {
  const controller = new AgentRunController()

  for (const input of ['', '   ', null, undefined, 7, {}, [], { text: '' }, { text: 7 }]) {
    assert.equal(controller.continue(input), false)
  }
  assert.equal(controller.pendingContinuationCount, 0)
})

test('AgentRunController combines external and local aborts and drops pending input', () => {
  const external = new AbortController()
  const externallyControlled = new AgentRunController({ signal: external.signal })
  assert.equal(externallyControlled.continue('queued'), true)

  external.abort('external-stop')

  assert.equal(externallyControlled.aborted, true)
  assert.equal(externallyControlled.signal.reason, 'external-stop')
  assert.equal(externallyControlled.pendingContinuationCount, 0)
  assert.deepEqual(externallyControlled.drainContinuations(), [])
  assert.equal(externallyControlled.continue('too late'), false)
  assert.equal(externallyControlled.abort('again'), false)

  const locallyControlled = new AgentRunController()
  assert.equal(locallyControlled.abort('local-stop'), true)
  assert.equal(locallyControlled.signal.reason, 'local-stop')
  assert.equal(locallyControlled.abort('again'), false)
  assert.equal(locallyControlled.continue({ text: 'too late' }), false)
})

test('AgentRunController resolves each interaction once and exposes detached snapshots', async () => {
  const controller = new AgentRunController()
  const answer = controller.requestInteraction<string>(' ask-1 ', { kind: ' ask_user ', ownerNodeId: ' agent-1 ' })

  const snapshot = controller.pendingInteractions
  assert.deepEqual(snapshot, [{ id: 'ask-1', kind: 'ask_user', ownerNodeId: 'agent-1' }])
  const mutableSnapshot = snapshot as AgentPendingInteractionSnapshotLike[]
  mutableSnapshot.push({ id: 'fake', kind: 'fake', ownerNodeId: 'fake-node' })
  assert.deepEqual(controller.pendingInteractions, [{ id: 'ask-1', kind: 'ask_user', ownerNodeId: 'agent-1' }])
  assert.equal(controller.resolveInteraction('ask-1', 'Approved'), true)
  assert.equal(controller.resolveInteraction('ask-1', 'Ignored'), false)
  assert.equal(await answer, 'Approved')
  assert.deepEqual(controller.pendingInteractions, [])
})

test('AgentRunController settles all interactions deterministically when aborted', async () => {
  const controller = new AgentRunController()
  const safeAnswer = controller.requestInteraction('ask-1', {
    kind: 'ask_user',
    ownerNodeId: 'agent-1',
    abortValue: '[aborted]'
  })
  const rejectedAnswer = controller.requestInteraction('form-1', { kind: 'form', ownerNodeId: 'agent-1' })

  assert.equal(controller.abort('user-stop'), true)

  assert.equal(await safeAnswer, '[aborted]')
  await assert.rejects(rejectedAnswer, (error: unknown) => {
    assert.equal(error instanceof Error ? error.name : '', 'AbortError')
    assert.equal(error instanceof Error ? error.cause : undefined, 'user-stop')
    return true
  })
  assert.deepEqual(controller.pendingInteractions, [])
  assert.equal(controller.resolveInteraction('ask-1', 'late'), false)
})

test('AgentRunController settles only the interaction owned by an aborted nested run', async () => {
  const controller = new AgentRunController()
  const firstRun = new AbortController()
  const secondRun = new AbortController()
  const firstAnswer = controller.requestInteraction('nested-ask-1', {
    kind: 'ask_user',
    ownerNodeId: 'nested-agent-1',
    signal: firstRun.signal,
    abortValue: '[nested run aborted]'
  })
  const secondAnswer = controller.requestInteraction<string>('nested-ask-2', {
    kind: 'ask_user',
    ownerNodeId: 'nested-agent-2',
    signal: secondRun.signal
  })

  firstRun.abort('nested-stop')

  assert.equal(await firstAnswer, '[nested run aborted]')
  assert.equal(controller.aborted, false)
  assert.deepEqual(controller.pendingInteractions, [{ id: 'nested-ask-2', kind: 'ask_user', ownerNodeId: 'nested-agent-2' }])
  assert.equal(controller.resolveInteraction('nested-ask-2', 'continue'), true)
  assert.equal(await secondAnswer, 'continue')
})

test('AgentRunController rejects a duplicate interaction id without disturbing the original', async () => {
  const controller = new AgentRunController()
  const original = controller.requestInteraction<string>('ask-1', { kind: 'ask_user', ownerNodeId: 'agent-1' })

  await assert.rejects(
    controller.requestInteraction(' ask-1 ', { kind: 'form', ownerNodeId: 'agent-1' }),
    /interaction already exists: ask-1/
  )
  assert.deepEqual(controller.pendingInteractions, [{ id: 'ask-1', kind: 'ask_user', ownerNodeId: 'agent-1' }])
  assert.equal(controller.resolveInteraction('ask-1', 'original answer'), true)
  assert.equal(await original, 'original answer')
})

test('AgentRunController applies the interaction abort contract when its run signal is already aborted', async () => {
  const controller = new AgentRunController()
  const nestedRun = new AbortController()
  nestedRun.abort('nested-stop')

  assert.equal(await controller.requestInteraction('ask-1', {
    kind: 'ask_user',
    ownerNodeId: 'agent-1',
    signal: nestedRun.signal,
    abortValue: undefined
  }), undefined)
  await assert.rejects(
    controller.requestInteraction('form-1', { kind: 'form', ownerNodeId: 'agent-1', signal: nestedRun.signal }),
    (error: unknown) => {
      assert.equal(error instanceof Error ? error.name : '', 'AbortError')
      assert.equal(error instanceof Error ? error.cause : undefined, 'nested-stop')
      return true
    }
  )
})

test('AgentRunController applies the abort settlement contract to late interaction requests', async () => {
  const controller = new AgentRunController()
  controller.abort()

  assert.equal(await controller.requestInteraction('ask-1', {
    kind: 'ask_user',
    ownerNodeId: 'agent-1',
    abortValue: undefined
  }), undefined)
  await assert.rejects(
    controller.requestInteraction('form-1', { kind: 'form', ownerNodeId: 'agent-1' }),
    (error: unknown) => error instanceof Error && error.name === 'AbortError'
  )
  await assert.rejects(
    controller.requestInteraction('', { kind: 'form', ownerNodeId: 'agent-1' }),
    /non-empty id, kind, and ownerNodeId/
  )
})

type AgentPendingInteractionSnapshotLike = { id: string; kind: string; ownerNodeId: string }
