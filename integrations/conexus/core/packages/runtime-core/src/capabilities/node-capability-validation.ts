import AjvModule, { type ErrorObject, type ValidateFunction } from 'ajv'
import type { NodeCapabilityContract } from './node-capability-contracts.js'

export interface NodeCapabilityValidationDetail {
  path: string
  keyword: string
  message: string
  params: Record<string, unknown>
}

export type NodeCapabilityInputValidation =
  | { ok: true }
  | {
      ok: false
      error: string
      details: NodeCapabilityValidationDetail[]
    }

const AjvConstructor = (
  (AjvModule as unknown as { default?: unknown }).default ?? AjvModule
) as new (options: { allErrors: boolean; strict: boolean }) => {
  compile: (schema: unknown) => ValidateFunction<unknown>
}
const ajv = new AjvConstructor({ allErrors: true, strict: false })

function validationDetails(errors: ErrorObject[] | null | undefined): NodeCapabilityValidationDetail[] {
  return (errors ?? []).map((error) => ({
    path: error.instancePath || '/',
    keyword: error.keyword,
    message: error.message ?? 'Schema validation failed.',
    params: error.params
  }))
}

export function validateNodeCapabilityInput(
  contract: Pick<NodeCapabilityContract, 'id' | 'input_schema'>,
  input: unknown
): NodeCapabilityInputValidation {
  let validate: ValidateFunction<unknown>
  try {
    validate = ajv.compile(contract.input_schema)
  } catch (error) {
    return {
      ok: false,
      error: `Invalid input schema for ${contract.id}.`,
      details: [{
        path: '/',
        keyword: 'schema',
        message: error instanceof Error ? error.message : String(error),
        params: {}
      }]
    }
  }

  if (validate(input)) return { ok: true }
  return {
    ok: false,
    error: `Invalid input for ${contract.id}.`,
    details: validationDetails(validate.errors)
  }
}
