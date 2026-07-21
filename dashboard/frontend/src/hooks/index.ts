export {
  useMarketKPI,
  useCumulativeReturns,
  useAnnualReturns,
  useFactorStats,
  useDrawdowns,
  useVolatilityAnalysis,
  useMarketCorrelation,
} from './use-market-data'
export { useFactorPerformance, useCorrelation, useRollingCorrelation, useFactorDistribution, useFactorAnnualReturns } from './use-factor-data'
export { useBacktestComparison, useBacktestRun, useBacktestRunMutation } from './use-backtest-data'
export { useOptimizerRun, useOptimizerRunMutation, useEfficientFrontier, useEfficientFrontierMutation } from './use-optimizer-data'
export { useFactorAttribution, useRollingExposure, useStyleDrift, useStressTest } from './use-risk-data'
export {
  useStrategyCatalog,
  isResearchArtifactStrategy,
  isRunnableStrategy,
  marketProfileForStrategy,
  strategyLabel,
} from './use-strategy-catalog'
export type { MarketProfile, StrategyInfo, StrategyCatalog, StrategyKind } from './use-strategy-catalog'
export { useBacktestRunner } from './use-backtest-runner'
export type { BacktestJob, BacktestJobResult, RunBacktestArgs, UseBacktestRunner } from './use-backtest-runner'
