// In Electron production, no Vite proxy; the main process passes the sidecar URL.
interface ElectronApiBridge {
  getApiOrigin?: () => string
  getApiBase?: () => string
}

export interface StrategyTemplate {
  id: string
  name: string
  description: string
  path: string
  factors: string[]
  warnings: string[]
}

export interface StrategyTemplateDetail extends StrategyTemplate {
  yaml: string
}

export interface BacktestRecord {
  id: string
  strategy_id: string
  start_date: string
  end_date: string
  total_return: number | ''
  annual_return: number | ''
  annual_vol: number | ''
  sharpe: number | ''
  max_drawdown: number | ''
  run_at: string
}

export interface BacktestRunResult {
  id: string
  strategy_id: string
  metrics: Record<string, number>
  returns: { date: string; value: number }[]
  weights_count: number
}

export interface DataManifest {
  sample_start: string
  cutoff_date: string
  symbol_count: number
  rows: Record<string, number>
  realtime: string
  caveat: string
}

export interface ProviderStatus {
  providers: Record<string, string[]>
  latest_date: string | null
  sample_start: string | null
  symbol_count: number
  realtime: { status: string; source: string | null }
  datasets: Record<string, { status: string; path: string; bytes: number; sha256?: string }>
}

export interface MarketBar {
  date: string
  symbol: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
}

export interface FactorReturnsPayload {
  names: string[]
  rows: Array<Record<string, string | number>>
}

export interface SignalResult {
  id: string | null
  strategy_id: string
  signal_date: string
  targets: Record<string, number>
  diagnostics: Record<string, string | number | string[]>
}

export interface PaperOrder {
  id: string
  symbol: string
  action: string
  quantity: number
  fill_price: number | ''
  commission: number | ''
  status: string
  submitted_at: string
}

function electronBridge(): ElectronApiBridge | undefined {
  if (typeof window === "undefined") return undefined
  return (window as unknown as { api?: ElectronApiBridge }).api
}

function rendererConfigParam(name: string): string | undefined {
  if (typeof window === "undefined") return undefined
  const value = new URLSearchParams(window.location.search).get(name)
  return value?.trim() || undefined
}

function hasRendererApiConfig(): boolean {
  if (typeof window === "undefined") return false
  const params = new URLSearchParams(window.location.search)
  return params.has("alphalabApiOrigin") || params.has("alphalabApiBase")
}

function isElectronRuntime(): boolean {
  if (typeof window === "undefined") return false
  return Boolean(electronBridge()) || hasRendererApiConfig()
}

export function getApiOrigin(): string {
  if (!isElectronRuntime()) return ""
  return rendererConfigParam("alphalabApiOrigin")
    ?? electronBridge()?.getApiOrigin?.()
    ?? "http://127.0.0.1:8000"
}

export function getApiBase(): string {
  if (!isElectronRuntime()) return "/api"
  return rendererConfigParam("alphalabApiBase")
    ?? electronBridge()?.getApiBase?.()
    ?? `${getApiOrigin()}/api`
}

export function buildParams(params: Record<string, string | undefined>): string {
  const entries = Object.entries(params).filter(
    (entry): entry is [string, string] => entry[1] !== undefined && entry[1] !== "",
  )
  if (entries.length === 0) return ""
  return "?" + new URLSearchParams(entries).toString()
}

type ApiRequestInit = RequestInit & {
  timeoutMs?: number
}

function timeoutSignal(signal: AbortSignal | undefined, timeoutMs: number | undefined): { signal?: AbortSignal; cleanup: () => void } {
  if (!timeoutMs) return { signal, cleanup: () => undefined }
  const controller = new AbortController()
  const abort = () => controller.abort()
  if (signal?.aborted) {
    abort()
  } else if (signal) {
    signal.addEventListener("abort", abort, { once: true })
  }
  const timeoutId = window.setTimeout(abort, timeoutMs)
  return {
    signal: controller.signal,
    cleanup: () => {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId)
      signal?.removeEventListener("abort", abort)
    },
  }
}

async function fetchJSON<T>(url: string, options?: ApiRequestInit): Promise<T> {
  const { timeoutMs, signal, ...fetchOptions } = options ?? {}
  const requestSignal = timeoutSignal(signal ?? undefined, timeoutMs)
  try {
    const response = await fetch(`${getApiBase()}${url}`, {
      headers: { "Content-Type": "application/json" },
      ...fetchOptions,
      signal: requestSignal.signal,
    }).finally(requestSignal.cleanup)
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }))
      const detail = error.detail ?? error.message
      if (typeof detail === "string") {
        throw new Error(detail || `API Error: ${response.status}`)
      }
      if (detail && typeof detail === "object") {
        const message = typeof detail.message === "string"
          ? detail.message
          : `API Error: ${response.status}`
        const suffix = detail.error ? `: ${detail.error}` : ""
        throw new Error(`${message}${suffix}`)
      }
      throw new Error(`API Error: ${response.status}`)
    }
    return response.json() as Promise<T>
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(timeoutMs ? `API request timed out after ${timeoutMs}ms` : "API request was cancelled")
    }
    throw error
  }
}

export const api = {
  get: <T>(url: string, options?: ApiRequestInit) => fetchJSON<T>(url, options),
  post: <T>(url: string, body: unknown) =>
    fetchJSON<T>(url, { method: "POST", body: JSON.stringify(body) }),
  put: <T>(url: string, body: unknown) =>
    fetchJSON<T>(url, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(url: string, body: unknown) =>
    fetchJSON<T>(url, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(url: string) => fetchJSON<T>(url, { method: "DELETE" }),
}

export async function apiGet<T>(path: string): Promise<T> {
  return api.get<T>(path)
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  return api.post<T>(path, body)
}
