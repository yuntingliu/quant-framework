export interface AShareHealthItem {
  id: string
  label: string
  group: string
  available: boolean
  rows: number | null
  symbols: number | null
  start_date: string | null
  end_date: string | null
  path: string | null
  coverage: number | null
  source_quality: string
  notes: string[]
  extras: Record<string, unknown>
  stale_days_vs_market?: number | null
  source_bias_warning?: string
}

export interface AShareSafeSyncPolicy {
  max_symbols_per_chunk: number
  request_sleep_min_seconds: number
  request_sleep_max_seconds: number
  chunk_pause_seconds: number
  remote_workers: string[]
  startup_auto_sync: boolean
  notes: string[]
}

export interface AShareDataHealth {
  generated_at: string
  safe_sync_policy: AShareSafeSyncPolicy
  summary: {
    items: number
    available: number
    latest_market_date: string | null
    latest_altdata_date: string | null
  }
  readiness: {
    ready: boolean
    market_latest: string | null
    allow_stale_days: number
    stale_items: string[]
    partial_items?: string[]
    missing_items: string[]
    warnings: string[]
  }
  items: AShareHealthItem[]
  jobs: AShareSyncJob[]
}

export interface AShareStrategySourceReadiness {
  ready: boolean
  required_factors: string[]
  available_factors: string[]
  missing_factors: string[]
  stale_factors: string[]
  empty_factors: string[]
  low_coverage_factors: string[]
  unsupported_factors: string[]
  blocking_reasons: string[]
  commands: string[]
  factor_dir?: string | null
  as_of_date?: string | null
  latest_common_date?: string | null
  latest_factor_date?: string | null
  items?: Record<string, unknown>
}

export interface AShareStrategyReadinessRow {
  id: string
  path: string
  ready: boolean
  sources: Record<string, AShareStrategySourceReadiness>
  blocking_reasons: string[]
  required_factor_count: number
  commands: string[]
}

export interface AShareStrategyReadiness {
  generated_at: string
  status: string
  strategy_id?: string | null
  as_of_date?: string | null
  allow_stale_days?: number
  min_coverage?: number
  summary: {
    strategies: number
    ready: number
    blocked: number
    sources?: Record<string, number>
  }
  source_readiness?: Record<string, unknown>
  strategies: AShareStrategyReadinessRow[]
}

export interface AShareSyncJob {
  id: string
  mode: string
  status: string
  message: string
  started_at: string
  ended_at: string | null
  request: Record<string, unknown>
  result: Record<string, unknown> | null
  error: string | null
  cancellation_requested: boolean
}

export interface AShareScreenCandidate {
  symbol: string
  name?: string
  date?: string
  signal?: string
  candidate_score?: number
  theme_id?: string
  theme_name?: string
  theme_state?: string
  theme_rank?: number
  theme_heat_score?: number
  theme_leader_score?: number
  theme_risk_score?: number
  theme_catalyst_score?: number
  theme_catalyst_latest_headline?: string
  main_flow_amount?: number
  main_flow_ratio?: number
  super_large_flow_amount?: number
  large_flow_amount?: number
  lhb_has_entry?: boolean
  lhb_net_buy_ratio?: number
  lhb_net_buy_amount?: number
  lhb_institution_net_ratio?: number
  lhb_seat_net_amount?: number
  source_quality?: string
}

export interface AShareThemeRow {
  theme_id?: string
  theme_name?: string
  theme_state?: string
  theme_rank?: number
  theme_heat_score?: number
  theme_risk_score?: number
  theme_catalyst_score?: number
  theme_catalyst_latest_headline?: string
  [key: string]: unknown
}

export interface AShareScreenResponse {
  generated_at: string
  as_of: string | null
  source: string
  reason?: string
  candidates: AShareScreenCandidate[]
  exit_warnings: AShareScreenCandidate[]
  themes: AShareThemeRow[]
  counts?: Record<string, number>
}

export interface AShareCandle {
  time: string
  open: number
  high: number
  low: number
  close: number
}

export interface AShareHistogramPoint {
  time: string
  value: number
  color?: string
}

export interface AShareLinePoint {
  time: string
  value: number
}

export interface AShareMarker {
  time: string
  position: "aboveBar" | "belowBar" | "inBar" | string
  shape: "arrowUp" | "arrowDown" | "circle" | "square" | string
  color: string
  text: string
}

export interface AShareAnnotation {
  time: string
  label: string
  tone: "risk" | "flow" | string
}

export interface AShareChartPayload {
  candles: AShareCandle[]
  volumes: AShareHistogramPoint[]
  flow_histogram: AShareHistogramPoint[]
  indicators: Record<string, AShareLinePoint[]>
  markers: AShareMarker[]
  annotations: AShareAnnotation[]
}

export interface AShareSymbolSignal {
  generated_at: string
  symbol: string
  name: string
  as_of: string | null
  source: string
  source_quality: string
  signal: {
    label: string
    entry_momentum: number
    divergence_score: number
    confidence: number
    explanations: string[]
  }
  metrics: Record<string, number | string | null | undefined>
  flow: Record<string, unknown>
  theme: Record<string, unknown>
  dealer: Record<string, unknown>
  chip: Record<string, unknown>
  chart: AShareChartPayload
  warnings: string[]
}
