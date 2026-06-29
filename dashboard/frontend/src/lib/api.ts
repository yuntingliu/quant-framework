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

function noConnectionStatus() {
  return {
    connected: false,
    mode: "monitor",
    account_id: null,
    broker: null,
    broker_label: "adapter disabled",
    account_currency: null,
    readonly: true,
    supports_real_orders: false,
    supports_paper_orders: true,
    can_submit: false,
    session_state: "disabled",
  }
}

function bareboneFallback<T>(url: string, method: string): T | undefined {
  const path = url.split("?")[0]
  if (path === "/trading/status") return noConnectionStatus() as T
  if (path === "/trading/asset") return { cash: 0, total_asset: 0, market_value: 0 } as T
  if (path === "/data/status") {
    return {
      qmt: { latest_date: null, needs_update: false, provider_pending: true, live_connected: false },
      rq: { latest_date: null, needs_update: false, provider_pending: true },
      local: { latest_date: null, needs_update: false },
      external: { connected: false, ready: false },
    } as T
  }
  if (path === "/data/rq/connection") return { connected: false, mode: "disabled" } as T
  if (path === "/agent/config") {
    return { llm: { configured: false, available: false, provider: "none", model: null, mode: "barebone" } } as T
  }
  if (path === "/market/overview/index-board") {
    return {
      as_of: null,
      ashare: { source: "unavailable", items: [] },
      us: { source: "unavailable", items: [] },
    } as T
  }
  if (path === "/market/overview/breadth") return { as_of: null, up: 0, down: 0, flat: 0, top_gainers: [], top_losers: [] } as T
  if (path === "/market/overview/sectors") return { as_of: null, sectors: [] } as T
  if (path === "/market/overview/money-flow") return { as_of: null, items: [], source: "unavailable" } as T
  if (path.startsWith("/market/")) return {} as T
  if (path.startsWith("/strategy-hub/overview")) return { strategies: [], count: 0, generated_at: new Date().toISOString() } as T
  if (path.startsWith("/strategy-hub/")) return {} as T
  if (path.startsWith("/backtest-jobs")) return { id: "disabled", status: "disabled", message: "Backtest job service is not bundled in barebone." } as T
  if (path === "/backtest/run" && method === "POST") {
    return { returns: [], metrics: {}, trades: [], weights: [] } as T
  }
  if (path.startsWith("/research/")) return { status: "disabled", items: [], message: "Research product service is not bundled in barebone." } as T
  if (path.startsWith("/optimizer/")) return {} as T
  if (path.startsWith("/risk/")) return {} as T
  if (path.startsWith("/alerts/")) return { rules: [], events: [] } as T
  if (path.startsWith("/workflows")) return { workflows: [], runs: [] } as T
  if (path.startsWith("/data/sync/")) return { id: "disabled", status: "disabled", message: "External sync adapters are not bundled." } as T
  if (path.startsWith("/data/external") || path.startsWith("/trading/external")) {
    return { connected: false, ready: false, status: "disabled", message: "External adapter is not bundled in barebone." } as T
  }
  if (path.startsWith("/trading/")) {
    return method === "GET" ? ([] as T) : ({ status: "disabled", message: "Live trading adapters are not bundled." } as T)
  }
  return undefined
}

async function fetchJSON<T>(url: string, options?: ApiRequestInit): Promise<T> {
  const { timeoutMs, signal, ...fetchOptions } = options ?? {}
  const method = String(fetchOptions.method ?? "GET").toUpperCase()
  const requestSignal = timeoutSignal(signal ?? undefined, timeoutMs)
  try {
    const response = await fetch(`${getApiBase()}${url}`, {
      headers: { "Content-Type": "application/json" },
      ...fetchOptions,
      signal: requestSignal.signal,
    }).finally(requestSignal.cleanup)
    if (!response.ok) {
      const fallback = bareboneFallback<T>(url, method)
      if (fallback !== undefined && (response.status === 404 || response.status === 405 || response.status === 500)) {
        return fallback
      }
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
    const fallback = bareboneFallback<T>(url, method)
    if (fallback !== undefined) return fallback
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
