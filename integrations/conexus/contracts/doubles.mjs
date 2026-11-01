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

/** Waits `delayMs`, rejecting as soon as `signal` aborts and leaking neither timer nor listener. */
function settleAfter(delayMs, signal) {
  return new Promise((done, fail) => {
    const finish = outcome => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      outcome()
    }
    const onAbort = () => finish(() => fail(signal.reason))
    const timer = setTimeout(() => finish(done), delayMs)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

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
      // Checked before the timer is scheduled: an `abort` event is not
      // replayed for a signal that was already aborted, so waiting first would
      // sit out the whole delay on a Run that is already cancelled.
      params.signal?.throwIfAborted()
      if (delayMs) await settleAfter(delayMs, params.signal)
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
 * A financial Tool node body double: the shipped command-and-args contract with
 * one asynchronous job, and nothing behind it.
 *
 * This mirrors `alphalab_project_run`, not the simplified example tool. That
 * distinction is the whole point. The production tool has **no single success
 * envelope**: a refusal is `publicFailure`'s five fields, a submission is
 * `{ status, job_id, strategy_id }`, a status read is a wide job record, and
 * `backtest.summary` returns the backend payload unchanged. A double that
 * invented a uniform `{ success, command }` envelope would let a benchmark
 * reward calls the application rejects, and would hide which identifier each
 * command actually takes — `args.job_id` for a status read, `args.backtest_id`
 * for a summary.
 *
 * The command surface is deliberately five of the shipped 44, declared in
 * `financialTool.reducedCommandSurface` so a benchmark reads the limit rather
 * than discovering it. Required arguments, confirmation gates and refusal
 * wording all match the shipped tool, and
 * tests/conexus-financial-tool-contracts.test.mjs drives the production code
 * and this double through identical sequences to keep them that way.
 *
 * `steps` is how many status reads a job needs before it reaches a terminal
 * state, so submit/poll/wait/read is exercised without a backtest engine and
 * without a sleep.
 */
export function contractFinancialTool({ steps = 2, metrics = { total_return: 0.12, sharpe: 0.77 } } = {}) {
  const jobs = new Map()
  let nextJob = 0

  // The shipped tool's own wording, so a benchmark Agent reads the same text
  // from either backend and cannot learn to correct one but not the other.
  const failure = summary => assertContract('financial.tool.failure', {
    status: 'failed', error_code: 'PROJECT_RUN_FAILED',
    error_summary: String(summary).split(/\r?\n/, 1)[0].slice(0, 500),
    error_details: {}, log_reference: null,
  })
  const required = (args, key, command) => {
    const value = args[key]
    if (value === undefined || value === null || value === '') throw new Error(`${command} requires args.${key}`)
    return value
  }

  return async input => {
    const violations = checkContract('financial.tool.request', input)
    if (violations.length) {
      return failure(`Invalid tool input: ${violations[0].path} ${violations[0].keyword}.`)
    }
    const { command } = input
    const args = input.args ?? {}
    try {
      if (command === 'data.status') {
        return assertContract('financial.tool.result', {
          status: 'succeeded', data_status: 'ready', ready: true, total: 1,
          datasets: [{ id: 'rq.bars', rows: 690, status: 'ready' }],
        })
      }
      if (command === 'backtest.run') {
        // The gate is enforced before the arguments are read, exactly as
        // `requirePython` runs before `required` in the shipped tool.
        if (input.confirm_python_execution !== true) {
          throw new Error(`${command} requires confirm_python_execution=true`)
        }
        const projectId = String(required(args, 'project_id', command))
        required(args, 'start_date', command)
        required(args, 'end_date', command)
        const jobId = `job-${++nextJob}`
        jobs.set(jobId, { remaining: steps, status: 'queued', projectId, backtestId: null })
        return assertContract('financial.tool.job', {
          status: 'queued', job_id: jobId, strategy_id: String(args.strategy_id ?? 'main'),
        })
      }
      if (command === 'backtest.status' || command === 'backtest.wait') {
        const jobId = String(required(args, 'job_id', command))
        const job = jobs.get(jobId)
        if (!job) throw new Error(`${command} requires args.job_id`)
        // A status read advances one step; a wait runs to a terminal state, as
        // the shipped tool's server-side bounded wait does.
        job.remaining -= command === 'backtest.wait' ? job.remaining : Math.min(job.remaining, 1)
        job.status = job.remaining > 0 ? 'running' : 'succeeded'
        if (job.status === 'succeeded') job.backtestId ??= jobId.replace('job-', 'bt-')
        return assertContract('financial.tool.job', {
          status: job.status, job_id: jobId, backtest_id: job.backtestId,
          project_id: job.projectId, error_code: null, error_summary: null,
          error_details: {}, log_reference: null,
          metrics: job.status === 'succeeded' ? metrics : {},
        })
      }
      if (command === 'backtest.summary') {
        const backtestId = String(required(args, 'backtest_id', command))
        const job = [...jobs.values()].find(entry => entry.backtestId === backtestId)
        if (!job) throw new Error('The backtest has not finished.')
        // Transport passthrough: the shipped tool returns the backend payload
        // unchanged here, so the double returns a payload and claims no
        // envelope. Nothing structural may be assumed of it.
        return { backtest_id: backtestId, status: 'succeeded', metrics }
      }
      throw new Error('Unsupported project-run command. Read the injected AlphaLab SDK skill for valid commands.')
    } catch (reason) {
      return failure(reason?.message ?? reason)
    }
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
