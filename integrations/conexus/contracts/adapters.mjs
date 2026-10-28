// The versioned adapter boundary between AlphaLab and the pinned Conexus core.
//
// Conexus has no plugin system: an adapter is a plain function the launcher
// hands to `createLocalHost` or `createLocalRuntime`. That makes substitution
// cheap and makes drift invisible, because nothing declares what those
// functions promised. This module is the declaration.
//
// The contracts live in adapter-contracts.v1.json rather than in code so the
// Python benchmark suites can validate the same shapes with `jsonschema`
// instead of a second, divergent copy. Here they gain:
//
//   * named checkers with Runtime Core's error-detail shape,
//   * the error-code taxonomy, split by which side owns each code, and
//   * profile equivalence, which is what makes a substitute adapter
//     defensible rather than merely runnable.
//
// Versioning rule: adding an optional field or a new error code is a revision
// of v1. Removing a field, tightening a type, or changing what an existing
// code means requires adapter-contracts.v2.json, because a benchmark adapter
// pinned to v1 must keep passing.
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { unsupportedKeywords, validate } from './validate.mjs'

const here = dirname(fileURLToPath(import.meta.url))

export const CONTRACT_DOCUMENT = Object.freeze(
  JSON.parse(readFileSync(resolve(here, 'adapter-contracts.v1.json'), 'utf8')),
)

export const CONTRACT_SCHEMA_ID = CONTRACT_DOCUMENT.schema
export const CONTRACT_VERSION = CONTRACT_DOCUMENT.version

/** The core revision these contracts were verified against. */
export const VERIFIED_CORE_REVISION = CONTRACT_DOCUMENT.verifiedCoreRevision

export const CONTRACT_IDS = Object.freeze(Object.keys(CONTRACT_DOCUMENT.contracts))

/** Every declared code, flattened. A code outside this set is undeclared drift. */
export const ERROR_CODES = Object.freeze(
  Object.values(CONTRACT_DOCUMENT.errorCodes).flatMap(family => family.codes),
)

export const RETRYABLE_CODES = Object.freeze(CONTRACT_DOCUMENT.retryableCodes)

export class AdapterContractError extends Error {
  constructor(contractId, details) {
    super(`${contractId} contract violated: ${details.map(item => `${item.path} ${item.keyword}`).join(', ')}`)
    this.name = 'AdapterContractError'
    this.code = 'adapter_contract_violated'
    this.contractId = contractId
    this.details = details
  }
}

function contract(contractId) {
  const found = CONTRACT_DOCUMENT.contracts[contractId]
  if (!found) throw new Error(`Unknown adapter contract: ${contractId}. Known: ${CONTRACT_IDS.join(', ')}.`)
  return found
}

/** Returns the violations of one contract. Empty means the value conforms. */
export function checkContract(contractId, value) {
  return validate(contract(contractId).schema, value)
}

/** Throws AdapterContractError when `value` does not conform. */
export function assertContract(contractId, value) {
  const details = checkContract(contractId, value)
  if (details.length) throw new AdapterContractError(contractId, details)
  return value
}

/**
 * Wraps an adapter so every call is checked in both directions.
 *
 * Use it in tests and in a benchmark harness, not in the shipped launcher: a
 * production Host should not fail a Run because a provider added a field, and
 * the cost of validating every completion is paid on the hot path.
 */
export function withContracts(adapter, { request, response }) {
  return async (...args) => {
    if (request) assertContract(request, args[0])
    const result = await adapter(...args)
    if (response) assertContract(response, result)
    return result
  }
}

/** True when `code` is declared, so a caller can reject invented codes. */
export function isDeclaredCode(code) {
  return ERROR_CODES.includes(code)
}

export function isRetryableCode(code) {
  return RETRYABLE_CODES.includes(code)
}

/**
 * Compares a substitute adapter's profile against the one it stands in for.
 *
 * Equivalence is deliberately one-directional on capability ids and
 * permissions: a substitute may not offer *less*, because a Harness that
 * deploys against the real Host must deploy against the substitute, and
 * `inspectHostedRelease` only checks what the profile claims. It may offer
 * more — a double that also answers a capability the real host lacks changes
 * nothing about whether the benchmark task is reachable.
 */
export function profileShortfall(reference, substitute) {
  assertContract('runtime.adapter.profile', reference)
  assertContract('runtime.adapter.profile', substitute)
  const missing = []
  for (const field of ['nodeTypes', 'toolRuntimes', 'permissions', 'isolation', 'capabilityIds', 'runNodeTypes']) {
    for (const value of reference[field]) {
      if (!substitute[field].includes(value)) missing.push(`${field}: ${value}`)
    }
  }
  return missing
}

export function assertEquivalentProfiles(reference, substitute) {
  const missing = profileShortfall(reference, substitute)
  if (missing.length) {
    throw new Error(
      `Substitute adapter "${substitute.name}" cannot stand in for "${reference.name}": ` +
      `it does not offer ${missing.join('; ')}.`,
    )
  }
}

/** Guards against a contract keyword this repository's validator ignores. */
export function contractDocumentProblems() {
  const problems = []
  if (CONTRACT_SCHEMA_ID !== 'alphalab.conexus.adapter-contracts.v1' || CONTRACT_VERSION !== 1) {
    problems.push(`unexpected contract document identity: ${CONTRACT_SCHEMA_ID} v${CONTRACT_VERSION}`)
  }
  for (const contractId of CONTRACT_IDS) {
    const entry = CONTRACT_DOCUMENT.contracts[contractId]
    for (const field of ['title', 'owner', 'direction', 'notes', 'schema', 'examples']) {
      if (!entry[field]) problems.push(`${contractId}: missing ${field}`)
    }
    if (!entry.examples?.valid?.length) problems.push(`${contractId}: declares no valid example`)
    if (!entry.examples?.invalid?.length) problems.push(`${contractId}: declares no invalid example`)
    problems.push(...unsupportedKeywords(entry.schema ?? {}, `${contractId}:/`))
  }
  return problems
}
