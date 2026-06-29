import { useQuery, useMutation } from "@tanstack/react-query"
import { api, buildParams } from "@/lib/api"
import { STALE_TIME } from "@/lib/constants"
import type { OptimizerResult, EfficientFrontier } from "@/types/optimizer"


export function useOptimizerRun(
  models?: string[],
  nStocks?: number,
  maxWeight?: number
) {
  return useQuery({
    queryKey: ["optimizer", "run", models, nStocks, maxWeight],
    queryFn: () =>
      api.get<OptimizerResult>(
        `/optimizer/run${buildParams({
          models: models?.join(","),
          n_stocks: nStocks?.toString(),
          max_weight: maxWeight?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.LONG,
    enabled: !!models && models.length > 0,
  })
}

interface OptimizerRunParams {
  models: string[]
  n_stocks?: number
  max_weight?: number
  risk_aversion?: number
  mkt_neutral?: boolean
  smb_neutral?: boolean
  hml_neutral?: boolean
}

export function useOptimizerRunMutation() {
  return useMutation({
    mutationFn: (params: OptimizerRunParams) =>
      api.post<OptimizerResult>("/optimizer/run", params),
  })
}

export function useEfficientFrontier(
  nStocks?: number,
  maxWeight?: number,
  nPoints?: number
) {
  return useQuery({
    queryKey: ["optimizer", "frontier", nStocks, maxWeight, nPoints],
    queryFn: () =>
      api.get<EfficientFrontier>(
        `/optimizer/frontier${buildParams({
          n_stocks: nStocks?.toString(),
          max_weight: maxWeight?.toString(),
          n_points: nPoints?.toString(),
        })}`
      ),
    staleTime: STALE_TIME.LONG,
  })
}

interface EfficientFrontierParams {
  n_stocks: number
  max_weight: number
  n_points?: number
}

export function useEfficientFrontierMutation() {
  return useMutation({
    mutationFn: (params: EfficientFrontierParams) =>
      api.post<EfficientFrontier>("/optimizer/efficient-frontier", params),
  })
}
