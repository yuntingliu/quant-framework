import { NODE_AGENT_TOOL_NAMES, type NodeAgentToolName } from '../tools/node-agent-tools.js'

export const DEFAULT_AGENT_TOOL_NAMES: readonly NodeAgentToolName[] = NODE_AGENT_TOOL_NAMES

// Exact historical automatic selections persisted by released Canvas versions.
// These names are data migration signatures only and are never accepted by the runtime.
const HISTORICAL_AUTOMATIC_AGENT_TOOL_SELECTIONS: readonly (readonly string[])[] = [
  [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_nodes', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_nodes', 'update_nodes', 'generate_image',
    'shell_exec', 'control_browser', 'run_node', 'execute_sql', 'control_node_run',
    'web_search', 'search_harness_library', 'complete'
  ],
  [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_nodes', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_nodes', 'update_nodes', 'generate_image',
    'shell_exec', 'control_browser', 'run_node', 'publish_harness', 'execute_sql',
    'web_search', 'search_harness_library', 'complete'
  ],
  [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_nodes', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_nodes', 'update_nodes', 'generate_image',
    'shell_exec', 'control_browser', 'run_node', 'publish_harness', 'execute_sql',
    'control_node_run', 'web_search', 'search_harness_library', 'complete'
  ],
  [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_nodes', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_nodes', 'update_nodes', 'generate_image',
    'shell_exec', 'control_browser', 'run_node', 'save_harness', 'execute_sql',
    'control_node_run', 'web_search', 'search_harness_library', 'complete'
  ],
  [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_node', 'connect_nodes',
    'delete_node', 'list_nodes', 'attach_node', 'web_search',
    'search_subharness_library', 'complete'
  ],
  [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_node', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_node', 'update_node', 'generate_image',
    'shell_exec', 'control_browser', 'call_agent', 'run_node', 'run_harness',
    'save_harness', 'execute_sql', 'wait_node_run', 'cancel_node_run', 'invoke_tool',
    'web_search', 'search_harness_library', 'complete'
  ],
  [
    'ask_user', 'describe_node_type', 'ask_with_form', 'create_node', 'connect_nodes',
    'delete_node', 'list_nodes', 'observe_node', 'update_node', 'generate_image',
    'shell_exec', 'control_browser', 'run_node', 'save_harness', 'execute_sql',
    'control_node_run', 'web_search', 'search_harness_library', 'complete'
  ]
]

export function normalizeAgentToolNames(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return [...new Set(value
    .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    .map((item) => item.trim()))]
}

function historicalAutomaticSelection(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined
  const names = normalizeAgentToolNames(value)
  const selected = new Set(names)
  const signature = HISTORICAL_AUTOMATIC_AGENT_TOOL_SELECTIONS.find((candidate) =>
    candidate.every((name) => selected.has(name))
  )
  return signature ? names.filter((name) => !signature.includes(name)) : undefined
}

/** Returns obsolete dynamic additions attached to a historical automatic selection. */
export function materializedAutomaticAgentToolExtensions(value: unknown): string[] | undefined {
  return historicalAutomaticSelection(value)
}

/** Historical dynamic tools are node capabilities now, so no top-level extension is retained. */
export function legacyAutomaticAgentToolExtensions(
  value: unknown,
  _allowedExtensionNames: ReadonlySet<string> = new Set()
): string[] | undefined {
  return historicalAutomaticSelection(value) === undefined ? undefined : []
}

export function isLegacyAutomaticAgentToolNames(value: unknown): boolean {
  return historicalAutomaticSelection(value) !== undefined
}

export function usesAutomaticAgentToolSelection(
  value: unknown,
  _allowedExtensionNames: ReadonlySet<string> = new Set()
): boolean {
  return value === undefined || historicalAutomaticSelection(value) !== undefined
}

/** Narrow persisted-data migration from materialized host defaults to host-resolved omission. */
export function migrateLegacyAutomaticAgentToolNames(
  value: unknown,
  _allowedExtensionNames: ReadonlySet<string> = new Set()
): unknown {
  return historicalAutomaticSelection(value) !== undefined ? undefined : value
}
