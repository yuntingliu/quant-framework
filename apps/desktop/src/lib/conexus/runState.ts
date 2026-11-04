import type {
  PublishedHarnessRun,
  PublishedHarnessRunEvent,
  PublishedHarnessWorkspaceOutput,
  PublishedHarnessWorkspaceSnapshot,
} from "./types"

const TERMINAL_RUN_STATUSES = new Set([
  "completed",
  "blocked",
  "failed",
  "cancelled",
])

export function isTerminalRun(
  value: PublishedHarnessRun | PublishedHarnessRunEvent,
): boolean {
  return TERMINAL_RUN_STATUSES.has(value.status)
}

export function mergeRunSnapshot(
  current: PublishedHarnessRun,
  incoming: PublishedHarnessRun,
): PublishedHarnessRun {
  if (current.id !== incoming.id) return incoming
  // A stale active snapshot says nothing about a finished task, including any old question.
  if (isTerminalRun(current) && !isTerminalRun(incoming)) return current
  return {
    ...current,
    ...incoming,
    ...(incoming.pendingInteraction
      ? { pendingInteraction: incoming.pendingInteraction }
      : { pendingInteraction: undefined }),
  }
}

export type AgentErrorSource = "action" | "connection" | "history" | "poll" | "stream"

// A failed action stays visible until the next action. A failed read is only as true as
// the most recent read: once one succeeds, its error describes nothing that is still the case.
export function errorClearedBy(
  source: AgentErrorSource | null,
  success: "history" | "run",
): boolean {
  if (source === "connection") return true
  if (success === "run") return source === "poll" || source === "stream"
  return source === "history"
}

export function changedWorkspaceNodesForRun(
  run: PublishedHarnessRun,
  workspace: PublishedHarnessWorkspaceSnapshot,
): PublishedHarnessWorkspaceOutput[] {
  const changedNodeIds = new Set([
    ...(run.nodeChanges?.created ?? []),
    ...(run.nodeChanges?.updated ?? []),
  ])
  return workspace.nodes
    .filter((node) => (
      node.updatedByRunId === run.id
      && (changedNodeIds.size === 0 || changedNodeIds.has(node.id))
    ))
    .map(({ id, type, label, description, values }) => ({
      id,
      type,
      label,
      ...(description ? { description } : {}),
      values,
    }))
}

export function parseRunEventBlock(block: string): PublishedHarnessRunEvent | null {
  const data = block.split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n")
  return data ? JSON.parse(data) as PublishedHarnessRunEvent : null
}
