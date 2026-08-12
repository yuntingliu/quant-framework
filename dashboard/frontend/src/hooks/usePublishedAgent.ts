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
  selectRunExposure,
  streamRunEvents,
} from "@/lib/conexus/publishedHarnessClient"
import type {
  AgentToolActivity,
  ConexusStatus,
  HostedHarnessManifest,
  LocalAgentConversation,
  LocalAgentConversationMessage,
  PublishedHarnessRun,
} from "@/lib/conexus/types"

interface Options {
  onCompleted?: () => void
}

interface LocalHistoryState {
  conversations: LocalAgentConversation[]
  selectedId: string | null
}

const LOCAL_CONVERSATIONS_KEY = "alphalab.anonymous-agent-conversations.v1"
const MAX_LOCAL_CONVERSATIONS = 20
const MAX_LOCAL_MESSAGES = 80
const MAX_STORED_MESSAGE_CHARACTERS = 40_000
const MAX_CONTEXT_MESSAGES = 16
const MAX_CONTEXT_MESSAGE_CHARACTERS = 8_000

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function id(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function localMessage(value: unknown): LocalAgentConversationMessage | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const candidate = value as Record<string, unknown>
  if (
    typeof candidate.id !== "string"
    || (candidate.role !== "user" && candidate.role !== "assistant")
    || typeof candidate.content !== "string"
    || typeof candidate.createdAt !== "string"
    || typeof candidate.runId !== "string"
  ) return null
  return {
    id: candidate.id,
    role: candidate.role,
    content: candidate.content.slice(0, MAX_STORED_MESSAGE_CHARACTERS),
    createdAt: candidate.createdAt,
    runId: candidate.runId,
    ...(candidate.error === true ? { error: true } : {}),
  }
}

function localConversation(value: unknown): LocalAgentConversation | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const candidate = value as Record<string, unknown>
  if (
    typeof candidate.id !== "string"
    || typeof candidate.title !== "string"
    || typeof candidate.createdAt !== "string"
    || typeof candidate.updatedAt !== "string"
    || !Array.isArray(candidate.messages)
  ) return null
  return {
    id: candidate.id,
    title: candidate.title.slice(0, 120),
    createdAt: candidate.createdAt,
    updatedAt: candidate.updatedAt,
    messages: candidate.messages
      .map(localMessage)
      .filter((item): item is LocalAgentConversationMessage => item !== null)
      .slice(-MAX_LOCAL_MESSAGES),
  }
}

function loadLocalHistory(): LocalHistoryState {
  try {
    const raw = localStorage.getItem(LOCAL_CONVERSATIONS_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    const conversations = Array.isArray(parsed)
      ? parsed
          .map(localConversation)
          .filter((item): item is LocalAgentConversation => item !== null)
          .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
          .slice(0, MAX_LOCAL_CONVERSATIONS)
      : []
    return { conversations, selectedId: conversations[0]?.id ?? null }
  } catch {
    return { conversations: [], selectedId: null }
  }
}

function compactConversation(
  conversation: LocalAgentConversation,
  messageLimit: number,
): LocalAgentConversation {
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
  }
}

function persistLocalConversations(conversations: LocalAgentConversation[]): void {
  const ordered = [...conversations]
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    .slice(0, MAX_LOCAL_CONVERSATIONS)
  for (const messageLimit of [MAX_LOCAL_MESSAGES, 40, 20, 10]) {
    for (let count = ordered.length; count > 0; count -= 1) {
      try {
        localStorage.setItem(
          LOCAL_CONVERSATIONS_KEY,
          JSON.stringify(ordered.slice(0, count).map((item) => compactConversation(item, messageLimit))),
        )
        return
      } catch {
        // Reduce retained history until the newest local conversations fit the browser quota.
      }
    }
  }
  try { localStorage.removeItem(LOCAL_CONVERSATIONS_KEY) } catch { /* ignore */ }
}

function conversationTitle(content: string): string {
  const normalized = content.replace(/\s+/g, " ").trim()
  return normalized.length > 60 ? `${normalized.slice(0, 57)}...` : normalized || "New conversation"
}

function withConversation(
  conversations: LocalAgentConversation[],
  conversation: LocalAgentConversation,
): LocalAgentConversation[] {
  return [
    conversation,
    ...conversations.filter((item) => item.id !== conversation.id),
  ].slice(0, MAX_LOCAL_CONVERSATIONS)
}

function localConversationContext(conversation: LocalAgentConversation | null): Record<string, unknown> {
  const messages = (conversation?.messages ?? [])
    .filter((item) => !item.error)
    .slice(-MAX_CONTEXT_MESSAGES)
    .map((item) => ({
      role: item.role,
      content: item.content.slice(0, MAX_CONTEXT_MESSAGE_CHARACTERS),
    }))
  return {
    version: 1,
    storage: "browser-local",
    messages,
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
  const [history, setHistory] = useState<LocalHistoryState>(loadLocalHistory)
  const [run, setRun] = useState<PublishedHarnessRun | null>(null)
  const [toolActivities, setToolActivities] = useState<AgentToolActivity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const runAccessTokenRef = useRef("")
  const streamRef = useRef<AbortController | null>(null)
  const finalizedRunIdsRef = useRef(new Set<string>())

  const conversation = history.conversations.find((item) => item.id === history.selectedId) ?? null

  useEffect(() => {
    persistLocalConversations(history.conversations)
  }, [history.conversations])

  const acceptError = useCallback((reason: unknown) => {
    setError(message(reason))
  }, [])

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
      if (nextManifest.accessPolicy !== "anonymous" || nextManifest.billingPolicy !== "publisher") {
        throw new Error("Hosted AlphaLab Agent must use anonymous access with publisher billing.")
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
    const assistantMessage: LocalAgentConversationMessage = {
      id: id(),
      role: "assistant",
      content: terminalContent(completed),
      createdAt,
      runId: completed.id,
      ...(completed.artifacts?.length ? { artifacts: completed.artifacts } : {}),
      ...(["failed", "cancelled"].includes(completed.status) ? { error: true } : {}),
    }
    setHistory((current) => {
      const selected = current.conversations.find((item) => item.id === conversationId)
      if (!selected) return current
      const updated = {
        ...selected,
        updatedAt: createdAt,
        messages: [...selected.messages, assistantMessage].slice(-MAX_LOCAL_MESSAGES),
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
          localConversationHistory: localConversationContext(conversation),
        }),
      })
      const now = new Date().toISOString()
      const conversationId = conversation?.id ?? id()
      const userEntry: LocalAgentConversationMessage = {
        id: id(),
        role: "user",
        content: userMessage,
        createdAt: now,
        runId: result.run.id,
      }
      setHistory((current) => {
        const existing = current.conversations.find((item) => item.id === conversationId)
        const updated: LocalAgentConversation = existing
          ? {
              ...existing,
              updatedAt: now,
              messages: [...existing.messages, userEntry].slice(-MAX_LOCAL_MESSAGES),
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
          if (event.tool && (event.type === "run.tool_started" || event.type === "run.tool_completed")) {
            setToolActivities((current) => {
              const next: AgentToolActivity = {
                ...event.tool!,
                state: event.type === "run.tool_started" ? "running" : "completed",
                at: event.at,
              }
              const existing = current.findIndex((item) => item.callId === next.callId)
              if (existing < 0) return [...current, next].slice(-12)
              const updated = [...current]
              updated[existing] = { ...updated[existing], ...next }
              return updated
            })
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
