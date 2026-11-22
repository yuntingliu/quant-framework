import { createHash } from 'node:crypto'

// Optional context compaction for issue #22 (ALPHALAB_AGENT_CONTEXT_COMPACTION=1).
// Older, large tool results sent to the provider are shortened; the stored
// conversation is never changed. Numbers, identifiers, dates, statuses, errors
// and confirmation flags are kept; long arrays and long free text are trimmed.

const KEEP_RECENT = 6
const MIN_CHARS = 4000
const MAX_ITEMS = 6
const MAX_TEXT = 300
// Results the Agent still acts on, or whose loss would hide a failure or a permission.
const PROTECTED_TOOLS = new Set(['edit', 'create', 'complete', 'request_user_input'])
const PROTECTED_COMMANDS = new Set(['workspace.outputs.prepare'])
// Values under these keys are kept whole, however long.
const PROTECTED_KEY = /^(confirm_.*|pending.*|permissions?|authori[sz].*|status|state|errors?|error_.*|blocker.*|warnings?|ids?|.*_ids?|as_?of.*|pit.*|point_in_time.*|date|.*_date|.*_dates|start|end|window|execution_reliable|research_valid|.*invalid_reasons)$/i
const NOTE = 'Older tool result shortened by the host. Numbers, ids, dates, statuses, errors and confirmations are kept; '
  + 'long lists and text are trimmed. Repeat the read if the full result is needed.'

const hash = text => createHash('sha256').update(text).digest('hex')

function shorten(value, key = '') {
  if (PROTECTED_KEY.test(key)) return value
  if (typeof value === 'string') {
    return value.length > MAX_TEXT ? `${value.slice(0, MAX_TEXT)}…[+${value.length - MAX_TEXT} chars]` : value
  }
  if (Array.isArray(value)) {
    const items = value.length > MAX_ITEMS ? [...value.slice(0, 4), ...value.slice(-2)] : value
    const kept = items.map(item => shorten(item))
    return value.length > MAX_ITEMS ? { _items: value.length, _first: kept.slice(0, 4), _last: kept.slice(-2) } : kept
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, shorten(item, name)]))
  }
  return value
}

const failed = result => result?.success === false || (result?.output ?? result)?.status === 'failed'

/** Compact form of one tool result, or null when it must stay whole. */
export function compactToolResult(content) {
  if (typeof content !== 'string' || content.length < MIN_CHARS) return null
  let result
  try { result = JSON.parse(content) } catch { return null }
  if (!result || typeof result !== 'object' || failed(result)) return null
  const compacted = JSON.stringify({ _compacted: NOTE, result: shorten(result) })
  return compacted.length < content.length ? compacted : null
}

function commandsByCall(messages) {
  const calls = new Map()
  for (const message of messages) {
    for (const call of message.tool_calls ?? []) {
      let command = null
      try { command = JSON.parse(call.function?.arguments || '{}')?.input?.command ?? null } catch {}
      calls.set(call.id, { tool: call.function?.name, command })
    }
  }
  return calls
}

/** Wraps a per-run completion; the cache lives with the run, so nothing crosses runs. */
export function compactedCompletion(completion) {
  const cache = new Map()
  const stats = { computed: 0, reused: 0 }
  const wrapped = async input => {
    const messages = input.messages ?? []
    const toolIndexes = messages.flatMap((message, index) => message.role === 'tool' ? [index] : [])
    const recent = new Set(toolIndexes.slice(-KEEP_RECENT))
    const calls = commandsByCall(messages)
    let compactedCount = 0
    let savedChars = 0
    const sent = messages.map((message, index) => {
      if (message.role !== 'tool' || recent.has(index)) return message
      const call = calls.get(message.tool_call_id)
      if (PROTECTED_TOOLS.has(call?.tool ?? message.name) || PROTECTED_COMMANDS.has(call?.command)) return message
      const key = `${message.tool_call_id}:${hash(String(message.content))}`
      let compacted = cache.get(key)
      if (compacted === undefined) {
        compacted = compactToolResult(message.content)
        cache.set(key, compacted)
        stats.computed += 1
      } else {
        stats.reused += 1
      }
      if (compacted === null) return message
      compactedCount += 1
      savedChars += message.content.length - compacted.length
      return { ...message, content: compacted }
    })
    return completion({ ...input, messages: sent, compaction: { messages: compactedCount, saved_chars: savedChars } })
  }
  wrapped.stats = stats
  return wrapped
}
