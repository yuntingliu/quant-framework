// Per-run model-call and time budget for issue #22. The caller sets it in
// workspaceContext.budget; the host enforces it on every model call, so the
// Agent stops with an explicit summary instead of being cancelled mid-work.
// Notes are appended to the provider request only, never to the stored
// conversation, so the cached prompt prefix is unchanged.

const RESERVE_CALLS = 4
const RESERVE_SECONDS = 120
const FINAL_SECONDS = 45

const positive = value => Number.isInteger(value) && value > 0 ? value : null

const textOf = content => typeof content === 'string' ? content
  : Array.isArray(content) ? content.filter(part => part?.type === 'text').map(part => part.text).join('') : ''

/** The budget of the latest user request that carries one, or null. */
export function readRunBudget(messages = []) {
  for (const message of [...messages].reverse()) {
    if (message?.role !== 'user') continue
    let request
    try { request = JSON.parse(textOf(message.content)) } catch { continue }
    const budget = request?.workspaceContext?.budget
    const calls = positive(budget?.max_model_calls)
    const seconds = positive(budget?.max_seconds)
    if (calls || seconds) return { max_model_calls: calls, max_seconds: seconds }
  }
  return null
}

/** Where this call stands: normal work, wrap-up, or the last call the budget allows. */
export function budgetPhase(budget, call, elapsedSeconds) {
  const { max_model_calls: calls, max_seconds: seconds } = budget
  const reserveSeconds = seconds ? Math.min(RESERVE_SECONDS, seconds * 0.25) : 0
  const finalSeconds = seconds ? Math.min(FINAL_SECONDS, seconds * 0.1) : 0
  if ((calls && call >= calls) || (seconds && elapsedSeconds >= seconds - finalSeconds)) return 'final'
  if ((calls && call > calls - RESERVE_CALLS) || (seconds && elapsedSeconds >= seconds - reserveSeconds)) return 'wrap_up'
  return 'normal'
}

const DELIVERY = 'If the request is not finished, complete with status=blocked and blocker "run budget exhausted". '
  + 'The summary must say what was done, which parts or candidates were not evaluated, and state no conclusion '
  + 'that this run\'s tool evidence does not support.'

function note(budget, phase, call, elapsed) {
  const { max_model_calls: calls, max_seconds: seconds } = budget
  const used = [calls ? `model call ${call} of ${calls}` : null, seconds ? `${elapsed}s of ${seconds}s` : null]
    .filter(Boolean).join(', ')
  if (phase === 'final') {
    return `[Run budget, set by the host] Budget exhausted (${used}). This is the last model call: call complete now. ${DELIVERY}`
  }
  if (phase === 'wrap_up') {
    return `[Run budget, set by the host] Budget nearly exhausted (${used}). Start no new research, backtests, syncs or waits. `
      + `Deliver now: workspace.outputs.prepare, the single edit, then complete. ${DELIVERY}`
  }
  const workLeft = seconds ? Math.max(0, Math.floor(seconds - Math.min(RESERVE_SECONDS, seconds * 0.25) - elapsed)) : null
  return `[Run budget, set by the host] ${used} used. Plan the remaining work to fit, keeping the last `
    + `${RESERVE_CALLS} calls for delivering outputs`
    + (workLeft !== null ? `; ${workLeft}s remain for work, so keep any wait_seconds at or below that.` : '.')
}

/** Wraps a per-run completion; runs without a budget pass through unchanged. */
export function budgetedCompletion(completion, { clock = Date.now } = {}) {
  let started = null
  let calls = 0
  let budget
  return async input => {
    if (budget === undefined) budget = readRunBudget(input.messages)
    if (!budget) return completion(input)
    started ??= clock()
    calls += 1
    const elapsed = Math.round((clock() - started) / 1000)
    const phase = budgetPhase(budget, calls, elapsed)
    const messages = [...(input.messages ?? []), { role: 'user', content: note(budget, phase, calls, elapsed) }]
    const final = phase === 'final' && input.tools?.some(tool => tool.function?.name === 'complete')
    // No tool_choice: DeepSeek thinking mode rejects any forced choice with HTTP 400.
    return completion({
      ...input, messages,
      ...(final ? { tools: input.tools.filter(tool => tool.function?.name === 'complete') } : {}),
      runBudget: { phase, call: calls, elapsed_s: elapsed, ...budget },
    })
  }
}
