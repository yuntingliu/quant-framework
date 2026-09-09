import { useQuery } from "@tanstack/react-query"
import { api, buildParams } from "@/lib/api"
import { STALE_TIME } from "@/lib/constants"
import type {
  FactorPerformance,
  CorrelationMatrix,
  RollingCorrelation,
  FactorDistribution,
  FactorAnnualReturns,
} from "@/types/factors"


export function useFactorPerformance(
  start?: string,
  end?: string,
  factors?: string[]
) {
  return useQuery({
    queryKey: ["factors", "performance", start, end, factors],
    queryFn: () =>
      api.get<FactorPerformance>(
        `/factors/performance${buildParams({
          start,
          end,
          factors: factors?.join(","),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useCorrelation(
  start?: string,
  end?: string,
  factors?: string[]
) {
  return useQuery({
    queryKey: ["factors", "correlation", start, end, factors],
    queryFn: () =>
      api.get<CorrelationMatrix>(
        `/factors/correlation${buildParams({
          start,
          end,
          factors: factors?.join(","),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useRollingCorrelation(
  start?: string,
  end?: string,
  factors?: string[],
  window?: number
) {
  return useQuery({
    queryKey: ["factors", "rolling-correlation", start, end, factors, window],
    queryFn: () =>
      api.get<RollingCorrelation>(
        `/factors/rolling-correlation${buildParams({
          start,
          end,
          factors: factors?.join(","),
          window: window?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useFactorDistribution(
  start?: string,
  end?: string,
  factors?: string[]
) {
  return useQuery({
    queryKey: ["factors", "distribution", start, end, factors],
    queryFn: () =>
      api.get<FactorDistribution>(
        `/factors/distribution${buildParams({
          start,
          end,
          factors: factors?.join(","),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}

export function useFactorAnnualReturns(
  start?: string,
  end?: string,
  factors?: string[]
) {
  return useQuery({
    queryKey: ["factors", "annual-returns", start, end, factors],
    queryFn: () =>
      api.get<FactorAnnualReturns>(
        `/factors/annual-returns${buildParams({
          start,
          end,
          factors: factors?.join(","),
        })}`
      ),
    staleTime: STALE_TIME.SHORT,
  })
}
