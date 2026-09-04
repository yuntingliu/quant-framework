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
  const keepTerminalStatus = isTerminalRun(current) && !isTerminalRun(incoming)
  return {
    ...current,
    ...incoming,
    status: keepTerminalStatus ? current.status : incoming.status,
    ...(incoming.pendingInteraction
      ? { pendingInteraction: incoming.pendingInteraction }
      : { pendingInteraction: undefined }),
  }
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
