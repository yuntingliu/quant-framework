import { useQuery } from "@tanstack/react-query"
import { api, buildParams } from "@/lib/api"
import { STALE_TIME } from "@/lib/constants"
import type {
  MarketKPI,
  CumulativeReturns,
  AnnualReturns,
  FactorStats,
  DrawdownData,
  VolatilityData,
  IndexBoard,
  BreadthData,
  SectorHeatmap,
  MoneyFlowData,
  MarketKlineData,
  MarketKlinePeriod,
} from "@/types/market"


export function useMarketKPI(start?: string, end?: string) {
  return useQuery({
    queryKey: ["market", "kpi", start, end],
    queryFn: () =>
      api.get<MarketKPI>(`/market/kpi${buildParams({ start, end })}`),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useCumulativeReturns(
  start?: string,
  end?: string,
  factors?: string[]
) {
  return useQuery({
    queryKey: ["market", "cumulative-returns", start, end, factors],
    queryFn: () =>
      api.get<CumulativeReturns>(
        `/market/cumulative-returns${buildParams({
          start,
          end,
          factors: factors?.join(","),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useAnnualReturns(
  start?: string,
  end?: string,
  factors?: string[]
) {
  return useQuery({
    queryKey: ["market", "annual-returns", start, end, factors],
    queryFn: () =>
      api.get<AnnualReturns>(
        `/market/annual-returns${buildParams({
          start,
          end,
          factors: factors?.join(","),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useFactorStats(start?: string, end?: string) {
  return useQuery({
    queryKey: ["market", "factor-stats", start, end],
    queryFn: () =>
      api.get<FactorStats>(
        `/market/factor-stats${buildParams({ start, end })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useDrawdowns(topN?: number) {
  return useQuery({
    queryKey: ["market", "drawdowns", topN],
    queryFn: () =>
      api.get<DrawdownData>(
        `/market/drawdowns${buildParams({
          top_n: topN?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useVolatilityAnalysis() {
  return useQuery({
    queryKey: ["market", "volatility"],
    queryFn: () => api.get<VolatilityData>("/market/volatility"),
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
