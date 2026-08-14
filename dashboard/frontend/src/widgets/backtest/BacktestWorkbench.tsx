import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Play, RefreshCw, ShieldCheck, Workflow } from "lucide-react"

import { CumulativeReturnsChart, DrawdownChart } from "@/components/charts"
import { CSVExportButton } from "@/components/shared/CSVExportButton"
import { useLanguage } from "@/contexts/LanguageContext"
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
  const { language } = useLanguage()
  const copy = language === "zh" ? {
    title: "回测工作台",
    refresh: "刷新已保存的结果",
    mode: "回测工作台模式",
    inspect: "运行与查看",
    compare: "对比",
    profile: "回测数据画像",
    demo: "演示数据",
    runtime: "本地 RQ",
    strategy: "策略",
    startDate: "回测开始日期",
    endDate: "回测结束日期",
    running: "运行中",
    run: "运行",
    studying: "研究中",
    study: "研究",
    savedBacktest: "已保存的回测",
    savedRun: "回测记录",
    noBacktests: "没有已保存的回测。",
    resultView: "回测结果视图",
    performance: "收益",
    robustness: "稳健性",
    holdings: "持仓",
    totalReturn: "总收益",
    annualReturn: "年化收益",
    volatility: "波动率",
    maxDrawdown: "最大回撤",
    avgTurnover: "平均换手",
    savedExecution: "已保存执行记录",
    periods: "期",
    modeledCost: "模拟总成本",
    averageCash: "平均现金比例",
    constrainedPeriods: "个流动性受限期",
    data: "数据",
    code: "代码",
    source: "源码",
    exportSeries: "导出序列",
    evaluatingGates: "正在评估基准和研究闸门…",
    exportCosts: "导出成本敏感性",
    annualExcess: "年化超额",
    benchmarkAnnual: "基准年化收益",
    benchmarkCoverage: "基准覆盖率",
    validationExcess: "验证期超额",
    adjustedPValue: "校正后 p 值",
    validationFrom: "验证期开始于",
    holdoutPeriods: "个留出期",
    bootstrapExcess: "Bootstrap 平均超额 95%",
    declaredTrials: "次已声明试验",
    integrityChecks: "完整性检查",
    candidateRules: "候选规则",
    pass: "通过",
    fail: "失败",
    watch: "观察",
    costBps: "成本（bps）",
    holdingDate: "持仓日期",
    names: "只证券",
    gross: "总敞口",
    max: "最大权重",
    exportHoldings: "导出持仓",
    symbol: "证券代码",
    weight: "权重",
  } : {
    title: "Backtest Workbench",
    refresh: "Refresh saved result",
    mode: "Backtest workbench mode",
    inspect: "Run & Inspect",
    compare: "Compare",
    profile: "Backtest data profile",
    demo: "Demo",
    runtime: "Local RQ",
    strategy: "Strategy",
    startDate: "Backtest start date",
    endDate: "Backtest end date",
    running: "Running",
    run: "Run",
    studying: "Studying",
    study: "Study",
    savedBacktest: "Saved backtest",
    savedRun: "Saved run",
    noBacktests: "has no persisted backtests.",
    resultView: "Backtest result view",
    performance: "Performance",
    robustness: "Robustness",
    holdings: "Holdings",
    totalReturn: "Total return",
    annualReturn: "Annual return",
    volatility: "Volatility",
    maxDrawdown: "Max drawdown",
    avgTurnover: "Avg turnover",
    savedExecution: "saved execution",
    periods: "periods",
    modeledCost: "Total modeled cost",
    averageCash: "Average cash",
    constrainedPeriods: "liquidity-constrained periods",
    data: "data",
    code: "code",
    source: "source",
    exportSeries: "Export series",
    evaluatingGates: "Evaluating benchmark and research gates…",
    exportCosts: "Export costs",
    annualExcess: "Annual excess",
    benchmarkAnnual: "Benchmark annual",
    benchmarkCoverage: "Benchmark coverage",
    validationExcess: "Validation excess",
    adjustedPValue: "Adjusted p-value",
    validationFrom: "Validation from",
    holdoutPeriods: "holdout periods",
    bootstrapExcess: "Bootstrap mean excess 95%",
    declaredTrials: "declared trials",
    integrityChecks: "Integrity checks",
    candidateRules: "Candidate rules",
    pass: "PASS",
    fail: "FAIL",
    watch: "WATCH",
    costBps: "Cost bps",
    holdingDate: "Holding date",
    names: "names",
    gross: "Gross",
    max: "Max",
    exportHoldings: "Export holdings",
    symbol: "Symbol",
    weight: "Weight",
  }
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
  const performanceExportRows = useMemo(() => {
    const result = analysis.data
    if (!result) return []
    const turnoverByDate = new Map(result.turnover.map((item) => [item.date, item.value]))
    return result.dates.map((date, index) => ({
      date,
      return: result.returns[index],
      equity_curve: result.equity_curve[index],
      drawdown: result.drawdown[index],
      turnover: turnoverByDate.get(date) ?? null,
    }))
  }, [analysis.data])
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
      title={copy.title}
      loading={view === "inspect" && analysis.isLoading}
      error={view === "inspect" ? analyticsError(analysis.error) : undefined}
      onRetry={() => analysis.refetch()}
      actions={
        <button
          className="icon-command"
          type="button"
          title={copy.refresh}
          onClick={() => analysis.refetch()}
          disabled={!selectedId}
        >
          <RefreshCw aria-hidden="true" />
        </button>
      }
      bodyPadding="none"
    >
      <div className="workbench-tabs" role="tablist" aria-label={copy.mode}>
        <button type="button" role="tab" aria-selected={view === "inspect"} onClick={() => setView("inspect")}>{copy.inspect}</button>
        <button type="button" role="tab" aria-selected={view === "compare"} onClick={() => setView("compare")}>{copy.compare}</button>
      </div>
      {view === "compare" ? <BacktestCompareWidget /> : <>
      <div className="workbench-controls">
        <select
          aria-label={copy.profile}
          value={profile}
          onChange={(event) => setProfile(event.target.value as DataProfile)}
        >
          <option value="demo">{copy.demo}</option>
          <option value="runtime">{copy.runtime}</option>
        </select>
        <select
          aria-label={copy.strategy}
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
          aria-label={copy.startDate}
          type="date"
          value={startDate}
          onChange={(event) => setStartDate(event.target.value)}
        />
        <input
          aria-label={copy.endDate}
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
          {running ? copy.running : copy.run}
        </button>
        <button
          className="secondary-command"
          type="button"
          onClick={runResearch}
          disabled={researching || running || !startDate || !endDate}
        >
          <Workflow aria-hidden="true" />
          {researching ? copy.studying : copy.study}
        </button>
        <select
          aria-label={copy.savedBacktest}
          value={selectedId}
          onChange={(event) => {
            setSelectedId(event.target.value)
            setSelectedBacktest(event.target.value || null)
          }}
        >
          <option value="">{copy.savedRun}</option>
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
        <div className="analytics-empty">{profile} {copy.noBacktests}</div>
      ) : analysis.data ? (
        <>
          <div className="workbench-tabs" role="tablist" aria-label={copy.resultView}>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "performance"}
              onClick={() => setTab("performance")}
            >
              {copy.performance}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "robustness"}
              onClick={() => setTab("robustness")}
            >
              {copy.robustness}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "holdings"}
              onClick={() => setTab("holdings")}
            >
              {copy.holdings}
            </button>
          </div>
          {tab === "performance" ? (
            <div className="workbench-body">
              <div className="analytics-kpi-grid workbench-kpis">
                <div className="analytics-kpi"><span>{copy.totalReturn}</span><strong>{metric(analysis.data.metrics.total_return, "pct")}</strong></div>
                <div className="analytics-kpi"><span>{copy.annualReturn}</span><strong>{metric(analysis.data.metrics.annual_return, "pct")}</strong></div>
                <div className="analytics-kpi"><span>{copy.volatility}</span><strong>{metric(analysis.data.metrics.annual_vol, "pct")}</strong></div>
                <div className="analytics-kpi"><span>Sharpe</span><strong>{metric(analysis.data.metrics.sharpe, "number")}</strong></div>
                <div className="analytics-kpi"><span>{copy.maxDrawdown}</span><strong>{metric(analysis.data.metrics.max_drawdown, "pct")}</strong></div>
                <div className="analytics-kpi"><span>{copy.avgTurnover}</span><strong>{metric(analysis.data.average_turnover, "pct")}</strong></div>
              </div>
              <div className="detail-strip">
                <span>{executions[0]?.execution_price?.replace("_", " ") ?? copy.savedExecution} · {executions.length} {copy.periods}</span>
                <span>{copy.modeledCost} {metric(executionCost, "pct")}</span>
                <span>{copy.averageCash} {metric(averageCash, "pct")}</span>
                <span>{constrainedPeriods} {copy.constrainedPeriods}</span>
                {provenance?.data?.aggregate_sha256 && (
                  <span className="font-mono" title={provenance.data.aggregate_sha256}>{copy.data} {provenance.data.aggregate_sha256.slice(0, 12)}</span>
                )}
                {provenance?.code?.commit && (
                  <span className="font-mono" title={provenance.code.commit}>{copy.code} {provenance.code.commit.slice(0, 10)}{provenance.code.dirty ? "-dirty" : ""}</span>
                )}
                {provenance?.code?.source_sha256 && (
                  <span className="font-mono" title={provenance.code.source_sha256}>{copy.source} {provenance.code.source_sha256.slice(0, 12)}</span>
                )}
                <CSVExportButton
                  data={performanceExportRows}
                  filename={`alphalab-backtest-${analysis.data.id}-performance`}
                  label={copy.exportSeries}
                />
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
                <div className="analytics-empty">{copy.evaluatingGates}</div>
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
                    <CSVExportButton
                      data={Object.entries(robustness.data.cost_sensitivity).map(([costBps, values]) => ({
                        cost_bps: costBps,
                        annual_return: values.annual_return,
                        sharpe: values.sharpe,
                        max_drawdown: values.max_drawdown,
                      }))}
                      filename={`alphalab-backtest-${analysis.data.id}-cost-sensitivity`}
                      label={copy.exportCosts}
                    />
                  </div>
                  <div className="analytics-kpi-grid workbench-kpis">
                    <div className="analytics-kpi">
                      <span>{copy.annualExcess}</span>
                      <strong>{metric(robustness.data.metrics.excess.annual_return, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>{copy.benchmarkAnnual}</span>
                      <strong>{metric(robustness.data.metrics.benchmark.annual_return, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>{copy.benchmarkCoverage}</span>
                      <strong>{metric(robustness.data.benchmark_coverage, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>{copy.avgTurnover}</span>
                      <strong>{metric(robustness.data.turnover.average, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>{copy.validationExcess}</span>
                      <strong>{metric(robustness.data.validation.validation.excess?.annual_return, "pct")}</strong>
                    </div>
                    <div className="analytics-kpi">
                      <span>{copy.adjustedPValue}</span>
                      <strong>{metric(robustness.data.statistical.adjusted_p_value, "number")}</strong>
                    </div>
                  </div>
                  <div className="detail-strip">
                    <span>{copy.validationFrom} {robustness.data.validation.split_date ?? "--"}</span>
                    <span>{robustness.data.validation.validation.periods ?? 0} {copy.holdoutPeriods}</span>
                    <span>
                      {copy.bootstrapExcess} [{metric(robustness.data.statistical.bootstrap_mean_excess_95.lower, "pct")}, {metric(robustness.data.statistical.bootstrap_mean_excess_95.upper, "pct")}]
                    </span>
                    <span>{robustness.data.statistical.research_trials} {copy.declaredTrials}</span>
                  </div>
                  <div className="robustness-grid">
                    <section>
                      <h3>{copy.integrityChecks}</h3>
                      {robustness.data.checks.map((check) => (
                        <div className="gate-row" key={check.name}>
                          <span className={check.passed ? "gate-pass" : "gate-fail"}>
                            {check.passed ? copy.pass : copy.fail}
                          </span>
                          <strong>{check.name.replace(/_/g, " ")}</strong>
                          <small>{check.detail}</small>
                        </div>
                      ))}
                    </section>
                    <section>
                      <h3>{copy.candidateRules}</h3>
                      {Object.entries(robustness.data.candidate_rules).map(([name, passed]) => (
                        <div className="gate-row" key={name}>
                          <span className={passed ? "gate-pass" : "gate-watch"}>
                            {passed ? copy.pass : copy.watch}
                          </span>
                          <strong>{name.replace(/_/g, " ")}</strong>
                        </div>
                      ))}
                    </section>
                  </div>
                  <div className="analytics-table-wrap">
                    <table className="analytics-table compact">
                      <thead>
                        <tr><th>{copy.costBps}</th><th>{copy.annualReturn}</th><th>Sharpe</th><th>{copy.maxDrawdown}</th></tr>
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
                  aria-label={copy.holdingDate}
                  value={snapshot?.date ?? ""}
                  onChange={(event) => setHoldingDate(event.target.value)}
                >
                  {analysis.data.holdings.map((item) => (
                    <option key={item.date} value={item.date}>{item.date}</option>
                  ))}
                </select>
                <span>{snapshot?.holdings_count ?? 0} {copy.names}</span>
                <span>{copy.gross} {metric(snapshot?.gross_exposure, "pct")}</span>
                <span>{copy.max} {metric(snapshot?.max_weight, "pct")}</span>
                <span>HHI {metric(snapshot?.concentration, "number")}</span>
                <CSVExportButton
                  data={(snapshot?.top_holdings ?? []).map((holding) => ({
                    date: snapshot?.date,
                    symbol: holding.symbol,
                    weight: holding.weight,
                  }))}
                  filename={`alphalab-backtest-${analysis.data.id}-holdings-${snapshot?.date ?? "latest"}`}
                  label={copy.exportHoldings}
                />
              </div>
              <div className="analytics-table-wrap">
                <table className="analytics-table compact">
                  <thead><tr><th>{copy.symbol}</th><th>{copy.weight}</th></tr></thead>
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
