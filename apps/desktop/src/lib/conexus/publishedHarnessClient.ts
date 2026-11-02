import { getApiBase } from "@/lib/api"

import type {
  AgentConversation,
  AgentSessionSnapshot,
  ConexusStatus,
  HostedHarnessExposure,
  HostedHarnessManifest,
  PublishedHarnessRun,
  PublishedHarnessRunEvent,
  PublishedHarnessWorkspaceSnapshot,
} from "./types"
import { ConexusClientError, readWithRecovery, resumeRunEvents, submitRunOnce } from "./runConnection"
export { ConexusClientError } from "./runConnection"

function baseUrl(): string {
  return `${getApiBase()}/conexus`
}

async function responseError(response: Response): Promise<ConexusClientError> {
  const payload = await response.json().catch(() => null) as Record<string, unknown> | null
  const detail = payload?.detail
  const nested = payload?.error
  const message = typeof detail === "string"
    ? detail
    : nested && typeof nested === "object" && typeof (nested as Record<string, unknown>).message === "string"
      ? String((nested as Record<string, unknown>).message)
      : typeof payload?.message === "string"
        ? payload.message
        : response.statusText || `Conexus request failed (${response.status})`
  const code = payload?.code ?? (nested as Record<string, unknown> | null | undefined)?.code
  return new ConexusClientError(message, response.status, typeof code === "string" ? code : undefined)
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const request = async () => {
    const response = await fetch(`${baseUrl()}${path}`, init)
    if (!response.ok) throw await responseError(response)
    return response.json() as Promise<T>
  }
  return !init?.method || init.method === "GET"
    ? readWithRecovery(request, init?.signal ?? undefined) : request()
}

export function readConexusStatus(signal?: AbortSignal): Promise<ConexusStatus> {
  return json<ConexusStatus>("/status", { signal })
}

export function readManifest(
  signal?: AbortSignal,
): Promise<HostedHarnessManifest> {
  return json<HostedHarnessManifest>("/manifest", {
    headers: { Accept: "application/json" },
    signal,
  })
}

export function selectRunExposure(manifest: HostedHarnessManifest): HostedHarnessExposure {
  if (!Array.isArray(manifest.exposures)) {
    throw new Error("Hosted AlphaLab Harness manifest has no exposures.")
  }
  const exposureId = manifest.defaultExposureId?.trim()
  if (!exposureId) {
    throw new Error("Hosted AlphaLab Harness manifest has no default exposure.")
  }
  const exposure = manifest.exposures.find((candidate) => candidate.id === exposureId)
  if (!exposure) {
    throw new Error(`Hosted AlphaLab Harness default exposure was not found: ${exposureId}`)
  }
  if (!Array.isArray(exposure.surfaces) || !exposure.surfaces.includes("api")) {
    throw new Error(`Hosted AlphaLab Harness exposure is not available through the API: ${exposureId}`)
  }
  if (exposure.nodeType !== "agent") {
    throw new Error(`Hosted AlphaLab Harness default exposure is not an Agent: ${exposureId}`)
  }
  return exposure
}

export function buildRunInput(
  message: string,
  context: Record<string, unknown>,
): { request: string } {
  const userRequest = message.trim()
  if (!userRequest) throw new Error("Hosted AlphaLab Agent request is empty.")
  const request = JSON.stringify({
    version: 1,
    userRequest,
    workspaceContext: context,
  })
  if (request.length > 100_000) {
    throw new Error("Hosted AlphaLab Agent request exceeds 100000 characters.")
  }
  return { request }
}

export async function createRun(params: {
  exposureId: string
  input: Record<string, unknown>
  conversation: { id: string; title: string; createdAt: string; messageId: string; message: string }
}): Promise<{ run: PublishedHarnessRun; conversation: AgentConversation }> {
  // A response can be lost after acceptance. Recover its exact persisted message;
  // never repeat a model-billed POST after an ambiguous network failure.
  return submitRunOnce(() => json("/runs", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(params),
  }), () => readAgentSession(), params.conversation)
}

export async function readSharedAgentConversations(
  signal?: AbortSignal,
): Promise<AgentConversation[]> {
  const payload = await json<{ conversations: AgentConversation[] }>("/conversations", {
    headers: { Accept: "application/json" },
    signal,
  })
  return payload.conversations
}

export function readAgentSession(signal?: AbortSignal): Promise<AgentSessionSnapshot> {
  return json("/conversations", { headers: { Accept: "application/json" }, cache: "no-store", signal })
}

export async function upsertSharedAgentConversation(
  conversation: AgentConversation,
): Promise<AgentConversation> {
  const payload = await json<{ conversation: AgentConversation }>(
    `/conversations/${encodeURIComponent(conversation.id)}`,
    {
      method: "PUT",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(conversation),
    },
  )
  return payload.conversation
}

export async function readWorkspace(signal?: AbortSignal): Promise<PublishedHarnessWorkspaceSnapshot> {
  const payload = await json<{ workspace: PublishedHarnessWorkspaceSnapshot }>("/workspace", {
    headers: { Accept: "application/json" },
    signal,
  })
  return payload.workspace
}

export async function readRun(
  runId: string,
  signal?: AbortSignal,
): Promise<PublishedHarnessRun> {
  const payload = await json<{ run: PublishedHarnessRun }>(`/runs/${encodeURIComponent(runId)}`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  })
  return payload.run
}

export async function cancelRun(runId: string): Promise<PublishedHarnessRun> {
  const payload = await json<{ run: PublishedHarnessRun }>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    headers: { Accept: "application/json" },
  })
  return payload.run
}

export async function answerRunInteraction(
  runId: string,
  interactionId: string,
  answer: string,
): Promise<PublishedHarnessRun> {
  const payload = await json<{ run: PublishedHarnessRun }>(
    `/runs/${encodeURIComponent(runId)}/interactions/${encodeURIComponent(interactionId)}/answer`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ answer }),
    },
  )
  return payload.run
}

export async function streamRunEvents(params: {
  runId: string
  signal: AbortSignal
  onEvent: (event: PublishedHarnessRunEvent) => void | Promise<void>
}): Promise<void> {
  return resumeRunEvents({
    ...params,
    open: async (lastSequence) => {
      const response = await fetch(`${baseUrl()}/runs/${encodeURIComponent(params.runId)}/events`, {
        headers: { Accept: "text/event-stream", ...(lastSequence ? { "Last-Event-ID": String(lastSequence) } : {}) },
        cache: "no-store",
        signal: params.signal,
      })
      if (!response.ok) throw await responseError(response)
      if (!response.body) throw new TypeError("Conexus run event stream is unavailable.")
      return response.body
    },
  })
}
