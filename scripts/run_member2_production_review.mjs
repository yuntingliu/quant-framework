import { randomUUID } from 'node:crypto'
/** Opt-in archived-backtest review; never publishes or executes new backtests.
 * Scripted mode proves Host wiring only and intentionally returns INCOMPLETE.
 * Live mode invokes the configured model for the reviewer, with explicit consent.
 */
import { readFile } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { createCollaborationHost } from '../integrations/conexus/collaboration/host.mjs'
import { pinConfiguredModelProvider } from '../integrations/conexus/collaboration/model-provider.mjs'

const args = process.argv.slice(2)
const option = (name, fallback) => { const i = args.indexOf(name); return i < 0 ? fallback : args[i + 1] }
const fixturePath = resolve(option('--fixture', 'build/member-2/fixtures/production/fixture.json'))
const pythonExecutable = resolve(option('--python', process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'))
const live = args.includes('--live')
if (live && !args.includes('--confirm-paid')) throw new Error('Live review requires --live --confirm-paid')
const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
if (fixture.scenario !== 'archived-backtest' || fixture.versions?.length !== 1) throw new Error('Expected a prepared archived-backtest candidate')
const v = fixture.versions[0]
const manifestId = v.manifest.manifest_id
const seen = { researcher: 0, reviewer: 0 }
const reads = []
const call = (name, payload) => ({ id: `call-${name}-${randomUUID()}`, type: 'function', function: { name, arguments: JSON.stringify(payload) } })
const response = (name, payload) => ({ finish_reason: 'tool_calls', content: null, tool_calls: [call(name, payload)] })
const complete = (status, summary) => response('complete', { status, summary,
  ...(status === 'blocked' ? { blocker: 'Independent review is incomplete or requires a revised candidate.' } : {}) })
const scripted = async ({ identity }) => {
  const role = identity.role, step = seen[role]++
  if (role === 'researcher') {
    if (step === 0) return response('use', { node_id: 'review-control', capability: 'tool.invoke', input: { operation: 'start', delegation: v.delegation } })
    if (step === 1) return response('use', { node_id: 'review-control', capability: 'tool.invoke', input: { operation: 'wait', child_task_id: v.delegation.child_task_id } })
    // Never claim approval from a scripted or live review in this non-publishing runner.
    return complete('blocked', 'Review recorded; no publication. Inspect accepted findings and coverage before correction.')
  }
  if (step === 0) return response('use', { node_id: 'review-evidence', capability: 'tool.invoke', input: { operation: 'manifest', manifest_id: manifestId } })
  if (step === 1) { reads.push('backtest-result'); return response('use', { node_id: 'review-evidence', capability: 'tool.invoke', input: { operation: 'read', manifest_id: manifestId, artifact_id: 'backtest-result' } }) }
  if (step === 2) { reads.push('return-series'); return response('use', { node_id: 'review-evidence', capability: 'tool.invoke', input: { operation: 'read', manifest_id: manifestId, artifact_id: 'return-series' } }) }
  if (step === 3) {
    const review = {
      schema_version: 1, task_id: fixture.task_id, submission_id: `submission-${v.delegation.child_task_id}`,
      child_task_id: v.delegation.child_task_id, attempt_id: v.delegation.attempt_id,
      round: v.delegation.round, candidate: v.candidate, manifest: v.manifest,
      status: 'incomplete', coverage: fixture.task.required_categories.map(category => ({
        category, status: 'missing', reason: 'Scripted integration probe does not independently verify this category.', evidence: [],
      })), findings: [], missing_evidence: ['Independent model review not performed'],
      reason: 'Scripted Host wiring test only; no financial approval is asserted.',
    }
    return response('use', { node_id: 'review-submit', capability: 'tool.invoke', input: { review } })
  }
  return complete('done', 'Scripted incomplete review submitted.')
}
const scriptedModel = async request => ({ response: await scripted(request), usage: { input_tokens: 20, output_tokens: 20, total_tokens: 40 } })
const provider = live ? await pinConfiguredModelProvider({ settingsPath: resolve(option('--provider-settings', 'build/private/model-providers.json')) }) : null
const callModel = request => live && request.identity.role === 'reviewer' ? provider.complete(request) : scriptedModel(request)
const host = await createCollaborationHost({
  diagnosticMaxIterations: 12,
  diagnosticModelInput: true,
  pythonExecutable, storeRoot: resolve(dirname(fixturePath), 'control'),
  evidenceRoot: resolve(dirname(fixturePath), 'evidence'), taskId: fixture.task_id,
  candidate: v.candidate,
  scopes: ['model.invoke', 'evidence.read', 'review.submit', 'review.delegate'], operations: [],
  ...(provider ? { modelProvider: { configuration: provider.configuration, boundInput: provider.boundInput, complete: callModel } } : { callModel }),
  budget: {
    version: 1,
    max_model_calls: 10,
    max_tool_calls: 18,
    wall_time_ms: 90000,
    max_output_tokens: 2048,
    max_total_tokens: null,
    max_cost_micros: null,
    currency: null,
  },
})
const result = await host.run()
const snapshot = await host.snapshot()
const accounting = await host.accounting()
const child = snapshot.children?.at(-1)
const report = {
  scenario: fixture.scenario, backtest_id: fixture.backtest_id, mode: live ? 'live' : 'scripted-incomplete',
  root_status: result.status, task_state: snapshot.status, child_status: child?.status ?? null,
  review_status: child?.review?.status ?? null, review_accepted: Boolean(child?.review),
  findings: child?.review?.findings?.length ?? 0,
  evidence_reads_requested: live ? null : reads,
  coverage: fixture.coverage, accounting, publication_performed: false,
}
console.log(JSON.stringify(report, null, 2))
if (!child?.review || (!live && child.review.status !== 'incomplete')) process.exitCode = 1
