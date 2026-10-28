// Credential hygiene for anything that leaves an adapter: a tool result, a log
// line, a diagnostic export.
//
// The adapters already avoid returning keys — `web-search.mjs` resolves the
// Brave key locally and never echoes it, and `createModelCompletion` reports
// only a provider's HTTP status rather than its error body. The problem is
// that nothing proves it stays that way. `assertNoSecrets` turns "we were
// careful" into a test, and `redact` gives diagnostics a safe default for the
// cases where a payload has to be written down at all.
//
// Two distinct jobs, deliberately not merged:
//
//   redact          structural, for output you intend to keep. It does not
//                   need to know the secret values.
//   assertNoSecrets value-based, for tests. It needs the real values, and
//                   fails loudly when one of them survives anywhere.

export const REDACTED = '[redacted]'

// Matched against a property name, case-insensitively. `authorization` covers
// the header shape; `*_key` and `*_token` cover settings files like
// secrets/model-providers.json and search-providers.json.
const SENSITIVE_NAME = /(^|[_-])(api[_-]?key|key|token|secret|password|credential|authorization)([_-]|$)/i

const BEARER = /\bBearer\s+[\w.\-~+/]+=*/gi
// A 40-64 character hex run is a digest or a session id, not prose. AlphaLab's
// Project-Run tool strips the same shape, so the two agree.
const HEX_RUN = /\b[0-9a-f]{40,64}\b/gi

/** Replaces userinfo in any URL, so https://user:pw@host never round-trips. */
function redactUrlCredentials(text) {
  return text.replace(/\b([a-z][a-z0-9+.-]*):\/\/[^/\s@]*@/gi, `$1://${REDACTED}@`)
}

function redactText(text) {
  return redactUrlCredentials(text).replace(BEARER, `Bearer ${REDACTED}`).replace(HEX_RUN, REDACTED)
}

/**
 * Returns a copy of `value` with credential-shaped content replaced.
 *
 * Structural, so it is safe on a payload whose secrets are unknown: any
 * property whose *name* looks like a credential is replaced whole, and every
 * remaining string is scrubbed for bearer tokens, URL userinfo and digests.
 */
export function redact(value, depth = 8) {
  if (typeof value === 'string') return redactText(value)
  if (depth <= 0 || value === null || typeof value !== 'object') return value
  if (Array.isArray(value)) return value.map(item => redact(item, depth - 1))
  return Object.fromEntries(Object.entries(value).map(([name, item]) => [
    name,
    SENSITIVE_NAME.test(name) ? (item === undefined || item === '' ? item : REDACTED) : redact(item, depth - 1),
  ]))
}

/** Every secret-looking string reachable from `value`, for assertions. */
function serialize(value) {
  return JSON.stringify(value, (_name, item) => (item instanceof Error ? { message: item.message, code: item.code } : item))
}

/**
 * Throws when any of `secrets` appears anywhere in `value`.
 *
 * Intended for contract tests: configure an adapter with known fixture
 * credentials, exercise every path including its failures, and assert none of
 * them reached the response, the error message or the exported diagnostics.
 * Short or empty values are rejected outright, because asserting the absence
 * of "" or "a" would pass vacuously and read as coverage.
 */
export function assertNoSecrets(value, secrets, context = 'adapter output') {
  const serialized = serialize(value) ?? ''
  for (const secret of secrets) {
    if (typeof secret !== 'string' || secret.length < 8) {
      throw new Error(`assertNoSecrets needs fixture secrets of at least 8 characters; got ${JSON.stringify(secret)}.`)
    }
    if (serialized.includes(secret)) {
      throw new Error(`${context} disclosed a credential: ${secret.slice(0, 4)}… appears in ${serialized.slice(0, 200)}`)
    }
  }
  return value
}
