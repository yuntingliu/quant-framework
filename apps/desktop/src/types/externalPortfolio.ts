export interface FlexNavPoint {
  date: string
  account_id?: string
  currency?: string
  nav: number
  cash: number
  stocks: number
  options: number
  external_cash_flow: number
  daily_return: number
}

export interface PortfolioPerformanceSummary {
  latest_date?: string | null
  latest_nav: number
  latest_cash: number
  currency: string
  total_return: number
  cagr: number
  volatility: number
  sharpe_ratio: number
  sortino_ratio: number
  max_drawdown: number
  ytd: number
  mtd: number
  fees: number
  dividends: number
}

export interface PortfolioHistoryResponse {
  source: string
  configured: boolean
  nav: FlexNavPoint[]
  returns: Array<{ date: string; daily_return: number; equity: number; drawdown: number }>
  coverage: {
    missing_sections?: string[]
    date_range?: Array<string | null>
    rows?: Record<string, number>
    provider?: {
      configured?: boolean
      missing_config?: string[]
      data_dir?: string
      env_files?: string[]
      latest_fetch_time?: string | null
      date_range?: Array<string | null>
      fields?: Array<{ name: string; rows?: number; exists?: boolean; file_size_mb?: number }>
    }
  }
  warnings: string[]
}

export interface MarketBenchmarkComparison {
  symbol: string
  label: string
  status: string
  verdict: "beats_market" | "lags_market" | "matches_market" | "unavailable" | string
  start_date?: string | null
  end_date?: string | null
  observations: number
  benchmark_rows: number
  portfolio_total_return: number
  benchmark_total_return: number
  excess_total_return: number
  portfolio_cagr: number
  benchmark_cagr: number
  excess_cagr: number
  portfolio_volatility: number
  benchmark_volatility: number
  portfolio_sharpe: number
  benchmark_sharpe: number
  portfolio_max_drawdown: number
  benchmark_max_drawdown: number
  tracking_error: number
  information_ratio: number
  beta: number
  correlation: number
  alpha_annualized: number
  error?: string | null
}

export interface MarketComparisonPoint {
  date: string
  portfolio_equity: number
  benchmark_equity: number
  portfolio_drawdown: number
  benchmark_drawdown: number
  portfolio_return: number
  benchmark_return: number
  excess_return: number
}

export interface PortfolioMarketComparison {
  primary: MarketBenchmarkComparison
  benchmarks: MarketBenchmarkComparison[]
  chart: MarketComparisonPoint[]
  charts?: Record<string, MarketComparisonPoint[]>
  warnings: string[]
}

export interface PortfolioReviewResponse {
  source: string
  configured: boolean
  summary: PortfolioPerformanceSummary
  coverage: PortfolioHistoryResponse["coverage"]
  contribution: Array<Record<string, number | string>>
  currency_exposure: Array<Record<string, number | string>>
  asset_type_exposure: Array<Record<string, number | string>>
  concentration: {
    hhi?: number
    effective_positions?: number
    top_positions?: Array<{ symbol: string; asset_category?: string; market_value: number; weight: number }>
  }
  market_comparison: PortfolioMarketComparison
  reconciliation: {
    statement_position_value?: number
    latest_nav?: number
    live_connected?: boolean
    live_position_value?: number | null
    difference?: number | null
    difference_pct_nav?: number | null
    status?: string
    error?: string
  }
  warnings: string[]
}

export interface PortfolioOptimizationReview {
  source: string
  mode: "review_only"
  generated_at: string
  max_weight: number
  min_delta_nav: number
  current: Record<string, number | string>
  suggested: {
    weights?: Array<{ symbol: string; current_weight: number; target_weight: number; delta_weight: number }>
    [key: string]: number | string | undefined | Array<{ symbol: string; current_weight: number; target_weight: number; delta_weight: number }>
  }
  recommendations: Array<{
    symbol: string
    action: "add" | "trim" | string
    current_weight: number
    target_weight: number
    delta_weight: number
    delta_value: number
    reason?: string
  }>
  locked_exposures: Array<Record<string, number | string>>
  data_coverage: Array<Record<string, number | string>>
  warnings: string[]
}
