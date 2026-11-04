// Test orchestration only. Every Agent turn and native dispatch uses the real Host.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, writeFile, realpath } from 'node:fs/promises'
import { resolve, join, relative, dirname, isAbsolute } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createCollaborationHost, COLLABORATION_NODES as nodes } from '../../integrations/conexus/collaboration/host.mjs'
import { createControlClient } from '../../integrations/conexus/collaboration/control-client.mjs'
import { runScenario as runHostScenario } from './collaboration-host-scenarios.mjs'
import { runBudgetScenario } from '../check_collaboration_budget.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const digest = bytes => createHash('sha256').update(bytes).digest('hex')
const tool = (name, args) => ({ id: randomUUID(), type: 'function', function: { name, arguments: JSON.stringify(args) } })
const use = (node_id, input) => tool('use', { node_id, capability: 'tool.invoke', input })
const finish = status => tool('complete', { status, summary: 'Synthetic foundation acceptance.', ...(status === 'blocked' ? { blocker: 'Review did not approve the current candidate.' } : {}) })
const localScenarios = ['clean', 'seeded-formula', 'seeded-pit', 'seeded-report', 'seeded-claim', 'missing-evidence',
  'disputed', 'correction', 'stale-candidate', 'hash-mismatch', 'duplicate-delivery', 'staging-interruption', 'accepted-recovery']
export const acceptanceScenarios = [...localScenarios, 'forbidden-write', 'ownership', 'blocked-result',
  'cancel', 'timeout', 'root-deadline', 'recovery', 'grants', 'missing-grant', 'root-input', 'budget']

export async function privateOutput(output) {
  const boundary = await realpath(join(root, 'build'))
  output = resolve(output)
  const inside = value => { const suffix = relative(boundary, value); return suffix && suffix !== '..' && !suffix.startsWith('..' + (process.platform === 'win32' ? '\\' : '/')) && !isAbsolute(suffix) }
  assert(inside(output), 'Output must be inside this checkout\'s ignored build directory.')
  let existing = output
  for (;;) {
    try { assert(inside(await realpath(existing)) || await realpath(existing) === boundary); break }
    catch (error) { if (error.code !== 'ENOENT') throw error; existing = dirname(existing) }
  }
  await mkdir(output, { recursive: true })
  assert(inside(await realpath(output)))
  return output
}

function python(pythonExecutable, output, operation, scenario) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(pythonExecutable, ['scripts/prepare_collaboration_acceptance.py', operation, '--output', output,
      ...(scenario ? ['--scenario', scenario] : [])], { cwd: root, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = '', stderr = ''
    const timer = setTimeout(() => { child.kill(); reject(new Error('Fixture operation timed out.')) }, 45000)
    child.on('error', error => { clearTimeout(timer); reject(error) })
    child.stdout.on('data', chunk => { stdout += chunk; if (stdout.length > 4 * 1024 * 1024) child.kill() })
    child.stderr.on('data', chunk => { if (stderr.length < 16000) stderr += chunk })
    child.on('exit', async code => {
      clearTimeout(timer)
      if (operation === 'crash' && code === 17) return resolvePromise({ dedicated_writer_exit: 17 })
      if (code !== 0) {
        await writeFile(join(output, 'fixture-diagnostic.log'), stderr)
        return reject(new Error('Fixture operation failed; inspect the local diagnostic log.'))
      }
      try { resolvePromise(JSON.parse(stdout)) } catch { reject(new Error('Invalid fixture response.')) }
    })
  })
}
const save = (output, name, value) => writeFile(join(output, `${name}.json`), JSON.stringify(value, null, 2) + '\n')

export async function runAcceptanceScenario({ scenario, output, pythonExecutable }) {
  output = await privateOutput(output)
  const core = JSON.parse(await readFile(join(root, 'integrations/conexus/runtime.lock.json')))
  const identity = { core_revision: core.revision, node_version: process.version, mode: 'scripted', external_model_calls: 0, final_publication: false }
  try {
    let result
    if (scenario === 'seeded-errors' || scenario === 'stale-evidence') {
      const cases = scenario === 'seeded-errors' ? localScenarios.filter(s => s.startsWith('seeded-')) : ['stale-candidate', 'hash-mismatch']
      result = { status: 'passed', cases: [] }
      for (const item of cases) result.cases.push(await runAcceptanceScenario({ scenario: item, output: join(output, item), pythonExecutable }))
    } else if (scenario === 'budget') result = await runBudgetScenario({ scenario: 'exhausted', output, pythonExecutable })
    else if (!localScenarios.includes(scenario)) {
      assert(acceptanceScenarios.includes(scenario))
      result = await runHostScenario({ scenario, output, pythonExecutable })
    } else result = await runDevelopmentScenario({ scenario, output, pythonExecutable })
    result = { ...result, ...identity, scenario, artifacts: output }
    await save(output, 'verification', result)
    return result
  } catch (error) {
    await save(output, 'verification', { ...identity, scenario, status: 'failed', error_code: error.code ?? 'ACCEPTANCE_ASSERTION_FAILED', artifacts: output })
    throw error
  }
}

async function runDevelopmentScenario({ scenario, output, pythonExecutable }) {
  const fixture = await python(pythonExecutable, output, 'prepare', scenario)
  const initial = fixture.versions[scenario === 'stale-candidate' ? 1 : 0]
  if (scenario === 'staging-interruption') {
    await python(pythonExecutable, output, 'crash')
    await save(output, 'staging', await python(pythonExecutable, output, 'check-staging'))
  }
  const businessPath = join(output, 'business.json'), dbPath = join(output, 'evidence/evidence.sqlite3')
  await writeFile(businessPath, '{"source":"original","reports":["historical"],"outputs":null}\n')
  const before = digest(await readFile(businessPath)), evidenceBefore = digest(await readFile(dbPath))
  let host, options, writes = 0, rootTurns = 0, calls = 0, active = initial, restarted = false
  const childTurns = new Map(), observed = [], toolResults = [], audit = []
  options = { pythonExecutable, evidenceRoot: join(output, 'evidence'), storeRoot: join(output, 'control'),
    taskId: fixture.task_id, candidate: initial.candidate, scopes: ['model.invoke', 'evidence.read', 'review.submit', 'review.delegate'],
    budget: { max_model_calls: 16, max_tool_calls: 64, max_output_tokens: 10, max_total_tokens: 320, max_cost_micros: 1000, currency: 'USD', wall_time_ms: 120000 },
    operations: [{ id: 'research-write', version: '1', requiredScopes: ['project.write'], inputSchema: { type: 'object' },
      execute: () => { writes++; throw new Error('Unexpected business write.')}}],
    modelProvider: {
      configuration: { provider_id: 'in-process-scripted', model_id: 'foundation-fixture', profile_id: 'fixture-v1',
        configuration_id: 'phase8-scripted-v1', bounds_id: 'synthetic-fixed-input-10-v1', supports_output_cap: true,
        pricing: { id: 'synthetic-v1', currency: 'USD', input_micros_per_million: 1000000, output_micros_per_million: 2000000 } },
      boundInput: () => 10,
      async complete({ messages, identity, maxTokens }) {
        calls++
        assert(maxTokens >= 10)
        const requestText = JSON.stringify(messages)
        assert(!requestText.includes('expected_status') && !requestText.includes('oracle.json') && !requestText.includes('oracle_checks'))
        // Only safe role/turn metadata is emitted. A scripted response knows its
        // expected answer; that response is never added to the initial workspace.
        audit.push({ role: identity.role, child_task_id: identity.child_task_id ?? null, oracle_in_request: false })
        const results = messages.filter(m => m.role === 'tool').map(m => JSON.parse(m.content))
        toolResults.push(...results)
        let responseCalls
        if (identity.role === 'researcher') {
          rootTurns++
          if (rootTurns === 1) responseCalls = [use(nodes.control, { operation: 'start', delegation: active.delegation }),
            ...(scenario === 'duplicate-delivery' ? [use(nodes.control, { operation: 'start', delegation: active.delegation })] : [])]
          else if (rootTurns === 2 || scenario === 'correction' && rootTurns === 4) responseCalls = [use(nodes.control, { operation: 'wait', child_task_id: active.delegation.child_task_id })]
          else if (scenario === 'correction' && rootTurns === 3) {
            const state = await host.snapshot()
            assert.equal(state.children[0].review.status, 'findings')
            active = fixture.versions[1]
            await host.setCandidate(active.candidate) // Fixture driver transition, not a financial correction engine.
            responseCalls = [use(nodes.control, { operation: 'start', delegation: active.delegation })]
          } else responseCalls = [finish(['clean', 'correction', 'duplicate-delivery', 'staging-interruption'].includes(scenario) ? 'done' : 'blocked')]
        } else {
          const turn = (childTurns.get(identity.child_task_id) ?? 0) + 1
          childTurns.set(identity.child_task_id, turn)
          if (turn === 1) {
            if (scenario === 'hash-mismatch') await python(pythonExecutable, output, 'corrupt')
            const review = scenario === 'stale-candidate'
              ? { ...fixture.versions[0].review, child_task_id: active.delegation.child_task_id, attempt_id: active.delegation.attempt_id, round: active.delegation.round }
              : active.review
            responseCalls = [use(nodes.evidence, { operation: 'manifest', manifest_id: active.manifest.manifest_id }),
              use(nodes.evidence, { operation: 'read', manifest_id: active.manifest.manifest_id, artifact_id: 'report' }),
              ...active.evidence_ids.map(artifact_id => use(nodes.evidence, { operation: 'read', manifest_id: active.manifest.manifest_id, artifact_id })),
              use(nodes.submit, { review })]
            if (scenario === 'duplicate-delivery') responseCalls.push(use(nodes.submit, { review }), use(nodes.submit, {
              review: { ...review, coverage: review.coverage.map((item, index) => index ? item : { ...item, reason: 'A conflicting but shape-valid replay.' }) } }))
          } else {
            observed.push(...results)
            if (scenario === 'accepted-recovery' && !restarted) {
              restarted = true
              const recovered = await createCollaborationHost(options)
              await recovered.recover()
              await host.cancel()
              const control = createControlClient({ pythonExecutable, storeRoot: options.storeRoot, evidenceRoot: options.evidenceRoot, taskId: fixture.task_id })
              const payload_json = JSON.stringify(active.review)
              const receipt = await control('submit', { identity, child_task_id: identity.child_task_id,
                submission_id: active.review.submission_id, payload_json, sha256: digest(payload_json) })
              await save(output, 'recovery-receipt', receipt)
              await assert.rejects(recovered.run())
            }
            responseCalls = [finish(['hash-mismatch', 'stale-candidate'].includes(scenario) ? 'blocked' : 'done')]
          }
        }
        return { response: { content: null, finish_reason: 'tool_calls', tool_calls: responseCalls },
          usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 } }
      },
    },
  }
  host = await createCollaborationHost(options)
  const result = await host.run()
  await host.flushAccounting()
  const state = await host.snapshot(), accounting = await host.accounting(), events = await host.events(), requests = await host.requests()
  assert.equal(accounting.totals.model_calls, calls)
  assert.equal(accounting.totals.total_tokens, calls * 20)
  assert.equal(accounting.totals.pending, 0)
  assert.equal(result.pending_interactions, 0)
  assert.equal(writes, 0)
  assert.equal(digest(await readFile(businessPath)), before)
  if (scenario !== 'hash-mismatch') assert.equal(digest(await readFile(dbPath)), evidenceBefore)
  if (!['hash-mismatch', 'accepted-recovery'].includes(scenario)) {
    for (const artifact of ['report', ...active.evidence_ids]) assert(observed.some(r => r.artifact_id === artifact && Object.hasOwn(r, 'value')))
  }
  if (scenario === 'duplicate-delivery') {
    assert(toolResults.some(r => r.error_code === 'DUPLICATE_CHILD_TASK'))
    assert(observed.some(r => r.error_code === 'DUPLICATE_CONFLICT'))
    const receipts = observed.filter(r => r.status === 'accepted')
    assert.equal(receipts.length, 2)
    assert.deepEqual(receipts[0], receipts[1])
  }
  if (scenario === 'stale-candidate') assert(observed.some(r => r.error_code === 'STALE_CANDIDATE' || r.error_code === 'EVIDENCE_CONFLICT'))
  if (scenario === 'hash-mismatch') assert(observed.some(r => r.error_code === 'INTEGRITY_ERROR'))
  if (scenario === 'accepted-recovery') {
    assert(restarted)
    assert.equal(state.status, 'blocked')
    assert.equal(state.children[0].review.submission_id, active.review.submission_id)
  }
  const preflight = await python(pythonExecutable, output, 'verify')
  const fingerprints = { business_before: before, business_after: digest(await readFile(businessPath)),
    evidence_before: evidenceBefore, evidence_after: digest(await readFile(dbPath)),
    deliberate_evidence_corruption: scenario === 'hash-mismatch', business_write_callbacks: writes }
  for (const [name, value] of Object.entries({ state, accounting, events, requests, fingerprints, preflight, 'request-audit': audit })) await save(output, name, value)
  return { status: 'passed', task_state: state.status, fixture_id: fixture.fixture_id, input_sha256: fixture.input_sha256,
    review_status: state.children.at(-1)?.review?.status ?? null, model_calls: calls, total_tokens: accounting.totals.total_tokens,
    business_unchanged: true, ...preflight, quality_evaluation: false }
}
