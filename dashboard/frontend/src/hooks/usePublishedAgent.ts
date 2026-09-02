import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import {
  answerRunInteraction,
  buildRunInput,
  cancelRun,
  createRun,
  isTerminalRun,
  readConexusStatus,
  readManifest,
  readRun,
  readSharedAgentConversations,
  selectRunExposure,
  streamRunEvents,
  upsertSharedAgentConversation,
} from "@/lib/conexus/publishedHarnessClient"
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

// Keep the original key so existing browser-only history can migrate to the server.
const CONVERSATIONS_CACHE_KEY = "alphalab.anonymous-agent-conversations.v1"
const MAX_SHARED_CONVERSATIONS = 100
const MAX_MESSAGES = 80
const MAX_STORED_MESSAGE_CHARACTERS = 40_000
const MAX_CONTEXT_MESSAGES = 16
const MAX_CONTEXT_MESSAGE_CHARACTERS = 8_000
const MAX_LOCAL_CONTEXT_CHARACTERS = 60_000
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
  if (!result || result.version !== 1 || typeof result.requestId !== "string" || typeof result.kind !== "string") {
    return undefined
  }
  return {
    version: 1,
    requestId: result.requestId.slice(0, 200),
    kind: result.kind.slice(0, 40),
    ...(boundedText(result.reportId, 200) ? { reportId: boundedText(result.reportId, 200) } : {}),
    ...(boundedText(result.title, 200) ? { title: boundedText(result.title, 200) } : {}),
    ...(boundedText(result.description, 1_000) ? { description: boundedText(result.description, 1_000) } : {}),
    ...(Array.isArray(result.sources)
      ? { sources: result.sources.filter((item): item is string => typeof item === "string").slice(0, 20).map((item) => item.slice(0, 500)) }
      : {}),
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

function checkpointFromRun(run: PublishedHarnessRun): AgentResearchCheckpoint | undefined {
  let decisionNotebook: Record<string, unknown> | undefined
  let workspaceResult: Record<string, unknown> | undefined
  for (const output of run.workspaceOutputs ?? []) {
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

function workspaceOutputArtifacts(run: PublishedHarnessRun): PublishedHarnessArtifact[] {
  const createdAt = run.completedAt ?? new Date().toISOString()
  return (run.workspaceOutputs ?? []).map((output) => {
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

function loadCachedHistory(): ConversationHistoryState {
  try {
    const raw = localStorage.getItem(CONVERSATIONS_CACHE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    const conversations = Array.isArray(parsed)
      ? parsed
          .map(storedConversation)
          .filter((item): item is AgentConversation => item !== null)
          .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
          .slice(0, MAX_SHARED_CONVERSATIONS)
      : []
    return { conversations, selectedId: conversations[0]?.id ?? null }
  } catch {
    return { conversations: [], selectedId: null }
  }
}

function compactConversation(
  conversation: AgentConversation,
  messageLimit: number,
): AgentConversation {
  return {
    ...conversation,
    messages: conversation.messages.slice(-messageLimit).map((item) => ({
      id: item.id,
      role: item.role,
      content: item.content.slice(0, MAX_STORED_MESSAGE_CHARACTERS),
      createdAt: item.createdAt,
      runId: item.runId,
      ...(item.error ? { error: true } : {}),
    })),
    ...(conversation.researchCheckpoint ? { researchCheckpoint: conversation.researchCheckpoint } : {}),
  }
}

function persistConversationCache(conversations: AgentConversation[]): void {
  const ordered = [...conversations]
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    .slice(0, MAX_SHARED_CONVERSATIONS)
  for (const messageLimit of [MAX_MESSAGES, 40, 20, 10]) {
    for (let count = ordered.length; count > 0; count -= 1) {
      try {
        localStorage.setItem(
          CONVERSATIONS_CACHE_KEY,
          JSON.stringify(ordered.slice(0, count).map((item) => compactConversation(item, messageLimit))),
        )
        return
      } catch {
        // Reduce retained history until the newest local conversations fit the browser quota.
      }
    }
  }
  try { localStorage.removeItem(CONVERSATIONS_CACHE_KEY) } catch { /* ignore */ }
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
    MAX_LOCAL_CONTEXT_CHARACTERS - (checkpoint ? JSON.stringify(checkpoint).length : 0),
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
  const [history, setHistory] = useState<ConversationHistoryState>(loadCachedHistory)
  const [run, setRun] = useState<PublishedHarnessRun | null>(null)
  const [toolActivities, setToolActivities] = useState<AgentToolActivity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const runAccessTokenRef = useRef("")
  const streamRef = useRef<AbortController | null>(null)
  const finalizedRunIdsRef = useRef(new Set<string>())
  const cachedHistoryRef = useRef(history.conversations)
  const sharedHistoryReadyRef = useRef(false)
  const sharedFingerprintsRef = useRef(new Map<string, string>())

  const conversation = history.conversations.find((item) => item.id === history.selectedId) ?? null

  const acceptError = useCallback((reason: unknown) => {
    setError(message(reason))
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
    persistConversationCache(history.conversations)
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
        for (const cached of cachedHistoryRef.current) {
          try {
            const savedValue = await upsertSharedAgentConversation(cached)
            const saved = storedConversation(savedValue)
            if (saved) {
              sharedFingerprintsRef.current.set(saved.id, conversationFingerprint(saved))
            }
          } catch {
            // A malformed legacy cache entry must not block loading server history.
          }
        }
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

  const finalizeRun = useCallback((completed: PublishedHarnessRun, conversationId: string) => {
    if (finalizedRunIdsRef.current.has(completed.id)) return
    finalizedRunIdsRef.current.add(completed.id)
    const createdAt = completed.completedAt ?? new Date().toISOString()
    const artifacts = workspaceOutputArtifacts(completed)
    const checkpoint = checkpointFromRun(completed)
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
    setRun(completed)
    onCompleted?.()
  }, [onCompleted])

  const selectConversation = useCallback((conversationId: string) => {
    setHistory((current) => current.conversations.some((item) => item.id === conversationId)
      ? { ...current, selectedId: conversationId }
      : current)
    setRun(null)
    setToolActivities([])
    setError("")
    runAccessTokenRef.current = ""
  }, [])

  const newConversation = useCallback(() => {
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
        setRun(await answerRunInteraction(
          run.id,
          run.pendingInteraction.id,
          userMessage,
          runAccessTokenRef.current,
        ))
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
          localConversationHistory: conversationContext(conversation),
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
            status: event.status,
            ...(event.type === "run.interaction_requested" && event.interaction
              ? { pendingInteraction: event.interaction }
              : {}),
            ...(event.type === "run.interaction_resolved" ? { pendingInteraction: undefined } : {}),
          } : current)
          if (!isTerminalRun(event)) return
          const completed = await readRun(result.run.id, result.accessToken)
          finalizeRun(completed, conversationId)
          controller.abort()
        },
      }).catch((reason) => {
        if (!controller.signal.aborted) acceptError(reason)
      })
      return true
    } catch (reason) {
      acceptError(reason)
      return false
    }
  }, [acceptError, conversation, finalizeRun, manifest, responseActive, run, status?.available])

  const cancel = useCallback(async () => {
    if (!runActive || !run || !runAccessTokenRef.current || !conversation) return
    setError("")
    try {
      const cancelled = await cancelRun(run.id, runAccessTokenRef.current)
      streamRef.current?.abort()
      finalizeRun(cancelled, conversation.id)
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
