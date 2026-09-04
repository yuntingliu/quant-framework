import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import {
  answerRunInteraction,
  buildRunInput,
  cancelRun,
  createRun,
  readConexusStatus,
  readManifest,
  readRun,
  readWorkspace,
  readSharedAgentConversations,
  selectRunExposure,
  streamRunEvents,
  upsertSharedAgentConversation,
} from "@/lib/conexus/publishedHarnessClient"
import {
  changedWorkspaceNodesForRun,
  isTerminalRun,
  mergeRunSnapshot,
} from "@/lib/conexus/runState"
import type {
  AgentConversation,
  AgentConversationMessage,
  AgentResearchCheckpoint,
  AgentToolActivity,
  ConexusStatus,
  HostedHarnessManifest,
  PublishedHarnessArtifact,
  PublishedHarnessRun,
  PublishedHarnessWorkspaceOutput,
} from "@/lib/conexus/types"
import { ALPHALAB_REPORT_DESCRIPTION } from "@/workspace/researchResults"

interface Options {
  onCompleted?: () => void
}

interface ConversationHistoryState {
  conversations: AgentConversation[]
  selectedId: string | null
}

const MAX_SHARED_CONVERSATIONS = 100
const MAX_MESSAGES = 80
const MAX_STORED_MESSAGE_CHARACTERS = 40_000
const MAX_CONTEXT_MESSAGES = 16
const MAX_CONTEXT_MESSAGE_CHARACTERS = 8_000
const MAX_CONVERSATION_CONTEXT_CHARACTERS = 60_000
const DECISION_NOTEBOOK_NODE_ID = "alphalab-decision-notebook-v1"
const WORKSPACE_RESULT_NODE_ID = "alphalab-workspace-result-v1"

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function boundedText(value: unknown, limit: number): string | undefined {
  return typeof value === "string" ? value.slice(0, limit) : undefined
}

function customOutputData(
  output: PublishedHarnessWorkspaceOutput,
  expectedType: string,
): Record<string, unknown> | null {
  const outer = record(output.values.data)
  if (!outer) return null
  const nested = record(outer.data)
  return outer.customType === expectedType && nested ? nested : outer
}

function boundedDecisionNotebook(value: unknown): Record<string, unknown> | undefined {
  const notebook = record(value)
  if (!notebook) return undefined
  const classification = boundedText(notebook.classification, 120)
  const baseCase = boundedText(notebook.baseCase, 4_000)
  const riskCase = boundedText(notebook.riskCase, 4_000)
  const nextAction = boundedText(notebook.nextAction, 4_000)
  const candidateExpressions = Array.isArray(notebook.candidateExpressions)
    ? notebook.candidateExpressions
        .filter((item): item is string => typeof item === "string")
        .slice(0, 20)
        .map((item) => item.slice(0, 500))
    : []
  if (!classification || baseCase === undefined || riskCase === undefined || nextAction === undefined) {
    return undefined
  }
  return { classification, baseCase, riskCase, nextAction, candidateExpressions }
}

function boundedWorkspaceResult(value: unknown): Record<string, unknown> | undefined {
  const result = record(value)
  const requestId = result ? boundedText(result.requestId, 200)?.trim() : undefined
  if (!result || result.version !== 1 || !requestId || (result.kind !== "none" && result.kind !== "document")) {
    return undefined
  }
  if (result.kind === "none") return { version: 1, requestId, kind: "none" }
  const reportId = boundedText(result.reportId, 200)?.trim()
  const title = boundedText(result.title, 200)?.trim()
  if (
    !reportId
    || !/^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$/.test(reportId)
    || !title
    || !Array.isArray(result.sources)
    || result.sources.length > 20
    || result.sources.some((item) => !boundedText(item, 500)?.trim())
  ) return undefined
  return {
    version: 1,
    requestId,
    kind: "document",
    reportId,
    title,
    ...(boundedText(result.description, 1_000) ? { description: boundedText(result.description, 1_000) } : {}),
    sources: result.sources.map((item) => (item as string).trim()),
  }
}

function researchCheckpoint(value: unknown): AgentResearchCheckpoint | undefined {
  const checkpoint = record(value)
  if (
    !checkpoint
    || checkpoint.version !== 1
    || typeof checkpoint.runId !== "string"
    || typeof checkpoint.updatedAt !== "string"
  ) return undefined
  const decisionNotebook = boundedDecisionNotebook(checkpoint.decisionNotebook)
  const workspaceResult = boundedWorkspaceResult(checkpoint.workspaceResult)
  if (!decisionNotebook && !workspaceResult) return undefined
  return {
    version: 1,
    runId: checkpoint.runId,
    updatedAt: checkpoint.updatedAt,
    ...(decisionNotebook ? { decisionNotebook } : {}),
    ...(workspaceResult ? { workspaceResult } : {}),
  }
}

function checkpointFromRun(
  run: PublishedHarnessRun,
  changedNodes: PublishedHarnessWorkspaceOutput[],
): AgentResearchCheckpoint | undefined {
  let decisionNotebook: Record<string, unknown> | undefined
  let workspaceResult: Record<string, unknown> | undefined
  for (const output of changedNodes) {
    if (output.id === DECISION_NOTEBOOK_NODE_ID) {
      decisionNotebook = boundedDecisionNotebook(customOutputData(output, "alphalab_decision_notebook"))
    } else if (output.id === WORKSPACE_RESULT_NODE_ID) {
      workspaceResult = boundedWorkspaceResult(customOutputData(output, "alphalab_workspace_result"))
    }
  }
  if (!decisionNotebook && !workspaceResult) return undefined
  return {
    version: 1,
    runId: run.id,
    updatedAt: run.completedAt ?? new Date().toISOString(),
    ...(decisionNotebook ? { decisionNotebook } : {}),
    ...(workspaceResult ? { workspaceResult } : {}),
  }
}

function workspaceNodeArtifacts(
  run: PublishedHarnessRun,
  changedNodes: PublishedHarnessWorkspaceOutput[],
): PublishedHarnessArtifact[] {
  const createdAt = run.completedAt ?? new Date().toISOString()
  return changedNodes.map((output) => {
    const common = {
      id: `${run.id}:${output.id}`,
      runId: run.id,
      title: output.label,
      createdAt,
      producerNodeId: output.id,
    }
    if (
      (output.type === "note" || output.type === "document")
      && output.description === ALPHALAB_REPORT_DESCRIPTION
      && typeof output.values.content === "string"
    ) {
      return {
        ...common,
        kind: "document" as const,
        outputKey: "reportDocument",
        content: { markdown: output.values.content },
      }
    }
    return {
      ...common,
      kind: "node" as const,
      content: {
        node: {
          id: output.id,
          type: output.type,
          label: output.label,
          ...(output.description ? { description: output.description } : {}),
          values: output.values,
        },
      },
    }
  })
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function toolResult(content: string): { success?: boolean; message?: string } {
  try {
    const value = record(JSON.parse(content))
    if (!value) return {}
    const success = typeof value.success === "boolean" ? value.success : undefined
    const detail = typeof value.message === "string"
      ? value.message
      : typeof value.error === "string" ? value.error : undefined
    return {
      ...(success === undefined ? {} : { success }),
      ...(detail ? { message: detail.slice(0, 280) } : {}),
    }
  } catch {
    return {}
  }
}

function upsertToolActivities(
  current: AgentToolActivity[],
  next: AgentToolActivity[],
): AgentToolActivity[] {
  const updated = [...current]
  for (const activity of next) {
    const existing = updated.findIndex((item) => item.callId === activity.callId)
    if (existing < 0) updated.push(activity)
    else updated[existing] = { ...updated[existing], ...activity }
  }
  return updated.slice(-12)
}

function id(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function storedArtifacts(value: unknown): PublishedHarnessArtifact[] | undefined {
  if (!Array.isArray(value)) return undefined
  const artifacts = value
    .filter((item) => {
      const candidate = record(item)
      return Boolean(
        candidate
        && typeof candidate.id === "string"
        && typeof candidate.runId === "string"
        && typeof candidate.title === "string"
        && typeof candidate.createdAt === "string"
        && ["document", "image", "file", "json", "node"].includes(String(candidate.kind))
        && record(candidate.content),
      )
    })
    .slice(0, 40) as PublishedHarnessArtifact[]
  return artifacts.length ? artifacts : undefined
}

function storedMessage(value: unknown): AgentConversationMessage | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const candidate = value as Record<string, unknown>
  if (
    typeof candidate.id !== "string"
    || candidate.id.length === 0
    || candidate.id.length > 200
    || (candidate.role !== "user" && candidate.role !== "assistant")
    || typeof candidate.content !== "string"
    || typeof candidate.createdAt !== "string"
    || Number.isNaN(Date.parse(candidate.createdAt))
    || typeof candidate.runId !== "string"
    || candidate.runId.length === 0
    || candidate.runId.length > 200
  ) return null
  const artifacts = storedArtifacts(candidate.artifacts)
  return {
    id: candidate.id,
    role: candidate.role,
    content: candidate.content.slice(0, MAX_STORED_MESSAGE_CHARACTERS),
    createdAt: candidate.createdAt,
    runId: candidate.runId,
    ...(artifacts ? { artifacts } : {}),
    ...(candidate.error === true ? { error: true } : {}),
  }
}

function storedConversation(value: unknown): AgentConversation | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const candidate = value as Record<string, unknown>
  if (
    typeof candidate.id !== "string"
    || candidate.id.length === 0
    || candidate.id.length > 200
    || typeof candidate.title !== "string"
    || typeof candidate.createdAt !== "string"
    || Number.isNaN(Date.parse(candidate.createdAt))
    || typeof candidate.updatedAt !== "string"
    || Number.isNaN(Date.parse(candidate.updatedAt))
    || !Array.isArray(candidate.messages)
  ) return null
  return {
    id: candidate.id,
    title: candidate.title.slice(0, 120) || "New conversation",
    createdAt: candidate.createdAt,
    updatedAt: candidate.updatedAt,
    messages: candidate.messages
      .map(storedMessage)
      .filter((item): item is AgentConversationMessage => item !== null)
      .slice(-MAX_MESSAGES),
    ...(researchCheckpoint(candidate.researchCheckpoint)
      ? { researchCheckpoint: researchCheckpoint(candidate.researchCheckpoint) }
      : {}),
  }
}

function conversationTitle(content: string): string {
  const normalized = content.replace(/\s+/g, " ").trim()
  return normalized.length > 60 ? `${normalized.slice(0, 57)}...` : normalized || "New conversation"
}

function withConversation(
  conversations: AgentConversation[],
  conversation: AgentConversation,
): AgentConversation[] {
  return [
    conversation,
    ...conversations.filter((item) => item.id !== conversation.id),
  ].slice(0, MAX_SHARED_CONVERSATIONS)
}

function mergeMessages(
  left: AgentConversationMessage[],
  right: AgentConversationMessage[],
): AgentConversationMessage[] {
  const messages = new Map<string, AgentConversationMessage>()
  for (const message of [...left, ...right]) {
    const previous = messages.get(message.id)
    messages.set(message.id, previous ? {
      ...previous,
      ...message,
      ...(message.artifacts?.length
        ? { artifacts: message.artifacts }
        : previous.artifacts?.length ? { artifacts: previous.artifacts } : {}),
      ...(previous.error || message.error ? { error: true } : {}),
    } : message)
  }
  return [...messages.values()]
    .sort((first, second) => first.createdAt.localeCompare(second.createdAt) || first.id.localeCompare(second.id))
    .slice(-MAX_MESSAGES)
}

function mergeConversation(left: AgentConversation, right: AgentConversation): AgentConversation {
  const newer = right.updatedAt >= left.updatedAt ? right : left
  const messages = mergeMessages(left.messages, right.messages)
  const checkpoints = [left.researchCheckpoint, right.researchCheckpoint]
    .filter((item): item is AgentResearchCheckpoint => Boolean(item))
    .sort((first, second) => second.updatedAt.localeCompare(first.updatedAt))
  const updateTimes = [left.updatedAt, right.updatedAt, ...messages.map((item) => item.createdAt)].sort()
  return {
    id: left.id,
    title: newer.title,
    createdAt: left.createdAt <= right.createdAt ? left.createdAt : right.createdAt,
    updatedAt: updateTimes[updateTimes.length - 1] ?? newer.updatedAt,
    messages,
    ...(checkpoints[0] ? { researchCheckpoint: checkpoints[0] } : {}),
  }
}

function mergeConversationLists(
  left: AgentConversation[],
  right: AgentConversation[],
): AgentConversation[] {
  const conversations = new Map(left.map((item) => [item.id, item]))
  for (const conversation of right) {
    const previous = conversations.get(conversation.id)
    conversations.set(
      conversation.id,
      previous ? mergeConversation(previous, conversation) : conversation,
    )
  }
  return [...conversations.values()]
    .sort((first, second) => second.updatedAt.localeCompare(first.updatedAt) || first.id.localeCompare(second.id))
    .slice(0, MAX_SHARED_CONVERSATIONS)
}

function conversationFingerprint(conversation: AgentConversation): string {
  return JSON.stringify(conversation)
}

function conversationContext(conversation: AgentConversation | null): Record<string, unknown> {
  const candidates = (conversation?.messages ?? [])
    .filter((item) => !item.error)
    .slice(-MAX_CONTEXT_MESSAGES)
  const checkpoint = conversation?.researchCheckpoint
  let remaining = Math.max(
    0,
    MAX_CONVERSATION_CONTEXT_CHARACTERS - (checkpoint ? JSON.stringify(checkpoint).length : 0),
  )
  const messages: Array<{ role: "user" | "assistant"; content: string }> = []
  for (let index = candidates.length - 1; index >= 0 && remaining > 0; index -= 1) {
    const item = candidates[index]!
    const contentLimit = Math.min(MAX_CONTEXT_MESSAGE_CHARACTERS, Math.max(0, remaining - 64))
    if (contentLimit <= 0) break
    const content = item.content.slice(0, contentLimit)
    messages.unshift({ role: item.role, content })
    remaining -= content.length + 64
  }
  return {
    version: 1,
    storage: "server-shared",
    messages,
    ...(checkpoint ? { researchCheckpoint: checkpoint } : {}),
  }
}

function terminalContent(run: PublishedHarnessRun): string {
  if (run.summary?.trim()) return run.summary.trim()
  if (run.error?.message) return run.error.message
  if (run.status === "cancelled") return "The response was cancelled."
  if (run.status === "blocked") return "The Agent could not complete this request."
  return "The Agent completed without a text response."
}

export function usePublishedAgent({ onCompleted }: Options = {}) {
  const [status, setStatus] = useState<ConexusStatus | null>(null)
  const [manifest, setManifest] = useState<HostedHarnessManifest | null>(null)
  const [history, setHistory] = useState<ConversationHistoryState>({
    conversations: [],
    selectedId: null,
  })
  const [run, setRun] = useState<PublishedHarnessRun | null>(null)
  const [toolActivities, setToolActivities] = useState<AgentToolActivity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const runAccessTokenRef = useRef("")
  const streamRef = useRef<AbortController | null>(null)
  const finalizedRunIdsRef = useRef(new Set<string>())
  const sharedHistoryReadyRef = useRef(false)
  const sharedFingerprintsRef = useRef(new Map<string, string>())

  const conversation = history.conversations.find((item) => item.id === history.selectedId) ?? null

  const acceptError = useCallback((reason: unknown) => {
    setError(message(reason))
  }, [])

  const resolveChangedWorkspaceNodes = useCallback(async (
    completed: PublishedHarnessRun,
    signal?: AbortSignal,
  ): Promise<PublishedHarnessWorkspaceOutput[]> => {
    const changed = [
      ...(completed.nodeChanges?.created ?? []),
      ...(completed.nodeChanges?.updated ?? []),
    ]
    if (changed.length === 0) return []
    const workspace = await readWorkspace(signal)
    return changedWorkspaceNodesForRun(completed, workspace)
  }, [])

  const refreshSharedHistory = useCallback(async (
    signal?: AbortSignal,
    selectNewest = false,
  ) => {
    const shared = (await readSharedAgentConversations(signal))
      .map(storedConversation)
      .filter((item): item is AgentConversation => item !== null)
    for (const item of shared) {
      sharedFingerprintsRef.current.set(item.id, conversationFingerprint(item))
    }
    sharedHistoryReadyRef.current = true
    setHistory((current) => {
      const conversations = mergeConversationLists(shared, current.conversations)
      const selectedId = current.selectedId && conversations.some((item) => item.id === current.selectedId)
        ? current.selectedId
        : selectNewest ? conversations[0]?.id ?? null : null
      return { conversations, selectedId }
    })
  }, [])

  useEffect(() => {
    if (!sharedHistoryReadyRef.current) return
    for (const item of history.conversations) {
      const fingerprint = conversationFingerprint(item)
      if (sharedFingerprintsRef.current.get(item.id) === fingerprint) continue
      sharedFingerprintsRef.current.set(item.id, fingerprint)
      void upsertSharedAgentConversation(item).then((savedValue) => {
        const saved = storedConversation(savedValue)
        if (!saved) throw new Error("Shared Agent history returned an invalid conversation.")
        sharedFingerprintsRef.current.set(saved.id, conversationFingerprint(saved))
        setHistory((current) => ({
          conversations: mergeConversationLists(current.conversations, [saved]),
          selectedId: current.selectedId,
        }))
      }).catch((reason) => {
        if (sharedFingerprintsRef.current.get(item.id) === fingerprint) {
          sharedFingerprintsRef.current.delete(item.id)
        }
        acceptError(reason)
      })
    }
  }, [acceptError, history.conversations])

  useEffect(() => {
    const controller = new AbortController()
    const initialize = async () => {
      try {
        await refreshSharedHistory(controller.signal, true)
      } catch (reason) {
        if (!controller.signal.aborted) acceptError(reason)
      }
    }
    void initialize()
    const timer = globalThis.setInterval(() => {
      void refreshSharedHistory(controller.signal).catch((reason) => {
        if (!controller.signal.aborted && !sharedHistoryReadyRef.current) acceptError(reason)
      })
    }, 10_000)
    return () => {
      controller.abort()
      globalThis.clearInterval(timer)
    }
  }, [acceptError, refreshSharedHistory])

  const reloadStatus = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError("")
    try {
      const nextStatus = await readConexusStatus(signal)
      setStatus(nextStatus)
      if (!nextStatus.available) {
        setManifest(null)
        return
      }
      const nextManifest = await readManifest(signal)
      if (nextManifest.identityPolicy !== "enterprise" || nextManifest.billingPolicy !== "publisher") {
        throw new Error("Hosted AlphaLab Agent must use enterprise service identity with publisher billing.")
      }
      selectRunExposure(nextManifest)
      setManifest(nextManifest)
    } catch (reason) {
      if (!signal?.aborted) acceptError(reason)
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [acceptError])

  useEffect(() => {
    const controller = new AbortController()
    void reloadStatus(controller.signal)
    return () => controller.abort()
  }, [reloadStatus])

  useEffect(() => () => streamRef.current?.abort(), [])

  const finalizeRun = useCallback((
    completed: PublishedHarnessRun,
    conversationId: string,
    changedNodes: PublishedHarnessWorkspaceOutput[],
  ) => {
    if (!isTerminalRun(completed)) return
    if (finalizedRunIdsRef.current.has(completed.id)) return
    finalizedRunIdsRef.current.add(completed.id)
    streamRef.current?.abort()
    streamRef.current = null
    const createdAt = completed.completedAt ?? new Date().toISOString()
    const artifacts = workspaceNodeArtifacts(completed, changedNodes)
    const checkpoint = checkpointFromRun(completed, changedNodes)
    const assistantMessage: AgentConversationMessage = {
      id: id(),
      role: "assistant",
      content: terminalContent(completed),
      createdAt,
      runId: completed.id,
      ...(artifacts.length ? { artifacts } : {}),
      ...(["failed", "cancelled"].includes(completed.status) ? { error: true } : {}),
    }
    setHistory((current) => {
      const selected = current.conversations.find((item) => item.id === conversationId)
      if (!selected) return current
      const updated = {
        ...selected,
        updatedAt: createdAt,
        messages: [...selected.messages, assistantMessage].slice(-MAX_MESSAGES),
        ...(checkpoint ? { researchCheckpoint: checkpoint } : {}),
      }
      return { conversations: withConversation(current.conversations, updated), selectedId: conversationId }
    })
    setToolActivities((current) => current.map((activity): AgentToolActivity => activity.state === "running"
      ? {
          ...activity,
          state: "completed",
          ...(completed.status === "completed" ? {} : { success: false }),
        }
      : activity))
    setRun(completed)
    setError("")
    runAccessTokenRef.current = ""
    onCompleted?.()
  }, [onCompleted])

  useEffect(() => {
    const runId = run?.id
    const conversationId = conversation?.id
    const accessToken = runAccessTokenRef.current
    if (
      !runId
      || !conversationId
      || !accessToken
      || finalizedRunIdsRef.current.has(runId)
    ) return

    const controller = new AbortController()
    let timer: ReturnType<typeof globalThis.setTimeout> | undefined
    const reconcile = async () => {
      if (controller.signal.aborted || finalizedRunIdsRef.current.has(runId)) return
      try {
        const latest = await readRun(runId, accessToken, controller.signal)
        const changedNodes = isTerminalRun(latest)
          ? await resolveChangedWorkspaceNodes(latest, controller.signal)
          : []
        setRun((current) => current?.id === runId
          ? mergeRunSnapshot(current, latest)
          : current)
        if (isTerminalRun(latest)) {
          finalizeRun(latest, conversationId, changedNodes)
          controller.abort()
          return
        }
      } catch {
        if (controller.signal.aborted) return
      }
      timer = globalThis.setTimeout(() => {
        void reconcile()
      }, 2_000)
    }
    timer = globalThis.setTimeout(() => {
      void reconcile()
    }, 2_000)
    return () => {
      controller.abort()
      if (timer !== undefined) globalThis.clearTimeout(timer)
    }
  }, [conversation?.id, finalizeRun, resolveChangedWorkspaceNodes, run?.id])

  const selectConversation = useCallback((conversationId: string) => {
    streamRef.current?.abort()
    streamRef.current = null
    setHistory((current) => current.conversations.some((item) => item.id === conversationId)
      ? { ...current, selectedId: conversationId }
      : current)
    setRun(null)
    setToolActivities([])
    setError("")
    runAccessTokenRef.current = ""
  }, [])

  const newConversation = useCallback(() => {
    streamRef.current?.abort()
    streamRef.current = null
    setHistory((current) => ({ ...current, selectedId: null }))
    setRun(null)
    setToolActivities([])
    setError("")
    runAccessTokenRef.current = ""
  }, [])

  const runActive = run?.status === "queued" || run?.status === "running"
  const responseActive = runActive

  const send = useCallback(async (rawMessage: string, context: Record<string, unknown>) => {
    const userMessage = rawMessage.trim()
    if (!userMessage || !status?.available || !manifest) return false
    setError("")

    if (run?.pendingInteraction && runAccessTokenRef.current) {
      try {
        const answered = await answerRunInteraction(
          run.id,
          run.pendingInteraction.id,
          userMessage,
          runAccessTokenRef.current,
        )
        setRun((current) => current ? mergeRunSnapshot(current, answered) : answered)
        return true
      } catch (reason) {
        acceptError(reason)
        return false
      }
    }
    if (responseActive) return false

    streamRef.current?.abort()
    setToolActivities([])
    try {
      const exposure = selectRunExposure(manifest)
      const result = await createRun({
        exposureId: exposure.id,
        input: buildRunInput(userMessage, {
          ...context,
          conversationHistory: conversationContext(conversation),
        }),
      })
      const now = new Date().toISOString()
      const conversationId = conversation?.id ?? id()
      const userEntry: AgentConversationMessage = {
        id: id(),
        role: "user",
        content: userMessage,
        createdAt: now,
        runId: result.run.id,
      }
      setHistory((current) => {
        const existing = current.conversations.find((item) => item.id === conversationId)
        const updated: AgentConversation = existing
          ? {
              ...existing,
              updatedAt: now,
              messages: [...existing.messages, userEntry].slice(-MAX_MESSAGES),
            }
          : {
              id: conversationId,
              title: conversationTitle(userMessage),
              createdAt: now,
              updatedAt: now,
              messages: [userEntry],
            }
        return { conversations: withConversation(current.conversations, updated), selectedId: conversationId }
      })
      runAccessTokenRef.current = result.accessToken
      setRun(result.run)

      const controller = new AbortController()
      streamRef.current = controller
      void streamRunEvents({
        runId: result.run.id,
        accessToken: result.accessToken,
        signal: controller.signal,
        onEvent: async (event) => {
          if (event.type === "run.message" && event.message?.role === "assistant") {
            const activities = (event.message.tool_calls ?? []).map((call): AgentToolActivity => ({
              callId: call.id,
              name: call.function.name,
              ownerNodeId: event.ownerNodeId ?? "",
              state: "running",
              at: event.at,
            }))
            if (activities.length > 0) {
              setToolActivities((current) => upsertToolActivities(current, activities))
            }
          } else if (
            event.type === "run.message"
            && event.message?.role === "tool"
            && event.message.tool_call_id
            && event.message.name
          ) {
            const result = toolResult(event.message.content)
            const activity: AgentToolActivity = {
              callId: event.message.tool_call_id,
              name: event.message.name,
              ownerNodeId: event.ownerNodeId ?? "",
              state: "completed",
              at: event.at,
              ...result,
            }
            setToolActivities((current) => upsertToolActivities(current, [activity]))
          }
          setRun((current) => current ? {
            ...current,
            status: isTerminalRun(current) && !isTerminalRun(event)
              ? current.status
              : event.status,
            ...(event.type === "run.interaction_requested" && event.interaction
              ? { pendingInteraction: event.interaction }
              : {}),
            ...(event.type === "run.interaction_resolved" ? { pendingInteraction: undefined } : {}),
          } : current)
          if (!isTerminalRun(event)) return
          const completed = await readRun(result.run.id, result.accessToken, controller.signal)
          const changedNodes = isTerminalRun(completed)
            ? await resolveChangedWorkspaceNodes(completed, controller.signal)
            : []
          setRun((current) => current?.id === completed.id
            ? mergeRunSnapshot(current, completed)
            : current)
          if (!isTerminalRun(completed)) return
          finalizeRun(completed, conversationId, changedNodes)
        },
      }).catch((reason) => {
        if (!controller.signal.aborted) acceptError(reason)
      })
      return true
    } catch (reason) {
      acceptError(reason)
      return false
    }
  }, [acceptError, conversation, finalizeRun, manifest, resolveChangedWorkspaceNodes, responseActive, run, status?.available])

  const cancel = useCallback(async () => {
    if (!runActive || !run || !runAccessTokenRef.current || !conversation) return
    setError("")
    try {
      const cancelled = await cancelRun(run.id, runAccessTokenRef.current)
      streamRef.current?.abort()
      finalizeRun(cancelled, conversation.id, [])
    } catch (reason) {
      acceptError(reason)
    }
  }, [acceptError, conversation, finalizeRun, run, runActive])

  return useMemo(() => ({
    status,
    manifest,
    conversations: history.conversations,
    conversation,
    run,
    toolActivities,
    loading,
    error,
    responseActive,
    reloadStatus,
    selectConversation,
    newConversation,
    send,
    cancel,
  }), [cancel, conversation, error, history.conversations, loading, manifest, newConversation, reloadStatus, responseActive, run, selectConversation, send, status, toolActivities])
}
