/**
 * useBacktestRunner — shared V2 async-job runner for backtest widgets.
 *
 * Owns the run mutation (POST /backtest-jobs), the valuation/flow research-screener
 * mutation, the job-status polling query, the elapsed timer, and the settle effect
 * (toasts, query invalidation, and workspace selection writes). Both the Runner and
 * the Workbench consume this so the run/poll machinery exists in exactly one place.
 *
 * Note: distinct from `useBacktestRunMutation` in use-backtest-data.ts, which targets
 * the legacy `/backtest/run` vol-timing endpoint — do not merge them.
 */
import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { api } from "@/lib/api"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { useLanguage } from "@/contexts/LanguageContext"
import type { MarketProfile } from "@/hooks/use-strategy-catalog"

export interface BacktestJobResult {
  backtest_id: string
  metrics: {
    annual_return: number
    sharpe: number
    max_drawdown: number
    calmar: number
  }
  n_months: number
  run_status?: string
  quality_gate?: { status?: string; summary?: string; failed_checks?: string[] }
  live_candidate_status?: { status?: string; reason?: string }
  order_preview_status?: { status?: string; latest_target_count?: number }
}

export interface BacktestJob {
  job_id: string
  strategy_id: string
  status: "queued" | "running" | "succeeded" | "failed"
  progress: number
  message?: string
  backtest_id?: string | null
  result?: BacktestJobResult | null
  error?: string | null
  latest_event?: {
    event?: string
    message?: string
    current?: number
    total?: number
    target_count?: number
    data_coverage?: number
  }
}

interface ResearchScreenerResult {
  status: string
  elapsed_seconds?: number | null
  combined?: unknown[]
}

export interface RunBacktestArgs {
  strategyId: string
  startDate: string
  endDate: string
  market: MarketProfile
  notes?: string
  isResearchScreener: boolean
}

export interface UseBacktestRunner {
  run: (args: RunBacktestArgs) => void
  isRunning: boolean
  progress: number
  elapsedSeconds: number
  activeJob: BacktestJob | null
  message: string | undefined
  error: Error | null
}

export function useBacktestRunner(opts?: {
  onCompleted?: (result: BacktestJobResult, strategyId: string) => void
}): UseBacktestRunner {
  const queryClient = useQueryClient()
  const { setSelectedStrategy, setSelectedBacktest } = useWorkspace()
  const { t } = useLanguage()

  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  const runMutation = useMutation({
    mutationFn: (payload: { strategy_id: string; start_date: string; end_date: string; notes?: string; market: string }) =>
      api.post<BacktestJob>("/backtest-jobs", payload),
    onSuccess: (data) => {
      setActiveJobId(data.job_id)
    },
    onError: (err) => {
      toast.error(t("backtest.runner.failed"), {
        description: (err as Error)?.message || t("backtest.runner.checkConfig"),
      })
      setRunStartedAt(null)
    },
  })

  const runResearchMutation = useMutation({
    mutationFn: () => api.post<ResearchScreenerResult>("/research/valuation-flow/run", {}),
    onSuccess: (data) => {
      setSelectedStrategy("valuation_flow_stock_picks")
      setSelectedBacktest(null)
      queryClient.invalidateQueries({ queryKey: ["research", "valuation-flow"] })
      toast.success(t("research.valuationFlow.ready"), {
        description: data.elapsed_seconds != null
          ? `${t("research.valuationFlow.elapsed")}: ${data.elapsed_seconds.toFixed(1)} ${t("research.valuationFlow.seconds")}`
          : undefined,
      })
    },
    onError: (err) => {
      toast.error(t("backtest.runner.failed"), {
        description: (err as Error)?.message || "Valuation/flow screener failed",
      })
    },
    onSettled: () => {
      setRunStartedAt(null)
    },
  })

  const { data: activeJob } = useQuery({
    queryKey: ["v2", "backtest-jobs", activeJobId],
    queryFn: () => api.get<BacktestJob>(`/backtest-jobs/${activeJobId}`),
    enabled: !!activeJobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === "succeeded" || status === "failed" ? false : 1500
    },
  })

  useEffect(() => {
    if (!activeJob) return
    if (activeJob.status === "succeeded" && activeJob.result) {
      const data = activeJob.result
      setSelectedStrategy(activeJob.strategy_id)
      setSelectedBacktest(data.backtest_id)
      setActiveJobId(null)
      setRunStartedAt(null)
      queryClient.invalidateQueries({ queryKey: ["v2", "backtests"] })
      toast.success(t("backtest.runner.completed"), {
        description: `${t("backtest.runner.annualReturn")} ${(data.metrics.annual_return * 100).toFixed(1)}% · ${t("backtest.runner.sharpe")} ${data.metrics.sharpe.toFixed(2)} · ${t("backtest.runner.drawdown")} ${(data.metrics.max_drawdown * 100).toFixed(1)}% · ${data.quality_gate?.status ?? "gate"}`,
      })
      opts?.onCompleted?.(data, activeJob.strategy_id)
    } else if (activeJob.status === "failed") {
      setActiveJobId(null)
      setRunStartedAt(null)
      toast.error(t("backtest.runner.failed"), {
        description: activeJob.error || activeJob.message || t("backtest.runner.checkConfig"),
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeJob, queryClient, setSelectedBacktest, setSelectedStrategy, t])

  const isRunning = runMutation.isPending || !!activeJobId || runResearchMutation.isPending

  useEffect(() => {
    if (!isRunning || !runStartedAt) {
      setElapsedSeconds(0)
      return
    }
    const tick = () => setElapsedSeconds(Math.floor((Date.now() - runStartedAt) / 1000))
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [isRunning, runStartedAt])

  const run = (args: RunBacktestArgs) => {
    if (!args.strategyId || isRunning) return
    setRunStartedAt(Date.now())
    if (args.isResearchScreener) {
      setSelectedStrategy("valuation_flow_stock_picks")
      setSelectedBacktest(null)
      runResearchMutation.mutate()
      return
    }
    setSelectedStrategy(args.strategyId)
    setSelectedBacktest(null)
    runMutation.mutate({
      strategy_id: args.strategyId,
      start_date: args.startDate,
      end_date: args.endDate,
      notes: args.notes || undefined,
      market: args.market,
    })
  }

  return {
    run,
    isRunning,
    progress: activeJob?.progress ?? 0,
    elapsedSeconds,
    activeJob: activeJob ?? null,
    message: activeJob?.message,
    error: (runMutation.error as Error | null) ?? (runResearchMutation.error as Error | null),
  }
}
