import { useQuery } from "@tanstack/react-query"
import { api, buildParams } from "@/lib/api"
import { STALE_TIME } from "@/lib/constants"
import type {
  FactorAttribution,
  RollingExposure,
  StyleDrift,
  StressTest,
} from "@/types/risk"


export function useFactorAttribution(
  timingMethod?: string,
  volTarget?: number,
  startYear?: number,
  endYear?: number,
  rollingWindow?: number
) {
  return useQuery({
    queryKey: [
      "risk",
      "attribution",
      timingMethod,
      volTarget,
      startYear,
      endYear,
      rollingWindow,
    ],
    queryFn: () =>
      api.get<FactorAttribution>(
        `/risk/attribution${buildParams({
          timing_method: timingMethod,
          vol_target: volTarget?.toString(),
          start_year: startYear?.toString(),
          end_year: endYear?.toString(),
          rolling_window: rollingWindow?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.LONG,
  })
}

export function useRollingExposure(
  timingMethod?: string,
  volTarget?: number,
  startYear?: number,
  endYear?: number,
  rollingWindow?: number
) {
  return useQuery({
    queryKey: [
      "risk",
      "rolling-exposure",
      timingMethod,
      volTarget,
      startYear,
      endYear,
      rollingWindow,
    ],
    queryFn: () =>
      api.get<RollingExposure>(
        `/risk/rolling-exposure${buildParams({
          timing_method: timingMethod,
          vol_target: volTarget?.toString(),
          start_year: startYear?.toString(),
          end_year: endYear?.toString(),
          rolling_window: rollingWindow?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.LONG,
  })
}

export function useStyleDrift(
  timingMethod?: string,
  volTarget?: number,
  startYear?: number,
  endYear?: number,
  rollingWindow?: number
) {
  return useQuery({
    queryKey: [
      "risk",
      "style-drift",
      timingMethod,
      volTarget,
      startYear,
      endYear,
      rollingWindow,
    ],
    queryFn: () =>
      api.get<StyleDrift>(
        `/risk/style-drift${buildParams({
          timing_method: timingMethod,
          vol_target: volTarget?.toString(),
          start_year: startYear?.toString(),
          end_year: endYear?.toString(),
          rolling_window: rollingWindow?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.LONG,
  })
}

export function useStressTest(
  timingMethod?: string,
  volTarget?: number,
  startYear?: number,
  endYear?: number
) {
  return useQuery({
    queryKey: [
      "risk",
      "stress-test",
      timingMethod,
      volTarget,
      startYear,
      endYear,
    ],
    queryFn: () =>
      api.get<StressTest>(
        `/risk/stress-test${buildParams({
          timing_method: timingMethod,
          vol_target: volTarget?.toString(),
          start_year: startYear?.toString(),
          end_year: endYear?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.LONG,
  })
}
