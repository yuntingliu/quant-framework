/** Trusted Python-owner handoff, not persistent activation or a backend toggle.
 * The parent must hold ConfigurationBackend.borrow() until the Agent exits.
 * Production entry points accept only JSON; SQLite is explicit composition only.
 */
import { realpath, lstat } from 'node:fs/promises'
import { isAbsolute, resolve } from 'node:path'
import { isMainThread } from 'node:worker_threads'
import { types } from 'node:util'
import { createJsonAgentConfiguration, createSqliteAgentConfiguration } from './configuration-runtime.mjs'

export class ConfigurationBackendError extends Error {
  constructor() {
    super('The coordinated configuration backend is unavailable or inconsistent.')
    this.name = 'ConfigurationBackendError'
    this.code = 'BACKEND_UNAVAILABLE'
  }
  toJSON() { return { code: this.code, message: this.message } }
}

export async function validateBackendContract(input, { production = false } = {}) {
  let result
  try {
    if (!isMainThread || types.isProxy(input) || input === null || typeof input !== 'object') throw 0
    const fields = ['version', 'root', 'root_key', 'backend', 'decision_id', 'installation_id']
    const own = Object.getOwnPropertyDescriptors(input)
    if (Reflect.ownKeys(own).length !== fields.length || fields.some(key => !own[key] || !Object.hasOwn(own[key], 'value'))) throw 0
    const value = Object.fromEntries(fields.map(key => [key, own[key].value]))
    if (value.version !== 1 || !['json', 'sqlite'].includes(value.backend)
      || (production && value.backend !== 'json') || typeof value.root !== 'string'
      || !isAbsolute(value.root) || value.root.includes('\0')
      || (process.platform === 'win32' && value.root.startsWith('\\\\'))
      || typeof value.root_key !== 'string' || !/^[0-9a-f]{64}$/.test(value.root_key)
      || typeof value.decision_id !== 'string' || !/^[0-9a-f]{32}$/.test(value.decision_id)
      || (value.backend === 'json' ? value.installation_id !== null
        : typeof value.installation_id !== 'string' || !/^[0-9a-f]{32}$/.test(value.installation_id))) throw 0
    // Reuse the authoritative Python/Windows-compatible root-key calculation.
    // Import only on the main thread: the worker module has a worker entry point.
    const { configurationRootKey } = await import('./configuration-store-worker.mjs')
    const canonical = await realpath(value.root)
    if (!(await lstat(value.root)).isDirectory() || resolve(value.root) !== value.root
      || configurationRootKey(canonical) !== value.root_key
      || configurationRootKey(value.root) !== value.root_key) throw 0
    result = Object.freeze(value)
  } catch { /* Never retain input exceptions, paths, or private arguments. */ }
  if (!result) throw new ConfigurationBackendError()
  return result
}

export async function createCoordinatedAgentConfiguration(contract, createModelCompletion, options = {}) {
  let runtime, value, ready = false
  try {
    value = await validateBackendContract(contract, { production: options.production === true })
    runtime = value.backend === 'json'
      ? createJsonAgentConfiguration(resolve(value.root, 'conexus/secrets/model-providers.json'), createModelCompletion, options)
      : createSqliteAgentConfiguration(value.root, createModelCompletion,
        { ...options, expectedInstallationId: value.installation_id })
    // An incomplete saved profile is valid but not ready; unavailable storage is
    // a startup failure. Do not construct a partial model/search composition.
    await runtime.modelConfigured()
    ready = true
  } catch {
    try { await runtime?.close() } catch { /* Sanitized below. */ }
  }
  if (!ready) throw new ConfigurationBackendError()
  return Object.freeze({ ...runtime, identity: Object.freeze({ backend: value.backend,
    decision_id: value.decision_id, installation_id: value.installation_id }) })
}
