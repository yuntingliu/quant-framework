// In Electron production, no Vite proxy; the main process passes the sidecar URL.
interface ElectronApiBridge {
  getApiOrigin?: () => string
  getApiBase?: () => string
}

export interface StrategyPipelineManifest {
  strategy_type: "python_pipeline"
  order: string[]
  python_stages: string[]
  stages: Array<{
    order: number
    name: string
    kind: "python"
    entrypoint: string
    runtime_function: string
    timeout_seconds: number
    contract: { input: string; output: string; hard_gate: string }
    source: string
  }>
  composed_source: string
  invariants: string[]
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
  profile: "demo" | "runtime"
}

export interface BacktestRunResult {
  id: string
  project_id: string
  strategy_id: string
  strategy_type: "sdk_v1"
  revision: number
  source_sha256: string
  validation_revision?: number
  validation_source_sha256?: string
  metrics: Record<string, number>
  returns: { date: string; value: number }[]
  weights_count: number
  execution: Record<string, unknown>
  attribution: BacktestAttribution
  provenance: ResearchProvenance
}

export interface BacktestJob {
  id: string
  status: "queued" | "running" | "succeeded" | "failed" | "interrupted"
  request: {
    project_id: string
    start_date: string
    end_date: string
    profile: "demo" | "runtime"
    revision: number
    validation_revision?: number
  }
  result: BacktestRunResult | null
  result_id: string | null
  message: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface ResearchProvenance {
  version?: number
  created_at?: string
  profile?: "demo" | "runtime"
  strategy_source_sha256?: string | null
  strategy_python_sha256?: string | null
  strategy_python?: { source: string; sha256: string } | null
  code?: {
    commit?: string | null
    dirty?: boolean | null
    source_sha256?: string
    source_files?: number
  }
  data?: { aggregate_sha256?: string; kind?: string }
}

export interface BacktestExecution {
  signal_date: string
  entry_date: string
  exit_date: string
  execution_price?: "next_open" | "next_close" | "monthly_factor_close"
  turnover: number
  traded_weight: number
  fixed_cost: number
  impact_cost: number
  total_cost: number
  gross_return: number
  net_return: number
  cash_weight: number
  constrained_symbols: string[]
  missing_amount_symbols: string[]
  stage_outputs?: Record<string, Record<string, unknown>>
  selection_forward_returns?: Record<string, number>
}

export interface BacktestHoldingSnapshot {
  date: string
  holdings_count: number
  gross_exposure: number
  concentration: number
  max_weight: number
  top_holdings: Array<{ symbol: string; weight: number }>
}

export interface BacktestAnalysis {
  id: string
  strategy_id: string
  profile: "demo" | "runtime"
  start_date: string
  end_date: string
  run_at: string
  metrics: Record<string, number | null>
  dates: string[]
  returns: Array<number | null>
  equity_curve: Array<number | null>
  drawdown: Array<number | null>
  benchmark_returns: Array<number | null>
  benchmark_equity_curve: Array<number | null>
  benchmark_drawdown: Array<number | null>
  excess_returns: Array<number | null>
  excess_equity_curve: Array<number | null>
  benchmark_coverage: number | null
  turnover: Array<{ date: string; value: number }>
  average_turnover: number | null
  holdings: BacktestHoldingSnapshot[]
  executions: BacktestExecution[]
  has_execution_audit: boolean
  strategy_snapshot: {
    strategy_type: "sdk_v1" | "python_pipeline" | "legacy_snapshot"
    implementation?: "python"
    python_stages?: string[]
    pipeline?: Record<string, unknown>
    pipeline_manifest?: StrategyPipelineManifest
    name: string
    description: string
    factors: string[]
    signals: string[]
    market_factor?: string
    rebalance_freq: "daily" | "monthly" | "weekly"
    execution_price: "next_open" | "next_close" | "monthly_factor_close"
    cost_bps: number
    max_weight?: number
    max_exposure?: number
  } | null
  provenance: ResearchProvenance
}

export interface BacktestSignalDiagnostics {
  id: string
  periods: number
  evidence_periods: number
  summary: {
    mean_ic: number | null
    positive_ic_ratio: number | null
    average_coverage: number | null
    average_selection_turnover: number | null
  }
  rows: Array<{
    signal_date: string
    universe_count: number
    scored_count: number
    selected_count: number
    coverage: number | null
    ic: number | null
    quantile_spread: number | null
    selection_turnover: number | null
  }>
  warning: string | null
}

export interface BacktestRegression {
  observations: number
  alpha_monthly: number | null
  alpha_annualized: number | null
  betas: Record<string, number | null>
  r_squared: number | null
  residual_volatility_annualized: number | null
  estimates: Record<string, {
    estimate: number
    standard_error: number
    t_stat: number
    confidence_95: [number, number]
  }>
  warning: string | null
}

export interface BacktestAttribution {
  id?: string
  frequency: "monthly"
  observations: number
  coverage: number
  capm: BacktestRegression
  multi_factor: BacktestRegression
  factor_return_correlation: {
    labels: string[]
    observations: number
    pearson: Array<Array<number | null>>
    spearman: Array<Array<number | null>>
  }
  selection_score_correlation: {
    labels: string[]
    periods: number
    median_spearman: Array<Array<number | null>>
  }
  research_checks: {
    passed?: boolean
    thresholds?: Record<string, number>
    observed?: Record<string, number | null>
    checks?: Record<string, boolean>
  }
  input_snapshot: Array<Record<string, string | number | null>>
  warnings: string[]
}

export interface BacktestComparison {
  ids: string[]
  dates: string[]
  series: Record<string, Array<number | null>>
  labels: Record<string, string>
  metrics: Array<Record<string, string | number | null>>
}

export interface BacktestRobustness {
  id: string
  strategy_id: string
  profile: "demo" | "runtime"
  start_date: string
  end_date: string
  status: "research_candidate" | "watch" | "weak" | "invalid"
  disclaimer: string
  periods: number
  benchmark_coverage: number
  metrics: {
    strategy: Record<string, number | null>
    benchmark: Record<string, number | null>
    excess: Record<string, number | null>
  }
  candidate_rules: Record<string, boolean>
  checks: Array<{ name: string; passed: boolean; detail: string }>
  annual: Array<{
    year: number
    strategy: number
    benchmark: number
    excess: number
    periods: number
    complete: boolean
  }>
  rolling: Record<string, Array<{
    date: string
    strategy: number
    benchmark: number
    excess: number
  }>>
  validation: {
    split_date: string | null
    development: RobustnessPeriodMetrics
    validation: RobustnessPeriodMetrics
  }
  statistical: {
    bootstrap_mean_excess_95: { lower: number | null; upper: number | null }
    one_sided_p_value: number
    research_trials: number
    adjusted_p_value: number
  }
  cost_sensitivity: Record<string, Record<string, number | null>>
  turnover: { average: number | null; maximum: number | null }
  portfolio: {
    average_holdings: number | null
    average_concentration: number | null
    maximum_weight: number | null
  }
}

export interface ResearchRun {
  id: string
  strategy_id: string
  profile: "demo" | "runtime"
  start_date: string
  end_date: string
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted"
  error?: string | null
  result?: {
    backtest_id: string
    robustness_status: string
    signal_id: string | null
    preview_id: string | null
    paper_execution: string
  } | null
  steps: Array<{
    name: string
    status: string
    detail?: Record<string, unknown> | null
  }>
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
  active_profile: "demo" | "runtime"
  profiles: Record<string, {
    status: string
    latest_date: string | null
    symbol_count: number
    factor_returns: string
  }>
  runtime: RuntimeCatalog
}

export interface RuntimeDataset {
  id: string
  label: string
  status: string
  path: string
  files: number
  rows: number
  bytes: number
  date_start: string | null
  date_end: string | null
  symbol_count: number
  error?: string | null
  provider_api?: string[]
  research_role?: string
  field_policy?: string
}

export interface RuntimeCatalog {
  root: string
  status: string
  ready: number
  total: number
  configured: number
  datasets: RuntimeDataset[]
}

export interface SyncJob {
  id: string
  source: string
  status: string
  progress: number
  total: number
  message?: string | null
  error?: string | null
  created_at: string
  request?: { template_id?: string; datasets?: string[] }
}

export interface SyncPlan {
  source: string
  template_id: string
  template: {
    id: string
    label: string
    market: string
    instrument_types: string[]
    datasets: string[]
    scope: string
  }
  scope: string
  symbol_source: string
  symbols_resolved: boolean
  symbol_count: number | null
  requested_start: string
  requested_end: string
  estimated_batches: number | null
  writes_are_local: boolean
  steps: Array<Record<string, string | number | null>>
}

export interface DataSyncHealth {
  status: string
  runtime: RuntimeCatalog
  rq: {
    status: string
    installed: boolean
    configured: boolean
    ready: boolean
    missing: string[]
    connected: boolean
    connection_test_required?: boolean
    last_error?: string | null
  }
  realtime: { status: string }
  tools: { status: string; count: number }
  planner: { status: string }
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

export interface MarketInstrument {
  symbol: string
  name: string | null
}

export interface FactorReturnsPayload {
  names: string[]
  rows: Array<Record<string, string | number>>
}

export interface CustomRiskFactorResult {
  profile: "demo" | "runtime"
  name: string
  expression: string
  dependencies: string[]
  dates: string[]
  returns: Array<number | null>
  cumulative: Array<number | null>
  summary: {
    observations: number
    annual_return: number | null
    annual_volatility: number | null
    sharpe: number | null
    max_drawdown: number | null
    positive_ratio: number | null
  }
  warnings: string[]
}

export interface RobustnessPeriodMetrics {
  periods?: number
  strategy?: Record<string, number | null>
  benchmark?: Record<string, number | null>
  excess?: Record<string, number | null>
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

export interface PaperPosition {
  account_id: string
  symbol: string
  quantity: number
  avg_cost: number
  market_price: number | ""
  market_value: number | ""
  unrealized_pnl: number | ""
  price_date: string | ""
}

export interface PaperAccount {
  id: string
  name: string
  initial_cash: number
  cash: number
  market_value: number
  equity: number
  total_return: number
  realized_pnl: number
  positions_count: number
}

export interface PaperAccountPayload {
  account: PaperAccount
  positions: PaperPosition[]
  nav: Array<{
    date: string
    cash: number
    market_value: number
    equity: number
  }>
  profile: "demo" | "runtime"
  price_date: string
}

export interface PaperRiskCheck {
  name: string
  passed: boolean
  detail: string
}

export interface PaperRebalancePreview {
  preview_id: string
  account_id: string
  signal_id: string
  strategy_id: string
  profile: "demo" | "runtime"
  price_date: string
  allowed: boolean
  risk_status: "ready" | "blocked"
  checks: PaperRiskCheck[]
  account: PaperAccount
  projected_cash: number
  turnover: number
  gross_target: number
  maximum_target: number
  orders: Array<{
    symbol: string
    action: "buy" | "sell"
    quantity: number
    price: number
    notional: number
    commission: number
    target_weight: number
  }>
  disclaimer: string
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
    if (response.status === 204) return undefined as T
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
