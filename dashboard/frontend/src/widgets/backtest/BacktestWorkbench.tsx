import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Play, RefreshCw, ShieldCheck, Workflow } from "lucide-react"

import { CumulativeReturnsChart, DrawdownChart } from "@/components/charts"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import {
  api,
  type BacktestAnalysis,
  type BacktestRecord,
  type BacktestRobustness,
  type BacktestRunResult,
  type DataManifest,
  type ProviderStatus,
  type RuntimeCatalog,
  type ResearchRun,
  type StrategyTemplate,
} from "@/lib/api"
import { useDataProfile, type DataProfile } from "@/lib/data-profile"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"
import { analyticsError } from "@/widgets/market/analytics-utils"
import { BacktestCompareWidget } from "./BacktestCompare"

type WorkbenchTab = "performance" | "robustness" | "holdings"
type WorkbenchView = "inspect" | "compare"

function metric(value: number | null | undefined, kind: "pct" | "number"): string {
  if (value == null) return "—"
  return kind === "pct" ? formatPercent(value, 1) : formatNumber(value, 2)
}

export function BacktestWorkbenchWidget() {
  const { selectedStrategy, setSelectedStrategy, selectedBacktest, setSelectedBacktest } = useWorkspace()
  const [profile, setProfile] = useDataProfile()
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [records, setRecords] = useState<BacktestRecord[]>([])
  const [strategyId, setStrategyId] = useState(selectedStrategy ?? "balanced")
  const [selectedId, setSelectedId] = useState(selectedBacktest ?? "")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [setupError, setSetupError] = useState("")
  const [running, setRunning] = useState(false)
  const [researching, setResearching] = useState(false)
  const [researchMessage, setResearchMessage] = useState("")
  const [tab, setTab] = useState<WorkbenchTab>("performance")
  const [view, setView] = useState<WorkbenchView>("inspect")
  const [holdingDate, setHoldingDate] = useState("")

  useEffect(() => {
    setSetupError("")
    setSelectedId("")
    Promise.all([
      api.get<StrategyTemplate[]>("/strategies"),
      api.get<DataManifest>("/data/manifest"),
      api.get<ProviderStatus>("/data/providers"),
      api.get<RuntimeCatalog>("/data-sync/catalog"),
      api.get<BacktestRecord[]>("/backtests?limit=100"),
    ])
      .then(([templates, manifest, providers, catalog, saved]) => {
        setStrategies(templates)
        const nextStrategy = templates.some((item) => item.id === "balanced")
            ? "balanced"
            : templates[0]?.id ?? ""
        setStrategyId(nextStrategy)
        setSelectedStrategy(nextStrategy || null)
        const matching = saved.filter((item) => item.profile === profile)
        setRecords(matching)
        const nextBacktest = matching[0]?.id ?? ""
        setSelectedId(nextBacktest)
        setSelectedBacktest(nextBacktest || null)
        if (profile === "demo") {
          setStartDate(manifest.sample_start)
          setEndDate(manifest.cutoff_date)
          return
        }
        const runtimeBars = catalog.datasets.find((dataset) => dataset.id === "rq.bars")
        if (providers.profiles.runtime.status === "ready" && runtimeBars?.date_start && runtimeBars.date_end) {
          setStartDate(runtimeBars.date_start)
          setEndDate(runtimeBars.date_end)
        } else {
          setStartDate("")
          setEndDate("")
          setSetupError("Runtime data is not ready. Complete an RQ sync in Data Workbench.")
        }
      })
      .catch((error: Error) => setSetupError(error.message))
  }, [profile, setSelectedBacktest, setSelectedStrategy])

  useEffect(() => {
    if (selectedStrategy && selectedStrategy !== strategyId && strategies.some((item) => item.id === selectedStrategy)) {
      setStrategyId(selectedStrategy)
    }
  }, [selectedStrategy, strategies, strategyId])

  useEffect(() => {
    if (selectedBacktest && selectedBacktest !== selectedId && records.some((item) => item.id === selectedBacktest)) {
      setSelectedId(selectedBacktest)
    }
  }, [records, selectedBacktest, selectedId])

  const analysis = useQuery({
    queryKey: ["backtests", "analysis", selectedId],
    queryFn: () => api.get<BacktestAnalysis>(`/backtests/${selectedId}/analysis`),
    enabled: Boolean(selectedId),
  })
  const robustness = useQuery({
    queryKey: ["backtests", "robustness", selectedId],
    queryFn: () => api.get<BacktestRobustness>(`/backtests/${selectedId}/robustness`),
    enabled: Boolean(selectedId),
  })

  useEffect(() => {
    const latest = analysis.data?.holdings.at(-1)?.date ?? ""
    setHoldingDate(latest)
  }, [analysis.data])

  async function runBacktest() {
    setRunning(true)
    setSetupError("")
    try {
      const result = await api.post<BacktestRunResult>("/backtests/run", {
        strategy_id: strategyId,
        start_date: startDate,
        end_date: endDate,
        profile,
      })
      const saved = await api.get<BacktestRecord[]>("/backtests?limit=100")
      setRecords(saved.filter((item) => item.profile === profile))
      setSelectedId(result.id)
      setSelectedStrategy(strategyId)
      setSelectedBacktest(result.id)
      setTab("performance")
    } catch (error) {
      setSetupError(error instanceof Error ? error.message : String(error))
    } finally {
      setRunning(false)
    }
  }

  async function runResearch() {
    setResearching(true)
    setSetupError("")
    setResearchMessage("Research run queued")
    try {
      let run = await api.post<ResearchRun>("/research/runs", {
        strategy_id: strategyId,
        start_date: startDate,
        end_date: endDate,
        profile,
        account_id: "paper",
      })
      while (run.status === "queued" || run.status === "running") {
        const completed = run.steps.filter((step) => step.status === "succeeded").length
        setResearchMessage(`${run.status} · ${completed}/${run.steps.length} steps`)
        await new Promise((resolve) => window.setTimeout(resolve, 500))
        run = await api.get<ResearchRun>(`/research/runs/${run.id}`)
      }
      if (run.status !== "succeeded" || !run.result) {
        throw new Error(run.error || `Research run ${run.status}`)
      }
      const saved = await api.get<BacktestRecord[]>("/backtests?limit=100")
      setRecords(saved.filter((item) => item.profile === profile))
      setSelectedId(run.result.backtest_id)
      setSelectedStrategy(strategyId)
      setSelectedBacktest(run.result.backtest_id)
      setTab("robustness")
      setResearchMessage(
        `${run.result.robustness_status} · paper rebalance awaits confirmation`,
      )
    } catch (error) {
      setSetupError(error instanceof Error ? error.message : String(error))
    } finally {
      setResearching(false)
    }
  }

  const equityData = useMemo(
    () =>
      (analysis.data?.dates ?? []).map((date, index) => {
        const point: Record<string, string | number> = { date }
        const value = analysis.data?.equity_curve[index]
        if (value != null) point.strategy = value
        return point
      }),
    [analysis.data],
  )
  const drawdownData = useMemo(
    () =>
      (analysis.data?.dates ?? []).map((date, index) => ({
        date,
        strategy: analysis.data?.drawdown[index] ?? 0,
      })),
    [analysis.data],
  )
  const snapshot = analysis.data?.holdings.find((item) => item.date === holdingDate)
    ?? analysis.data?.holdings.at(-1)
  const executions = analysis.data?.executions ?? []
  const executionCost = executions.reduce((total, item) => total + item.total_cost, 0)
  const constrainedPeriods = executions.filter((item) => item.constrained_symbols.length > 0).length
  const averageCash = executions.length
    ? executions.reduce((total, item) => total + item.cash_weight, 0) / executions.length
    : null
  const provenance = analysis.data?.provenance

  return (
    <Widget
      title="Backtest Workbench"
      loading={view === "inspect" && analysis.isLoading}
      error={view === "inspect" ? analyticsError(analysis.error) : undefined}
      onRetry={() => analysis.refetch()}
      actions={
        <button
          className="icon-command"
          type="button"
          title="Refresh saved result"
          onClick={() => analysis.refetch()}
          disabled={!selectedId}
        >
          <RefreshCw aria-hidden="true" />
        </button>
      }
      bodyPadding="none"
    >
      <div className="workbench-tabs" role="tablist" aria-label="Backtest workbench mode">
        <button type="button" role="tab" aria-selected={view === "inspect"} onClick={() => setView("inspect")}>Run &amp; Inspect</button>
        <button type="button" role="tab" aria-selected={view === "compare"} onClick={() => setView("compare")}>Compare</button>
      </div>
      {view === "compare" ? <BacktestCompareWidget /> : <>
      <div className="workbench-controls">
        <select
          aria-label="Backtest data profile"
          value={profile}
          onChange={(event) => setProfile(event.target.value as DataProfile)}
        >
          <option value="demo">Demo</option>
          <option value="runtime">Local RQ</option>
        </select>
        <select
          aria-label="Strategy"
          value={strategyId}
          onChange={(event) => {
            setStrategyId(event.target.value)
            setSelectedStrategy(event.target.value)
          }}
        >
          {strategies.map((strategy) => (
            <option key={strategy.id} value={strategy.id}>{strategy.name}</option>
          ))}
        </select>
        <input
          aria-label="Backtest start date"
          type="date"
          value={startDate}
          onChange={(event) => setStartDate(event.target.value)}
        />
        <input
          aria-label="Backtest end date"
          type="date"
          value={endDate}
          onChange={(event) => setEndDate(event.target.value)}
        />
        <button
          className="primary-command"
          type="button"
          onClick={runBacktest}
          disabled={running || !startDate || !endDate}
        >
          <Play aria-hidden="true" />
          {running ? "Running" : "Run"}
        </button>
        <button
          className="secondary-command"
          type="button"
          onClick={runResearch}
          disabled={researching || running || !startDate || !endDate}
        >
          <Workflow aria-hidden="true" />
          {researching ? "Studying" : "Study"}
        </button>
        <select
          aria-label="Saved backtest"
          value={selectedId}
          onChange={(event) => {
            setSelectedId(event.target.value)
            setSelectedBacktest(event.target.value || null)
          }}
        >
          <option value="">Saved run</option>
          {records.map((record) => (
            <option key={record.id} value={record.id}>
              {record.strategy_id} · {record.id.slice(0, 6)}
            </option>
          ))}
        </select>
      </div>
      {setupError && <div className="workbench-message error">{setupError}</div>}
      {researchMessage && !setupError && (
        <div className="workbench-message research-message">{researchMessage}</div>
      )}
      {!selectedId && !setupError ? (
        <div className="analytics-empty">No persisted {profile} backtests.</div>
      ) : analysis.data ? (
        <>
          <div className="workbench-tabs" role="tablist" aria-label="Backtest result view">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "performance"}
              onClick={() => setTab("performance")}
            >
              Performance
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "robustness"}
              onClick={() => setTab("robustness")}
            >
              Robustness
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "holdings"}
              onClick={() => setTab("holdings")}
            >
              Holdings
            </button>
          </div>
          {tab === "performance" ? (
            <div className="workbench-body">
              <div className="analytics-kpi-grid workbench-kpis">
                <div className="analytics-kpi"><span>Total return</span><strong>{metric(analysis.data.metrics.total_return, "pct")}</strong></div>
                <div className="analytics-kpi"><span>Annual return</span><strong>{metric(analysis.data.metrics.annual_return, "pct")}</strong></div>
                <div className="analytics-kpi"><span>Volatility</span><strong>{metric(analysis.data.metrics.annual_vol, "pct")}</strong></div>
                <div className="analytics-kpi"><span>Sharpe</span><strong>{metric(analysis.data.metrics.sharpe, "number")}</strong></div>
                <div className="analytics-kpi"><span>Max drawdown</span><strong>{metric(analysis.data.metrics.max_drawdown, "pct")}</strong></div>
                <div className="analytics-kpi"><span>Avg turnover</span><strong>{metric(analysis.data.average_turnover, "pct")}</strong></div>
              </div>
              <div className="detail-strip">
                <span>{executions[0]?.execution_price?.replace("_", " ") ?? "saved execution"} · {executions.length} periods</span>
                <span>Total modeled cost {metric(executionCost, "pct")}</span>
                <span>Average cash {metric(averageCash, "pct")}</span>
                <span>{constrainedPeriods} liquidity-constrained periods</span>
                {provenance?.data?.aggregate_sha256 && (
                  <span className="font-mono" title={provenance.data.aggregate_sha256}>data {provenance.data.aggregate_sha256.slice(0, 12)}</span>
                )}
                {provenance?.code?.commit && (
                  <span className="font-mono" title={provenance.code.commit}>code {provenance.code.commit.slice(0, 10)}{provenance.code.dirty ? "-dirty" : ""}</span>
                )}
                {provenance?.code?.source_sha256 && (
                  <span className="font-mono" title={provenance.code.source_sha256}>source {provenance.code.source_sha256.slice(0, 12)}</span>
                )}
              </div>
              <CumulativeReturnsChart
                data={equityData}
                series={[{ key: "strategy", name: analysis.data.strategy_id }]}
                height={250}
              />
              <DrawdownChart data={drawdownData} height={190} />
            </div>
          ) : tab === "robustness" ? (
            <div className="workbench-body">
              {robustness.isLoading ? (
                <div className="analytics-empty">Evaluating benchmark and research gates…</div>
              ) : robustness.error ? (
                <div className="workbench-message error">{analyticsError(robustness.error)}</div>
              ) : robustness.data ? (
                <>
                  <div className="robustness-header">
                    <span className={`research-status ${robustness.data.status}`}>
                      <ShieldCheck aria-hidden="true" />
                      {robustness.data.status}
                    </span>
                    <span>{robustness.data.disclaimer}</span>
                  </div>
                  <div className="analytics-kpi-grid workbench-kpis">
                    <div className="analytics-kpi">
                      <span>Annual excess</span>
                      <strong>{metric(robustness.data.metrics.excess.annual_return, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>Benchmark annual</span>
                      <strong>{metric(robustness.data.metrics.benchmark.annual_return, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>Benchmark coverage</span>
                      <strong>{metric(robustness.data.benchmark_coverage, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>Avg turnover</span>
                      <strong>{metric(robustness.data.turnover.average, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>Validation excess</span>
                      <strong>{metric(robustness.data.validation.validation.excess?.annual_return, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>Adjusted p-value</span>
                      <strong>{metric(robustness.data.statistical.adjusted_p_value, "number")}</strong>
                    </div>
                  </div>
                  <div className="detail-strip">
                    <span>Validation from {robustness.data.validation.split_date ?? "--"}</span>
                    <span>{robustness.data.validation.validation.periods ?? 0} holdout periods</span>
                    <span>
                      Bootstrap mean excess 95% [{metric(robustness.data.statistical.bootstrap_mean_excess_95.lower, "pct")}, {metric(robustness.data.statistical.bootstrap_mean_excess_95.upper, "pct")}]
                    </span>
                    <span>{robustness.data.statistical.research_trials} declared trials</span>
                  </div>
                  <div className="robustness-grid">
                    <section>
                      <h3>Integrity checks</h3>
                      {robustness.data.checks.map((check) => (
                        <div className="gate-row" key={check.name}>
                          <span className={check.passed ? "gate-pass" : "gate-fail"}>
                            {check.passed ? "PASS" : "FAIL"}
                          </span>
                          <strong>{check.name.replace(/_/g, " ")}</strong>
                          <small>{check.detail}</small>
                        </div>
                      ))}
                    </section>
                    <section>
                      <h3>Candidate rules</h3>
                      {Object.entries(robustness.data.candidate_rules).map(([name, passed]) => (
                        <div className="gate-row" key={name}>
                          <span className={passed ? "gate-pass" : "gate-watch"}>
                            {passed ? "PASS" : "WATCH"}
                          </span>
                          <strong>{name.replace(/_/g, " ")}</strong>
                        </div>
                      ))}
                    </section>
                  </div>
                  <div className="analytics-table-wrap">
                    <table className="analytics-table compact">
                      <thead>
                        <tr><th>Cost bps</th><th>Annual return</th><th>Sharpe</th><th>Max drawdown</th></tr>
                      </thead>
                      <tbody>
                        {Object.entries(robustness.data.cost_sensitivity).map(([cost, values]) => (
                          <tr key={cost}>
                            <td>{cost}</td>
                            <td>{metric(values.annual_return, "pct")}</td>
                            <td>{metric(values.sharpe, "number")}</td>
                            <td>{metric(values.max_drawdown, "pct")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : null}
            </div>
          ) : (
            <div className="workbench-body">
              <div className="holdings-toolbar">
                <select
                  aria-label="Holding date"
                  value={snapshot?.date ?? ""}
                  onChange={(event) => setHoldingDate(event.target.value)}
                >
                  {analysis.data.holdings.map((item) => (
                    <option key={item.date} value={item.date}>{item.date}</option>
                  ))}
                </select>
                <span>{snapshot?.holdings_count ?? 0} names</span>
                <span>Gross {metric(snapshot?.gross_exposure, "pct")}</span>
                <span>Max {metric(snapshot?.max_weight, "pct")}</span>
                <span>HHI {metric(snapshot?.concentration, "number")}</span>
              </div>
              <div className="analytics-table-wrap">
                <table className="analytics-table compact">
                  <thead><tr><th>Symbol</th><th>Weight</th></tr></thead>
                  <tbody>
                    {(snapshot?.top_holdings ?? []).map((holding) => (
                      <tr key={holding.symbol}>
                        <td><strong>{holding.symbol}</strong></td>
                        <td>{formatPercent(holding.weight, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      ) : null}
      </>}
    </Widget>
  )
}
