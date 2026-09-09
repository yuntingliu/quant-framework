import { DEFAULT_AGENT_TOOL_NAMES } from './agent-tool-selection.js'
import type { RuntimeToolSchema } from '../contracts.js'
import {
  NODE_AGENT_TOOL_METADATA,
  NODE_AGENT_TOOL_SCHEMAS,
  type NodeAgentToolName,
  type NodeAgentToolRuntime
} from '../tools/node-agent-tools.js'

export interface AgentToolMetadata {
  summary: string
  capability: string
  runtime: NodeAgentToolRuntime
}

export interface AgentToolDefinition extends AgentToolMetadata {
  name: NodeAgentToolName
  schema: RuntimeToolSchema
}

export interface AgentToolCatalogEntry extends AgentToolDefinition {
  available: boolean
  unavailableReason?: string
}

export interface AgentToolProfile {
  name: string
  catalog: readonly AgentToolCatalogEntry[]
  tools: readonly AgentToolDefinition[]
  defaultToolNames: readonly NodeAgentToolName[]
  availableToolNames: readonly NodeAgentToolName[]
}

export interface CreateAgentToolProfileOptions {
  name: string
  availableToolNames?: readonly NodeAgentToolName[]
}

/** Builds the stable node-oriented Agent ABI shared by every host. */
export function createAgentToolProfile(options: CreateAgentToolProfileOptions): AgentToolProfile {
  const availableNames = new Set(options.availableToolNames ?? DEFAULT_AGENT_TOOL_NAMES)
  const catalog = DEFAULT_AGENT_TOOL_NAMES.map((name): AgentToolCatalogEntry => {
    const available = availableNames.has(name)
    return {
      name,
      ...NODE_AGENT_TOOL_METADATA[name],
      schema: NODE_AGENT_TOOL_SCHEMAS[name],
      available,
      ...(available ? {} : { unavailableReason: `This runtime does not provide ${name}.` })
    }
  })
  const tools = catalog
    .filter((tool) => tool.available)
    .map(({ available: _available, unavailableReason: _unavailableReason, ...tool }) => tool)
  const toolNames = tools.map((tool) => tool.name)
  return {
    name: options.name,
    catalog,
    tools,
    defaultToolNames: toolNames,
    availableToolNames: toolNames
  }
}
