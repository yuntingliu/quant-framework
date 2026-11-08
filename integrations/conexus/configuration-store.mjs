/**
 * Isolated protocol-1 reader. No live adapter imports this module.
 * Supported runtime: Node 22 >= 22.18; verified on 22.23.4.
 * SQLite is experimental. All filesystem/SQLite work runs in one-shot Workers.
 * Explicit snapshot.documents access is PRIVATE and must never be logged.
 */
import { Worker } from 'node:worker_threads'
import { inspect, types } from 'node:util'
import { addAbortListener } from 'node:events'

const MESSAGES = Object.freeze({
  INVALID_ARGUMENT: 'Check the configuration-reader arguments.',
  UNSUPPORTED_RUNTIME: 'This runtime does not support the configuration reader.',
  STORE_MISSING: 'The configuration store has not been initialized.',
  STORE_CORRUPT: 'The configuration store is invalid or incomplete.',
  UNSUPPORTED_PROTOCOL: 'The configuration-store protocol is not supported.',
  UNSUPPORTED_STORAGE: 'The SQLite storage mode is not supported.',
  UNSAFE_STORAGE: 'The configuration-store location is not private or supported.',
  INSTALLATION_MISMATCH: 'The configuration store belongs to another installation.',
  RECOVERY_REQUIRED: 'The configuration store requires explicit transaction recovery.',
  STORE_BUSY: 'The configuration store is busy. Retry the operation.',
  STORE_UNAVAILABLE: 'The configuration store could not be accessed.',
  STORE_TIMEOUT: 'The configuration read exceeded its time limit.',
  STORE_CANCELLED: 'The configuration read was cancelled.',
  STORE_CLOSED: 'The configuration reader is closed.',
  WORKER_FAILED: 'The configuration reader worker stopped unexpectedly.',
})
const OPAQUE = /^[0-9a-f]{32}$/
const EXIT_GRACE_MS = 500
const workerURL = new URL('./configuration-store-worker.mjs', import.meta.url)
// Native inheritance filters process-only options and retains feature/security
// flags and preloads. A module trampoline tolerates inherited eval/input modes
// without applying those modes to a file-backed entrypoint.
const workerEntry = new URL('data:text/javascript,' + encodeURIComponent(`import ${JSON.stringify(workerURL.href)}`))
const aborted = Object.getOwnPropertyDescriptor(AbortSignal.prototype, 'aborted').get
const addListener = EventTarget.prototype.addEventListener
const removeListener = EventTarget.prototype.removeEventListener

function signalAborted(signal) {
  // A Proxy is not an accepted signal. Inspect its type without invoking traps.
  if (types.isProxy(signal)) throw 0
  const value = aborted.call(signal)
  if (typeof value !== 'boolean') throw 0
  return value
}

function nativeSignalOperations(signal) {
  // addAbortListener reads public members. Reject overrides by inspecting
  // descriptors, rather than invoking caller-supplied getters or methods.
  for (const [name, expected] of [['aborted', aborted], ['addEventListener', addListener], ['removeEventListener', removeListener]]) {
    let target = signal, found = false
    while (target !== null) {
      if (types.isProxy(target)) throw 0
      const descriptor = Object.getOwnPropertyDescriptor(target, name)
      if (descriptor) {
        if (name === 'aborted' ? descriptor.get !== expected : descriptor.value !== expected) throw 0
        found = true
        break
      }
      target = Object.getPrototypeOf(target)
    }
    if (!found) throw 0
  }
}

function abortListener(state) {
  // Failed removal must not leave an external signal retaining the reader or
  // request. Cleanup disconnects this indirection before touching the signal.
  return () => state.notify?.()
}

export class ConfigurationStoreError extends Error {
  constructor(code) {
    const safe = typeof code === 'string' && Object.hasOwn(MESSAGES, code) ? code : 'STORE_UNAVAILABLE'
    super(MESSAGES[safe])
    this.name = 'ConfigurationStoreError'
    Object.defineProperty(this, 'code', { value: safe, enumerable: true })
  }
  toJSON() { return { name: this.name, code: this.code, message: this.message } }
}

function fields(value, allowed) {
  if (!value || typeof value !== 'object' || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    throw new ConfigurationStoreError('INVALID_ARGUMENT')
  }
  const descriptors = Object.getOwnPropertyDescriptors(value)
  if (Reflect.ownKeys(descriptors).some(key => !allowed.includes(key) || !Object.hasOwn(descriptors[key], 'value'))) {
    throw new ConfigurationStoreError('INVALID_ARGUMENT')
  }
  return Object.fromEntries(Object.entries(descriptors).map(([key, descriptor]) => [key, descriptor.value]))
}

function runtimeSupported() {
  const [major, minor] = process.versions.node.split('.').map(Number)
  return major === 22 && minor >= 18
}

class PrivateSnapshot {
  #value
  constructor(value) { this.#value = value; Object.freeze(this) }
  get protocol_version() { return this.#value.protocol_version }
  get installation_id() { return this.#value.installation_id }
  get revision() { return this.#value.revision }
  get documents() { return structuredClone({ models: this.#value.models, search: this.#value.search }) }
  get selections() { return structuredClone(this.#value.selections) }
  toJSON() { return { private: true } }
  toString() { return 'ConfigurationSnapshot(<private>)' }
  [inspect.custom]() { return this.toString() }
}

/**
 * readSnapshot({ signal, timeoutMs }) returns a detached private snapshot.
 * The timeout includes queueing/startup/shutdown. SQLite waits cap at 250ms.
 * close() rejects outstanding requests and awaits test/application-owned Workers.
 * Retiring Workers get 500ms to exit naturally, then forced termination is used.
 * Forced termination still carries native runtime/OS risks. Capacity lasts until exit.
 */
export class ConfigurationStoreReader {
  #root
  #timeout
  #concurrency
  #limit
  #queue = []
  #active = new Set()
  #requests = new Set()
  #closed = false
  #installation = null
  #closing = null

  constructor(root, options = {}) {
    let settings
    try {
      settings = fields(options, ['timeoutMs', 'maxConcurrency', 'maxPending'])
      if (typeof root !== 'string' || !root.trim() || root.length > 32768 || root.includes('\0')) throw 0
      this.#timeout = settings.timeoutMs ?? 5000
      this.#concurrency = settings.maxConcurrency ?? 2
      this.#limit = settings.maxPending ?? 32
      if (!Number.isInteger(this.#timeout) || this.#timeout < 1 || this.#timeout > 30000
        || !Number.isInteger(this.#concurrency) || this.#concurrency < 1 || this.#concurrency > 4
        || !Number.isInteger(this.#limit) || this.#limit < this.#concurrency || this.#limit > 64) throw 0
    } catch { throw new ConfigurationStoreError('INVALID_ARGUMENT') }
    if (!runtimeSupported()) throw new ConfigurationStoreError('UNSUPPORTED_RUNTIME')
    this.#root = root
  }

  readSnapshot(options = {}) {
    let settings, duration, signal, alreadyAborted = false
    try {
      settings = fields(options, ['timeoutMs', 'signal'])
      duration = settings.timeoutMs ?? this.#timeout
      if (!Number.isInteger(duration) || duration < 1 || duration > 30000) throw 0
      signal = settings.signal
      if (signal !== undefined) {
        nativeSignalOperations(signal)
        alreadyAborted = signalAborted(signal)
      }
    } catch { return Promise.reject(new ConfigurationStoreError('INVALID_ARGUMENT')) }
    if (this.#closed) return Promise.reject(new ConfigurationStoreError('STORE_CLOSED'))
    if (alreadyAborted) return Promise.reject(new ConfigurationStoreError('STORE_CANCELLED'))
    if (this.#requests.size >= this.#limit) return Promise.reject(new ConfigurationStoreError('STORE_BUSY'))
    return new Promise((resolve, reject) => {
      const request = { resolve, reject, signal, deadline: Date.now() + duration, duration, done: false }
      request.stopped = new Promise(resolveStopped => { request.resolveStopped = resolveStopped })
      request.timer = setTimeout(() => this.#finish(request, 'STORE_TIMEOUT'), duration)
      request.listenerState = { notify: () => this.#finish(request, 'STORE_CANCELLED') }
      request.abort = abortListener(request.listenerState)
      this.#requests.add(request)
      request.registering = true
      try {
        if (signal !== undefined) {
          request.subscription = addAbortListener(signal, request.abort)
          // Registration can synchronously invoke caller code. Recheck inside
          // the same guard so an abort before attachment cannot be missed.
          if (signalAborted(signal)) this.#finish(request, 'STORE_CANCELLED')
        }
      } catch { this.#finish(request, 'INVALID_ARGUMENT') }
      finally {
        request.registering = false
        // Synchronous abort/close may finish before the disposable is returned.
        if (request.done) this.#dispose(request)
      }
      if (request.done) return
      this.#queue.push(request)
      this.#drain()
    })
  }

  #drain() {
    while (!this.#closed && this.#active.size < this.#concurrency && this.#queue.length) {
      const request = this.#queue.shift()
      if (request.done) continue
      if (Date.now() >= request.deadline) { this.#finish(request, 'STORE_TIMEOUT'); continue }
      this.#active.add(request)
      request.starting = true
      try {
        request.worker = new Worker(workerEntry, {
          workerData: { root: this.#root, deadline: request.deadline, duration: request.duration, installation: this.#installation },
          stdout: true,
          stderr: true,
          resourceLimits: { maxOldGenerationSizeMb: 64, maxYoungGenerationSizeMb: 16, stackSizeMb: 4 },
          name: 'configuration-store-reader',
        })
        // Never forward worker warnings, stacks, or captured output to logs.
        request.worker.stdout.resume()
        request.worker.stderr.resume()
        request.onError = () => this.#finish(request, 'WORKER_FAILED')
        request.onExit = code => this.#exited(request, code)
        request.onMessage = message => {
          if (request.done) return
          if (Date.now() >= request.deadline) { this.#finish(request, 'STORE_TIMEOUT'); return }
          try {
            const response = fields(message, ['ok', 'code', 'snapshot'])
            if (response.ok === false && Object.keys(response).length === 2 && typeof response.code === 'string' && Object.hasOwn(MESSAGES, response.code)) {
              request.result = { code: response.code }
              this.#retire(request)
              return
            }
            if (response.ok !== true || Object.keys(response).length !== 2 || response.code !== undefined) throw 0
            const value = fields(response.snapshot, ['protocol_version', 'installation_id', 'revision', 'models', 'search', 'selections'])
            if (Object.keys(value).length !== 6 || value.protocol_version !== 1
              || typeof value.installation_id !== 'string' || value.installation_id.length !== 32 || !OPAQUE.test(value.installation_id)
              || typeof value.revision !== 'string' || value.revision.length !== 32 || !OPAQUE.test(value.revision)
              || !value.models || typeof value.models !== 'object' || Array.isArray(value.models)
              || !value.search || typeof value.search !== 'object' || Array.isArray(value.search)
              || !value.selections || Object.keys(value.selections).sort().join(',') !== 'models,search') throw 0
            if (this.#installation !== null && value.installation_id !== this.#installation) {
              this.#finish(request, 'INSTALLATION_MISMATCH')
              return
            }
            request.result = { code: null, value: new PrivateSnapshot(value) }
            this.#retire(request)
          } catch { this.#finish(request, 'STORE_CORRUPT') }
        }
        request.worker.on('error', request.onError)
        request.worker.once('exit', request.onExit)
        request.worker.once('message', request.onMessage)
      } catch { this.#finish(request, 'WORKER_FAILED') }
      finally {
        request.starting = false
        if (request.done) this.#retire(request)
      }
    }
  }

  #finish(request, code, value) {
    if (request.done) return
    const wasSuccess = code === null, signal = request.signal
    request.done = true
    clearTimeout(request.timer)
    request.listenerState.notify = null
    this.#requests.delete(request)
    request.result = undefined
    const queued = this.#queue.indexOf(request)
    if (queued >= 0) this.#queue.splice(queued, 1)
    if (this.#dispose(request)) code ??= 'INVALID_ARGUMENT'
    this.#retire(request)
    // Disposal and exit bookkeeping can synchronously run caller code. Check
    // afterwards, immediately before resolving; later aborts cannot revoke success.
    if (wasSuccess && this.#closed) code ??= 'STORE_CLOSED'
    if (wasSuccess && signal !== undefined) {
      try { if (signalAborted(signal)) code = 'STORE_CANCELLED' }
      catch { code = 'INVALID_ARGUMENT' }
    }
    if (!code && value) {
      if (this.#installation !== null && value.installation_id !== this.#installation) code = 'INSTALLATION_MISMATCH'
      else this.#installation = value.installation_id
    }
    if (code) request.reject(new ConfigurationStoreError(code))
    else request.resolve(value)
    this.#drain()
  }

  #dispose(request) {
    if (request.registering) return false
    const subscription = request.subscription, signal = request.signal, listener = request.abort
    request.subscription = undefined
    request.signal = undefined
    request.abort = undefined
    try {
      if (subscription !== undefined) subscription[Symbol.dispose]()
      // Native registration may attach before throwing, without returning its
      // disposable. Remove that partial listener with the captured intrinsic.
      else if (signal !== undefined) removeListener.call(signal, 'abort', listener)
    } catch { return true }
    return false
  }

  #retire(request) {
    if (request.starting || request.released) return
    if (!request.worker || request.exited) { this.#release(request); return }
    // A result is not proof of shutdown. Let module evaluation and cleanup
    // finish; even cancelled reads reject immediately while their Worker retires.
    if (!request.graceExpired) {
      request.graceTimer ??= setTimeout(() => {
        request.graceTimer = undefined
        request.graceExpired = true
        this.#finish(request, 'WORKER_FAILED')
        this.#retire(request)
      }, EXIT_GRACE_MS)
      return
    }
    if (request.termination) return
    try {
      request.termination = request.worker.terminate().catch(() => this.#finish(request, 'WORKER_FAILED'))
    } catch { this.#finish(request, 'WORKER_FAILED') }
  }

  #exited(request, code) {
    request.exited = true
    clearTimeout(request.graceTimer)
    if (!request.done) {
      if (code !== 0 || !request.result) this.#finish(request, 'WORKER_FAILED')
      else if (Date.now() >= request.deadline) this.#finish(request, 'STORE_TIMEOUT')
      else this.#finish(request, request.result.code, request.result.value)
    }
    this.#release(request)
  }

  #release(request) {
    if (request.released) return
    request.released = true
    clearTimeout(request.graceTimer)
    request.graceTimer = request.termination = undefined
    for (const [event, listener] of [['error', request.onError], ['exit', request.onExit], ['message', request.onMessage]]) {
      try { if (listener) request.worker?.removeListener(event, listener) } catch { /* Do not forward cleanup exceptions. */ }
    }
    request.worker = undefined
    request.onError = request.onExit = request.onMessage = request.result = undefined
    this.#active.delete(request)
    request.resolveStopped()
    this.#drain()
  }

  close() {
    if (this.#closing) return this.#closing
    this.#closed = true
    for (const request of [...this.#requests]) this.#finish(request, 'STORE_CLOSED')
    this.#closing = Promise.all([...this.#active].map(request => request.stopped)).then(() => undefined)
    return this.#closing
  }
  toJSON() { return { private: true } }
  toString() { return 'ConfigurationStoreReader(<private>)' }
  [inspect.custom]() { return this.toString() }
}
