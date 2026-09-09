/** Five-method comparison table from compare_timing_methods. */
export interface BacktestComparison {
  columns: string[]
  index: string[]
  data: (number | string | null)[][]
}

/** Descriptive statistics for factor weights. */
export interface WeightStats {
  factor: string
  mean: number
  std: number
  min_val: number
  max_val: number
}

/** Annual performance for a single year. */
export interface AnnualPerformanceRow {
  year: number
  strategy: number
  benchmark: number
  excess: number
  [key: string]: unknown
}

/** Monthly returns heatmap data (year x month). */
export interface MonthlyHeatmapData {
  years: string[]
  months: string[]
  values: (number | null)[][]
}

/** Single backtest run result. */
export interface BacktestResult {
  // Equity curve data
  dates: string[]
  strategy_returns: number[]
  benchmark_returns: number[]
  strategy_cumulative: number[]
  benchmark_cumulative: number[]
  strategy_drawdown: number[]
  benchmark_drawdown: number[]

  // Factor weights over time
  weight_dates: string[]
  factor_weights: Record<string, number[]>
  weight_stats: WeightStats[]

  // Annual performance
  annual_performance: AnnualPerformanceRow[]

  // Monthly heatmap
  monthly_heatmap: MonthlyHeatmapData
}

export interface BacktestDetail {
  dates: string[]
  cumulative: number[]
  strategy: number[]
  benchmark: number[]
  excess: number[]
  strategy_returns: number[]
  benchmark_returns: number[]
  excess_returns: number[]
  drawdown: number[]
  rolling_sharpe: (number | null)[]
  rolling_vol: (number | null)[]
}

export interface TradeLogRow {
  date: string
  signal_date?: string
  symbol: string
  side: "buy" | "sell"
  previous_weight: number
  target_weight: number
  weight_delta: number
}

export interface DrawdownWindow {
  start: string | null
  trough: string | null
  recovery: string | null
  max_drawdown: number
  duration_months: number
}

export interface ContributionRow {
  symbol: string
  contribution: number
  avg_weight: number
  months_held: number
}

export interface AttributionSummary {
  status: "ready" | "unavailable"
  reason?: string
  regression?: {
    alpha: number
    r_squared: number
    adj_r_squared: number
    betas: Record<string, number>
  }
  summary?: {
    annualized_contributions?: Record<string, number>
    variance_contribution?: Record<string, number>
    style_classification?: string[]
    alpha_annualized?: number
  }
  cumulative_contribution?: Array<Record<string, number | string | null>>
  period_summary?: Array<Record<string, number | string | null>>
}
