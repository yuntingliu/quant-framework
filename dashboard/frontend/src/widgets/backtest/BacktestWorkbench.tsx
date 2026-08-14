import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Clock3, Database, Play, ReceiptText, RefreshCw, ShieldCheck, Workflow } from "lucide-react"

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

type WorkbenchTab = "performance" | "robustness" | "execution" | "holdings"
type WorkbenchView = "inspect" | "compare"

function metric(value: number | null | undefined, kind: "pct" | "number"): string {
  if (value == null) return "—"
  return kind === "pct" ? formatPercent(value, 1) : formatNumber(value, 2)
}

function researchStatusLabel(status: string, language: "zh" | "en"): string {
  const labels: Record<string, { zh: string; en: string }> = {
    research_candidate: { zh: "研究候选", en: "Research candidate" },
    watch: { zh: "观察", en: "Watch" },
    weak: { zh: "较弱", en: "Weak" },
    invalid: { zh: "无效", en: "Invalid" },
  }
  return labels[status]?.[language] ?? status.replace(/_/g, " ")
}

export function BacktestWorkbenchWidget() {
  const { language } = useLanguage()
  const copy = language === "zh" ? {
    title: "回测工作台",
    refresh: "刷新已保存的结果",
    mode: "回测工作台模式",
    inspect: "运行与查看",
    compare: "对比",
    profile: "回测数据",
    demo: "演示数据",
    runtime: "本地 RQ",
    strategy: "策略",
    factorCount: "个因子",
    signalCount: "个择时信号",
    stockSelection: "选股策略",
    marketTiming: "择时策略",
    startDate: "回测开始日期",
    endDate: "回测结束日期",
    running: "运行中",
    run: "运行",
    quickRun: "快速回测",
    studying: "研究中",
    study: "完整研究验证",
    runSettings: "运行设置",
    runHint: "这里的设置只影响下一次运行，不会改变下方正在查看的历史结果。",
    researchHint: "完整研究会运行回测、稳健性闸门和模拟调仓预览，但不会自动交易。",
    timingResearchHint: "完整研究会运行择时回测与稳健性闸门；择时只输出市场仓位，不生成个股模拟订单。",
    paperAwaiting: "模拟调仓等待用户确认",
    timingCompleted: "最新市场仓位已生成，不涉及个股订单",
    savedBacktest: "已保存的回测",
    savedRun: "回测记录",
    noBacktests: "没有已保存的回测。",
    resultView: "回测结果视图",
    performance: "收益",
    robustness: "稳健性",
    holdings: "持仓",
    exposure: "仓位",
    execution: "交易执行",
    currentResult: "当前结果",
    resultIdentity: "结果身份",
    runAt: "运行于",
    strategySnapshot: "策略快照",
    benchmark: "等权基准",
    marketBenchmark: "MKT 市场基准",
    benchmarkMissing: "该历史记录未保存基准序列，无法自动给出研究结论。",
    verdictUnavailable: "基准缺失",
    rebuildBenchmark: "重建基准并评估",
    rebuildHint: "需要重新读取历史行情，可能耗时较长。",
    excess: "超额净值",
    strategyCurve: "策略净值",
    verdict: "研究结论",
    failedShort: "未通过",
    watchShort: "需观察",
    loadingResult: "正在载入回测结果…",
    verdictLoading: "正在评估…",
    noExecutionAudit: "此历史记录没有执行审计，成本、现金和流动性限制均无法判断。",
    invalidRange: "开始日期必须早于结束日期。",
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
    signalDate: "信号日期",
    entryDate: "成交日期",
    exitDate: "退出日期",
    turnover: "换手率",
    totalCost: "总成本",
    cashWeight: "现金比例",
    restrictions: "交易限制",
    grossReturn: "毛收益",
    netReturn: "净收益",
    exportExecution: "导出执行记录",
    data: "数据",
    code: "代码",
    source: "源码",
    pythonSource: "策略 Python",
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
    marketExposure: "市场仓位",
    weight: "权重",
  } : {
    title: "Backtest Workbench",
    refresh: "Refresh saved result",
    mode: "Backtest workbench mode",
    inspect: "Run & Inspect",
    compare: "Compare",
    profile: "Data profile",
    demo: "Demo",
    runtime: "Local RQ",
    strategy: "Strategy",
    factorCount: "factors",
    signalCount: "timing signals",
    stockSelection: "Stock selection",
    marketTiming: "Market timing",
    startDate: "Backtest start date",
    endDate: "Backtest end date",
    running: "Running",
    run: "Run",
    quickRun: "Quick backtest",
    studying: "Studying",
    study: "Full research validation",
    runSettings: "Run setup",
    runHint: "These settings affect the next run only; they do not describe the saved result below.",
    researchHint: "Full research runs the backtest, robustness gates, and a paper rebalance preview, but never auto-trades.",
    timingResearchHint: "Full research runs timing backtests and robustness gates. Timing emits market exposure and never creates stock orders.",
    paperAwaiting: "paper rebalance awaits confirmation",
    timingCompleted: "latest market exposure generated; no stock orders",
    savedBacktest: "Saved backtest",
    savedRun: "Saved run",
    noBacktests: "has no persisted backtests.",
    resultView: "Backtest result view",
    performance: "Performance",
    robustness: "Robustness",
    holdings: "Holdings",
    exposure: "Exposure",
    execution: "Execution",
    currentResult: "Current result",
    resultIdentity: "Result identity",
    runAt: "run at",
    strategySnapshot: "Strategy snapshot",
    benchmark: "Equal-weight benchmark",
    marketBenchmark: "MKT benchmark",
    benchmarkMissing: "This legacy result did not persist a benchmark series, so a verdict cannot be produced automatically.",
    verdictUnavailable: "Benchmark unavailable",
    rebuildBenchmark: "Rebuild benchmark and evaluate",
    rebuildHint: "This reloads historical market data and may take a while.",
    excess: "Excess equity",
    strategyCurve: "Strategy equity",
    verdict: "Research verdict",
    failedShort: "failed",
    watchShort: "need review",
    loadingResult: "Loading backtest result…",
    verdictLoading: "Evaluating…",
    noExecutionAudit: "This legacy result has no execution audit; cost, cash, and liquidity constraints are unknown.",
    invalidRange: "Start date must be earlier than end date.",
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
    signalDate: "Signal date",
    entryDate: "Entry date",
    exitDate: "Exit date",
    turnover: "Turnover",
    totalCost: "Total cost",
    cashWeight: "Cash",
    restrictions: "Restrictions",
    grossReturn: "Gross return",
    netReturn: "Net return",
    exportExecution: "Export execution",
    data: "data",
    code: "code",
    source: "source",
    pythonSource: "strategy Python",
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
    marketExposure: "Market exposure",
    weight: "Weight",
  }
  const { selectedStrategy, setSelectedStrategy, selectedBacktest, setSelectedBacktest } = useWorkspace()
  const selectedStrategyRef = useRef(selectedStrategy)
  const selectedBacktestRef = useRef(selectedBacktest)
  selectedStrategyRef.current = selectedStrategy
  selectedBacktestRef.current = selectedBacktest
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
  const [forceRobustness, setForceRobustness] = useState(false)

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
        const requestedStrategy = selectedStrategyRef.current
        const nextStrategy = requestedStrategy && templates.some((item) => item.id === requestedStrategy)
          ? requestedStrategy
          : templates.some((item) => item.id === "balanced")
            ? "balanced"
            : templates[0]?.id ?? ""
        setStrategyId(nextStrategy)
        setSelectedStrategy(nextStrategy || null)
        const matching = saved.filter((item) => item.profile === profile)
        setRecords(matching)
        const requestedBacktest = selectedBacktestRef.current
        const nextBacktest = requestedBacktest && matching.some((item) => item.id === requestedBacktest)
          ? requestedBacktest
          : matching[0]?.id ?? ""
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
    enabled: Boolean(
      selectedId
      && analysis.data
      && ((analysis.data.benchmark_coverage ?? 0) >= 0.80 || forceRobustness)
    ),
  })

  useEffect(() => {
    setForceRobustness(false)
  }, [selectedId])

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
        `${researchStatusLabel(run.result.robustness_status, language)} · ${
          selectedStrategyDefinition?.strategy_type === "market_timing"
            ? copy.timingCompleted
            : copy.paperAwaiting
        }`,
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
        const strategyValue = analysis.data?.equity_curve[index]
        const benchmarkValue = analysis.data?.benchmark_equity_curve[index]
        const excessValue = analysis.data?.excess_equity_curve[index]
        if (strategyValue != null) point.strategy = strategyValue
        if (benchmarkValue != null) point.benchmark = benchmarkValue
        if (excessValue != null) point.excess = excessValue
        return point
      }),
    [analysis.data],
  )
  const drawdownData = useMemo(
    () =>
      (analysis.data?.dates ?? []).map((date, index) => {
        const point: { date: string; strategy: number; benchmark?: number } = {
          date,
          strategy: analysis.data?.drawdown[index] ?? 0,
        }
        const benchmarkValue = analysis.data?.benchmark_drawdown[index]
        if (benchmarkValue != null) point.benchmark = benchmarkValue
        return point
      }),
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
      benchmark_return: result.benchmark_returns[index],
      benchmark_equity_curve: result.benchmark_equity_curve[index],
      benchmark_drawdown: result.benchmark_drawdown[index],
      excess_return: result.excess_returns[index],
      excess_equity_curve: result.excess_equity_curve[index],
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
  const selectedRecord = records.find((record) => record.id === selectedId)
  const selectedStrategyDefinition = strategies.find((strategy) => strategy.id === strategyId)
  const isTimingSelection = selectedStrategyDefinition?.strategy_type === "market_timing"
  const isTimingResult = analysis.data?.strategy_snapshot?.strategy_type === "market_timing"
  const invalidDateRange = Boolean(startDate && endDate && startDate > endDate)
  const failedChecks = robustness.data
    ? robustness.data.checks.filter((check) => !check.passed).length
    : null
  const watchRules = robustness.data
    ? Object.values(robustness.data.candidate_rules).filter((passed) => !passed).length
    : null
  const benchmarkReady = (analysis.data?.benchmark_coverage ?? 0) >= 0.80

  return (
    <Widget
      title={copy.title}
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
      <section className="backtest-run-setup">
        <div className="backtest-section-heading">
          <div>
            <strong>{copy.runSettings}</strong>
            <span>{copy.runHint}</span>
          </div>
          {selectedStrategyDefinition && (
            <small>
              {selectedStrategyDefinition.strategy_type === "market_timing" ? copy.marketTiming : copy.stockSelection}
              {" · "}{selectedStrategyDefinition.name}{" · "}
              {selectedStrategyDefinition.implementation === "python"
                ? "Python"
                : selectedStrategyDefinition.strategy_type === "market_timing"
                ? `${selectedStrategyDefinition.signals?.length ?? 0} ${copy.signalCount}`
                : `${selectedStrategyDefinition.factors.length} ${copy.factorCount}`}
            </small>
          )}
        </div>
        <div className="backtest-run-controls">
          <label>
            <span>{copy.profile}</span>
            <select value={profile} onChange={(event) => setProfile(event.target.value as DataProfile)}>
              <option value="demo">{copy.demo}</option>
              <option value="runtime">{copy.runtime}</option>
            </select>
          </label>
          <label>
            <span>{copy.strategy}</span>
            <select
              value={strategyId}
              onChange={(event) => {
                setStrategyId(event.target.value)
                setSelectedStrategy(event.target.value)
              }}
            >
              <optgroup label={copy.stockSelection}>
                {strategies.filter((strategy) => strategy.strategy_type === "stock_selection").map((strategy) => (
                  <option key={strategy.id} value={strategy.id}>{strategy.name}</option>
                ))}
              </optgroup>
              <optgroup label={copy.marketTiming}>
                {strategies.filter((strategy) => strategy.strategy_type === "market_timing").map((strategy) => (
                  <option key={strategy.id} value={strategy.id}>{strategy.name}</option>
                ))}
              </optgroup>
            </select>
          </label>
          <label>
            <span>{copy.startDate}</span>
            <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </label>
          <label>
            <span>{copy.endDate}</span>
            <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </label>
          <div className="backtest-run-actions">
            <button
              className="primary-command"
              type="button"
              onClick={runBacktest}
              disabled={running || researching || !startDate || !endDate || invalidDateRange}
            >
              <Play aria-hidden="true" />
              {running ? copy.running : copy.quickRun}
            </button>
            <button
              className="secondary-command"
              type="button"
              title={isTimingSelection ? copy.timingResearchHint : copy.researchHint}
              onClick={runResearch}
              disabled={researching || running || !startDate || !endDate || invalidDateRange}
            >
              <Workflow aria-hidden="true" />
              {researching ? copy.studying : copy.study}
            </button>
          </div>
        </div>
        <div className="backtest-research-hint">{isTimingSelection ? copy.timingResearchHint : copy.researchHint}</div>
      </section>
      {invalidDateRange && <div className="workbench-message error">{copy.invalidRange}</div>}
      {setupError && <div className="workbench-message error">{setupError}</div>}
      {researchMessage && !setupError && (
        <div className="workbench-message research-message">{researchMessage}</div>
      )}
      {records.length > 0 && (
        <section className="backtest-result-identity">
          <div className="backtest-result-title">
            <strong>{copy.currentResult}</strong>
            {analysis.data ? (
              <span>
                {analysis.data.strategy_id} · {analysis.data.profile === "demo" ? copy.demo : copy.runtime} · {analysis.data.start_date} — {analysis.data.end_date}
              </span>
            ) : selectedRecord ? <span>{selectedRecord.strategy_id}</span> : null}
          </div>
          {analysis.data?.strategy_snapshot && (
            <div
              className="backtest-snapshot-summary"
              title={(isTimingResult ? analysis.data.strategy_snapshot.signals : analysis.data.strategy_snapshot.factors).join(", ")}
            >
              <Database size={13} />
              <span>
                {copy.strategySnapshot}: {isTimingResult ? copy.marketTiming : copy.stockSelection}{" · "}
                {analysis.data.strategy_snapshot.implementation === "python"
                  ? "Python"
                  : isTimingResult
                  ? `${analysis.data.strategy_snapshot.signals.length} ${copy.signalCount}`
                  : `${analysis.data.strategy_snapshot.factors.length} ${copy.factorCount}`}
                {" · "}{analysis.data.strategy_snapshot.rebalance_freq}
              </span>
            </div>
          )}
          {analysis.data?.run_at && (
            <div className="backtest-run-time"><Clock3 size={13} /><span>{copy.runAt} {analysis.data.run_at.slice(0, 16).replace("T", " ")}</span></div>
          )}
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
                {record.strategy_id} · {record.start_date.slice(0, 7)}–{record.end_date.slice(0, 7)} · {record.run_at.slice(0, 10)}
              </option>
            ))}
          </select>
        </section>
      )}
      {selectedId && analysis.isLoading ? (
        <div className="analytics-empty">{copy.loadingResult}</div>
      ) : selectedId && analysis.error ? (
        <div className="workbench-message error">{analyticsError(analysis.error)}</div>
      ) : !selectedId && !setupError ? (
        <div className="analytics-empty">{profile} {copy.noBacktests}</div>
      ) : analysis.data ? (
        <>
          <div className="backtest-verdict-summary">
            <div>
              <span>{copy.verdict}</span>
              {robustness.data ? (
                <strong className={`research-status ${robustness.data.status}`}>
                  <ShieldCheck aria-hidden="true" />
                  {researchStatusLabel(robustness.data.status, language)}
                </strong>
              ) : !benchmarkReady ? (
                <strong>{copy.verdictUnavailable}</strong>
              ) : robustness.isLoading ? (
                <strong>{copy.verdictLoading}</strong>
              ) : <strong>—</strong>}
            </div>
            <div><span>{copy.annualExcess}</span><strong>{metric(robustness.data?.metrics.excess.annual_return, "pct")}</strong></div>
            <div><span>{copy.validationExcess}</span><strong>{metric(robustness.data?.validation.validation.excess?.annual_return, "pct")}</strong></div>
            <div><span>{copy.benchmarkCoverage}</span><strong>{metric(analysis.data.benchmark_coverage, "pct")}</strong></div>
            <div className={(failedChecks ?? 0) > 0 ? "attention" : ""}><span>{copy.integrityChecks}</span><strong>{failedChecks == null ? "—" : `${failedChecks} ${copy.failedShort}`}</strong></div>
            <div className={(watchRules ?? 0) > 0 ? "attention" : ""}><span>{copy.candidateRules}</span><strong>{watchRules == null ? "—" : `${watchRules} ${copy.watchShort}`}</strong></div>
          </div>
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
              aria-selected={tab === "execution"}
              onClick={() => setTab("execution")}
            >
              {copy.execution}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "holdings"}
              onClick={() => setTab("holdings")}
            >
              {isTimingResult ? copy.exposure : copy.holdings}
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
                {analysis.data.has_execution_audit ? <>
                  <span>{executions[0]?.execution_price?.replace("_", " ") ?? copy.savedExecution} · {executions.length} {copy.periods}</span>
                  <span>{copy.modeledCost} {metric(executionCost, "pct")}</span>
                  <span>{copy.averageCash} {metric(averageCash, "pct")}</span>
                  <span>{constrainedPeriods} {copy.constrainedPeriods}</span>
                </> : <span className="backtest-no-audit"><ReceiptText size={13} />{copy.noExecutionAudit}</span>}
                {provenance?.data?.aggregate_sha256 && (
                  <span className="font-mono" title={provenance.data.aggregate_sha256}>{copy.data} {provenance.data.aggregate_sha256.slice(0, 12)}</span>
                )}
                {provenance?.code?.commit && (
                  <span className="font-mono" title={provenance.code.commit}>{copy.code} {provenance.code.commit.slice(0, 10)}{provenance.code.dirty ? "-dirty" : ""}</span>
                )}
                {provenance?.code?.source_sha256 && (
                  <span className="font-mono" title={provenance.code.source_sha256}>{copy.source} {provenance.code.source_sha256.slice(0, 12)}</span>
                )}
                {provenance?.strategy_python_sha256 && (
                  <span className="font-mono" title={provenance.strategy_python_sha256}>{copy.pythonSource} {provenance.strategy_python_sha256.slice(0, 12)}</span>
                )}
                <CSVExportButton
                  data={performanceExportRows}
                  filename={`alphalab-backtest-${analysis.data.id}-performance`}
                  label={copy.exportSeries}
                />
              </div>
              <CumulativeReturnsChart
                data={equityData}
                series={[
                  { key: "strategy", name: copy.strategyCurve },
                  ...(analysis.data.benchmark_coverage
                    ? [
                        { key: "benchmark", name: isTimingResult ? copy.marketBenchmark : copy.benchmark },
                        { key: "excess", name: copy.excess },
                      ]
                    : []),
                ]}
                height={250}
              />
              <DrawdownChart data={drawdownData} height={190} />
            </div>
          ) : tab === "robustness" ? (
            <div className="workbench-body">
              {!benchmarkReady && !forceRobustness ? (
                <div className="backtest-benchmark-missing">
                  <Database size={18} />
                  <div><strong>{copy.benchmarkMissing}</strong><span>{copy.rebuildHint}</span></div>
                  <button type="button" className="secondary-command" onClick={() => setForceRobustness(true)}>
                    <RefreshCw size={13} />{copy.rebuildBenchmark}
                  </button>
                </div>
              ) : robustness.isLoading ? (
                <div className="analytics-empty">{copy.evaluatingGates}</div>
              ) : robustness.error ? (
                <div className="workbench-message error">{analyticsError(robustness.error)}</div>
              ) : robustness.data ? (
                <>
                  <div className="robustness-header">
                    <span className={`research-status ${robustness.data.status}`}>
                      <ShieldCheck aria-hidden="true" />
                      {researchStatusLabel(robustness.data.status, language)}
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
          ) : tab === "execution" ? (
            <div className="workbench-body">
              {!analysis.data.has_execution_audit ? (
                <div className="backtest-audit-empty">
                  <ReceiptText size={18} />
                  <span>{copy.noExecutionAudit}</span>
                </div>
              ) : (
                <>
                  <div className="holdings-toolbar">
                    <span>{executions.length} {copy.periods}</span>
                    <span>{copy.modeledCost} {metric(executionCost, "pct")}</span>
                    <span>{copy.averageCash} {metric(averageCash, "pct")}</span>
                    <span>{constrainedPeriods} {copy.constrainedPeriods}</span>
                    <CSVExportButton
                      data={executions.map((item) => ({
                        ...item,
                        constrained_symbols: item.constrained_symbols.join("|"),
                        missing_amount_symbols: item.missing_amount_symbols.join("|"),
                      }))}
                      filename={`alphalab-backtest-${analysis.data.id}-execution`}
                      label={copy.exportExecution}
                    />
                  </div>
                  <div className="analytics-table-wrap">
                    <table className="analytics-table backtest-execution-table">
                      <thead>
                        <tr>
                          <th>{copy.signalDate}</th><th>{copy.entryDate}</th><th>{copy.exitDate}</th>
                          <th>{copy.turnover}</th><th>{copy.totalCost}</th><th>{copy.cashWeight}</th>
                          <th>{copy.grossReturn}</th><th>{copy.netReturn}</th><th>{copy.restrictions}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {executions.map((item, index) => {
                          const restrictions = [...item.constrained_symbols, ...item.missing_amount_symbols]
                          return (
                            <tr key={`${item.entry_date}-${index}`}>
                              <td>{item.signal_date}</td><td>{item.entry_date}</td><td>{item.exit_date}</td>
                              <td>{metric(item.turnover, "pct")}</td><td>{metric(item.total_cost, "pct")}</td><td>{metric(item.cash_weight, "pct")}</td>
                              <td>{metric(item.gross_return, "pct")}</td><td>{metric(item.net_return, "pct")}</td>
                              <td title={restrictions.join(", ")}>{restrictions.length ? restrictions.join(", ") : "—"}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
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
                <span>{isTimingResult ? copy.marketExposure : `${snapshot?.holdings_count ?? 0} ${copy.names}`}</span>
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
                        <td><strong>{isTimingResult && holding.symbol === "MARKET_EXPOSURE" ? copy.marketExposure : holding.symbol}</strong></td>
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
