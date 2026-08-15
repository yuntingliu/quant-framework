// In Electron production, no Vite proxy; the main process passes the sidecar URL.
interface ElectronApiBridge {
  getApiOrigin?: () => string
  getApiBase?: () => string
}

export interface StrategyTemplate {
  id: string
  strategy_type: "stock_selection" | "market_timing" | "allocation_rotation"
  name: string
  description: string
  path: string
  factors: string[]
  signals?: string[]
  sleeves?: string[]
  implementation: "configured" | "python"
  warnings: string[]
  built_in: boolean
  editable: boolean
  source: "built_in" | "local"
  research_status?: "research_candidate" | "watch" | "weak" | "invalid"
  latest_signal_date?: string
  latest_backtest?: {
    id: string
    run_at: string
    total_return: number | null
    sharpe: number | null
    max_drawdown: number | null
    profile: "demo" | "runtime"
  }
}

export interface StrategyTemplateDetail extends StrategyTemplate {
  yaml: string
  config: StrategyConfigPayload
  python_source: string | null
  python_source_sha256: string | null
}

export interface StrategyImplementationConfig {
  kind: "configured" | "python"
  entrypoint: string
  timeout_seconds: number
}

export interface StrategyFactorConfig {
  name: string
  weight: number
  direction: "long" | "short"
  source: "technical" | "fundamental" | "expression"
  expression?: string | null
  winsorize: number
  neutralize: string[]
}

export interface StrategyConfigPayload {
  strategy_type: "stock_selection"
  name: string
  description: string
  universe: {
    pool: string
    symbols: string[]
    min_price: number
    min_history_days: number
    min_average_amount: number
    max_stale_days: number
    require_positive_volume: boolean
  }
  factors: StrategyFactorConfig[]
  selection: {
    min_factor_coverage: number
    n_stocks: number
  }
  portfolio: {
    max_weight: number
    rebalance_freq: "monthly" | "weekly"
    optimizer: "equal_weight"
  }
  execution: {
    cost_bps: number
    slippage_bps: number
    impact_bps: number
    execution_price: "next_open" | "next_close"
    portfolio_value: number
    max_participation_rate: number
  }
  implementation: StrategyImplementationConfig
  metadata?: Record<string, unknown>
}

export interface StrategyValidationCheck {
  code: string
  status: "passed" | "warning" | "failed"
  severity: "info" | "warning" | "error"
  message: string
}

export interface StrategyValidationResult {
  valid: boolean
  strategy_type: "stock_selection"
  name: string
  factors: string[]
  warnings: string[]
  normalized_yaml: string
  config: StrategyConfigPayload
  python_source: string | null
  python_source_sha256: string | null
  checks: StrategyValidationCheck[]
}

export interface TimingSignalConfig {
  kind: "trend" | "momentum" | "volatility_control"
  weight: number
  window: number
  threshold: number
}

export interface TimingStrategyConfigPayload {
  strategy_type: "market_timing"
  name: string
  description: string
  market_factor: "MKT"
  signals: TimingSignalConfig[]
  position: {
    min_exposure: number
    max_exposure: number
  }
  execution: {
    cost_bps: number
    slippage_bps: number
  }
  implementation: StrategyImplementationConfig
  metadata?: Record<string, unknown>
}

export interface TimingStrategyTemplateDetail extends StrategyTemplate {
  strategy_type: "market_timing"
  yaml: string
  config: TimingStrategyConfigPayload
  python_source: string | null
  python_source_sha256: string | null
}

export interface TimingStrategyValidationResult {
  valid: boolean
  strategy_type: "market_timing"
  name: string
  factors: string[]
  signals: string[]
  warnings: string[]
  normalized_yaml: string
  config: TimingStrategyConfigPayload
  python_source: string | null
  python_source_sha256: string | null
  checks: StrategyValidationCheck[]
}

export interface TimingResearchResult {
  strategy_id: string
  strategy_type: "market_timing"
  profile: "demo" | "runtime"
  metrics: Record<string, number>
  diagnostics: {
    market_factor: string
    frequency: "monthly"
    periods: number
    average_exposure: number
    latest_exposure: number
    latest_signal_date: string
    turnover: number
  }
  series: Array<{
    date: string
    strategy: number
    benchmark: number
    exposure: number
  }>
  signals: Array<{
    date: string
    combined_score: number
    exposure: number
    signals: Record<string, number>
  }>
}

export interface RotationStrategyConfigPayload {
  strategy_type: "allocation_rotation"
  name: string
  description: string
  sleeves: Array<"MKT" | "SMB" | "HML" | "MOM" | "RMW">
  benchmark: "MKT" | "SMB" | "HML" | "MOM" | "RMW"
  signal: {
    kind: "momentum"
    lookback: number
    skip: number
  }
  selection: { top_k: number }
  portfolio: {
    max_weight: number
    optimizer: "equal_weight"
  }
  execution: {
    cost_bps: number
    slippage_bps: number
  }
  implementation: StrategyImplementationConfig
  metadata?: Record<string, unknown>
}

export interface RotationStrategyTemplateDetail extends StrategyTemplate {
  strategy_type: "allocation_rotation"
  yaml: string
  config: RotationStrategyConfigPayload
  python_source: string | null
  python_source_sha256: string | null
}

export interface RotationStrategyValidationResult {
  valid: boolean
  strategy_type: "allocation_rotation"
  implementation: "configured" | "python"
  name: string
  factors: string[]
  signals: string[]
  sleeves: string[]
  warnings: string[]
  normalized_yaml: string
  config: RotationStrategyConfigPayload
  python_source: string | null
  python_source_sha256: string | null
  checks: StrategyValidationCheck[]
}

export interface RotationResearchResult {
  strategy_id: string
  strategy_type: "allocation_rotation"
  profile: "demo" | "runtime"
  metrics: Record<string, number>
  diagnostics: {
    frequency: "monthly"
    periods: number
    sleeves: string[]
    benchmark: string
    lookback: number
    top_k: number
    average_invested: number
    latest_signal_date: string
    latest_targets: Record<string, number>
    turnover: number
  }
  series: Array<{
    date: string
    strategy: number
    benchmark: number
    invested: number
  }>
  allocations: Array<{
    date: string
    cash: number
    weights: Record<string, number>
    scores: Record<string, number | null>
  }>
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
  strategy_id: string
  strategy_type: "stock_selection" | "market_timing" | "allocation_rotation"
  metrics: Record<string, number>
  returns: { date: string; value: number }[]
  weights_count: number
  execution: Record<string, unknown>
  provenance: ResearchProvenance
}

export interface ResearchProvenance {
  version?: number
  created_at?: string
  profile?: "demo" | "runtime"
  strategy_sha256?: string | null
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
    strategy_type: "stock_selection" | "market_timing" | "allocation_rotation"
    implementation: "configured" | "python"
    name: string
    description: string
    factors: string[]
    signals: string[]
    market_factor?: string
    sleeves?: string[]
    benchmark?: string
    rebalance_freq: "monthly" | "weekly"
    execution_price: "next_open" | "next_close" | "monthly_factor_close"
    cost_bps: number
    max_weight?: number
    max_exposure?: number
  } | null
  provenance: ResearchProvenance
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
}

export interface SyncPlan {
  source: string
  symbol_count: number
  requested_start: string
  requested_end: string
  estimated_batches: number
  writes_are_local: boolean
  steps: Array<Record<string, string | number | null>>
}

export interface DataSyncHealth {
  status: string
  runtime: RuntimeCatalog
  rq: {
    status: string
    configured: boolean
    missing: string[]
    connected: boolean
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

export interface RobustnessPeriodMetrics {
  periods?: number
  strategy?: Record<string, number | null>
  benchmark?: Record<string, number | null>
  excess?: Record<string, number | null>
}

export interface FactorResearchLibrary {
  factors: Array<{
    name: string
    source: "technical" | "fundamental"
    description: string
  }>
  expression_functions: string[]
  neutralizers: string[]
}

export interface FactorResearchResult {
  factor: {
    name: string
    source: "technical" | "fundamental" | "expression"
    expression?: string | null
    direction: "long" | "short"
    winsorize: number
    neutralize: string[]
  }
  frequency: "monthly" | "weekly"
  quantiles: number
  universe_size: number
  periods: number
  summary: {
    ic_mean?: number
    ic_std?: number
    icir?: number
    ic_t_stat?: number
    ic_positive_ratio?: number
    coverage_mean?: number
    top_turnover_mean?: number | null
    bootstrap_ic_95?: { lower: number | null; upper: number | null }
    long_short?: Record<string, number>
  }
  decay: Record<string, { mean_ic: number | null; observations: number }>
  rows: Array<{
    date: string
    observations: number
    coverage: number
    ic: number
    long_short: number
    top_turnover: number | null
    quantile_returns: Record<string, number>
  }>
  warnings: string[]
}

export interface SignalResult {
  id: string | null
  strategy_id: string
  profile: "demo" | "runtime"
  signal_date: string
  targets: Record<string, number>
  diagnostics: Record<string, unknown>
  selection: {
    as_of_date: string
    universe_size: number
    eligible_count: number
    scored_count: number
    requested_count: number
    selected_count: number
    cash_weight: number
    factor_names: string[]
    exclusions: Record<string, number>
    rows: Array<{
      rank: number
      symbol: string
      selected: boolean
      composite_score: number
      factor_coverage: number
      target_weight: number
      factor_scores: Record<string, number | null>
    }>
  }
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
