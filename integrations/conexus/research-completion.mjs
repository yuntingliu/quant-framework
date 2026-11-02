/** Give the model an explicit, run-local accounting of its tool budget. */
export function researchCompletion(complete, { maxToolCalls = 40 } = {}) {
  if (!Number.isSafeInteger(maxToolCalls) || maxToolCalls < 1) throw new Error('Invalid research tool budget')
  const runs = new WeakMap()
  return async request => {
    let state = runs.get(request.signal)
    if (!state) { state = { attempted: 0 }; runs.set(request.signal, state) }
    const remaining = maxToolCalls - state.attempted
    if (remaining <= 0) throw new Error('Research tool budget exhausted before delivery')
    const guidance = `Host tool budget: ${state.attempted}/${maxToolCalls} calls attempted; ${remaining} remain. Every find, observe, use, create, edit, request_user_input and complete counts, including each call in a batch. Reserve at least 5 calls for report creation/update, workspace.outputs.prepare, atomic edit and complete. Reuse discovered nodes. Commands listed in the SDK are inputs to the two AlphaLab tools, not separately discoverable tools. ${remaining <= 6 ? 'Finish now with the evidence already available; report missing evidence honestly. Do not start another investigation.' : 'Stop investigating as soon as the requested conclusion has sufficient evidence.'}`
    const result = await complete({ ...request, messages: [
      ...request.messages, { role: 'system', content: guidance },
    ] })
    state.attempted += result.tool_calls?.length ?? 0
    if (state.attempted > maxToolCalls) throw new Error('Research tool budget exceeded; entire tool batch rejected')
    return result
  }
}
