/**
 * Member 2 opt-in review integration runner. Uses Member 1's frozen fixture and real Host.
 * No market data, no publication, and no external model calls in scripted mode.
 * Usage: node scripts/run_member2_review.mjs --fixture build/member-2/fixtures/clean/fixture.json --python .venv/Scripts/python.exe
 */
import { readFile } from 'node:fs/promises'
import { pinConfiguredModelProvider } from '../integrations/conexus/collaboration/model-provider.mjs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createCollaborationHost } from '../integrations/conexus/collaboration/host.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const args = process.argv.slice(2)
const option = (name, fallback) => { const i = args.indexOf(name); return i < 0 ? fallback : args[i + 1] }
const fixturePath = resolve(option('--fixture', 'build/member-2/fixtures/clean/fixture.json'))
const pythonExecutable = resolve(option('--python', process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'))
const live = args.includes('--live')
const probeWrite = args.includes('--probe-reviewer-write')
if (live && probeWrite) throw new Error('Write probes are scripted-only.')
if (live && !args.includes('--confirm-paid')) throw new Error('Live reviewer requires both --live and --confirm-paid.')
const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
if (!['clean', 'seeded-formula', 'seeded-pit', 'seeded-report', 'seeded-claim', 'missing-evidence'].includes(fixture.scenario)) {
  throw new Error('This Member 2 runner supports single-candidate review scenarios only.')
}
const v = fixture.versions[0]
const taskId = fixture.task_id
const manifestId = v.manifest.manifest_id
const seen = { researcher: 0, reviewer: 0 }
let writeProbeRequested = false
const reads = []
const makeCall = (name, payload) => ({
  id: `call-${name}-${Math.random().toString(36).slice(2)}`,
  type: 'function', function: { name, arguments: JSON.stringify(payload) },
})
const response = (call) => ({
  finish_reason: 'tool_calls', content: null, tool_calls: [call],
})
const done = (status, summary) => response(makeCall('complete', { status, summary,
  ...(status === 'blocked' ? { blocker: 'Unresolved review findings require a revised research candidate and re-review.' } : {}),
}))
const scripted = async ({ identity }) => {
  const role = identity.role
  const step = seen[role]++
  if (role === 'researcher') {
    if (step === 0) return response(makeCall('use', { node_id: 'review-control', capability: 'tool.invoke',
      input: { operation: 'start', delegation: v.delegation } }))
    if (step === 1) return response(makeCall('use', { node_id: 'review-control', capability: 'tool.invoke',
      input: { operation: 'wait', child_task_id: v.delegation.child_task_id } }))
    return done(!live && v.review.status === 'pass' ? 'done' : 'blocked',
      !live && v.review.status === 'pass' ? 'Review completed; final publication not performed.' : 'Review requires follow-up; no publication performed.')
  }
  if (probeWrite && step === 0) {
    writeProbeRequested = true
    return response(makeCall('edit', { operations: [] }))
  }
  const evidenceStep = step - (probeWrite ? 1 : 0)
  if (evidenceStep === 0) return response(makeCall('use', { node_id: 'review-evidence', capability: 'tool.invoke',
    input: { operation: 'manifest', manifest_id: manifestId } }))
  if (evidenceStep <= v.evidence_ids.length) {
    const artifact_id = v.evidence_ids[evidenceStep - 1]
    reads.push(artifact_id)
    return response(makeCall('use', { node_id: 'review-evidence', capability: 'tool.invoke',
      input: { operation: 'read', manifest_id: manifestId, artifact_id } }))
  }
  if (evidenceStep === v.evidence_ids.length + 1) return response(makeCall('use', { node_id: 'review-submit',
    capability: 'tool.invoke', input: { review: v.review } }))
  return done('done', 'Scripted review submitted.')
}
const scriptedModel = async request => ({ response: await scripted(request),
  usage: { input_tokens: 20, output_tokens: 20, total_tokens: 40 } })
// The researcher remains scripted: the live model is ONLY the reviewer.
// The fixture's private expected review is never supplied to the live model.
const provider = live ? await pinConfiguredModelProvider({
  settingsPath: resolve(option('--provider-settings', 'build/private/model-providers.json')),
}) : null
const callModel = async request => request.identity.role === 'reviewer' && live
  ? provider.complete(request) : scriptedModel(request)
const host = await createCollaborationHost({
  pythonExecutable, storeRoot: resolve(dirname(fixturePath), 'control'),
  evidenceRoot: resolve(dirname(fixturePath), 'evidence'), taskId, candidate: v.candidate,
  scopes: ['model.invoke', 'evidence.read', 'review.submit', 'review.delegate'],
  operations: [],
  ...(provider ? { modelProvider: { configuration: provider.configuration,
    boundInput: provider.boundInput, complete: callModel } } : { callModel }),
  budget: { version: 1, max_model_calls: 48, max_tool_calls: 160, wall_time_ms: 120000,
    max_output_tokens: 4096, max_total_tokens: null, max_cost_micros: null, currency: null },
})
const result = await host.run()
const snapshot = await host.snapshot()
const accounting = await host.accounting()
const child = snapshot.children.at(-1)
// Verify reviewer write-denial events
const events = await host.events()

const reviewerDeniedEvents = events.filter(
  event =>
    event.role === 'reviewer' &&
    event.kind === 'denied' &&
    event.error_code === 'ACCESS_DENIED'
)
const reviewerWriteDenied = reviewerDeniedEvents.length > 0

const deniedEvents = events
  .filter(event =>
    event.role === 'reviewer' &&
    event.kind === 'denied'
  )
  .map(event => ({
    kind: event.kind,
    error_code: event.error_code,
    role: event.role
  }))

const report = {
  scenario: fixture.scenario,
  mode: live ? 'live' : 'scripted',
  root_status: result.status,
  task_state: snapshot.status,
  child_status: child?.status ?? null,
  review_status: child?.review?.status ?? null,
  review_accepted: Boolean(child?.review),
  evidence_reads_requested: live ? null : reads,

  reviewer_write_probe_requested: writeProbeRequested,
  reviewer_write_denied: reviewerWriteDenied,
  reviewer_denied_operations: reviewerDeniedEvents.length,

  // Reviewer denial diagnostics
  denied_events: deniedEvents,

  // Actual accepted reviewer findings
  findings: child?.review?.findings?.length ?? 0,
  review_findings: child?.review?.findings?.map(finding => ({
    category: finding.category,
    severity: finding.severity,
    certainty: finding.certainty,
    explanation: finding.explanation,
    expected: finding.expected,
    observed: finding.observed
  })) ?? [],

  accounting,
  publication_performed: false,
}

console.log(JSON.stringify(report, null, 2))
if (!child?.review || (!live && child.review.status !== v.review.status)) process.exitCode = 1
if (probeWrite && (!writeProbeRequested || !reviewerWriteDenied)) {
  console.error('Reviewer write-denial verification failed.')
  process.exitCode = 1
}
