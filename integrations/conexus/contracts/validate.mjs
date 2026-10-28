// A JSON Schema validator covering exactly the keywords
// adapter-contracts.v1.json uses, and nothing else.
//
// Why not a library: these contract checks have to run on a fresh clone,
// before `python scripts/build_conexus_runtime.py` has installed anything, so
// they cannot reach the `ajv` the pinned core depends on. Adding a second
// validator to apps/desktop would make the contract tests depend on the
// frontend's dependency tree, which is the opposite of what a contract is for.
//
// The risk of a hand-written validator is that it silently disagrees with the
// real one. That is why every contract in the document carries valid and
// invalid examples with the expected failing path, and why
// tests/contracts/test_conexus_adapter_contracts.py replays the same corpus
// through `jsonschema`. A disagreement fails a test rather than passing a
// malformed adapter response.
//
// Error details use Runtime Core's shape — { path, keyword, message } — so a
// contract violation reads like the schema violations the Agent already sees
// from `validateNodeCapabilityInput`.

const SUPPORTED = new Set([
  'type', 'required', 'properties', 'additionalProperties', 'items', 'enum', 'const',
  'minItems', 'maxItems', 'minLength', 'maxLength', 'minimum', 'pattern', 'oneOf', 'anyOf', 'not',
])

const TYPES = {
  object: value => value !== null && typeof value === 'object' && !Array.isArray(value),
  array: Array.isArray,
  string: value => typeof value === 'string',
  number: value => typeof value === 'number' && Number.isFinite(value),
  integer: value => typeof value === 'number' && Number.isInteger(value),
  boolean: value => typeof value === 'boolean',
  null: value => value === null,
}

/** Reject a schema using a keyword this validator would silently ignore. */
export function unsupportedKeywords(schema, path = '/') {
  if (!TYPES.object(schema)) return []
  const found = []
  for (const [keyword, value] of Object.entries(schema)) {
    if (!SUPPORTED.has(keyword)) {
      found.push(`${path}: unsupported schema keyword: ${keyword}`)
      continue
    }
    if (keyword === 'properties') {
      for (const [name, child] of Object.entries(value)) found.push(...unsupportedKeywords(child, `${path}${name}/`))
    } else if (keyword === 'additionalProperties' && value !== false) {
      // Only the `false` form is enforced below. A subschema here would be
      // ignored, which is exactly the silent pass this function exists to stop.
      found.push(`${path}: additionalProperties must be false, not a subschema`)
    } else if (keyword === 'items') {
      if (Array.isArray(value)) found.push(`${path}: tuple-form items is not supported`)
      else found.push(...unsupportedKeywords(value, path))
    } else if (keyword === 'not') {
      found.push(...unsupportedKeywords(value, path))
    } else if (keyword === 'oneOf' || keyword === 'anyOf') {
      for (const child of value) found.push(...unsupportedKeywords(child, path))
    }
  }
  return found
}

function fail(errors, path, keyword, message) {
  errors.push({ path: path || '/', keyword, message })
}

function check(schema, value, path, errors) {
  if (schema.type !== undefined) {
    const allowed = Array.isArray(schema.type) ? schema.type : [schema.type]
    if (!allowed.some(name => TYPES[name]?.(value))) {
      fail(errors, path, 'type', `must be ${allowed.join(' or ')}`)
      // Every other keyword assumes the type held; reporting them too would
      // bury the one error a caller can act on.
      return
    }
  }
  if (schema.const !== undefined && value !== schema.const) {
    fail(errors, path, 'const', `must be ${JSON.stringify(schema.const)}`)
  }
  if (schema.enum !== undefined && !schema.enum.includes(value)) {
    fail(errors, path, 'enum', `must be one of ${schema.enum.map(item => JSON.stringify(item)).join(', ')}`)
  }
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      fail(errors, path, 'minLength', `must be at least ${schema.minLength} characters`)
    }
    if (schema.maxLength !== undefined && value.length > schema.maxLength) {
      fail(errors, path, 'maxLength', `must be at most ${schema.maxLength} characters`)
    }
    if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(value)) {
      fail(errors, path, 'pattern', `must match ${schema.pattern}`)
    }
  }
  if (typeof value === 'number' && schema.minimum !== undefined && value < schema.minimum) {
    fail(errors, path, 'minimum', `must be at least ${schema.minimum}`)
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      fail(errors, path, 'minItems', `must have at least ${schema.minItems} items`)
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      fail(errors, path, 'maxItems', `must have at most ${schema.maxItems} items`)
    }
    if (schema.items) value.forEach((item, index) => check(schema.items, item, `${path}/${index}`, errors))
  }
  if (TYPES.object(value)) {
    for (const name of schema.required ?? []) {
      if (!Object.hasOwn(value, name)) fail(errors, path, 'required', `must have required property '${name}'`)
    }
    const declared = schema.properties ?? {}
    for (const [name, child] of Object.entries(declared)) {
      if (Object.hasOwn(value, name)) check(child, value[name], `${path}/${name}`, errors)
    }
    if (schema.additionalProperties === false) {
      for (const name of Object.keys(value)) {
        if (!Object.hasOwn(declared, name)) {
          fail(errors, path, 'additionalProperties', `must not have additional property '${name}'`)
        }
      }
    }
  }
  // Composition last, so a plain type or required failure is reported at the
  // offending path before the coarser branch keyword fires.
  if (schema.oneOf) {
    const matches = schema.oneOf.filter(branch => matchesSchema(branch, value)).length
    if (matches !== 1) fail(errors, path, 'oneOf', 'must match exactly one schema in oneOf')
  }
  if (schema.anyOf && !schema.anyOf.some(branch => matchesSchema(branch, value))) {
    fail(errors, path, 'anyOf', 'must match a schema in anyOf')
  }
  if (schema.not && matchesSchema(schema.not, value)) {
    fail(errors, path, 'not', 'must NOT be valid against the forbidden schema')
  }
}

function matchesSchema(schema, value) {
  const errors = []
  check(schema, value, '', errors)
  return errors.length === 0
}

/** Returns every violation of `schema`, in document order. Empty means valid. */
export function validate(schema, value) {
  const errors = []
  check(schema, value, '', errors)
  return errors
}
