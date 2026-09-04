import { getApiBase } from "@/lib/api"

import type {
  AgentConversation,
  ConexusStatus,
  HostedHarnessExposure,
  HostedHarnessManifest,
  PublishedHarnessRun,
  PublishedHarnessRunEvent,
  PublishedHarnessWorkspaceSnapshot,
} from "./types"
import { parseRunEventBlock } from "./runState"

export class ConexusClientError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
    this.name = "ConexusClientError"
  }
}

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
  return new ConexusClientError(message, response.status)
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl()}${path}`, init)
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<T>
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
}): Promise<{ run: PublishedHarnessRun; accessToken: string }> {
  return json("/runs", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(params),
  })
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
  accessToken: string,
  signal?: AbortSignal,
): Promise<PublishedHarnessRun> {
  const payload = await json<{ run: PublishedHarnessRun }>(`/runs/${encodeURIComponent(runId)}`, {
    headers: { Accept: "application/json", Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
    signal,
  })
  return payload.run
}

export async function cancelRun(runId: string, accessToken: string): Promise<PublishedHarnessRun> {
  const payload = await json<{ run: PublishedHarnessRun }>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    headers: { Accept: "application/json", Authorization: `Bearer ${accessToken}` },
  })
  return payload.run
}

export async function answerRunInteraction(
  runId: string,
  interactionId: string,
  answer: string,
  accessToken: string,
): Promise<PublishedHarnessRun> {
  const payload = await json<{ run: PublishedHarnessRun }>(
    `/runs/${encodeURIComponent(runId)}/interactions/${encodeURIComponent(interactionId)}/answer`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ answer }),
    },
  )
  return payload.run
}

export async function streamRunEvents(params: {
  runId: string
  accessToken: string
  signal: AbortSignal
  onEvent: (event: PublishedHarnessRunEvent) => void | Promise<void>
}): Promise<void> {
  const response = await fetch(`${baseUrl()}/runs/${encodeURIComponent(params.runId)}/events`, {
    headers: { Accept: "text/event-stream", Authorization: `Bearer ${params.accessToken}` },
    cache: "no-store",
    signal: params.signal,
  })
  if (!response.ok) throw await responseError(response)
  if (!response.body) throw new Error("Conexus run event stream is unavailable.")

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  while (!params.signal.aborted) {
    const chunk = await reader.read()
    if (chunk.done) {
      buffer = `${buffer}${decoder.decode()}`.replace(/\r\n/g, "\n")
      const trailing = parseRunEventBlock(buffer.trim())
      if (trailing) await params.onEvent(trailing)
      break
    }
    buffer = `${buffer}${decoder.decode(chunk.value, { stream: true })}`.replace(/\r\n/g, "\n")
    let boundary = buffer.indexOf("\n\n")
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const event = parseRunEventBlock(block)
      if (event) await params.onEvent(event)
      boundary = buffer.indexOf("\n\n")
    }
  }
}
