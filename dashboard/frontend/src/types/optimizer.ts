/** Portfolio-level statistics. */
export interface PortfolioStats {
  expected_return: number
  volatility: number
  sharpe_ratio: number
  n_positions: number
  effective_positions: number
  max_weight: number
  min_weight: number
}

/** Risk decomposition for factor-constrained optimizer. */
export interface RiskDecomposition {
  total_risk: number
  factor_risk: number
  specific_risk: number
  factor_contributions: Record<string, number>
}

/** Result for a single optimizer model. */
export interface ModelResult {
  model: string
  stats: PortfolioStats
  weights: Record<string, number>
  exposures?: Record<string, number> | null
  risk_decomp?: RiskDecomposition | null
}

/** Multi-model optimizer comparison result. */
export interface OptimizerResult {
  is_synthetic: boolean
  n_stocks_used: number
  n_months: number
  data_range: string | null
  models: ModelResult[]
}

/** A single point on the efficient frontier. */
export interface FrontierPoint {
  vol: number
  ret: number
}

/** Efficient frontier computation result. */
export interface EfficientFrontier {
  frontier_vols: number[]
  frontier_rets: number[]
  frontier_sharpes: number[]
  min_var: FrontierPoint
  max_sharpe_point: FrontierPoint
  max_sharpe_ratio: number
}

/** Portfolio weights (generic). */
export interface PortfolioWeights {
  weights: Record<string, number>
}
