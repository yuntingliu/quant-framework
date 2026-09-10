import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import {
  answerRunInteraction,
  buildRunInput,
  cancelRun,
  createRun,
  readConexusStatus,
  readManifest,
  readRun,
  readAgentSession,
  selectRunExposure,
  streamRunEvents,
} from "@/lib/conexus/publishedHarnessClient"
import {
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
} from "@/lib/conexus/types"

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

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function boundedText(value: unknown, limit: number): string | undefined {
  return typeof value === "string" ? value.slice(0, limit) : undefined
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

export function usePublishedAgent({ onCompleted }: Options = {}) {
  const [status, setStatus] = useState<ConexusStatus | null>(null)
  const [manifest, setManifest] = useState<HostedHarnessManifest | null>(null)
  const [history, setHistory] = useState<ConversationHistoryState>({ conversations: [], selectedId: null })
  const [run, setRun] = useState<PublishedHarnessRun | null>(null)
  const [runConversationId, setRunConversationId] = useState<string | null>(null)
  const [toolActivities, setToolActivities] = useState<AgentToolActivity[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const runRef = useRef<PublishedHarnessRun | null>(null)
  const submittingRef = useRef(false)
  const historyReadyRef = useRef(false)
  const epochRef = useRef(0)
  const finalizedRunIdsRef = useRef(new Set<string>())
  const onCompletedRef = useRef(onCompleted)
  useEffect(() => { onCompletedRef.current = onCompleted }, [onCompleted])

  const conversation = history.conversations.find((item) => item.id === history.selectedId) ?? null
  const acceptError = useCallback((reason: unknown) => setError(message(reason)), [])
  const adoptRun = useCallback((next: PublishedHarnessRun, conversationId: string) => {
    const current = runRef.current
    const merged = current?.id === next.id ? mergeRunSnapshot(current, next) : next
    runRef.current = merged
    setRun(merged)
    setRunConversationId(conversationId)
  }, [])

  const refreshSharedHistory = useCallback(async (
    signal?: AbortSignal, selectNewest = false, restoreRun = true,
  ) => {
    const epoch = epochRef.current
    const session = await readAgentSession(signal)
    if (signal?.aborted || epoch !== epochRef.current) return
    const shared = session.conversations.map(storedConversation)
      .filter((item): item is AgentConversation => item !== null)
    historyReadyRef.current = true
    const active = session.runs.find((item) => !isTerminalRun(item.run))
    const recovered = restoreRun && !submittingRef.current
      ? active ?? (selectNewest ? session.runs.find((item) => item.conversationId === shared[0]?.id) : undefined)
      : undefined
    if (recovered) adoptRun(recovered.run, recovered.conversationId)
    setHistory((current) => {
      const conversations = mergeConversationLists(current.conversations, shared)
      const selectedId = recovered?.conversationId
        ?? (current.selectedId && conversations.some((item) => item.id === current.selectedId)
          ? current.selectedId : selectNewest ? conversations[0]?.id ?? null : null)
      return { conversations, selectedId }
    })
  }, [adoptRun])

  const reloadStatus = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError("")
    try {
      const [nextStatus] = await Promise.all([
        readConexusStatus(signal), refreshSharedHistory(signal, true),
      ])
      if (signal?.aborted) return
      setStatus(nextStatus)
      if (!nextStatus.available) {
        setManifest(null)
        return
      }
      const nextManifest = await readManifest(signal)
      if (signal?.aborted) return
      if (nextStatus.mode === "published_harness" && (nextManifest.identityPolicy !== "enterprise" || nextManifest.billingPolicy !== "publisher")) {
        throw new Error("Hosted AlphaLab Agent must use enterprise service identity with publisher billing.")
      }
      selectRunExposure(nextManifest)
      setManifest(nextManifest)
    } catch (reason) {
      if (!signal?.aborted) acceptError(reason)
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [acceptError, refreshSharedHistory])

  useEffect(() => {
    const controller = new AbortController()
    void reloadStatus(controller.signal)
    const timer = globalThis.setInterval(() => {
      void refreshSharedHistory(controller.signal).catch((reason) => {
        if (!controller.signal.aborted) acceptError(reason)
      })
    }, 10_000)
    return () => {
      controller.abort()
      globalThis.clearInterval(timer)
    }
  }, [acceptError, refreshSharedHistory, reloadStatus])

  const finalizeRun = useCallback(async (
    completed: PublishedHarnessRun, conversationId: string, signal?: AbortSignal,
  ) => {
    if (!isTerminalRun(completed) || finalizedRunIdsRef.current.has(completed.id)) return
    // The Run read/cancel API commits its terminal reply before returning. Only read
    // shared history here; browser unmounts and competing tabs cannot lose or duplicate it.
    await refreshSharedHistory(signal, false, false)
    if (signal?.aborted || runRef.current?.id !== completed.id) return
    finalizedRunIdsRef.current.add(completed.id)
    adoptRun(completed, conversationId)
    setToolActivities((current) => current.map((activity): AgentToolActivity => activity.state === "running"
      ? { ...activity, state: "completed", ...(completed.status === "completed" ? {} : { success: false }) }
      : activity))
    setError("")
    onCompletedRef.current?.()
  }, [adoptRun, refreshSharedHistory])

  useEffect(() => {
    const runId = run?.id
    const conversationId = runConversationId
    if (!runId || !conversationId || finalizedRunIdsRef.current.has(runId)) return
    const controller = new AbortController()
    let timer: ReturnType<typeof globalThis.setTimeout> | undefined
    let reconciling = false
    const reconcile = async () => {
      if (controller.signal.aborted || reconciling) return
      reconciling = true
      if (timer !== undefined) globalThis.clearTimeout(timer)
      try {
        const latest = await readRun(runId, controller.signal)
        if (controller.signal.aborted || runRef.current?.id !== runId) return
        adoptRun(latest, conversationId)
        if (isTerminalRun(latest)) {
          await finalizeRun(latest, conversationId, controller.signal)
          controller.abort()
          return
        }
      } catch (reason) {
        if (!controller.signal.aborted) acceptError(reason)
      } finally {
        reconciling = false
        if (!controller.signal.aborted) timer = globalThis.setTimeout(() => { void reconcile() }, 2_000)
      }
    }
    void reconcile()
    // Subscribe on mount/recovery as well as new submission, so tool activity and
    // pending questions survive a refresh or an unmounted Agent rail.
    void streamRunEvents({
      runId,
      signal: controller.signal,
      onEvent: (event) => {
        if (controller.signal.aborted || runRef.current?.id !== runId || event.runId !== runId) return
        if (event.type === "run.message" && event.message?.role === "assistant") {
          const activities = (event.message.tool_calls ?? []).map((call): AgentToolActivity => ({
            callId: call.id, name: call.function.name, ownerNodeId: event.ownerNodeId ?? "",
            state: "running", at: event.at,
          }))
          if (activities.length) setToolActivities((current) => upsertToolActivities(current, activities))
        } else if (event.type === "run.message" && event.message?.role === "tool" && event.message.tool_call_id && event.message.name) {
          const activity: AgentToolActivity = {
            callId: event.message.tool_call_id, name: event.message.name, ownerNodeId: event.ownerNodeId ?? "",
            state: "completed", at: event.at, ...toolResult(event.message.content),
          }
          setToolActivities((current) => upsertToolActivities(current, [activity]))
        }
        // Historical SSE events can replay after a snapshot with a pending question.
        // Reconcile interactions from the authoritative snapshot instead of clearing
        // newer questions in response to an older event.
        if (isTerminalRun(event) || event.type.startsWith("run.interaction_")) void reconcile()
      },
    }).catch((reason) => { if (!controller.signal.aborted) acceptError(reason) })
    return () => {
      controller.abort()
      if (timer !== undefined) globalThis.clearTimeout(timer)
    }
  }, [acceptError, adoptRun, finalizeRun, run?.id, runConversationId])

  const selectConversation = useCallback((conversationId: string) => {
    if (submittingRef.current || (runRef.current && !isTerminalRun(runRef.current))) return
    epochRef.current += 1
    setHistory((current) => current.conversations.some((item) => item.id === conversationId)
      ? { ...current, selectedId: conversationId } : current)
    runRef.current = null
    setRun(null)
    setRunConversationId(null)
    setToolActivities([])
    setError("")
  }, [])

  const newConversation = useCallback(() => {
    if (submittingRef.current || (runRef.current && !isTerminalRun(runRef.current))) return
    epochRef.current += 1
    setHistory((current) => ({ ...current, selectedId: null }))
    runRef.current = null
    setRun(null)
    setRunConversationId(null)
    setToolActivities([])
    setError("")
  }, [])

  const runActive = run?.status === "queued" || run?.status === "running"
  const responseActive = runActive || submitting
  const send = useCallback(async (rawMessage: string, context: Record<string, unknown>) => {
    const userMessage = rawMessage.trim()
    if (!userMessage || !status?.available || !manifest || !historyReadyRef.current || submittingRef.current) return false
    setError("")
    const current = runRef.current
    if (current?.pendingInteraction && !isTerminalRun(current)) {
      try {
        const answered = await answerRunInteraction(current.id, current.pendingInteraction.id, userMessage)
        adoptRun(answered, runConversationId!)
        return true
      } catch (reason) {
        acceptError(reason)
        return false
      }
    }
    if (current && !isTerminalRun(current)) return false
    submittingRef.current = true
    setSubmitting(true)
    epochRef.current += 1
    setToolActivities([])
    try {
      const result = await createRun({
        exposureId: selectRunExposure(manifest).id,
        input: buildRunInput(userMessage, { ...context, conversationHistory: conversationContext(conversation) }),
        conversation: {
          id: conversation?.id ?? id(), title: conversation?.title ?? conversationTitle(userMessage),
          createdAt: conversation?.createdAt ?? new Date().toISOString(), messageId: id(), message: userMessage,
        },
      })
      epochRef.current += 1
      setHistory((previous) => ({
        conversations: mergeConversationLists(previous.conversations, [result.conversation]),
        selectedId: result.conversation.id,
      }))
      adoptRun(result.run, result.conversation.id)
      return true
    } catch (reason) {
      acceptError(reason)
      return false
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }, [acceptError, adoptRun, conversation, manifest, runConversationId, status?.available])

  const cancel = useCallback(async () => {
    const current = runRef.current
    if (!current || isTerminalRun(current) || !runConversationId) return
    setError("")
    try {
      const cancelled = await cancelRun(current.id)
      adoptRun(cancelled, runConversationId)
      await finalizeRun(cancelled, runConversationId)
    } catch (reason) {
      acceptError(reason)
    }
  }, [acceptError, adoptRun, finalizeRun, runConversationId])

  return useMemo(() => ({
    status, manifest, conversations: history.conversations, conversation, run, toolActivities,
    loading, error, responseActive, reloadStatus, selectConversation, newConversation, send, cancel,
  }), [cancel, conversation, error, history.conversations, loading, manifest, newConversation, reloadStatus, responseActive, run, selectConversation, send, status, toolActivities])
}
