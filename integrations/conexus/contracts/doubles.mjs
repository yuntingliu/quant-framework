// Local test doubles for every adapter in adapter-contracts.v1.json.
//
// These exist so a benchmark can give a candidate Agent the *same* backend the
// product gives it, with the provider accounts removed. Equivalent tool access
// is the whole point of a benchmark: if the harness quietly offers fewer
// commands, a weaker search, or a different failure shape, a score measures the
// harness rather than the Agent.
//
// Every double asserts the contract in both directions, so a double that
// drifts from the real adapter fails here instead of producing a benchmark
// result nobody can compare. They need no network, no credentials and no
// built runtime, so they also run on a fresh clone.
//
// The fixture credentials below are deliberately recognizable strings, not
// placeholders: tests configure an adapter with them, exercise every failure
// path, and assert with `assertNoSecrets` that none of them reached a
// response, an error message or an export.
import { assertContract, checkContract } from './adapters.mjs'

export const TEST_CREDENTIALS = Object.freeze({
  modelApiKey: 'fixture-model-key-0000000000',
  searchApiKey: 'fixture-search-key-000000000',
  workspaceToken: 'fixture-workspace-token-0000',
})

let callCounter = 0

/** One scripted assistant turn that calls a native Agent tool. */
export function toolTurn(name, args, { id = `call-${name}-${callCounter++}` } = {}) {
  return { content: null, finish_reason: 'tool_calls', tool_calls: [{ id, type: 'function', function: { name, arguments: JSON.stringify(args) } }] }
}

/** One scripted assistant turn that only speaks, ending the loop. */
export function textTurn(content) {
  return { content, finish_reason: 'stop' }
}

/**
 * A `complete(request)` adapter driven by a script of turns.
 *
 * Each entry is a turn or a function of (request, index) returning one, so a
 * script can read the tool result the Host fed back. The request is checked
 * against `model.completion.request` on every call and the turn against
 * `model.completion.response`, which is what makes this a contract double
 * rather than a stub: a core change to either shape fails the script.
 */
export function contractCompletion(script, { model } = {}) {
  let index = 0
  const requests = []
  const complete = async request => {
    // `signal` is an AbortSignal and `trace` may carry non-enumerable fields;
    // validate the JSON-representable projection the contract describes.
    assertContract('model.completion.request', JSON.parse(JSON.stringify(request)))
    if (model !== undefined && request.model !== undefined && request.model !== model) {
      throw Object.assign(new Error('The requested model is not configured on this host.'), { code: 'model_not_allowed' })
    }
    request.signal?.throwIfAborted()
    requests.push(request)
    const entry = script[index]
    index += 1
    if (!entry) throw new Error(`The scripted model ran out of turns at turn ${index}.`)
    return assertContract('model.completion.response', typeof entry === 'function' ? entry(request, index - 1) : entry)
  }
  complete.requests = requests
  return complete
}

const searchFailure = (code, error, retryable = false) => ({ result: { success: false, code, error, retryable } })

/**
 * A `web.search` capability double with the real adapter's refusal shape.
 *
 * It reproduces the bounds that matter to a benchmark — query length, result
 * count, cancellation — and it never performs I/O, so an invalid query
 * provably costs nothing. Pass `failWith` to pin a specific failure code, or
 * `delayMs` with an aborted signal to exercise the cancellation path.
 */
export function contractSearch({ sources = [], provider = 'brave', failWith, delayMs = 0 } = {}) {
  return async params => {
    const query = typeof params.input?.query === 'string' ? params.input.query.trim() : ''
    if (!query || query.length > 2000) {
      return searchFailure('invalid_query', 'Provide a nonempty search query of at most 2000 characters.')
    }
    if (failWith) {
      return searchFailure(failWith, `Search failed with ${failWith}.`, failWith === 'search_provider_error' || failWith === 'search_request_failed')
    }
    const requested = Number(params.input.count ?? 5)
    const count = Number.isFinite(requested) ? Math.max(1, Math.min(20, Math.trunc(requested))) : 5
    try {
      if (delayMs) {
        await new Promise((done, fail) => {
          const timer = setTimeout(done, delayMs)
          params.signal?.addEventListener('abort', () => { clearTimeout(timer); fail(params.signal.reason) }, { once: true })
        })
      }
      params.signal?.throwIfAborted()
    } catch {
      // Cancellation and timeout share the real adapter's catch-all code, so a
      // benchmark cannot tell a cancelled search from a broken one. That is
      // deliberate: neither is evidence, and both are retryable.
      return searchFailure('search_request_failed', 'Web search failed or timed out.', true)
    }
    return assertContract('search.response', {
      result: { success: true, provider, results: sources.slice(0, count).map(source => ({
        url: source.url, title: (source.title ?? source.url).slice(0, 500), snippet: (source.snippet ?? '').slice(0, 2000),
      })) },
    })
  }
}

/**
 * A financial Tool node body double: the command-and-args contract with one
 * asynchronous job, and nothing behind it.
 *
 * `backtest.run` stages a job that advances one step per status read, so a
 * benchmark exercises submit/poll/wait/read without a backtest engine and
 * without a sleep. The confirmation gate is enforced here exactly as the real
 * tool enforces it, because a double that executes without the flag would let
 * a benchmark reward an Agent for skipping it.
 */
export function contractFinancialTool({ steps = 2, summary = { total_return: 0.12, execution_reliable: true } } = {}) {
  const jobs = new Map()
  let nextJob = 0
  const reply = value => assertContract('financial.tool.response', value)

  return async input => {
    const violations = checkContract('financial.tool.request', input)
    if (violations.length) {
      return reply({ success: false, command: String(input?.command ?? ''), error: `Invalid tool input: ${violations[0].path} ${violations[0].keyword}.` })
    }
    const { command } = input
    const args = input.args ?? {}
    if (command === 'data.status') return reply({ success: true, command, result: { coverage: 1, rows: 690 } })
    if (command === 'backtest.run') {
      if (input.confirm_python_execution !== true) {
        return reply({ success: false, command, error: 'backtest.run requires confirm_python_execution=true.' })
      }
      const jobId = `job-${++nextJob}`
      jobs.set(jobId, { remaining: steps, status: 'queued' })
      return reply({ success: true, command, job_id: jobId, status: 'queued' })
    }
    if (command === 'backtest.status' || command === 'backtest.wait' || command === 'backtest.summary') {
      const jobId = String(args.job_id ?? '')
      const job = jobs.get(jobId)
      if (!job) return reply({ success: false, command, error: `${command} requires a known args.job_id.` })
      if (command === 'backtest.summary') {
        if (job.status !== 'succeeded') return reply({ success: false, command, error: 'The job has not finished.' })
        return reply({ success: true, command, result: { job_id: jobId, metrics: summary } })
      }
      const advance = command === 'backtest.wait' ? job.remaining : Math.min(job.remaining, 1)
      job.remaining -= advance
      job.status = job.remaining > 0 ? 'running' : 'succeeded'
      // A wait that has not finished is a successful call reporting an
      // unfinished job, never a failed invocation.
      const timedOut = command === 'backtest.wait' && job.remaining > 0
      return reply({ success: true, command, job_id: jobId, status: job.status, progress: { remaining: job.remaining }, ...(timedOut ? { timed_out: true } : {}) })
    }
    return reply({ success: false, command, error: `Unsupported command: ${command}` })
  }
}

/**
 * An `openState`-compatible store that keeps the snapshot in memory.
 *
 * Validates `state.snapshot` on every save, so a storage adapter that writes a
 * shape the real Host would refuse to read back fails at the write rather than
 * on the next restart. It takes no lock, which is the one thing it does not
 * stand in for.
 */
export function memoryStateStore(initial = { schema: 'conexus.local-host.v1', runs: [], nodes: [] }) {
  const state = structuredClone(assertContract('state.snapshot', initial))
  const writes = []
  return {
    state,
    writes,
    save: async () => {
      const snapshot = structuredClone(state)
      assertContract('state.snapshot', snapshot)
      writes.push(snapshot)
      return snapshot
    },
    close: async () => {},
  }
}

/**
 * The profile a substitute adapter must publish to stand in for `reference`.
 *
 * Returned rather than hand-written so a benchmark harness cannot accidentally
 * claim a narrower surface than the Host it is standing in for; pass
 * `capabilityIds` when the substitute really does offer a different set, and
 * `assertEquivalentProfiles` will say whether that is still enough.
 */
export function substituteProfile(reference, { name = 'alphalab.contract-double.v1', capabilityIds } = {}) {
  return assertContract('runtime.adapter.profile', {
    ...structuredClone(reference),
    name,
    ...(capabilityIds ? { capabilityIds } : {}),
  })
}
