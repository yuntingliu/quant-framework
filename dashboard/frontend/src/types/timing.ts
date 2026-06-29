import type { AnnualPerformanceRow, MonthlyHeatmapData, BacktestComparison } from "./backtest"

/** Current timing signal values. */
export interface TimingSignal {
  date: string
  signals: Record<string, number>
  raw_position: number
  position: number
  realized_vol: number
}

/** Annual performance for a single year (same shape as backtest). */
export type TimingAnnualRow = AnnualPerformanceRow

/** Monthly returns heatmap data (same shape as backtest). */
export type TimingMonthlyHeatmap = MonthlyHeatmapData

/** Index timing backtest result. */
export interface TimingBacktestResult {
  dates: string[]
  strategy_returns: number[]
  benchmark_returns: number[]
  strategy_cumulative: number[]
  benchmark_cumulative: number[]
  strategy_drawdown: number[]
  benchmark_drawdown: number[]
  positions: number[]
  signal_history: Record<string, number[]>
  signal_dates: string[]
  realized_vol: number[]
  annual_performance: TimingAnnualRow[]
  monthly_heatmap: TimingMonthlyHeatmap

  // Summary stats
  strategy_ann_return: number
  strategy_ann_vol: number
  strategy_sharpe: number
  strategy_max_dd: number
  benchmark_ann_return: number
  benchmark_ann_vol: number
  benchmark_sharpe: number
}

/** Comparison table of timing signal variants (same shape as backtest). */
export type TimingComparison = BacktestComparison
