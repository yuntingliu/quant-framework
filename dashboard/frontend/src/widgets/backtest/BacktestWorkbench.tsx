import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Clock3, Database, Play, ReceiptText, RefreshCw, ShieldCheck } from "lucide-react"

import { CumulativeReturnsChart, DrawdownChart } from "@/components/charts"
import { CSVExportButton } from "@/components/shared/CSVExportButton"
import { useLanguage } from "@/contexts/LanguageContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import {
  api,
  type BacktestAnalysis,
  type BacktestJob,
  type BacktestRecord,
  type BacktestRobustness,
  type BacktestSignalDiagnostics,
  type DataManifest,
  type ProviderStatus,
  type PipelineProjectSummary,
  type RuntimeCatalog,
} from "@/lib/api"
import { useDataProfile } from "@/lib/data-profile"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"
import { analyticsError } from "@/widgets/market/analytics-utils"
import { BacktestCompareWidget } from "./BacktestCompare"

type WorkbenchTab = "pipeline" | "performance" | "signals" | "robustness" | "execution" | "holdings"
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
    mode: "回测工作台模式",
    inspect: "完整策略回测",
    compare: "策略对比",
    profile: "回测数据",
    demo: "演示数据",
    runtime: "本地 RQ",
    strategy: "StrategySnapshot",
    factorCount: "个横截面信号",
    signalCount: "个择时信号",
    stockSelection: "纯选股",
    marketTiming: "纯择时",
    startDate: "回测开始日期",
    endDate: "回测结束日期",
    running: "运行中",
    queued: "已进入回测队列",
    runningBackground: "完整策略正在后台回测，离开此页面也不会中断。",
    run: "运行",
    quickRun: "运行完整策略回测",
    runSettings: "运行设置",
    runHint: "这里选择版本固定的六阶段策略项目和数据区间；回测运行并保存工作台中看到的同一份总 Python 源码。",
    paperAwaiting: "模拟调仓等待用户确认",
    timingCompleted: "最新市场仓位已生成，不涉及个股订单",
    savedBacktest: "已保存的回测",
    savedRun: "回测记录",
    noBacktests: "没有已保存的回测。",
    resultView: "回测结果视图",
    pipeline: "策略快照",
    pipelineSnapshot: "本次回测实际保存的策略管线",
    pipelineHint: "按标的池、选股、择时、组合、风控、执行展示；六个组件版本、总 Python 源码、数据和代码指纹一起固化。",
    customModule: "本次回测的自定义 Python 模块",
    hardGate: "核心闸门",
    performance: "收益与基准",
    signals: "信号诊断",
    signalEvidence: "信号预测能力",
    signalEvidenceHint: "这里使用该 BacktestRun 已保存的每期横截面评分、后续收益和择时仓位；它不重新运行策略。",
    meanIc: "平均 Rank IC",
    positiveIc: "IC 为正比例",
    scoreCoverage: "评分覆盖率",
    selectionTurnover: "选股换手",
    timingExposure: "平均择时仓位",
    quantileSpread: "头尾组收益差",
    evidencePeriods: "有效检验期",
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
    mode: "Backtest workbench mode",
    inspect: "Full Strategy Backtest",
    compare: "Strategy Compare",
    profile: "Data profile",
    demo: "Demo",
    runtime: "Local RQ",
    strategy: "StrategySnapshot",
    factorCount: "cross-sectional signals",
    signalCount: "timing signals",
    stockSelection: "Stock only",
    marketTiming: "Timing only",
    startDate: "Backtest start date",
    endDate: "Backtest end date",
    running: "Running",
    queued: "Backtest queued",
    runningBackground: "The complete strategy is running in the background and will continue if you leave this page.",
    run: "Run",
    quickRun: "Run full strategy backtest",
    runSettings: "Run setup",
    runHint: "Choose a version-pinned six-stage project and data range. The run executes and persists the same complete Python source shown here.",
    paperAwaiting: "paper rebalance awaits confirmation",
    timingCompleted: "latest market exposure generated; no stock orders",
    savedBacktest: "Saved backtest",
    savedRun: "Saved run",
    noBacktests: "has no persisted backtests.",
    resultView: "Backtest result view",
    pipeline: "Strategy Snapshot",
    pipelineSnapshot: "Persisted pipeline used by this backtest",
    pipelineHint: "Universe, selection, timing, portfolio, risk, and execution are shown in order with the frozen Python module, data, and code fingerprints.",
    customModule: "Persisted custom Python module",
    hardGate: "Core gate",
    performance: "Returns & Benchmark",
    signals: "Signal Diagnostics",
    signalEvidence: "Signal predictive evidence",
    signalEvidenceHint: "Derived from the cross-sectional scores, forward returns, and timing exposure persisted in this BacktestRun; the strategy is not rerun.",
    meanIc: "Mean Rank IC",
    positiveIc: "Positive IC ratio",
    scoreCoverage: "Score coverage",
    selectionTurnover: "Selection turnover",
    timingExposure: "Avg timing exposure",
    quantileSpread: "Top-bottom return",
    evidencePeriods: "evidence periods",
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
  const { selectedStrategy, setActiveMode, selectedBacktest, setSelectedBacktest } = useWorkspace()
  const selectedStrategyRef = useRef(selectedStrategy)
  const selectedBacktestRef = useRef(selectedBacktest)
  selectedStrategyRef.current = selectedStrategy
  selectedBacktestRef.current = selectedBacktest
  const [profile] = useDataProfile()
  const [strategies, setStrategies] = useState<PipelineProjectSummary[]>([])
  const [records, setRecords] = useState<BacktestRecord[]>([])
  const [strategyId, setStrategyId] = useState(selectedStrategy ?? "")
  const [selectedId, setSelectedId] = useState(selectedBacktest ?? "")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [setupError, setSetupError] = useState("")
  const [running, setRunning] = useState(false)
  const [activeJob, setActiveJob] = useState<BacktestJob | null>(null)
  const [tab, setTab] = useState<WorkbenchTab>("performance")
  const [view, setView] = useState<WorkbenchView>("inspect")
  const [holdingDate, setHoldingDate] = useState("")
  const [forceRobustness, setForceRobustness] = useState(false)

  useEffect(() => {
    setSetupError("")
    setSelectedId("")
    setActiveJob(null)
    setRunning(false)
    Promise.all([
      api.get<PipelineProjectSummary[]>("/pipeline/projects"),
      api.get<DataManifest>("/data/manifest"),
      api.get<ProviderStatus>("/data/providers"),
      api.get<RuntimeCatalog>("/data-sync/catalog"),
      api.get<BacktestRecord[]>("/backtests?limit=100"),
      api.get<BacktestJob[]>("/backtests/jobs?limit=20"),
    ])
      .then(([templates, manifest, providers, catalog, saved, jobs]) => {
        setStrategies(templates)
        const requestedStrategy = selectedStrategyRef.current
        const nextStrategy = requestedStrategy && templates.some((item) => item.id === requestedStrategy)
          ? requestedStrategy
          : ""
        setStrategyId(nextStrategy)
        const matching = saved.filter((item) => item.profile === profile)
        setRecords(matching)
        const requestedBacktest = selectedBacktestRef.current
        const nextBacktest = requestedBacktest && matching.some((item) => item.id === requestedBacktest)
          ? requestedBacktest
          : matching[0]?.id ?? ""
        setSelectedId(nextBacktest)
        setSelectedBacktest(nextBacktest || null)
        const resumable = jobs.find((job) =>
          (job.status === "queued" || job.status === "running")
          && job.request.profile === profile
          && job.request.project_id === nextStrategy
        )
        if (resumable) {
          setActiveJob(resumable)
          setRunning(true)
        }
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
  }, [profile, setSelectedBacktest])

  useEffect(() => {
    if (selectedStrategy && selectedStrategy !== strategyId && strategies.some((item) => item.id === selectedStrategy)) {
      setStrategyId(selectedStrategy)
    } else if (!selectedStrategy && strategyId) {
      setStrategyId("")
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
  const signals = useQuery({
    queryKey: ["backtests", "signals", selectedId],
    queryFn: () => api.get<BacktestSignalDiagnostics>(`/backtests/${selectedId}/signals`),
    enabled: Boolean(selectedId),
  })

  useEffect(() => {
    setForceRobustness(false)
  }, [selectedId])

  useEffect(() => {
    const latest = analysis.data?.holdings.at(-1)?.date ?? ""
    setHoldingDate(latest)
  }, [analysis.data])

  const activeJobId = activeJob?.id
  const activeJobStatus = activeJob?.status
  useEffect(() => {
    if (!activeJobId || !activeJobStatus || !["queued", "running"].includes(activeJobStatus)) return
    let cancelled = false
    const refresh = async () => {
      try {
        const job = await api.get<BacktestJob>(`/backtests/jobs/${activeJobId}`)
        if (!cancelled) setActiveJob(job)
      } catch (error) {
        if (!cancelled) {
          setSetupError(error instanceof Error ? error.message : String(error))
          setRunning(false)
        }
      }
    }
    const timer = window.setInterval(refresh, 1200)
    void refresh()
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [activeJobId, activeJobStatus])

  useEffect(() => {
    if (!activeJob || ["queued", "running"].includes(activeJob.status)) return
    let cancelled = false
    setRunning(false)
    if (activeJob.status === "succeeded" && activeJob.result_id) {
      api.get<BacktestRecord[]>("/backtests?limit=100")
        .then((saved) => {
          if (cancelled) return
          setRecords(saved.filter((item) => item.profile === profile))
          setSelectedId(activeJob.result_id ?? "")
          setSelectedBacktest(activeJob.result_id)
          setTab("performance")
          setActiveJob(null)
        })
        .catch((error: Error) => {
          if (!cancelled) setSetupError(error.message)
        })
    } else {
      setSetupError(activeJob.error || activeJob.message || "Backtest failed")
      setActiveJob(null)
    }
    return () => {
      cancelled = true
    }
  }, [activeJob, profile, setSelectedBacktest])

  async function runBacktest() {
    setRunning(true)
    setSetupError("")
    try {
      const job = await api.post<BacktestJob>("/backtests/jobs", {
        project_id: strategyId,
        start_date: startDate,
        end_date: endDate,
        profile,
      })
      setActiveJob(job)
    } catch (error) {
      setSetupError(error instanceof Error ? error.message : String(error))
      setRunning(false)
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
  const isTimingResult = false
  const invalidDateRange = Boolean(startDate && endDate && startDate > endDate)
  const failedChecks = robustness.data
    ? robustness.data.checks.filter((check) => !check.passed).length
    : null
  const watchRules = robustness.data
    ? Object.values(robustness.data.candidate_rules).filter((passed) => !passed).length
    : null
  const benchmarkReady = (analysis.data?.benchmark_coverage ?? 0) >= 0.80

  return (
    <Widget headerless>
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
              六阶段 Python · {selectedStrategyDefinition.name} · revision {selectedStrategyDefinition.revision}
            </small>
          )}
        </div>
        <div className="backtest-run-controls">
          <div className="pipeline-pinned-component">
            <span>{copy.profile}</span>
            <strong>{profile === "demo" ? copy.demo : copy.runtime}</strong>
          </div>
          <div className="pipeline-pinned-component">
            <span>{copy.strategy}</span>
            <strong>{selectedStrategyDefinition?.name ?? "—"}</strong>
          </div>
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
              disabled={running || !strategyId || !startDate || !endDate || invalidDateRange}
            >
              <Play aria-hidden="true" />
              {running ? copy.running : copy.quickRun}
            </button>
          </div>
        </div>
      </section>
      {running && activeJob && (
        <div className="workbench-message">
          <RefreshCw className="spin" aria-hidden="true" />
          {activeJob.status === "queued" ? copy.queued : copy.runningBackground}
          {activeJob.message ? ` · ${activeJob.message}` : ""}
        </div>
      )}
      {!strategyId && (
        <div className="workbench-message warning">
          {language === "zh" ? "请先在研究项目工作台选择或新建项目。" : "Select or create a project in Research Project first."}
          <button className="secondary-command" type="button" onClick={() => setActiveMode("project")}>
            {language === "zh" ? "前往研究项目" : "Open Research Project"}
          </button>
        </div>
      )}
      {invalidDateRange && <div className="workbench-message error">{copy.invalidRange}</div>}
      {setupError && <div className="workbench-message error">{setupError}</div>}
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
              title={analysis.data.strategy_snapshot.factors.join(", ")}
            >
              <Database size={13} />
              <span>
                {copy.strategySnapshot}: {analysis.data.strategy_snapshot.name}{" · "}
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
              aria-selected={tab === "pipeline"}
              onClick={() => setTab("pipeline")}
            >
              {copy.pipeline}
            </button>
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
              aria-selected={tab === "signals"}
              onClick={() => setTab("signals")}
            >
              {copy.signals}
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
          {tab === "pipeline" ? (
            <div className="workbench-body backtest-pipeline-view">
              <div className="backtest-section-heading">
                <div><strong>{copy.pipelineSnapshot}</strong><span>{copy.pipelineHint}</span></div>
              </div>
              {analysis.data.strategy_snapshot?.pipeline_manifest ? (
                <>
                  <div className="backtest-pipeline-grid">
                    {analysis.data.strategy_snapshot.pipeline_manifest.stages.map((pipelineStage) => (
                      <section className="backtest-pipeline-stage" key={pipelineStage.name}>
                        <div>
                          <span>{pipelineStage.order}</span>
                          <strong>{pipelineStage.name.toUpperCase()}</strong>
                          <small>{pipelineStage.kind === "python" ? "Python" : "Configured"}</small>
                        </div>
                        <p>{pipelineStage.contract.input}</p>
                        <code>{pipelineStage.contract.output}</code>
                        <p><ShieldCheck size={12} /> {copy.hardGate}: {pipelineStage.contract.hard_gate}</p>
                        <pre>{pipelineStage.source}</pre>
                      </section>
                    ))}
                  </div>
                  <section className="backtest-pipeline-source">
                    <strong>run_strategy</strong>
                    <pre>{analysis.data.strategy_snapshot.pipeline_manifest.composed_source}</pre>
                  </section>
                  {analysis.data.provenance.strategy_python?.source && (
                    <section className="backtest-pipeline-source">
                      <strong>{copy.customModule}</strong>
                      <pre>{analysis.data.provenance.strategy_python.source}</pre>
                    </section>
                  )}
                </>
              ) : <div className="analytics-empty">—</div>}
            </div>
          ) : tab === "performance" ? (
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
          ) : tab === "signals" ? (
            <div className="workbench-body">
              {signals.isLoading ? (
                <div className="analytics-empty">{copy.loadingResult}</div>
              ) : signals.error ? (
                <div className="workbench-message error">{analyticsError(signals.error)}</div>
              ) : signals.data ? (
                <>
                  <div className="backtest-section-heading">
                    <div><strong>{copy.signalEvidence}</strong><span>{copy.signalEvidenceHint}</span></div>
                  </div>
                  {signals.data.warning ? <div className="workbench-message warning">{signals.data.warning}</div> : null}
                  <div className="analytics-kpi-grid workbench-kpis">
                    <div className="analytics-kpi"><span>{copy.meanIc}</span><strong>{metric(signals.data.summary.mean_ic, "number")}</strong></div>
                    <div className="analytics-kpi"><span>{copy.positiveIc}</span><strong>{metric(signals.data.summary.positive_ic_ratio, "pct")}</strong></div>
                    <div className="analytics-kpi"><span>{copy.scoreCoverage}</span><strong>{metric(signals.data.summary.average_coverage, "pct")}</strong></div>
                    <div className="analytics-kpi"><span>{copy.selectionTurnover}</span><strong>{metric(signals.data.summary.average_selection_turnover, "pct")}</strong></div>
                    <div className="analytics-kpi"><span>{copy.timingExposure}</span><strong>{metric(signals.data.summary.average_timing_exposure, "pct")}</strong></div>
                    <div className="analytics-kpi"><span>{copy.evidencePeriods}</span><strong>{signals.data.evidence_periods}/{signals.data.periods}</strong></div>
                  </div>
                  <div className="analytics-table-wrap">
                    <table className="analytics-table compact">
                      <thead><tr><th>{copy.signalDate}</th><th>{copy.scoreCoverage}</th><th>{copy.meanIc}</th><th>{copy.quantileSpread}</th><th>{copy.selectionTurnover}</th><th>{copy.timingExposure}</th></tr></thead>
                      <tbody>{signals.data.rows.map((row) => (
                        <tr key={row.signal_date}>
                          <td>{row.signal_date}</td><td>{metric(row.coverage, "pct")}</td><td>{metric(row.ic, "number")}</td>
                          <td>{metric(row.quantile_spread, "pct")}</td><td>{metric(row.selection_turnover, "pct")}</td><td>{metric(row.timing_exposure, "pct")}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                </>
              ) : null}
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
