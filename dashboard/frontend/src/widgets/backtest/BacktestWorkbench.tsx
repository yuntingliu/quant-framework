import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Clock3, Database, Play, ReceiptText, RefreshCw, Save, ShieldCheck } from "lucide-react"

import { CorrelationHeatmap, CumulativeReturnsChart, DrawdownChart } from "@/components/charts"
import { CSVExportButton } from "@/components/shared/CSVExportButton"
import { useLanguage } from "@/contexts/LanguageContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import {
  api,
  type BacktestAnalysis,
  type BacktestAttribution,
  type BacktestJob,
  type BacktestRecord,
  type BacktestRobustness,
  type BacktestSignalDiagnostics,
  type DataManifest,
  type ProviderStatus,
  type PipelineProjectDetail,
  type PipelineProjectSummary,
  type RuntimeCatalog,
} from "@/lib/api"
import { useDataProfile } from "@/lib/data-profile"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"
import { analyticsError } from "@/widgets/market/analytics-utils"
import { BacktestCompareWidget } from "./BacktestCompare"

type WorkbenchTab = "pipeline" | "performance" | "signals" | "attribution" | "robustness" | "execution" | "holdings"
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

function CorrelationTable({
  labels,
  matrix,
  exactValuesLabel,
}: {
  labels: string[]
  matrix: Array<Array<number | null>>
  exactValuesLabel: string
}) {
  if (!labels.length || !matrix.length) return <div className="analytics-empty">—</div>
  return (
    <div className="backtest-correlation-view">
      <CorrelationHeatmap labels={labels} matrix={matrix} />
      <details>
        <summary>{exactValuesLabel}</summary>
        <div className="analytics-table-wrap">
          <table className="analytics-table compact">
            <thead><tr><th>Factor</th>{labels.map((label) => <th key={label}>{label}</th>)}</tr></thead>
            <tbody>{labels.map((label, rowIndex) => (
              <tr key={label}><td><strong>{label}</strong></td>{labels.map((column, columnIndex) => (
                <td key={column}>{metric(matrix[rowIndex]?.[columnIndex], "number")}</td>
              ))}</tr>
            ))}</tbody>
          </table>
        </div>
      </details>
    </div>
  )
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function asFiniteNumber(value: unknown, fallback: number): number {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
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
    startDate: "回测开始日期",
    endDate: "回测结束日期",
    running: "运行中",
    queued: "已进入回测队列",
    runningBackground: "完整策略正在后台回测，离开此页面也不会中断。",
    run: "运行",
    quickRun: "运行完整策略回测",
    runSettings: "运行设置",
    runHint: "在一个地方确认数据区间和成交假设；调仓频率与目标仓位始终继承信号模型。",
    strategySetup: "策略与样本",
    executionAssumptions: "成交与成本假设",
    executionAssumptionsHint: "每个信号日重新计算排名和目标仓位，并在下一可交易日按以下假设成交。",
    rebalanceFrequency: "调仓频率",
    frequencyHint: "由信号模型控制",
    allocationPolicy: "仓位策略",
    equalWeight: "等权",
    scoreWeight: "按综合得分",
    rankDecay: "按排名衰减",
    executionPrice: "成交价格",
    nextOpen: "下一交易日开盘",
    nextClose: "下一交易日收盘",
    portfolioValue: "模拟资金（元）",
    commission: "手续费（bps）",
    slippage: "滑点（bps）",
    marketImpact: "冲击成本（bps）",
    participation: "最大成交占比（%）",
    saveExecution: "保存回测假设",
    savingExecution: "保存中",
    executionSaved: "回测假设已保存",
    executionUnsaved: "成交假设有改动，请先保存",
    readonlyExecution: "内置项目只读；复制项目后可调整成交假设。",
    invalidExecution: "请检查模拟资金、成本和最大成交占比。",
    daily: "日频",
    weekly: "周频",
    monthly: "月频",
    paperAwaiting: "模拟调仓等待用户确认",
    savedBacktest: "已保存的回测",
    savedRun: "回测记录",
    noBacktests: "没有已保存的回测。",
    resultView: "回测结果视图",
    pipeline: "策略快照",
    pipelineSnapshot: "本次回测实际保存的策略管线",
    pipelineHint: "信号排名与仓位分配来自信号模型，成交假设来自本次回测设置；组件版本、总 Python 源码、数据和代码指纹一起固化。",
    customModule: "本次回测的自定义 Python 模块",
    hardGate: "核心闸门",
    performance: "收益与基准",
    signals: "信号诊断",
    signalEvidence: "信号预测能力",
    signalEvidenceHint: "这里使用该 BacktestRun 已保存的每期横截面评分和后续收益；它不重新运行策略。",
    meanIc: "平均 Rank IC",
    positiveIc: "IC 为正比例",
    scoreCoverage: "评分覆盖率",
    selectionTurnover: "信号集合换手",
    quantileSpread: "头尾组收益差",
    evidencePeriods: "有效检验期",
    attribution: "Alpha/Beta 归因",
    attributionHint: "使用随 BacktestRun 冻结的月频策略收益与因子收益，不重新执行策略。",
    capmAlpha: "CAPM 年化 Alpha",
    marketBeta: "市场 Beta",
    rSquared: "R²",
    attributionPeriods: "回归样本",
    multiFactor: "多因子回归",
    factorReturnCorrelation: "因子收益相关性（Pearson）",
    selectionFactorCorrelation: "信号因子截面相关性（Spearman 中位数）",
    factorReturnCorrelationHint: "检验因子收益序列是否长期同涨同跌，避免组合中堆叠高度相似的风险来源。",
    selectionFactorCorrelationHint: "汇总每个调仓截面的因子得分相关性，观察选股信息是否重复。",
    exactValues: "查看精确数值",
    estimate: "估计值",
    tStat: "t 值",
    robustness: "稳健性",
    holdings: "持仓",
    execution: "换手与成本",
    currentResult: "当前结果",
    resultIdentity: "结果身份",
    runAt: "运行于",
    strategySnapshot: "策略快照",
    benchmark: "等权基准",
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
    startDate: "Backtest start date",
    endDate: "Backtest end date",
    running: "Running",
    queued: "Backtest queued",
    runningBackground: "The complete strategy is running in the background and will continue if you leave this page.",
    run: "Run",
    quickRun: "Run full strategy backtest",
    runSettings: "Run setup",
    runHint: "Confirm the sample and fill assumptions in one place. Rebalance frequency and target weights always come from the Signal Model.",
    strategySetup: "Strategy & sample",
    executionAssumptions: "Fill & cost assumptions",
    executionAssumptionsHint: "Rankings and target weights are recomputed on each signal date, then filled on the next tradable session using these assumptions.",
    rebalanceFrequency: "Rebalance frequency",
    frequencyHint: "Controlled by Signal Model",
    allocationPolicy: "Allocation policy",
    equalWeight: "Equal weight",
    scoreWeight: "Score weighted",
    rankDecay: "Rank decay",
    executionPrice: "Fill price",
    nextOpen: "Next-session open",
    nextClose: "Next-session close",
    portfolioValue: "Portfolio value",
    commission: "Commission (bps)",
    slippage: "Slippage (bps)",
    marketImpact: "Market impact (bps)",
    participation: "Max participation (%)",
    saveExecution: "Save backtest assumptions",
    savingExecution: "Saving",
    executionSaved: "Backtest assumptions saved",
    executionUnsaved: "Save changed fill assumptions before running",
    readonlyExecution: "Built-in projects are read-only. Clone the project to change fill assumptions.",
    invalidExecution: "Check portfolio value, costs, and maximum participation.",
    daily: "Daily",
    weekly: "Weekly",
    monthly: "Monthly",
    paperAwaiting: "paper rebalance awaits confirmation",
    savedBacktest: "Saved backtest",
    savedRun: "Saved run",
    noBacktests: "has no persisted backtests.",
    resultView: "Backtest result view",
    pipeline: "Strategy Snapshot",
    pipelineSnapshot: "Persisted pipeline used by this backtest",
    pipelineHint: "Signal ranking and allocation come from Signal Model, while fill assumptions come from this backtest setup. Component versions, Python source, data, and code fingerprints are frozen together.",
    customModule: "Persisted custom Python module",
    hardGate: "Core gate",
    performance: "Returns & Benchmark",
    signals: "Signal Diagnostics",
    signalEvidence: "Signal predictive evidence",
    signalEvidenceHint: "Derived from the cross-sectional scores and forward returns persisted in this BacktestRun; the strategy is not rerun.",
    meanIc: "Mean Rank IC",
    positiveIc: "Positive IC ratio",
    scoreCoverage: "Score coverage",
    selectionTurnover: "Selection turnover",
    quantileSpread: "Top-bottom return",
    evidencePeriods: "evidence periods",
    attribution: "Alpha/Beta Attribution",
    attributionHint: "Uses monthly strategy and factor returns frozen with the BacktestRun; the strategy is not rerun.",
    capmAlpha: "CAPM annual alpha",
    marketBeta: "Market beta",
    rSquared: "R²",
    attributionPeriods: "Regression observations",
    multiFactor: "Multi-factor regression",
    factorReturnCorrelation: "Factor-return correlation (Pearson)",
    selectionFactorCorrelation: "Selection-factor cross-sectional correlation (median Spearman)",
    factorReturnCorrelationHint: "Shows whether factor-return series move together through time, helping detect duplicated risk sources.",
    selectionFactorCorrelationHint: "Aggregates score correlations across rebalance cross-sections to reveal overlapping selection information.",
    exactValues: "View exact values",
    estimate: "Estimate",
    tStat: "t-stat",
    robustness: "Robustness",
    holdings: "Holdings",
    execution: "Trading & Costs",
    currentResult: "Current result",
    resultIdentity: "Result identity",
    runAt: "run at",
    strategySnapshot: "Strategy snapshot",
    benchmark: "Equal-weight benchmark",
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
    weight: "Weight",
  }
  const {
    selectedStrategy,
    selectedStrategyRevision,
    setActiveMode,
    selectedBacktest,
    setSelectedBacktest,
    setSelectedStrategyRevision,
  } = useWorkspace()
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
  const [project, setProject] = useState<PipelineProjectDetail | null>(null)
  const [signalFrequency, setSignalFrequency] = useState("monthly")
  const [allocationMethod, setAllocationMethod] = useState("equal_weight")
  const [executionPrice, setExecutionPrice] = useState("next_open")
  const [portfolioValue, setPortfolioValue] = useState(1_000_000)
  const [costBps, setCostBps] = useState(20)
  const [slippageBps, setSlippageBps] = useState(0)
  const [impactBps, setImpactBps] = useState(0)
  const [participationPercent, setParticipationPercent] = useState(10)
  const [executionDirty, setExecutionDirty] = useState(false)
  const [executionSaved, setExecutionSaved] = useState(false)
  const [savingExecution, setSavingExecution] = useState(false)

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
    if (!strategyId) {
      setProject(null)
      return
    }
    let cancelled = false
    api.get<PipelineProjectDetail>(`/pipeline/projects/${strategyId}`)
      .then((value) => {
        if (cancelled) return
        const selection = asRecord(value.component_manifest.find((item) => item.stage === "selection")?.parameters)
        const portfolio = asRecord(value.component_manifest.find((item) => item.stage === "portfolio")?.parameters)
        const execution = asRecord(value.component_manifest.find((item) => item.stage === "execution")?.parameters)
        setProject(value)
        setSignalFrequency(String(selection.signal_frequency || "monthly"))
        setAllocationMethod(String(portfolio.optimizer || "equal_weight"))
        setExecutionPrice(String(execution.execution_price) === "next_close" ? "next_close" : "next_open")
        setPortfolioValue(asFiniteNumber(execution.portfolio_value, 1_000_000))
        setCostBps(asFiniteNumber(execution.cost_bps, 20))
        setSlippageBps(asFiniteNumber(execution.slippage_bps, 0))
        setImpactBps(asFiniteNumber(execution.impact_bps, 0))
        setParticipationPercent(asFiniteNumber(execution.max_participation_rate, 0.1) * 100)
        setExecutionDirty(false)
        setExecutionSaved(false)
        setSelectedStrategyRevision(value.revision)
      })
      .catch((error: Error) => {
        if (!cancelled) setSetupError(error.message)
      })
    return () => {
      cancelled = true
    }
  }, [strategyId, selectedStrategyRevision, setSelectedStrategyRevision])

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
  const attribution = useQuery({
    queryKey: ["backtests", "attribution", selectedId],
    queryFn: () => api.get<BacktestAttribution>(`/backtests/${selectedId}/attribution`),
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

  async function saveExecutionAssumptions() {
    if (!project?.editable || invalidExecutionSettings) return
    setSavingExecution(true)
    setSetupError("")
    setExecutionSaved(false)
    try {
      const stageParameters = asRecord(project.settings.stage_parameters)
      const currentExecution = asRecord(stageParameters.execution)
      const saved = await api.put<PipelineProjectDetail>(`/pipeline/projects/${project.id}`, {
        name: project.name,
        description: project.description,
        components: project.components,
        settings: {
          ...project.settings,
          stage_parameters: {
            ...stageParameters,
            execution: {
              ...currentExecution,
              execution_price: executionPrice,
              portfolio_value: portfolioValue,
              cost_bps: costBps,
              slippage_bps: slippageBps,
              impact_bps: impactBps,
              max_participation_rate: participationPercent / 100,
            },
          },
        },
      })
      setProject(saved)
      setStrategies((current) => current.map((item) => item.id === saved.id ? saved : item))
      setExecutionDirty(false)
      setExecutionSaved(true)
      setSelectedStrategyRevision(saved.revision)
      window.dispatchEvent(new CustomEvent("alphalab:projectUpdated", { detail: saved }))
    } catch (error) {
      setSetupError(error instanceof Error ? error.message : String(error))
    } finally {
      setSavingExecution(false)
    }
  }

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
  const invalidDateRange = Boolean(startDate && endDate && startDate > endDate)
  const invalidExecutionSettings = !Number.isFinite(portfolioValue)
    || portfolioValue <= 0
    || !Number.isFinite(costBps)
    || costBps < 0
    || !Number.isFinite(slippageBps)
    || slippageBps < 0
    || !Number.isFinite(impactBps)
    || impactBps < 0
    || !Number.isFinite(participationPercent)
    || participationPercent <= 0
    || participationPercent > 100
  const frequencyLabel = signalFrequency === "daily"
    ? copy.daily
    : signalFrequency === "weekly"
      ? copy.weekly
      : signalFrequency === "monthly"
        ? copy.monthly
        : signalFrequency
  const allocationLabel = allocationMethod === "score_weight"
    ? copy.scoreWeight
    : allocationMethod === "rank_decay"
      ? copy.rankDecay
      : copy.equalWeight
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
              Python Pipeline · {selectedStrategyDefinition.name} · revision {selectedStrategyDefinition.revision}
            </small>
          )}
        </div>
        <div className="backtest-setup-grid">
          <div className="backtest-setup-card">
            <div className="backtest-setup-card-heading">
              <strong>{copy.strategySetup}</strong>
              <span>{copy.frequencyHint}</span>
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
              <div className="pipeline-pinned-component readonly">
                <span>{copy.rebalanceFrequency}</span>
                <strong>{frequencyLabel}</strong>
              </div>
              <div className="pipeline-pinned-component readonly">
                <span>{copy.allocationPolicy}</span>
                <strong>{allocationLabel}</strong>
              </div>
              <label>
                <span>{copy.startDate}</span>
                <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
              </label>
              <label>
                <span>{copy.endDate}</span>
                <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
              </label>
            </div>
          </div>
          <div className="backtest-setup-card">
            <div className="backtest-setup-card-heading">
              <div><strong>{copy.executionAssumptions}</strong><span>{copy.executionAssumptionsHint}</span></div>
              {project && <small>{project.editable ? (executionDirty ? copy.executionUnsaved : executionSaved ? copy.executionSaved : `revision ${project.revision}`) : copy.readonlyExecution}</small>}
            </div>
            <div className="backtest-execution-controls">
              <label>
                <span>{copy.executionPrice}</span>
                <select
                  value={executionPrice}
                  disabled={!project?.editable}
                  onChange={(event) => { setExecutionPrice(event.target.value); setExecutionDirty(true); setExecutionSaved(false) }}
                >
                  <option value="next_open">{copy.nextOpen}</option>
                  <option value="next_close">{copy.nextClose}</option>
                </select>
              </label>
              <label><span>{copy.portfolioValue}</span><input type="number" min="1" step="10000" value={portfolioValue} disabled={!project?.editable} onChange={(event) => { setPortfolioValue(Number(event.target.value)); setExecutionDirty(true); setExecutionSaved(false) }} /></label>
              <label><span>{copy.commission}</span><input type="number" min="0" step="1" value={costBps} disabled={!project?.editable} onChange={(event) => { setCostBps(Number(event.target.value)); setExecutionDirty(true); setExecutionSaved(false) }} /></label>
              <label><span>{copy.slippage}</span><input type="number" min="0" step="1" value={slippageBps} disabled={!project?.editable} onChange={(event) => { setSlippageBps(Number(event.target.value)); setExecutionDirty(true); setExecutionSaved(false) }} /></label>
              <label><span>{copy.marketImpact}</span><input type="number" min="0" step="1" value={impactBps} disabled={!project?.editable} onChange={(event) => { setImpactBps(Number(event.target.value)); setExecutionDirty(true); setExecutionSaved(false) }} /></label>
              <label><span>{copy.participation}</span><input type="number" min="0.01" max="100" step="1" value={participationPercent} disabled={!project?.editable} onChange={(event) => { setParticipationPercent(Number(event.target.value)); setExecutionDirty(true); setExecutionSaved(false) }} /></label>
            </div>
          </div>
        </div>
        <div className="backtest-run-footer">
          <span className={executionDirty || invalidExecutionSettings ? "warning" : ""}>
            {invalidExecutionSettings ? copy.invalidExecution : executionDirty ? copy.executionUnsaved : copy.executionAssumptionsHint}
          </span>
          <div className="backtest-run-actions">
            {project?.editable && (
              <button
                className="secondary-command"
                type="button"
                onClick={() => void saveExecutionAssumptions()}
                disabled={savingExecution || !executionDirty || invalidExecutionSettings}
              >
                <Save aria-hidden="true" />
                {savingExecution ? copy.savingExecution : copy.saveExecution}
              </button>
            )}
            <button
              className="primary-command"
              type="button"
              onClick={runBacktest}
              disabled={running || savingExecution || executionDirty || invalidExecutionSettings || !strategyId || !startDate || !endDate || invalidDateRange}
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
              aria-selected={tab === "attribution"}
              onClick={() => setTab("attribution")}
            >
              {copy.attribution}
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
              {copy.holdings}
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
                        { key: "benchmark", name: copy.benchmark },
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
                    <div className="analytics-kpi"><span>{copy.evidencePeriods}</span><strong>{signals.data.evidence_periods}/{signals.data.periods}</strong></div>
                  </div>
                  <div className="analytics-table-wrap">
                    <table className="analytics-table compact">
                      <thead><tr><th>{copy.signalDate}</th><th>{copy.scoreCoverage}</th><th>{copy.meanIc}</th><th>{copy.quantileSpread}</th><th>{copy.selectionTurnover}</th></tr></thead>
                      <tbody>{signals.data.rows.map((row) => (
                        <tr key={row.signal_date}>
                          <td>{row.signal_date}</td><td>{metric(row.coverage, "pct")}</td><td>{metric(row.ic, "number")}</td>
                          <td>{metric(row.quantile_spread, "pct")}</td><td>{metric(row.selection_turnover, "pct")}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                </>
              ) : null}
            </div>
          ) : tab === "attribution" ? (
            <div className="workbench-body">
              {attribution.isLoading ? (
                <div className="analytics-empty">{copy.loadingResult}</div>
              ) : attribution.error ? (
                <div className="workbench-message error">{analyticsError(attribution.error)}</div>
              ) : attribution.data ? (
                <>
                  <div className="backtest-section-heading">
                    <div><strong>{copy.attribution}</strong><span>{copy.attributionHint}</span></div>
                  </div>
                  {attribution.data.warnings.map((warning) => <div className="workbench-message warning" key={warning}>{warning}</div>)}
                  <div className="analytics-kpi-grid workbench-kpis">
                    <div className="analytics-kpi"><span>{copy.capmAlpha}</span><strong>{metric(attribution.data.capm.alpha_annualized, "pct")}</strong></div>
                    <div className="analytics-kpi"><span>{copy.marketBeta}</span><strong>{metric(attribution.data.capm.betas.MKT, "number")}</strong></div>
                    <div className="analytics-kpi"><span>{copy.rSquared}</span><strong>{metric(attribution.data.capm.r_squared, "pct")}</strong></div>
                    <div className="analytics-kpi"><span>{copy.attributionPeriods}</span><strong>{attribution.data.observations}</strong></div>
                  </div>
                  <div className="backtest-section-heading"><div><strong>{copy.multiFactor}</strong></div></div>
                  <div className="analytics-table-wrap">
                    <table className="analytics-table compact">
                      <thead><tr><th>Factor</th><th>{copy.estimate}</th><th>{copy.tStat}</th></tr></thead>
                      <tbody>{Object.entries(attribution.data.multi_factor.estimates).map(([name, value]) => (
                        <tr key={name}><td><strong>{name}</strong></td><td>{metric(value.estimate, name === "alpha" ? "pct" : "number")}</td><td>{metric(value.t_stat, "number")}</td></tr>
                      ))}</tbody>
                    </table>
                  </div>
                  <div className="backtest-correlation-grid">
                    <section className="backtest-correlation-card">
                      <div className="backtest-section-heading"><div><strong>{copy.factorReturnCorrelation}</strong><span>{copy.factorReturnCorrelationHint}</span></div></div>
                      <CorrelationTable labels={attribution.data.factor_return_correlation.labels} matrix={attribution.data.factor_return_correlation.pearson} exactValuesLabel={copy.exactValues} />
                    </section>
                    <section className="backtest-correlation-card">
                      <div className="backtest-section-heading"><div><strong>{copy.selectionFactorCorrelation}</strong><span>{copy.selectionFactorCorrelationHint}</span></div></div>
                      <CorrelationTable labels={attribution.data.selection_score_correlation.labels} matrix={attribution.data.selection_score_correlation.median_spearman} exactValuesLabel={copy.exactValues} />
                    </section>
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
