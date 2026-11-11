/** Native profile access holds an actual Python OS lease through the operation.
 * The helper's private pipe proves its lifetime; a handoff dictionary does not.
 * No durable SQLite composition or activation publisher exists in this phase.
 */
import { spawn } from 'node:child_process'
import { dirname, resolve, basename } from 'node:path'
import { fileURLToPath } from 'node:url'
import { addAbortListener } from 'node:events'
import { types } from 'node:util'
import { AsyncLocalStorage } from 'node:async_hooks'
import { lstat, realpath, readdir, open } from 'node:fs/promises'
import { constants, realpathSync, statSync } from 'node:fs'
import { isMainThread } from 'node:worker_threads'

const aborted = Object.getOwnPropertyDescriptor(AbortSignal.prototype, 'aborted').get
const held = new AsyncLocalStorage()

async function checkAuthorityRecord(root, backend = 'json') {
  const directory = resolve(root, 'app/secrets/configuration-decision')
  let info
  try { info = await lstat(directory) } catch (error) {
    if (error.code === 'ENOENT' && backend !== 'sqlite') return
    throw 0
  }
  const { configurationRootKey } = await import('./configuration-store-worker.mjs')
  const samePath = (left, right) => configurationRootKey(left) === configurationRootKey(right)
  if (!info.isDirectory() || !samePath(await realpath(directory), directory)
    || (process.platform !== 'win32' && (info.mode & 0o077))) throw 0
  const names = await readdir(directory)
  if (names.length !== 1 || names[0] !== 'decision.json') throw 0
  const path = resolve(directory, 'decision.json'), before = await lstat(path, { bigint: true })
  if (!before.isFile() || before.nlink !== 1n || before.size > 16384n
    || (process.platform !== 'win32' && (before.mode & 0o077n))) throw 0
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW || 0))
  let wire
  try {
    const actual = await handle.stat({ bigint: true })
    const bytes = Buffer.alloc(16385)
    const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0)
    const after = await handle.stat({ bigint: true }), named = await lstat(path, { bigint: true })
    const identity = value => [value.dev, value.ino, value.size, value.mtimeNs].join('/')
    if ([actual, after, named].some(value => identity(value) !== identity(before))
      || bytesRead !== Number(before.size) || bytesRead > 16384 || !samePath(await realpath(directory), directory)) throw 0
    wire = bytes.subarray(0, bytesRead)
  } finally { await handle.close() }
  const { parseDecisionRecord } = await import('./configuration-decision.mjs')
  const record = parseDecisionRecord(wire, { rootKey: configurationRootKey(root) })
  if (backend !== 'observe' && (backend === 'sqlite' ? record.state !== 'committed' : record.state !== 'prepared')) throw 0
  return record
}

export async function readAuthorityRecord(root) {
  try { return await checkAuthorityRecord(canonicalPath(root), 'observe') }
  catch { throw new ConfigurationAuthorityError() }
}

export class ConfigurationAuthorityError extends Error {
  constructor() {
    super('Configuration authority is unavailable or incompatible.')
    this.name = 'ConfigurationAuthorityError'
    this.code = 'AUTHORITY_UNAVAILABLE'
  }
  toJSON() { return { code: this.code, message: this.message } }
}

// No external signal properties are read for diagnostics or classification.
function checkedSignal(signal) {
  if (signal !== undefined && (types.isProxy(signal) || Object.getPrototypeOf(signal) !== AbortSignal.prototype
    || ['constructor', 'aborted', 'addEventListener', 'removeEventListener'].some(key => Object.hasOwn(signal, key))
    || aborted.call(signal))) throw 0
}
const unavailable = () => new ConfigurationAuthorityError()
function bounded(promise, milliseconds) {
  let timer
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(unavailable()), milliseconds) })])
    .finally(() => clearTimeout(timer))
}

async function startLease(root, backend, signal, proof) {
  let child, expected, buffered = '', ready = false, failed = false, closing, retirement, serial = Promise.resolve()
  const pending = new Set(), watchers = new Set(), shutdown = new AbortController()
  let exited, rejectReady, readyDone
  const exit = new Promise(resolve => { exited = resolve })
  const admission = new Promise((resolve, reject) => { readyDone = resolve; rejectReady = reject })
  const alive = () => !failed && child.exitCode === null && child.signalCode === null
  function lose(markFailed = true) {
    if (markFailed) { failed = true; shutdown.abort() }
    rejectReady(unavailable())
    expected?.reject(unavailable())
    for (const resolve of watchers) resolve()
    watchers.clear()
  }
  function watch() {
    let resolve
    const promise = new Promise(done => { resolve = done })
    if (!alive()) resolve()
    else watchers.add(resolve)
    return { promise, dispose: () => watchers.delete(resolve) }
  }
  function command(token, response) {
    const result = serial.then(async () => {
      if (!alive()) throw 0
      let accepted
      try {
        accepted = await bounded(new Promise((resolve, reject) => {
          expected = { response, resolve, reject }
          child.stdin.write(token + '\n', error => { if (error) reject(unavailable()) })
        }), 5000)
      } catch { lose(); throw unavailable() }
      finally { expected = undefined }
      if (!accepted) throw unavailable()
    })
    serial = result.then(() => {}, () => {})
    return result
  }
  function retire() {
    if (!retirement) {
      shutdown.abort()
      retirement = (async () => {
        // Outward cancellation never releases an in-flight callback's OS credit.
        // No new calls can join after closing; late nested calls are rejected.
        await Promise.allSettled([...pending])
        await serial
        child.stdin.end()
        try { await bounded(exit, 1000) }
        catch {
          child.kill()
          try { await bounded(exit, 1000) }
          catch { child.kill('SIGKILL'); await bounded(exit, 1000) }
        }
        child.stdout.destroy(); child.stdin.destroy()
        if (failed || child.exitCode !== 0) throw 0
      })()
      // A bounded close may fail while work remains. Retirement continues to
      // retain the actual helper/leases until that work really finishes.
      retirement.catch(() => {})
    }
    return retirement
  }
  const owner = {
    root, backend, pending, shutdown, alive, watch, command,
    get closing() { return closing !== undefined },
    close() {
      if (!closing) {
        // Install the closed state before cancellation listeners can reenter.
        closing = Promise.resolve()
        closing = bounded(retire(), 1000).catch(() => { throw unavailable() })
      }
      return closing
    },
    startRetirement() { const value = owner.close(); value.catch(() => {}); return value },
  }
  let subscription
  try {
    child = spawn(process.env.ALPHALAB_AUTHORITY_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
      ['-B', '-m', 'alphalab.utils.configuration_authority', root, backend], {
        cwd: fileURLToPath(new URL('../..', import.meta.url)), windowsHide: true,
        env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, stdio: ['pipe', 'pipe', 'ignore'],
      })
    child.stdin.on('error', () => lose())
    child.once('error', () => { lose(); exited() })
    child.once('exit', () => { lose(!retirement || child.exitCode !== 0); exited() })
    child.stdout.on('data', chunk => {
      buffered += chunk
      while (buffered.includes('\n')) {
        const position = buffered.indexOf('\n'), line = buffered.slice(0, position)
        buffered = buffered.slice(position + 1)
        if (!ready && line === 'leased') { ready = true; readyDone() }
        else if (ready && expected && line === expected.response) expected.resolve(true)
        else if (ready && expected?.response === 'admitted' && line === 'denied') expected.resolve(false)
        else lose()
      }
      if (buffered.length > 16) lose()
    })
    if (backend === 'sqlite') child.stdin.write('auth ' + proof + '\n')
    const cancellation = new Promise((_, reject) => {
      if (signal !== undefined) subscription = addAbortListener(signal, () => reject(unavailable()))
    })
    await bounded(Promise.race([admission, cancellation]), 5000)
    if (!alive() || (signal !== undefined && aborted.call(signal))) throw 0
    return owner
  } catch {
    // No callback has run during startup, so it is safe to retire this helper.
    try { await owner.close() } catch { /* Static error below. */ }
    throw unavailable()
  } finally { try { subscription?.[Symbol.dispose]() } catch { owner.startRetirement() } }
}

async function runCall(owner, action, supplied) {
  if (owner.closing || !owner.alive()) throw 0
  const signal = AbortSignal.any([owner.shutdown.signal, ...(supplied === undefined ? [] : [supplied])])
  const loss = owner.watch()
  let subscription
  const cancellation = new Promise((_, reject) => {
    subscription = addAbortListener(signal, () => reject(unavailable()))
  })
  const work = Promise.resolve().then(async () => {
    let admitted = false
    try {
      if (aborted.call(signal) || owner.closing || !owner.alive()) throw 0
      // Observe invalid evidence before requesting a credit. A rejected read
      // must not destroy another callback's otherwise live shared owner.
      await checkAuthorityRecord(owner.root, owner.backend)
      if (aborted.call(signal) || owner.closing || !owner.alive()) throw 0
      await owner.command('enter', 'admitted')
      admitted = true
      if (aborted.call(signal) || owner.closing || !owner.alive()) throw 0
      await checkAuthorityRecord(owner.root, owner.backend)
      if (aborted.call(signal) || owner.closing || !owner.alive()) throw 0
      const value = await held.run(owner, () => action(signal))
      await checkAuthorityRecord(owner.root, owner.backend)
      if (aborted.call(signal) || !owner.alive()) throw 0
      return value
    } finally {
      if (admitted && owner.alive()) await owner.command('leave', 'released')
    }
  })
  // Store only completion notifications, never raw callback errors or results.
  const completed = work.then(() => {}, () => {})
  owner.pending.add(completed)
  completed.then(() => owner.pending.delete(completed))
  try {
    return await Promise.race([work, cancellation, loss.promise.then(() => { throw unavailable() })])
  } finally {
    loss.dispose()
    try { subscription?.[Symbol.dispose]() } catch { throw unavailable() }
  }
}

export async function withConfigurationAuthority(root, backend, action, signal) {
  let owner, owned = false, value, failed = false
  try {
    if (!isMainThread || typeof root !== 'string' || !['json', 'inactive_sqlite', 'sqlite'].includes(backend) || typeof action !== 'function') throw 0
    checkedSignal(signal)
    root = canonicalPath(root)
    const previous = held.getStore()
    if (previous?.root === root && previous.backend === 'sqlite' && backend === 'inactive_sqlite') backend = 'sqlite'
    if (previous?.root === root && previous.backend === backend) owner = previous
    else {
      if (backend === 'sqlite') throw 0 // An actual authenticated scope is required.
      // Rejection may be decided early. Accepted use still revalidates under
      // the real lease; this observation never grants configuration access.
      await checkAuthorityRecord(root)
      owner = await startLease(root, backend, signal); owned = true
    }
    value = await runCall(owner, action, signal)
  } catch { failed = true }
  finally {
    if (owned) {
      // Promptly reject an abort, but keep the helper alive for unfinished work.
      if (owner.pending.size) owner.startRetirement()
      else try { await owner.close() } catch { failed = true }
    }
  }
  try { if (!failed && signal !== undefined && aborted.call(signal)) failed = true } catch { failed = true }
  if (failed) throw unavailable()
  return value
}

function canonicalPath(path) {
  if (typeof path !== 'string' || !path || path.includes('\0')) throw unavailable()
  let candidate = resolve(path), suffix = []
  if (process.platform === 'win32' && candidate.slice(3).includes(':')) throw unavailable()
  // Win32 opens ordinary components after discarding trailing dots/spaces;
  // realpathSync can preserve those spellings even for the same actual file.
  if (process.platform === 'win32') candidate = candidate.split('\\').map(part => part.replace(/[ .]+$/, '')).join('\\')
  while (true) {
    try {
      const actual = realpathSync.native(candidate)
      const normalized = resolve(actual, ...suffix)
      if (!suffix.length) {
        const info = statSync(normalized)
        if (info.isFile() && info.nlink !== 1) throw unavailable()
      }
      return normalized
    } catch (error) {
      if (error?.code !== 'ENOENT') throw unavailable()
      const parent = dirname(candidate)
      if (parent === candidate) throw unavailable()
      suffix.unshift(basename(candidate)); candidate = parent
    }
  }
}
function rootForCanonical(absolute) {
  const key = value => process.platform === 'win32' ? value.toLowerCase() : value
  const secrets = dirname(absolute), service = dirname(secrets), file = key(basename(absolute))
  return key(basename(secrets)) === 'secrets'
    && ((['model-providers.json', 'search-providers.json'].includes(file) && key(basename(service)) === 'conexus')
      || (file === 'rqdata-profiles.json' && key(basename(service)) === 'app')) ? dirname(service) : null
}
export function nativeProfileRoot(path) {
  return rootForCanonical(canonicalPath(path))
}

/** Idle lifetime owns OS protection; each operation separately admits actual work. */
export async function openConfigurationAuthority(root, backend, options = {}) {
  let owner, borrowed = false, closed = false, closing
  const shutdown = new AbortController()
  try {
    if (!isMainThread || typeof root !== 'string' || !['json', 'inactive_sqlite', 'sqlite'].includes(backend)
      || types.isProxy(options) || options === null || typeof options !== 'object') throw 0
    const fields = Object.getOwnPropertyDescriptors(options)
    if (Reflect.ownKeys(fields).some(key => key !== 'proof' || !Object.hasOwn(fields[key], 'value'))) throw 0
    const proof = fields.proof?.value
    if (proof !== undefined && (typeof proof !== 'string' || !/^[0-9a-f]{64}$/.test(proof))) throw 0
    root = canonicalPath(root)
    const previous = held.getStore()
    if (previous?.root === root && previous.backend === backend) { owner = previous; borrowed = true }
    else {
      await checkAuthorityRecord(root, backend)
      if (backend === 'sqlite' && proof === undefined) throw 0
      owner = await startLease(root, backend, undefined, proof)
    }
    if (owner.closing || !owner.alive()) throw 0
    owner.activation = await checkAuthorityRecord(root, backend)
  } catch {
    if (owner && !borrowed) try { await owner.close() } catch { /* Static error below. */ }
    throw unavailable()
  }
  return Object.freeze({
    activation: owner.activation,
    async run(action, signal) {
      if (closed) throw unavailable()
      try {
        checkedSignal(signal)
        const combined = AbortSignal.any([shutdown.signal, ...(signal === undefined ? [] : [signal])])
        return await held.run(owner, () => withConfigurationAuthority(root, backend, action, combined))
      } catch { throw unavailable() }
    },
    close() {
      if (!closing) {
        closed = true; shutdown.abort()
        closing = borrowed ? Promise.resolve() : owner.close()
      }
      return closing
    },
  })
}

export async function withJsonProfileAuthority(path, action, signal) {
  let location, root
  try { location = canonicalPath(path); root = rootForCanonical(location) } catch { throw unavailable() }
  return root === null ? action(location) : withConfigurationAuthority(root, 'json', () => action(location), signal)
}
