import { useQuery, useMutation } from "@tanstack/react-query"
import { api, buildParams } from "@/lib/api"
import { STALE_TIME } from "@/lib/constants"
import type { BacktestComparison, BacktestResult } from "@/types/backtest"


export function useBacktestComparison(
  volTarget?: number,
  startYear?: number,
  endYear?: number
) {
  return useQuery({
    queryKey: ["backtest", "comparison", volTarget, startYear, endYear],
    queryFn: () =>
      api.get<BacktestComparison>(
        `/backtest/comparison${buildParams({
          vol_target: volTarget?.toString(),
          start_year: startYear?.toString(),
          end_year: endYear?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.LONG,
  })
}

export function useBacktestRun(
  timingMethod?: string,
  volTarget?: number,
  startYear?: number,
  endYear?: number
) {
  return useQuery({
    queryKey: [
      "backtest",
      "run",
      timingMethod,
      volTarget,
      startYear,
      endYear,
    ],
    queryFn: () =>
      api.get<BacktestResult>(
        `/backtest/run${buildParams({
          timing_method: timingMethod,
          vol_target: volTarget?.toString(),
          start_year: startYear?.toString(),
          end_year: endYear?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.LONG,
    enabled: !!timingMethod,
  })
}

interface BacktestRunParams {
  timing_method: string
  vol_target?: number
  start_year?: number
  end_year?: number
}

export function useBacktestRunMutation() {
  return useMutation({
    mutationFn: (params: BacktestRunParams) =>
      api.post<BacktestResult>("/backtest/run", params),
  })
}
