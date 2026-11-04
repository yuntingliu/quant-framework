// Pure protocol preflight. Host authorization and durable acceptance are separate.
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'

const requireRuntime = createRequire(new URL('../../../build/conexus/package.json', import.meta.url))
const Ajv = requireRuntime('ajv')
const addFormats = requireRuntime('ajv-formats')
const schema = JSON.parse(readFileSync(new URL('../../../alphalab/collaboration/schemas/protocol.v1.json', import.meta.url)))
const outputs = JSON.parse(readFileSync(new URL('../alphalab-research-agent/workspace-output.schema.json', import.meta.url)))
const ajv = new Ajv({ strict: false, allErrors: false, validateFormats: true, strictNumbers: true })
addFormats(ajv)
ajv.addSchema(outputs, 'urn:alphalab:workspace-outputs:v1')
ajv.addSchema(schema)
const kinds = new Set(['task_envelope', 'delegation_request', 'evidence_manifest', 'research_package', 'review_result', 'correction_response', 'final_output_request'])
const validators = new Map([...kinds].map(kind => [kind, ajv.compile({ $ref: `${schema.$id}#/definitions/${kind}` })]))

export class CollaborationError extends Error {
  constructor(code) {
    super('Collaboration validation failed; correct the message or refresh its registered evidence.')
    this.code = code
  }
  asResult() { return { status: 'failed', error_code: this.code, error_summary: this.message } }
}
function ensure(condition, code = 'INVALID_CONTRACT') { if (!condition) throw new CollaborationError(code) }
const equal = (a, b) => {
  if (a === b) return true
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false
  const keys = Object.keys(a)
  return keys.length === Object.keys(b).length && keys.every(key => Object.hasOwn(b, key) && equal(a[key], b[key]))
}
const stamp = (value, expected, code) => ensure(equal(value, expected), code)
const sameSet = (a, b) => equal([...a].sort(), [...b].sort())
function finite(value, depth = 0) {
  ensure(depth <= 64)
  if (typeof value === 'number') ensure(Number.isFinite(value))
  if (typeof value === 'bigint' || value === undefined) ensure(false)
  if (value && typeof value === 'object') for (const item of Object.values(value)) finite(item, depth + 1)
}
function unique(rows, key) { ensure(new Set(rows.map(row => row[key])).size === rows.length) }
function* citations(value) {
  if (!value || typeof value !== 'object') return
  if (sameSet(Object.keys(value), ['artifact_id', 'sha256', 'locator'])) yield value
  else for (const child of Object.values(value)) yield* citations(child)
}
export function validateContractShape(kind, value) {
  ensure(kinds.has(kind))
  try {
    finite(value)
    ensure(Buffer.byteLength(JSON.stringify(value)) <= 600000, 'EVIDENCE_LIMIT')
    ensure(validators.get(kind)(value))
  } catch (error) {
    if (error instanceof CollaborationError) throw error
    throw new CollaborationError('INVALID_CONTRACT')
  }
}
export function resolveLocator(content, locator, mediaType) {
  if (mediaType === 'application/json') {
    ensure(locator === '' || locator.startsWith('/'), 'INVALID_CITATION')
    let value = content
    for (const part of locator ? locator.slice(1).split('/') : []) {
      ensure(!/~(?![01])/.test(part), 'INVALID_CITATION')
      const key = part.replace(/~1/g, '/').replace(/~0/g, '~')
      if (Array.isArray(value)) ensure(/^(0|[1-9][0-9]*)$/.test(key), 'INVALID_CITATION')
      ensure(value && typeof value === 'object' && Object.hasOwn(value, key), 'INVALID_CITATION')
      value = value[key]
    }
    return value
  }
  if (locator === '') return content
  const match = /^lines:([1-9][0-9]*):([1-9][0-9]*)$/.exec(locator)
  ensure(match && typeof content === 'string', 'INVALID_CITATION')
  const lines = content.split(/\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/)
  if (lines.at(-1) === '') lines.pop()
  const [start, end] = match.slice(1).map(Number)
  ensure(start <= end && end <= lines.length, 'INVALID_CITATION')
  return lines.slice(start - 1, end).join('\n')
}
function workspace(value, requestId) {
  const result = value.workspaceResult, commands = value.workspaceCommands.commands
  ensure(result.requestId === requestId && value.workspaceCommands.requestId === requestId, 'INVALID_WORKSPACE_OUTPUTS')
  commands.forEach((command, index) => {
    if (command.type === 'open_result') ensure(result.kind === 'document' && command.resultId === result.reportId, 'INVALID_WORKSPACE_OUTPUTS')
    if (['open_widget', 'close_widget'].includes(command.type)) ensure(['project', 'data', 'factor', 'strategy', 'validation', 'report'].some(mode => command.widgetId === `${mode}.workbench`), 'INVALID_WORKSPACE_OUTPUTS')
    if (['switch_mode', 'open_widget', 'open_result'].includes(command.type)) ensure(!commands.slice(index + 1).some(item => item.type === 'set_focus'), 'INVALID_WORKSPACE_OUTPUTS')
    if (command.type === 'set_focus') ensure(['projectId', 'strategyId', 'backtestId', 'symbol', 'date'].some(key => key in command), 'INVALID_WORKSPACE_OUTPUTS')
  })
}
export function validateContract(kind, value, context) {
  validateContractShape(kind, value)
  if (kind === 'task_envelope') {
    ensure(value.researcher_node_id !== value.reviewer_node_id)
    ensure(value.not_applicable_categories.every(item => value.required_categories.includes(item)))
    return
  }
  if (kind === 'evidence_manifest') {
    unique(value.artifacts, 'artifact_id')
    ensure(value.artifacts.reduce((sum, item) => sum + item.byte_length, 0) <= 8 * 1024 * 1024, 'EVIDENCE_LIMIT')
    if (Object.values(value.coverage).some(status => status !== 'complete')) ensure(value.omissions.length)
    for (const [field, allowed] of Object.entries({ data: ['data', 'returns'], sources: ['source'], results: ['result', 'returns'], pit: ['timing'] })) {
      if (value.coverage[field] === 'complete') ensure(value.artifacts.some(item => allowed.includes(item.kind)))
    }
    if (value.coverage.pit === 'complete') ensure(value.coverage.data === 'complete')
    return
  }
  ensure(context && ['task', 'manifest', 'manifest_ref', 'evidence'].every(key => key in context), 'MISSING_CONTEXT')
  const { task, manifest } = context
  ensure(value.task_id === task.task_id && task.task_id === manifest.task_id, 'TASK_MISMATCH')
  if ('manifest' in value) stamp(value.manifest, context.manifest_ref, 'EVIDENCE_CONFLICT')
  const entries = Object.fromEntries(manifest.artifacts.map(item => [item.artifact_id, item]))
  for (const citation of citations(value)) {
    const entry = entries[citation.artifact_id]
    ensure(entry && entry.sha256 === citation.sha256, 'EVIDENCE_CONFLICT')
    ensure(entry.locators.includes(citation.locator), 'INVALID_CITATION')
    const content = context.evidence[citation.artifact_id]
    ensure(content && content.sha256 === entry.sha256 && content.media_type === entry.media_type, 'EVIDENCE_CONFLICT')
    resolveLocator(content.content, citation.locator, entry.media_type)
  }
  if (kind === 'research_package') {
    ensure(value.run_id === task.root_run_id && value.question === task.question, 'TASK_MISMATCH')
    unique(value.claims, 'claim_id'); unique(value.metrics, 'metric_id')
    for (const metric of value.metrics) {
      ensure(metric.period_start <= metric.period_end)
      ensure(!(metric.return_basis === 'net' && metric.fees === 'excluded'))
    }
    for (const [field, allowed] of Object.entries({ source_refs: ['source'], data_refs: ['data', 'returns', 'timing'], result_refs: ['result', 'returns', 'validation', 'positions', 'executions'] })) {
      ensure(value[field].every(item => allowed.includes(entries[item.artifact_id].kind)), 'EVIDENCE_CONFLICT')
    }
    if (manifest.coverage.pit === 'complete') ensure(value.timing.decision_at !== null && value.timing.availability_evidence.length)
    ensure(value.timing.availability_evidence.every(item => entries[item.artifact_id].kind === 'timing'), 'EVIDENCE_CONFLICT')
    ensure(entries[value.draft_report.artifact_id].kind === 'report' && entries[value.publication.artifact_id].kind === 'publication', 'EVIDENCE_CONFLICT')
    ensure(value.publication.locator === '' && value.draft_report.locator === '', 'INVALID_CITATION')
    const publication = context.evidence[value.publication.artifact_id].content
    ensure(publication && equal(publication.report, value.draft_report) && 'outputs' in publication, 'EVIDENCE_CONFLICT')
    if (Object.values(manifest.coverage).some(status => status !== 'complete')) ensure(value.limitations.length)
    return
  }
  if (kind === 'delegation_request') {
    ensure(['root_run_id', 'policy_id', 'grant_id', 'budget_id'].every(key => value[key] === task[key]), 'TASK_MISMATCH')
    ensure(value.parent_run_id === task.root_run_id && value.node_id === task.reviewer_node_id, 'TASK_MISMATCH')
    ensure(value.round <= task.max_review_rounds && Date.parse(value.deadline) <= Date.parse(task.deadline), 'TASK_MISMATCH')
    stamp(value.candidate, context.candidate, 'STALE_CANDIDATE')
    return
  }
  if (kind === 'review_result') {
    stamp(value.candidate, context.candidate, 'STALE_CANDIDATE')
    ensure(context.delegation && ['child_task_id', 'attempt_id', 'round'].every(key => value[key] === context.delegation[key]), 'TASK_MISMATCH')
    unique(value.findings, 'finding_id'); unique(value.coverage, 'category')
    ensure(sameSet(value.coverage.map(item => item.category), task.required_categories), 'INCOMPLETE_REVIEW')
    for (const item of value.coverage) {
      if (item.status === 'checked') {
        ensure(item.evidence.length, 'INCOMPLETE_REVIEW')
        if (item.category === 'pit') ensure(manifest.coverage.pit === 'complete' && item.evidence.some(citation => entries[citation.artifact_id].kind === 'timing'), 'INCOMPLETE_REVIEW')
      } else {
        ensure(item.reason.trim(), 'INCOMPLETE_REVIEW')
        if (item.status === 'not_applicable') ensure(task.not_applicable_categories.includes(item.category), 'INCOMPLETE_REVIEW')
      }
    }
    const research = context.research_package
    ensure(research, 'MISSING_CONTEXT')
    for (const item of value.findings) {
      ensure(item.claim_id !== null || item.metric_id !== null)
      ensure(item.claim_id === null || research.claims.some(claim => claim.claim_id === item.claim_id))
      ensure(item.metric_id === null || research.metrics.some(metric => metric.metric_id === item.metric_id))
    }
    const missing = value.coverage.some(item => item.status === 'missing') || value.missing_evidence.length
    if (value.status === 'pass') ensure(!value.findings.length && !missing && value.reason === null, 'INCOMPLETE_REVIEW')
    else if (value.status === 'findings') ensure(value.findings.length && !missing, 'INCOMPLETE_REVIEW')
    else ensure(value.reason?.trim(), 'INCOMPLETE_REVIEW')
    return
  }
  if (kind === 'correction_response') {
    const review = context.review
    ensure(review, 'MISSING_CONTEXT')
    stamp(value.reviewed_candidate, review.candidate, 'STALE_CANDIDATE')
    stamp(value.new_candidate, context.next_candidate, 'STALE_CANDIDATE')
    ensure(value.review_submission_id === review.submission_id, 'INVALID_CORRECTION')
    const ids = value.dispositions.map(item => item.finding_id)
    ensure(ids.length === new Set(ids).size && sameSet(ids, review.findings.map(item => item.finding_id)), 'INVALID_CORRECTION')
    ensure(sameSet(value.remaining_disagreements, value.dispositions.filter(item => item.status !== 'corrected').map(item => item.finding_id)), 'INVALID_CORRECTION')
    for (const item of value.dispositions) {
      ensure(item.changed_artifact_ids.every(id => Object.hasOwn(entries, id)), 'INVALID_CORRECTION')
      if (item.status === 'corrected') {
        ensure(context.previous_manifest, 'MISSING_CONTEXT')
        ensure(context.previous_manifest.task_id === value.task_id && context.previous_manifest.manifest_id === review.manifest.manifest_id && context.previous_manifest.revision === review.manifest.revision, 'INVALID_CORRECTION')
        const previous = Object.fromEntries(context.previous_manifest.artifacts.map(entry => [entry.artifact_id, entry]))
        ensure(item.changed_artifact_ids.every(id => !Object.hasOwn(previous, id) || entries[id].sha256 !== previous[id].sha256), 'INVALID_CORRECTION')
        ensure(item.changed_artifact_ids.length && value.new_candidate.candidate_id === value.reviewed_candidate.candidate_id && value.new_candidate.revision > value.reviewed_candidate.revision && value.new_candidate.sha256 !== value.reviewed_candidate.sha256, 'INVALID_CORRECTION')
      }
    }
    return
  }
  stamp(value.candidate, context.candidate, 'STALE_CANDIDATE')
  const review = context.review, research = context.research_package
  ensure(review && research, 'MISSING_CONTEXT')
  validateContract('review_result', review, context)
  ensure(review.status === 'pass' && equal(review.candidate, value.candidate) && equal(review.manifest, value.manifest) && review.submission_id === value.review_submission_id, 'FINALIZATION_BLOCKED')
  ensure(context.task_state?.status === 'ready' && context.task_state.version === value.expected_task_version, 'FINALIZATION_BLOCKED')
  stamp(value.destination, context.destination, 'VERSION_CONFLICT')
  ensure((value.destination.mode === 'create') === (value.destination.expected_report_revision === null), 'VERSION_CONFLICT')
  stamp(value.draft_report, research.draft_report, 'STALE_CANDIDATE')
  const publication = context.evidence[research.publication.artifact_id]
  ensure(value.payload_sha256 === publication.sha256 && equal(value.outputs, publication.content.outputs), 'STALE_CANDIDATE')
  ensure(value.outputs.workspaceResult.kind === 'document' && value.outputs.workspaceResult.reportId === value.destination.report_id, 'INVALID_WORKSPACE_OUTPUTS')
  workspace(value.outputs, value.request_id)
  ensure(value.correction_response_id === (context.correction?.response_id ?? null), 'FINALIZATION_BLOCKED')
  if (context.correction) ensure(context.correction.task_id === value.task_id && !context.correction.remaining_disagreements.length && equal(context.correction.new_candidate, value.candidate), 'FINALIZATION_BLOCKED')
}

// This adapter is ready for the Host wiring in Phases 5–6. It grants no tools,
// registers no Agent, and never edits a project, report or workspace output.
export function createReviewSubmissionHandler({ binding, context, accept }) {
  const fixed = structuredClone({ binding, context })
  ensure(typeof accept === 'function')
  return async ({ input, identity }) => {
    ensure(fixed.binding.role === 'reviewer' && equal(identity, fixed.binding) &&
      identity.task_id === fixed.context.task.task_id && identity.node_id === fixed.context.task.reviewer_node_id, 'TASK_MISMATCH')
    ensure(input && sameSet(Object.keys(input), ['review']))
    validateContract('review_result', input.review, fixed.context)
    ensure(input.review.child_task_id === identity.child_task_id && input.review.attempt_id === identity.attempt_id, 'TASK_MISMATCH')
    const payload = Buffer.from(JSON.stringify(input.review), 'utf8')
    const digest = createHash('sha256').update(payload).digest('hex')
    let receipt
    try {
      receipt = await accept({ submission_id: input.review.submission_id, task_id: identity.task_id,
        child_task_id: identity.child_task_id, attempt_id: identity.attempt_id, sha256: digest,
        payload_json: payload.toString('utf8') })
    } catch (error) {
      if (error instanceof CollaborationError) throw error
      throw new CollaborationError('STORAGE_UNAVAILABLE')
    }
    ensure(receipt?.submission_id === input.review.submission_id && receipt.sha256 === digest, 'EVIDENCE_CONFLICT')
    return { status: 'accepted', submission_id: receipt.submission_id, sha256: digest }
  }
}

// A small CLI used by the shared cross-language corpus, not a public service.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const raw = readFileSync(0, 'utf8')
  try {
    ensure(Buffer.byteLength(raw) <= 8 * 1024 * 1024, 'EVIDENCE_LIMIT')
    const cases = JSON.parse(raw)
    console.log(JSON.stringify(cases.map(item => {
      try { validateContract(item.kind, item.payload, item.context); return { valid: true } }
      catch (error) { return { valid: false, code: error.code ?? 'INVALID_CONTRACT' } }
    })))
  } catch { console.log(JSON.stringify({ valid: false, code: 'INVALID_CONTRACT' })); process.exitCode = 1 }
}
