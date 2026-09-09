import type { HarnessToolDefinition, RuntimeNode } from '../contracts.js'

function optionalText(value: unknown, maximum: number): string | undefined {
  if (typeof value !== 'string' || !value.trim()) return undefined
  return value.trim().slice(0, maximum)
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

/** Builds an executable Tool definition from the current mutable Canvas node. */
export function canvasToolDefinition(
  node: Pick<RuntimeNode, 'id' | 'data'>,
  baseline?: HarnessToolDefinition
): HarnessToolDefinition {
  const runtime = optionalText(node.data.runtime, 80)
  const acceptedRuntime = runtime === 'node'
    || runtime === 'python'
    || runtime === 'powershell'
    || runtime === 'bash'
    || runtime === 'shell'
    || runtime === 'registered'
    || runtime === 'mcp'
    ? runtime
    : undefined
  const description = optionalText(node.data.description, 20_000)
  const inputSchema = record(node.data.inputSchema)
  const outputSchema = record(node.data.outputSchema)
  return {
    name: optionalText(node.data.toolName ?? node.data.name ?? node.data.label, 240) ?? node.id,
    ...(description ? { description } : {}),
    ...(acceptedRuntime ? { runtime: acceptedRuntime } : {}),
    ...(typeof node.data.code === 'string' ? { code: node.data.code } : {}),
    ...(inputSchema ? { inputSchema } : {}),
    ...(outputSchema ? { outputSchema } : {}),
    ...(baseline?.permissions ? { permissions: baseline.permissions } : {}),
    ...(typeof node.data.exposeAsTool === 'boolean' ? { exposeAsTool: node.data.exposeAsTool } : {}),
    nodeId: node.id
  }
}
