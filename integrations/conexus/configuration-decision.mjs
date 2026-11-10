/** Inactive durable-record validation. Never constructs a backend or owns a lease. */
import { parseAgentMessage } from './configuration-agent-auth.mjs'
import { validateBackendContract } from './configuration-backend.mjs'
import { configurationRootKey } from './configuration-store-worker.mjs'
import { types } from 'node:util'

export class ConfigurationDecisionError extends Error {
  constructor() {
    super('The durable configuration decision is unavailable or inconsistent.')
    this.code = 'DECISION_UNAVAILABLE'
    this.name = 'ConfigurationDecisionError'
  }
  toJSON() { return { code: this.code, message: this.message } }
}

const exact = (value, fields) => {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false
  const keys = Object.keys(value)
  return keys.length === fields.length && fields.every(key => Object.hasOwn(value, key))
    && keys.every(key => fields.includes(key))
}
const id = value => typeof value === 'string' && /^[0-9a-f]{32}$/.test(value)

export function parseDecisionRecord(input, options = {}) {
  let value
  try {
    if (types.isProxy(input) || types.isProxy(options) || options === null || typeof options !== 'object'
      || (typeof input !== 'string' && !Buffer.isBuffer(input))) throw 0
    const fields = Object.getOwnPropertyDescriptors(options)
    if (Reflect.ownKeys(fields).some(key => !['rootKey', 'installationId', 'revision'].includes(key) || !Object.hasOwn(fields[key], 'value'))) throw 0
    const rootKey = fields.rootKey?.value, installationId = fields.installationId?.value, revision = fields.revision?.value
    if ((typeof input === 'string' ? Buffer.byteLength(input) : input.byteLength) > 16384) throw 0
    // Preserve a BOM as a character so the grammar rejects it, matching Python;
    // decoding must never silently repair malformed bytes or strip a prefix.
    const text = typeof input === 'string' ? input
      : new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(input)
    value = parseAgentMessage(text)
    if (typeof rootKey !== 'string' || !/^[0-9a-f]{64}$/.test(rootKey)
      || !exact(value, ['protocol_version', 'state', 'backend', 'root_key', 'installation_id', 'decision_id', 'revision', 'evidence'])
      || value.protocol_version !== 1 || !['prepared', 'committed'].includes(value.state)
      || value.backend !== 'sqlite' || value.root_key !== rootKey
      || !id(value.installation_id) || !id(value.decision_id) || !id(value.revision)
      || (installationId !== undefined && (!id(installationId) || value.installation_id !== installationId))
      || (revision !== undefined && (!id(revision) || value.revision !== revision))) throw 0
    const evidence = value.evidence
    if (!exact(evidence, ['kind', 'verification_id', 'migration_revision', 'store_protocol_version', 'selection_kinds'])
      || evidence.kind !== 'legacy-exact-v1' || !id(evidence.verification_id)
      || evidence.migration_revision !== value.revision || evidence.store_protocol_version !== 1
      || !exact(evidence.selection_kinds, ['models', 'search', 'rqdata'])
      || Object.values(evidence.selection_kinds).some(kind => !['profile', 'environment', 'none'].includes(kind))) throw 0
    Object.freeze(evidence.selection_kinds); Object.freeze(evidence); Object.freeze(value)
  } catch { throw new ConfigurationDecisionError() }
  return value
}

export async function validateDecisionForRoot(input, root) {
  let record
  try {
    record = parseDecisionRecord(input, { rootKey: configurationRootKey(root) })
    await validateBackendContract({ version: 1, root, root_key: record.root_key, backend: 'sqlite',
      installation_id: record.installation_id, decision_id: record.decision_id })
  } catch { throw new ConfigurationDecisionError() }
  // No filesystem authority reads, store selection, cache, or maintenance lease.
  // The future launcher must receive/revalidate this under actual owner leases.
  return record
}
