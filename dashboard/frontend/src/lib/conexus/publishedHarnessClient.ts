import { getApiBase } from "@/lib/api"

import type {
  ConexusStatus,
  PublishedHarnessManifest,
  PublishedHarnessRun,
  PublishedHarnessRunEvent,
} from "./types"

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
): Promise<PublishedHarnessManifest> {
  return json<PublishedHarnessManifest>("/manifest", {
    headers: { Accept: "application/json" },
    signal,
  })
}

export function buildRunInput(
  manifest: PublishedHarnessManifest,
  message: string,
  context: Record<string, unknown>,
): Record<string, unknown> {
  const values = Object.fromEntries(
    manifest.inputs
      .filter((input) => input.defaultValue !== undefined)
      .map((input) => [input.key, structuredClone(input.defaultValue)]),
  )
  const requestInput = manifest.inputs.find((input) => input.key === "request")
    ?? manifest.inputs.find((input) => input.type === "text" || input.type === "string")
  if (!requestInput) throw new Error("Published AlphaLab Harness has no text request input.")
  values[requestInput.key] = message
  if (manifest.inputs.some((input) => input.key === "context")) values.context = context
  return values
}

export async function createRun(params: {
  input: Record<string, unknown>
}): Promise<{ run: PublishedHarnessRun; accessToken: string }> {
  return json("/runs", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ input: params.input }),
  })
}

export async function readRun(runId: string, accessToken: string): Promise<PublishedHarnessRun> {
  const payload = await json<{ run: PublishedHarnessRun }>(`/runs/${encodeURIComponent(runId)}`, {
    headers: { Accept: "application/json", Authorization: `Bearer ${accessToken}` },
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

export function isTerminalRun(value: PublishedHarnessRun | PublishedHarnessRunEvent): boolean {
  return ["completed", "blocked", "failed", "cancelled"].includes(value.status)
}

export async function streamRunEvents(params: {
  runId: string
  accessToken: string
  signal: AbortSignal
  onEvent: (event: PublishedHarnessRunEvent) => void | Promise<void>
}): Promise<void> {
  const response = await fetch(`${baseUrl()}/runs/${encodeURIComponent(params.runId)}/events`, {
    headers: { Accept: "text/event-stream", Authorization: `Bearer ${params.accessToken}` },
    signal: params.signal,
  })
  if (!response.ok) throw await responseError(response)
  if (!response.body) throw new Error("Conexus run event stream is unavailable.")

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  while (!params.signal.aborted) {
    const chunk = await reader.read()
    if (chunk.done) break
    buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n")
    let boundary = buffer.indexOf("\n\n")
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const data = block.split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n")
      if (data) await params.onEvent(JSON.parse(data) as PublishedHarnessRunEvent)
      boundary = buffer.indexOf("\n\n")
    }
  }
}
