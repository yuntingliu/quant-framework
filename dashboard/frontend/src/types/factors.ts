/** A single factor's performance statistics. */
export interface FactorStatItem {
  factor: string
  annual_return: number | null
  annual_vol: number | null
  sharpe: number | null
  max_drawdown: number | null
}

/** Factor performance stats table plus cumulative return series. */
export interface FactorPerformance {
  stats: FactorStatItem[]
  dates: string[]
  cumulative_series: Record<string, (number | null)[]>
}

/** Correlation matrix between factors. */
export interface CorrelationMatrix {
  factors: string[]
  matrix: (number | null)[][]
}

/** Rolling correlation for a single pair of factors. */
export interface RollingCorrelationPair {
  pair: string
  dates: string[]
  values: (number | null)[]
}

/** Rolling correlation for all factor pairs. */
export interface RollingCorrelation {
  window: number
  pairs: RollingCorrelationPair[]
}

/** Distribution data for a single factor. */
export interface DistributionItem {
  factor: string
  values: number[]
  mean: number
}

/** Histogram/distribution data for selected factors. */
export interface FactorDistribution {
  distributions: DistributionItem[]
}

/** Annual returns table for selected factors. */
export interface FactorAnnualReturns {
  years: string[]
  series: Record<string, (number | null)[]>
}
