import { api } from "@/lib/api"

export interface OptionUniverse {
  revision: string
  underlyings: Array<{ symbol: string; name: string; dates: string[]; sessions: number; rows: number; start: string | null; latest: string | null }>
  total_rows: number
  rates: { status: string; date_start: string | null; date_end: string | null; rows: number }
  jobs: Array<{ id: string; status: string; message: string; progress: number; total: number }>
}
export interface OptionRow {
  order_book_id: string; expiry: string; option_type: "C" | "P"; strike: number; multiplier: number
  market_price: number; spot: number; volume: number; open_interest: number; maturity: number
  iv: number | null; status: string; reason?: string; delta?: number; gamma?: number
  vega_1pct?: number; theta_day?: number; rate: number; effective_dividend_yield?: number; forward?: number
}
export interface SmileFit {
  expiry: string; maturity: number; forward: number; atm_iv: number | null; rmse_iv: number
  observations: number; risk_reversal_25: number | null; butterfly_25: number | null
  smile: Array<{ k: number; strike: number; iv: number }>
  observed: Array<{ k: number; iv: number; strike: number; contract: string }>
}
export interface OptionAnalysis {
  underlying: string; name: string; date: string; source: string; total: number; eligible: number; retrieved_at: string
  contracts: OptionRow[]; status_counts: Record<string, number>
  activity: { volume_pcr: number | null; oi_pcr: number | null; call_volume: number; put_volume: number; call_oi: number; put_oi: number }
  discount_curve: { source: string; date?: string; stale_days?: number; convention?: string; rate?: number; nodes?: Array<{ maturity: number; continuous_rate: number }> }
  realized_volatility: { status: string; rv20: number | null; rv60: number | null; observations?: number }
  forward_curve: Array<{ expiry: string; status: string; forward?: number; pairs: number; relative_pair_range?: number }>
  put_call_parity: Array<{ expiry: string; strike: number; residual: number; flag: boolean }>
  static_arbitrage_flags: Array<{ expiry: string; option_type: string; butterfly_arbitrage_flag?: boolean }>
  surface: { fits: SmileFit[]; warnings: string[]; calendar_violations: number; usable_for_interpolation: boolean
    grid: Array<{ expiry: string; maturity: number; values: Array<{ k: number; iv: number; total_variance: number }> }> }
}
export interface BasketLeg { underlying: string; contract: string; quantity: number; label: string }
export interface McResult {
  value: number; standard_error: number; ci95: number[] | null; paths: number; seed: number
  warning: string | null; interpretation: string; payoff_quantiles: Record<string, number>
}
export interface BasketRisk {
  market_value: number; greeks: Record<string, number>; worst_pnl: number; best_pnl: number
  scenarios: Array<{ spot_shock: number; volatility_shift: number; value: number; pnl: number }>
  positions: Array<OptionRow & { underlying: string; quantity: number; market_value: number }>
  monte_carlo?: { base: McResult; crash: McResult; model_pnl: number }
}
export interface DatasetQuality {
  status: string; rows: number; files: number; metrics: Record<string, unknown>
  issues: Array<{ code: string; message: string; severity: string }>
}
export interface OptionQuality { snapshots: DatasetQuality; rates: DatasetQuality; underlyings: DatasetQuality }
export interface SyncPlan { source: string; planned: Array<{ dataset: string; operation: string }> }

export const optionApi = {
  universe: () => api.get<OptionUniverse>("/options/universe"),
  analysis: (symbol: string, day: string, field: string, rate?: number) => api.get<OptionAnalysis>(
    `/options/analysis?${new URLSearchParams({ underlying: symbol, day, price_field: field, ...(rate === undefined ? {} : { rate: String(rate) }) })}`,
    { timeoutMs: 60000 }),
  quality: () => api.get<OptionQuality>("/options/quality", { timeoutMs: 300000 }),
  history: (symbol: string, day: string) => api.get<{ points: Array<{ date: string; atm_iv: number | null; eligible: number }> }>(
    `/options/history?${new URLSearchParams({ underlying: symbol, end: day, limit: "40" })}`, { timeoutMs: 60000 }),
  portfolio: (body: unknown) => api.post<BasketRisk>("/options/portfolio", body),
  plan: (body: unknown) => api.post<SyncPlan>("/options/sync/plan", body),
  sync: (body: unknown) => api.post<{ id: string; status: string }>("/options/sync/jobs", body),
}

export function percent(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`
}
export function number(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("zh-CN", { maximumFractionDigits: digits })
}
