/** A single row in the regression coefficients table. */
export interface RegressionRow {
  coefficient: string
  value: number
  t_stat: number
  p_value: number
  significance: string
}

/** Monthly factor contribution time series. */
export interface ContributionSeries {
  dates: string[]
  series: Record<string, (number | null)[]>
}

/** Factor attribution regression result. */
export interface FactorAttribution {
  r_squared: number
  adj_r_squared: number
  dw_stat: number
  n_observations: number
  regression_table: RegressionRow[]
  contribution: ContributionSeries
}

/** Rolling factor exposure (betas, R-squared, alpha). */
export interface RollingExposure {
  dates: string[]
  betas: Record<string, (number | null)[]>
  r_squared: (number | null)[]
  alpha: (number | null)[]
}

/** Style drift data for a single factor. */
export interface StyleDriftFactor {
  factor: string
  z_score_dates: string[]
  z_score_values: (number | null)[]
  rolling_std_dates: string[]
  rolling_std_values: (number | null)[]
}

/** Stability summary for a single factor. */
export interface StabilitySummaryRow {
  factor: string
  full_period_mean: number
  latest_beta: number
  z_score: number
  trend: string
  regime: string
}

/** Style drift detection result. */
export interface StyleDrift {
  factors: StyleDriftFactor[]
  stability_summary: StabilitySummaryRow[]
}

/** Metrics for a single stress scenario. */
export interface StressScenarioMetrics {
  scenario: string
  strategy_return: number | null
  mkt_return: number | null
  excess_return: number | null
  max_drawdown: number | null
  recovery_months: number | null
}

/** Stress test result across historical crisis periods. */
export interface StressTest {
  data_start: string
  data_end: string
  scenarios: StressScenarioMetrics[]
}
