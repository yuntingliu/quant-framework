/**
 * useStrategyCatalog — shared strategy-catalog access for backtest widgets.
 *
 * Owns the `/strategies?market=` + global `/strategies` queries, the research /
 * backtest partition, and the dedup that the Runner and Workbench both need.
 */
import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { STALE_TIME } from "@/lib/constants"
import type { TranslationKey } from "@/contexts/LanguageContext"

export type MarketProfile = "a_share" | "external"

export interface StrategyInfo {
  id: string
  file: string
  description: string
  n_factors: number
  optimizer: string
  n_stocks: number
  market?: string
  data_source?: string
  kind?: "yaml_strategy" | "research_screener" | "ensemble_strategy" | "ported_strategy" | "research_strategy" | "research_overlay" | "event_study"
  category?: string
  category_label?: string
  catalog_code?: string
  catalog_group?: string
  catalog_group_label?: string
  usage?: string
  lifecycle?: string
  priority?: number
  tags?: string[]
  description_en?: string
  warnings?: string[]
  run_enabled?: boolean
  artifact_names?: Record<string, string>
  /** Ensemble-only: meta-allocation method + member strategies. */
  meta_method?: string
  components?: { strategy_id: string; kind: string }[]
}

export type StrategyKind = NonNullable<StrategyInfo["kind"]>

export function isRunnableStrategy(strategy: StrategyInfo): boolean {
  if (strategy.run_enabled === false) return false
  return (
    strategy.kind === undefined ||
    strategy.kind === "yaml_strategy" ||
    strategy.kind === "ensemble_strategy" ||
    strategy.kind === "ported_strategy" ||
    strategy.kind === "research_strategy"
  )
}

export function isResearchArtifactStrategy(strategy: StrategyInfo): boolean {
  return (
    strategy.kind === "research_screener" ||
    strategy.kind === "research_overlay" ||
    strategy.kind === "event_study" ||
    strategy.run_enabled === false
  )
}

export function marketProfileForStrategy(strategyId?: string | null): MarketProfile {
  return strategyId?.startsWith("external_") ? "external" : "a_share"
}

/** Display label for a strategy option. `detailed` matches the Runner's verbose form. */
export function strategyLabel(
  strategy: StrategyInfo,
  t: (key: TranslationKey) => string,
  detailed = false,
): string {
  const code = strategy.catalog_code ? `${strategy.catalog_code} ` : ""
  if (strategy.id === "valuation_flow_stock_picks") {
    return `${code}${t("backtest.runner.valuationFlowLabel")}`
  }
  if (isResearchArtifactStrategy(strategy)) {
    return `${code}${strategy.id} [${t("backtest.strategies.researchOnly")}]`
  }
  if (detailed) {
    return `${code}${strategy.id} (${strategy.category_label ?? strategy.n_factors + " " + t("backtest.strategies.factors")}, ${strategy.data_source ?? strategy.optimizer})`
  }
  return `${code}${strategy.id}`
}

export interface StrategyCatalog {
  strategies: StrategyInfo[]
  backtestStrategies: StrategyInfo[]
  researchStrategies: StrategyInfo[]
  isError: boolean
  loadError: boolean
}

export function useStrategyCatalog(market: MarketProfile): StrategyCatalog {
  const { data: stratData, isError: strategiesError } = useQuery({
    queryKey: ["v2", "strategies", market],
    queryFn: () => api.get<{ strategies: StrategyInfo[] }>(`/strategies?market=${market}`),
    staleTime: STALE_TIME.SHORT,
  })
  const { data: researchStratData } = useQuery({
    queryKey: ["v2", "strategies", "research-screeners"],
    queryFn: () => api.get<{ strategies: StrategyInfo[] }>("/strategies"),
    staleTime: STALE_TIME.SHORT,
  })

  const marketStrategies = stratData?.strategies ?? []
  const globalResearchStrategies = (researchStratData?.strategies ?? []).filter(isResearchArtifactStrategy)
  const marketResearchStrategies = marketStrategies.filter(isResearchArtifactStrategy)
  const backtestStrategies = marketStrategies.filter(isRunnableStrategy)
  const researchStrategies = marketResearchStrategies.length > 0 ? marketResearchStrategies : globalResearchStrategies

  const strategies = useMemo(() => {
    const byId = new Map<string, StrategyInfo>()
    for (const strategy of [...researchStrategies, ...backtestStrategies]) {
      byId.set(strategy.id, strategy)
    }
    return [...byId.values()]
  }, [researchStrategies, backtestStrategies])

  return {
    strategies,
    backtestStrategies,
    researchStrategies,
    isError: strategiesError,
    loadError: strategiesError && marketStrategies.length === 0,
  }
}
