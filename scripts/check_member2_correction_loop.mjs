
/**
 * Member 2 offline end-to-end correction and re-review acceptance.
 *
 * Uses the real collaboration Host, frozen synthetic evidence,
 * and scripted model responses. No external model calls,
 * backtest execution, or publication.
 */

import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'

import {
  createCollaborationHost,
  COLLABORATION_NODES as nodes,
} from '../integrations/conexus/collaboration/host.mjs'

const args = process.argv.slice(2)

const option = (name, fallback) => {
  const index = args.indexOf(name)
  return index < 0 ? fallback : args[index + 1]
}

const fixturePath = resolve(
  option(
    '--fixture',
    'build/member-2/fixtures/correction/fixture.json'
  )
)

const pythonExecutable = resolve(
  option(
    '--python',
    process.platform === 'win32'
      ? '.venv/Scripts/python.exe'
      : '.venv/bin/python'
  )
)

const fixture = JSON.parse(
  await readFile(fixturePath, 'utf8')
)

assert.equal(fixture.scenario, 'correction-loop')
assert.equal(fixture.versions.length, 2)

const [first, second] = fixture.versions

const tool = (name, input) => ({
  id: randomUUID(),
  type: 'function',
  function: {
    name,
    arguments: JSON.stringify(input),
  },
})

const use = (node_id, input) =>
  tool('use', {
    node_id,
    capability: 'tool.invoke',
    input,
  })

const response = calls => ({
  content: null,
  finish_reason: 'tool_calls',
  tool_calls: calls,
})

const complete = status =>
  tool('complete', {
    status,
    summary: 'Scripted Member 2 correction-loop acceptance.',
    ...(status === 'blocked'
      ? { blocker: 'Correction loop could not complete.' }
      : {}),
  })

const turns = {
  researcher: 0,
  reviewer: 0,
}

let host
let activationCount = 0
let reviewerActivationDenied = false

const hostOptions = {
  pythonExecutable,
  evidenceRoot: resolve(dirname(fixturePath), 'evidence'),
  storeRoot: resolve(dirname(fixturePath), 'control'),
  taskId: fixture.task_id,
  candidate: first.candidate,

  scopes: [
    'model.invoke',
    'evidence.read',
    'review.submit',
    'review.delegate',
    'project.write',
  ],

  diagnosticMaxIterations: 12,

  operations: [
    {
      id: 'research-activate-correction',
      version: '1',
      requiredScopes: ['project.write'],
      description:
        'Activate the pre-authorised corrected candidate.',

      inputSchema: {
        type: 'object',
        properties: {
          correction_id: {
            type: 'string',
            const: fixture.correction.response_id,
          },
        },
        required: ['correction_id'],
        additionalProperties: false,
      },

      async execute(input, { identity, signal }) {
        assert.equal(identity.role, 'researcher')
        assert.equal(signal.aborted, false)
        assert.equal(
          input.correction_id,
          fixture.correction.response_id
        )

        const result = await host.setCandidate(
          second.candidate,
          fixture.correction
        )

        activationCount++
        return result
      },
    },
  ],

  async callModel({ identity }) {
    const role = identity.role
    const step = turns[role]++

    if (role === 'researcher') {
      if (step === 0) {
        return response([
          use(nodes.control, {
            operation: 'start',
            delegation: first.delegation,
          }),
        ])
      }

      if (step === 1) {
        return response([
          use(nodes.control, {
            operation: 'wait',
            child_task_id: first.delegation.child_task_id,
          }),
        ])
      }

      if (step === 2) {
        return response([
          use('research-activate-correction', {
            correction_id: fixture.correction.response_id,
          }),
        ])
      }

      if (step === 3) {
        return response([
          use(nodes.control, {
            operation: 'start',
            delegation: second.delegation,
          }),
        ])
      }

      if (step === 4) {
        return response([
          use(nodes.control, {
            operation: 'wait',
            child_task_id: second.delegation.child_task_id,
          }),
        ])
      }

      return response([complete('done')])
    }

    // Each reviewer has a distinct child identity.
    if (identity.child_task_id === first.delegation.child_task_id) {
      if (step === 0) {
        return response([
          use(nodes.evidence, {
            operation: 'manifest',
            manifest_id: first.manifest.manifest_id,
          }),
          use(nodes.submit, {
            review: first.review,
          }),
        ])
      }

      return response([complete('done')])
    }

    if (identity.child_task_id === second.delegation.child_task_id) {
      if (step === 2) {
        return response([
          use(nodes.evidence, {
            operation: 'manifest',
            manifest_id: second.manifest.manifest_id,
          }),
          use('research-activate-correction', {
            correction_id: fixture.correction.response_id,
          }),
          use(nodes.submit, {
            review: second.review,
          }),
        ])
      }

      return response([complete('done')])
    }

    throw new Error('Unexpected reviewer identity')
  },
}

host = await createCollaborationHost(hostOptions)

const result = await host.run()
const state = await host.snapshot()
const events = await host.events()

assert.equal(state.status, 'completed')
assert.equal(state.candidate.revision, 2)
assert.equal(state.children.length, 2)

assert.equal(state.children[0].review.status, 'findings')
assert.equal(state.children[1].review.status, 'pass')

assert.deepEqual(
  state.children[1].review.candidate,
  second.candidate
)

assert.deepEqual(
  state.children[1].review.manifest,
  second.manifest
)

assert.equal(activationCount, 1)
assert.equal(state.corrections.length, 1)
assert.deepEqual(state.corrections[0], fixture.correction)

reviewerActivationDenied = events.some(event =>
  event.role === 'reviewer' &&
  event.kind === 'denied' &&
  event.error_code === 'ACCESS_DENIED'
)

assert.equal(reviewerActivationDenied, true)

console.log(JSON.stringify({
  scenario: 'correction-loop',
  status: 'passed',
  root_status: result.status,
  task_state: state.status,
  candidate_revision: state.candidate.revision,
  review_rounds: state.children.length,
  first_review: state.children[0].review.status,
  second_review: state.children[1].review.status,
  correction_count: state.corrections.length,
  reviewer_activation_denied: reviewerActivationDenied,
  external_model_calls: 0,
  publication_performed: false,
}, null, 2))
