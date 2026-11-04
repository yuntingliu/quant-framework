// Member 2 walkthrough: public facades and the existing executor/dispatcher.
// Scripted protocol responses only; no provider, credentials or business publisher.
import assert from 'node:assert/strict'
import { randomUUID, createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createCollaborationHost, COLLABORATION_NODES as nodes } from '../integrations/conexus/collaboration/host.mjs'
import { privateOutput } from './lib/collaboration-acceptance.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const execute = promisify(execFile)
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const tool = (name, args) => ({ id: randomUUID(), type: 'function', function: { name, arguments: JSON.stringify(args) } })
const use = (node_id, input) => tool('use', { node_id, capability: 'tool.invoke', input })
const done = () => tool('complete', { status: 'done', summary: 'Scripted handover protocol completed.' })

export async function runHandover({ output, pythonExecutable }) {
  output = await privateOutput(output)
  const helper = async operation => {
    const result = await execute(pythonExecutable, ['scripts/prepare_collaboration_handover.py', operation, '--output', output],
      { cwd: root, windowsHide: true, timeout: 60000, maxBuffer: 1024 * 1024 })
    return JSON.parse(result.stdout)
  }
  const fixture = await helper('prepare')
  const evidencePath = join(output, 'evidence/evidence.sqlite3'), businessPath = join(output, 'business.json')
  await writeFile(businessPath, '{"reports":[],"source":"unchanged"}\n')
  const before = { evidence: hash(await readFile(evidencePath)), business: hash(await readFile(businessPath)) }
  const turns = { researcher: 0, reviewer: 0 }, errors = new Set()
  let writes = 0
  const host = await createCollaborationHost({ pythonExecutable, taskId: fixture.task.task_id, candidate: fixture.candidate,
    storeRoot: join(output, 'control'), evidenceRoot: join(output, 'evidence'),
    scopes: ['model.invoke', 'review.delegate', 'evidence.read', 'review.submit'],
    budget: { max_model_calls: 8, max_tool_calls: 32, max_total_tokens: 120, max_output_tokens: 5, wall_time_ms: 120000 },
    operations: [{ id: 'research-write', version: '1', requiredScopes: ['project.write'], inputSchema: { type: 'object' },
      execute() { writes++; throw new Error('Business callback must remain unreachable') } }],
    modelProvider: {
      configuration: { provider_id: 'scripted-handover', model_id: 'scripted/protocol', profile_id: 'no-credentials',
        configuration_id: 'scripted-handover-v1', bounds_id: 'synthetic-fixed-units-v1', supports_output_cap: true, pricing: null },
      boundInput: () => 10,
      async complete({ identity, messages, maxTokens }) {
        assert.equal(maxTokens, 5)
        const step = ++turns[identity.role]
        const results = messages.filter(m => m.role === 'tool').map(m => { try { return JSON.parse(m.content) } catch { return {} } })
        for (const value of results) if (value.error_code) errors.add(value.error_code)
        let calls
        if (identity.role === 'researcher') {
          calls = step === 1 ? [use(nodes.control, { operation: 'start', delegation: fixture.delegation })]
            : step === 2 ? [use(nodes.control, { operation: 'wait', child_task_id: fixture.delegation.child_task_id })] : [done()]
          if (step > 2) assert(results.some(r => r.review?.status === 'pass' && r.task_state === 'completed'))
        } else if (step === 1) {
          calls = ['report', 'result'].map(artifact_id => use(nodes.evidence, { operation: 'read', manifest_id: fixture.manifest.manifest_id, artifact_id }))
        } else if (step === 2) {
          const report = results.find(r => r.artifact_id === 'report'), result = results.find(r => r.artifact_id === 'result')
          assert.equal(report.value, 'Synthetic net return: 8% after fees.')
          assert.deepEqual(result.value, { net_return: 0.08, fees_included: true })
          const review = { schema_version: 1, task_id: fixture.task.task_id, submission_id: 'handover-submission',
            child_task_id: fixture.delegation.child_task_id, attempt_id: fixture.delegation.attempt_id, round: 1,
            candidate: fixture.candidate, manifest: fixture.manifest, status: 'pass',
            coverage: [{ category: 'report_consistency', status: 'checked', reason: '',
              evidence: [{ artifact_id: result.artifact_id, sha256: result.sha256, locator: '' }] }],
            findings: [], missing_evidence: [], reason: null }
          calls = [use(nodes.submit, { review: { ...review, attempt_id: 'forged-attempt' } }),
            use('research-write', { confirm: true }), use(nodes.submit, { review }), use(nodes.submit, { review })]
        } else calls = [done()]
        return { response: { content: null, finish_reason: 'tool_calls', tool_calls: calls },
          // Attested synthetic units, not estimates of live model tokenization.
          usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15, cost_micros: 0, currency: 'USD' } }
      },
    },
  })
  await host.run()
  assert.equal((await host.flushAccounting()).pending, 0)
  const state = await host.snapshot(), accounting = await host.accounting(), requests = await host.requests()
  assert.equal(state.status, 'completed')
  assert.equal(state.children.length, 1)
  assert.deepEqual(state.children[0].scopes, ['evidence.read', 'model.invoke', 'review.submit'])
  assert(errors.has('TASK_MISMATCH') && errors.has('ACCESS_DENIED'))
  assert.equal(writes, 0)
  assert.equal(hash(await readFile(evidencePath)), before.evidence)
  assert.equal(hash(await readFile(businessPath)), before.business)
  assert.equal(accounting.totals.model_calls, 6)
  assert.equal(accounting.totals.total_tokens, 90)
  assert.equal(accounting.totals.pending, 0)
  assert(accounting.roles.researcher.model_calls > 0 && accounting.roles.reviewer.model_calls > 0)
  const result = { status: 'passed', mode: 'scripted', ...await helper('verify'), task_state: state.status,
    task_registered: true, evidence_frozen_and_read: true, restricted_child_count: 1, accepted_typed_review: true,
    forged_identity_denied: true, forbidden_write_denied: true, business_unchanged: true,
    shared_model_calls: accounting.totals.model_calls, shared_synthetic_token_units: accounting.totals.total_tokens,
    synthetic_units_note: 'Fixed test units with an explicit bound; not live token usage or a live-provider hard-cap certification.',
    final_publication: false, external_model_calls: 0 }
  for (const [name, value] of Object.entries({ state, accounting, requests, result })) {
    await writeFile(join(output, `${name}.json`), JSON.stringify(value, null, 2) + '\n')
  }
  return result
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2)
  if (args.includes('--help')) console.log('Usage: node scripts/check_collaboration_handover.mjs --python executable [--output build/handover]. Scripted; zero external model calls.')
  else {
    assert.equal(args.length % 2, 0)
    for (let i = 0; i < args.length; i += 2) assert(['--python', '--output'].includes(args[i]))
    const get = (flag, fallback) => args.includes(flag) ? args[args.indexOf(flag) + 1] : fallback
    assert(get('--python'), 'Supply the application Python executable with --python.')
    console.log(JSON.stringify(await runHandover({ pythonExecutable: resolve(get('--python')),
      output: join(resolve(get('--output', 'build/handover')), randomUUID()) })))
  }
}
