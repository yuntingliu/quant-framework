/** KPI metrics for the market overview page. */
export interface MarketKPI {
  profile: "runtime"
  data_start: string | null
  data_end: string | null
  n_months: number

  mkt_ann_return: number | null
  mkt_last_12m_return: number | null
  mkt_sharpe_full: number | null
  mkt_sharpe_recent: number | null

  smb_ann_return: number | null
  smb_last_12m_return: number | null
  hml_ann_return: number | null
  hml_last_12m_return: number | null

  avg_vol: number | null
  latest_vol: number | null
  current_regime: string
}

/** Cumulative return series for selected factors. */
export interface CumulativeReturns {
  profile: "runtime"
  dates: string[]
  series: Record<string, (number | null)[]>
}

/** Annual returns by year for each factor. */
export interface AnnualReturns {
  profile: "runtime"
  years: string[]
  series: Record<string, (number | null)[]>
}

/** A single row in the factor statistics table. */
export interface FactorStatRow {
  factor: string
  ann_return: number | null
  ann_vol: number | null
  sharpe: number | null
  max_dd: number | null
  pos_ratio: number | null
  skew: number | null
  kurt: number | null
}

/** Factor statistics comparison table. */
export interface FactorStats {
  profile: "runtime"
  stats: FactorStatRow[]
}

/** A single drawdown period. */
export interface DrawdownPeriod {
  start: string
  trough: string
  end: string
  depth: number
  recovery_months: number | null
}

/** MKT drawdown series plus top N drawdown periods. */
export interface DrawdownData {
  profile: "runtime"
  dates: string[]
  drawdown_values: (number | null)[]
  top_drawdowns: DrawdownPeriod[]
}

/** Statistics for a single volatility regime. */
export interface VolRegimeStat {
  regime: string
  n_months: number
  proportion: number
  mean: number
  median: number
  min_val: number
  max_val: number
}

/** Volatility time series, regime classification, and regime statistics. */
export interface VolatilityData {
  profile: "runtime"
  dates: string[]
  vol_values: (number | null)[]
  regimes: string[]
  t1: number | null
  t2: number | null
  regime_stats: VolRegimeStat[]
}

export interface CorrelationData {
  profile: "runtime"
  labels: string[]
  matrix: (number | null)[][]
}

// ---------------------------------------------------------------------------
// Live market overview (Home page)
// ---------------------------------------------------------------------------

/** A single index / ETF quote on the Home index board. */
export interface QuoteItem {
  code: string
  name: string
  price: number | null
  pre_close: number | null
  change_pct: number | null
}

/** One side of the index board (A-share or US). */
export interface BoardSection {
  source: string // qmt | external | unavailable | not_connected
  reason?: string | null
  items: QuoteItem[]
}

/** Live index board: A-share indices + US ETF proxies. */
export interface IndexBoard {
  ashare: BoardSection
  us: BoardSection
  as_of: string | null
}

/** A single stock in the top gainers/losers lists. */
export interface MoverItem {
  code: string
  name: string
  price: number | null
  change_pct: number | null
}

/** A-share market breadth plus top movers. */
export interface BreadthData {
  source: string // qmt | unavailable
  reason?: string | null
  advancers: number
  decliners: number
  unchanged: number
  limit_up: number
  limit_down: number
  total: number
  top_gainers: MoverItem[]
  top_losers: MoverItem[]
  as_of: string | null
}

/** One industry tile in the sector heatmap. */
export interface SectorItem {
  industry: string
  mean_change_pct: number | null
  amount_weighted_change_pct: number | null
  count: number
  advancers: number
  decliners: number
  total_amount: number | null
}

/** Per-industry mean change % heatmap. */
export interface SectorHeatmap {
  source: string // qmt+rq | unavailable
  reason?: string | null
  classification_source: string | null
  classification_date: string | null
  universe_count: number
  mapped_count: number
  unmapped_count: number
  coverage_pct: number | null
  sectors: SectorItem[]
  as_of: string | null
}

/** A single stock ranked by live turnover proxy. */
export interface MoneyFlowItem {
  code: string
  name: string
  industry?: string | null
  price: number | null
  change_pct: number | null
  amount: number | null
  volume: number | null
  net_amount?: number | null
  net_amount_ratio?: number | null
}

/** One industry ranked by QMT/RQ direction-weighted amount proxy. */
export interface MoneyFlowSectorItem {
  industry: string
  net_amount: number | null
  net_amount_ratio: number | null
  inflow: number | null
  outflow: number | null
  total_amount: number | null
  mean_change_pct: number | null
  amount_weighted_change_pct: number | null
  count: number
  advancers: number
  decliners: number
  lead_stock: string
}

/** QMT amount-based activity proxy. */
export interface MoneyFlowTurnover {
  source: string // qmt | unavailable
  reason?: string | null
  method: "amount_proxy" | string
  total_amount: number | null
  up_amount: number | null
  down_amount: number | null
  flat_amount: number | null
  up_amount_ratio: number | null
  down_amount_ratio: number | null
  active: MoneyFlowItem[]
  positive: MoneyFlowItem[]
  negative: MoneyFlowItem[]
  as_of: string | null
}

/** True signed net-flow provider state, or an explicitly labeled computed proxy. */
export interface MoneyFlowNetFlow {
  source: string
  available: boolean
  reason?: string | null
  method: string
  total_net_flow: number | null
  inflow: number | null
  outflow: number | null
  net_flow_ratio: number | null
  confidence: string
  coverage_pct: number | null
  leaders: MoneyFlowItem[]
  sectors: MoneyFlowSectorItem[]
  as_of: string | null
}

/** A-share live activity plus signed flow state or computed proxy. */
export interface MoneyFlowData {
  source: string // qmt | unavailable
  reason?: string | null
  method: "amount_proxy" | string
  total_amount: number | null
  up_amount: number | null
  down_amount: number | null
  flat_amount: number | null
  up_amount_ratio: number | null
  down_amount_ratio: number | null
  active: MoneyFlowItem[]
  positive: MoneyFlowItem[]
  negative: MoneyFlowItem[]
  turnover: MoneyFlowTurnover | null
  net_flow: MoneyFlowNetFlow | null
  as_of: string | null
}

export type MarketKlinePeriod = "1d" | "5m" | "15m"

export interface MarketKlineBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
  amount?: number | null
}

export interface MarketKlineData {
  source: string // qmt | local | unavailable
  reason?: string | null
  symbol: string
  name: string
  period: MarketKlinePeriod
  as_of: string | null
  data: MarketKlineBar[]
}
