import { useQuery } from "@tanstack/react-query"
import { api, buildParams } from "@/lib/api"
import { STALE_TIME } from "@/lib/constants"
import type { DataProfile } from "@/lib/data-profile"
import type {
  MarketKPI,
  CumulativeReturns,
  AnnualReturns,
  FactorStats,
  DrawdownData,
  VolatilityData,
  CorrelationData,
  IndexBoard,
  BreadthData,
  SectorHeatmap,
  MoneyFlowData,
  MarketKlineData,
  MarketKlinePeriod,
} from "@/types/market"


export function useMarketKPI(profile: DataProfile, start?: string, end?: string) {
  return useQuery({
    queryKey: ["market", "kpi", profile, start, end],
    queryFn: () =>
      api.get<MarketKPI>(`/market/kpi${buildParams({ profile, start, end })}`),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useCumulativeReturns(
  profile: DataProfile,
  start?: string,
  end?: string,
  factors?: string[]
) {
  return useQuery({
    queryKey: ["market", "cumulative-returns", profile, start, end, factors],
    queryFn: () =>
      api.get<CumulativeReturns>(
        `/market/cumulative-returns${buildParams({
          start,
          end,
          profile,
          factors: factors?.join(","),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useAnnualReturns(
  profile: DataProfile,
  start?: string,
  end?: string,
  factors?: string[]
) {
  return useQuery({
    queryKey: ["market", "annual-returns", profile, start, end, factors],
    queryFn: () =>
      api.get<AnnualReturns>(
        `/market/annual-returns${buildParams({
          start,
          end,
          profile,
          factors: factors?.join(","),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useFactorStats(profile: DataProfile, start?: string, end?: string) {
  return useQuery({
    queryKey: ["market", "factor-stats", profile, start, end],
    queryFn: () =>
      api.get<FactorStats>(
        `/market/factor-stats${buildParams({ profile, start, end })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useDrawdowns(
  profile: DataProfile,
  start?: string,
  end?: string,
  topN?: number,
) {
  return useQuery({
    queryKey: ["market", "drawdowns", profile, start, end, topN],
    queryFn: () =>
      api.get<DrawdownData>(
        `/market/drawdowns${buildParams({
          profile,
          start,
          end,
          top_n: topN?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useVolatilityAnalysis(
  profile: DataProfile,
  start?: string,
  end?: string,
) {
  return useQuery({
    queryKey: ["market", "volatility", profile, start, end],
    queryFn: () =>
      api.get<VolatilityData>(
        `/market/volatility${buildParams({ profile, start, end })}`,
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useMarketCorrelation(
  profile: DataProfile,
  start?: string,
  end?: string,
  factors?: string[],
) {
  return useQuery({
    queryKey: ["market", "correlation", profile, start, end, factors],
    queryFn: () =>
      api.get<CorrelationData>(
        `/market/correlation${buildParams({
          profile,
          start,
          end,
          factors: factors?.join(","),
        })}`,
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

// ---------------------------------------------------------------------------
// Live market overview (Home page)
// ---------------------------------------------------------------------------

/** Live index board: A-share indices plus external market slots. */
export function useIndexBoard() {
  return useQuery({
    queryKey: ["market", "overview", "index-board"],
    queryFn: () => api.get<IndexBoard>("/market/overview/index-board"),
    staleTime: 0,
    refetchInterval: 6_000,
  })
}

/** A-share advance/decline breadth + top movers. */
export function useBreadth(topN = 10) {
  return useQuery({
    queryKey: ["market", "overview", "breadth", topN],
    queryFn: () =>
      api.get<BreadthData>(
        `/market/overview/breadth${buildParams({ top_n: String(topN) })}`
      ),
    staleTime: 0,
    refetchInterval: 8_000,
  })
}

/** Per-industry mean change % heatmap. */
export function useSectors() {
  return useQuery({
    queryKey: ["market", "overview", "sectors"],
    queryFn: () => api.get<SectorHeatmap>("/market/overview/sectors"),
    staleTime: 0,
    refetchInterval: 12_000,
  })
}

/** A-share turnover-based money-flow proxy. */
export function useMoneyFlow(topN = 10) {
  return useQuery({
    queryKey: ["market", "overview", "money-flow", topN],
    queryFn: () =>
      api.get<MoneyFlowData>(
        `/market/overview/money-flow${buildParams({ top_n: String(topN) })}`
      ),
    staleTime: 0,
    refetchInterval: 10_000,
  })
}

/** On-demand A-share/index bars for the Home market-detail panel. */
export function useMarketKline(symbol: string, period: MarketKlinePeriod = "1d", count = 300) {
  return useQuery({
    queryKey: ["market", "overview", "kline", symbol, period, count],
    queryFn: () =>
      api.get<MarketKlineData>(
        `/market/overview/kline/${encodeURIComponent(symbol)}${buildParams({
          period,
          count: String(count),
        })}`
      ),
    enabled: Boolean(symbol),
    staleTime: 0,
    refetchInterval: period === "1d" ? 30_000 : 10_000,
  })
}
