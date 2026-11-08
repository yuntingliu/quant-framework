/** Explicit Agent configuration composition. Normal startup uses only JSON.
 * SQLite construction is internal/test-only until coordinated cutover.
 * No environment switch, initialization, adoption, recovery or writable API.
 */
import { addAbortListener } from 'node:events'
import { inspect, types } from 'node:util'
import { ConfigurationStoreReader } from './configuration-store.mjs'
import { configuredCompletion, readActiveProvider, providerFromSnapshot, containsProviderCredential } from './model-providers.mjs'
import { configuredWebSearch, searchExecutionFromSnapshot } from './web-search.mjs'

const aborted = Object.getOwnPropertyDescriptor(AbortSignal.prototype, 'aborted').get
const ENV_KEYS = ['CONEXUS_MODEL_BASE_URL', 'CONEXUS_MODEL_ID', 'CONEXUS_MODEL_API_KEY',
  'ALPHALAB_WEB_SEARCH_PROVIDER', 'BRAVE_SEARCH_API_KEY']
const MESSAGES = Object.freeze({
  INVALID_ARGUMENT: 'Check the configuration runtime arguments.',
  CONFIGURATION_UNAVAILABLE: 'The configuration store is unavailable. Check local settings.',
  MODEL_UNAVAILABLE: 'The model is unavailable. Check model configuration before retrying.',
  RUNTIME_CANCELLED: 'The configuration operation was cancelled.',
  RUNTIME_CLOSED: 'The configuration runtime is closed.',
})

export class ConfigurationRuntimeError extends Error {
  constructor(code) {
    const safe = typeof code === 'string' && Object.hasOwn(MESSAGES, code) ? code : 'CONFIGURATION_UNAVAILABLE'
    super(MESSAGES[safe])
    this.name = 'ConfigurationRuntimeError'
    this.code = safe
  }
  toJSON() { return { code: this.code, message: this.message } }
}

/** The active launcher has this fixed JSON composition, with no backend option. */
export function createJsonAgentConfiguration(providerPath, createModelCompletion, { env = process.env, fetchImpl = fetch } = {}) {
  return Object.freeze({
    backend: 'json',
    complete: configuredCompletion(providerPath, createModelCompletion, env),
    webSearch: configuredWebSearch(providerPath, { env, fetchImpl }),
    testSearch: (settingsOverride, params) => configuredWebSearch(providerPath, { env, fetchImpl, settingsOverride })(params),
    modelConfigured: async () => Boolean(await readActiveProvider(providerPath, env)),
    close: async () => {},
  })
}

/** Explicit root + dependencies only. Owns its reader and closes it on shutdown.
 * One signal identifies one run. Model/search first uses capture independently;
 * each search's profile and referenced model always share one SQLite snapshot.
 */
export function createSqliteAgentConfiguration(root, createModelCompletion, options = {}) {
  let env, fetchImpl, reader
  try {
    if (typeof createModelCompletion !== 'function') throw 0
    const supplied = options.env ?? {}
    env = Object.freeze(Object.fromEntries(ENV_KEYS.map(key => {
      const value = supplied[key]
      if (value !== undefined && typeof value !== 'string') throw 0
      return [key, value?.trim() || '']
    })))
    fetchImpl = options.fetchImpl ?? fetch
    if (typeof fetchImpl !== 'function') throw 0
    reader = new ConfigurationStoreReader(root, options.readerOptions)
  } catch { throw new ConfigurationRuntimeError('INVALID_ARGUMENT') }
  const shutdown = new AbortController(), signals = new WeakMap()
  let closing, closed = false
  function signalFor(signal) {
    if (signal === undefined) return AbortSignal.any([shutdown.signal])
    if (types.isProxy(signal)) throw 0
    aborted.call(signal) // Native brand check; never inspect signal.reason.
    let linked = signals.get(signal)
    if (!linked) {
      linked = AbortSignal.any([signal, shutdown.signal])
      signals.set(signal, linked)
    }
    return linked
  }
  async function invoke(signal, action, code) {
    let subscription
    const state = { notify: null }
    try {
      if (closed) throw 0
      if (aborted.call(signal)) throw 0
      const cancelled = new Promise((_, reject) => {
        state.notify = () => reject(new ConfigurationRuntimeError('RUNTIME_CANCELLED'))
        subscription = addAbortListener(signal, () => state.notify?.())
      })
      const value = await Promise.race([Promise.resolve().then(action), cancelled])
      if (closed || aborted.call(signal)) throw 0
      return value
    } catch {
      const safe = closed ? 'RUNTIME_CLOSED' : aborted.call(signal) ? 'RUNTIME_CANCELLED' : code
      throw new ConfigurationRuntimeError(safe)
    } finally {
      state.notify = null
      try { subscription?.[Symbol.dispose]() } catch { /* Never expose cleanup failures. */ }
    }
  }
  const resolveProvider = async signal => providerFromSnapshot(await reader.readSnapshot({ signal }), env)
  const resolveExecution = async (signal, override) => searchExecutionFromSnapshot(await reader.readSnapshot({ signal }), env, override)
  const complete = configuredCompletion(null, profile => {
    const call = createModelCompletion(profile)
    return async request => {
      const result = await call(request)
      if (containsProviderCredential(result, [profile.apiKey])) throw new ConfigurationRuntimeError('MODEL_UNAVAILABLE')
      return result
    }
  }, env, { resolveProvider })
  const webSearch = configuredWebSearch(null, { env, fetchImpl, resolveExecution })
  async function search(params, override) {
    try {
      const signal = signalFor(params.signal)
      const search = override === undefined ? webSearch : configuredWebSearch(null, { env, fetchImpl, resolveExecution, settingsOverride: override })
      return await invoke(signal, () => search({ ...params, signal }), 'CONFIGURATION_UNAVAILABLE')
    } catch {
      return { result: { success: false, code: 'search_request_failed', retryable: false,
        error: 'Web search is unavailable or cancelled. Check configuration before retrying.' } }
    }
  }
  return Object.freeze({
    backend: 'sqlite',
    async complete(request) {
      let signal
      try {
        signal = signalFor(request.signal)
        return await invoke(signal, () => complete({ ...request, signal }), 'MODEL_UNAVAILABLE')
      } catch {
        throw new ConfigurationRuntimeError(closed ? 'RUNTIME_CLOSED'
          : signal && aborted.call(signal) ? 'RUNTIME_CANCELLED' : 'MODEL_UNAVAILABLE')
      }
    },
    webSearch: params => search(params),
    testSearch: (override, params) => search(params, override),
    async modelConfigured() {
      const signal = signalFor(undefined)
      return invoke(signal, async () => Boolean(await resolveProvider(signal)), 'CONFIGURATION_UNAVAILABLE')
    },
    close() {
      if (!closing) {
        closed = true
        shutdown.abort()
        closing = reader.close().catch(() => { throw new ConfigurationRuntimeError('RUNTIME_CLOSED') })
      }
      return closing
    },
    toJSON: () => ({ backend: 'sqlite' }),
    [inspect.custom]: () => 'AgentConfiguration(<private>)',
  })
}
