import type { LinkGroup } from "@/contexts/WorkspaceContext"
import type { WorkspaceMode } from "@/layouts/presets"
import type { AgentResearchResult } from "@/workspace/researchResults"

export const WORKSPACE_COMMAND_EVENT = "alphalab:workspaceCommands"

export type AgentWorkspaceCommand =
  | { type: "switch_mode"; mode: WorkspaceMode }
  | { type: "open_widget"; widgetId: string; mode?: WorkspaceMode; title?: string }
  | { type: "open_result"; resultId: string; mode?: WorkspaceMode; title?: string }
  | { type: "close_widget"; widgetId: string; mode?: WorkspaceMode }
  | {
      type: "set_focus"
      symbol?: string | null
      strategyId?: string | null
      backtestId?: string | null
      date?: string | null
    }
  | { type: "set_link_symbol"; group: LinkGroup; symbol: string | null }
  | { type: "show_right_rail"; open?: boolean }
  | { type: "refresh_data" }
  | { type: "save_layout"; mode?: WorkspaceMode }
  | { type: "reset_layout"; mode?: WorkspaceMode }

export interface AgentWorkspaceCommandBatch {
  version: 1
  requestId: string
  commands: AgentWorkspaceCommand[]
}

export interface AgentWorkspaceCommandReceipt {
  index: number
  type: AgentWorkspaceCommand["type"] | "invalid"
  success: boolean
  message: string
}

export interface AgentWorkspaceCommandEventDetail {
  artifactId: string
  runId: string
  batch: AgentWorkspaceCommandBatch
  researchResult?: AgentResearchResult
  receipts: AgentWorkspaceCommandReceipt[]
  receiptPromise?: Promise<AgentWorkspaceCommandReceipt[]>
}

const MODES = new Set<WorkspaceMode>([
  "data", "factor", "project", "selection", "portfolio", "execution", "backtest", "report",
])
const LINK_GROUPS = new Set<LinkGroup>(["a", "b", "c", "d"])

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function optionalText(value: unknown, maxLength = 200): string | null | undefined {
  if (value === undefined || value === null) return value
  if (typeof value !== "string") return undefined
  const text = value.trim()
  return text && text.length <= maxLength ? text : undefined
}

function mode(value: unknown): WorkspaceMode | undefined {
  return typeof value === "string" && MODES.has(value as WorkspaceMode)
    ? value as WorkspaceMode
    : undefined
}

function parseCommand(value: unknown): AgentWorkspaceCommand | null {
  if (!isRecord(value) || typeof value.type !== "string") return null
  switch (value.type) {
    case "switch_mode": {
      const target = mode(value.mode)
      return target ? { type: "switch_mode", mode: target } : null
    }
    case "open_widget": {
      const widgetId = optionalText(value.widgetId)
      const targetMode = value.mode === undefined ? undefined : mode(value.mode)
      const title = optionalText(value.title)
      if (!widgetId || (value.mode !== undefined && !targetMode)) return null
      return { type: "open_widget", widgetId, ...(targetMode ? { mode: targetMode } : {}), ...(title ? { title } : {}) }
    }
    case "open_result": {
      const resultId = optionalText(value.resultId)
      const targetMode = value.mode === undefined ? undefined : mode(value.mode)
      const title = optionalText(value.title)
      if (!resultId || (value.mode !== undefined && !targetMode)) return null
      return { type: "open_result", resultId, ...(targetMode ? { mode: targetMode } : {}), ...(title ? { title } : {}) }
    }
    case "close_widget": {
      const widgetId = optionalText(value.widgetId)
      const targetMode = value.mode === undefined ? undefined : mode(value.mode)
      if (!widgetId || (value.mode !== undefined && !targetMode)) return null
      return { type: "close_widget", widgetId, ...(targetMode ? { mode: targetMode } : {}) }
    }
    case "set_focus": {
      const command: Extract<AgentWorkspaceCommand, { type: "set_focus" }> = { type: "set_focus" }
      for (const [source, target] of [
        ["symbol", "symbol"],
        ["strategyId", "strategyId"],
        ["backtestId", "backtestId"],
        ["date", "date"],
      ] as const) {
        if (!(source in value)) continue
        const parsed = optionalText(value[source])
        if (parsed === undefined) return null
        command[target] = parsed
      }
      return Object.keys(command).length > 1 ? command : null
    }
    case "set_link_symbol": {
      const group = typeof value.group === "string" ? value.group as LinkGroup : null
      const symbol = optionalText(value.symbol)
      return group && LINK_GROUPS.has(group) && symbol !== undefined
        ? { type: "set_link_symbol", group, symbol }
        : null
    }
    case "show_right_rail": {
      if (value.tab !== undefined) return null
      if (value.open !== undefined && typeof value.open !== "boolean") return null
      return { type: "show_right_rail", ...(typeof value.open === "boolean" ? { open: value.open } : {}) }
    }
    case "refresh_data":
      return { type: "refresh_data" }
    case "save_layout":
    case "reset_layout": {
      const targetMode = value.mode === undefined ? undefined : mode(value.mode)
      if (value.mode !== undefined && !targetMode) return null
      return { type: value.type, ...(targetMode ? { mode: targetMode } : {}) }
    }
    default:
      return null
  }
}

export function parseAgentWorkspaceCommandBatch(value: unknown): AgentWorkspaceCommandBatch | null {
  if (!isRecord(value) || value.version !== 1 || !Array.isArray(value.commands)) return null
  const requestId = optionalText(value.requestId)
  if (!requestId || value.commands.length > 20) return null
  const commands = value.commands.map(parseCommand)
  if (commands.some((command) => command === null)) return null
  return { version: 1, requestId, commands: commands as AgentWorkspaceCommand[] }
}
