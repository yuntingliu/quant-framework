import { Fragment, useEffect, useMemo, useState } from "react"
import {
  Braces,
  CheckCircle2,
  CircleAlert,
  Code2,
  Copy,
  FileCode2,
  FlaskConical,
  Layers3,
  ListChecks,
  Play,
  Plus,
  Save,
  ShieldCheck,
  Sigma,
  Target,
  Trash2,
  Workflow,
  X,
} from "lucide-react"

import { useLanguage } from "../../contexts/LanguageContext"
import { useWorkspace } from "../../contexts/WorkspaceContext"
import { useWorkspaceRefresh } from "../../hooks/useWorkspaceRefresh"
import {
  api,
  type FactorResearchLibrary,
  type SignalResult,
  type StrategyConfigPayload,
  type StrategyFactorConfig,
  type StrategyTemplate,
  type StrategyTemplateDetail,
  type StrategyValidationResult,
} from "../../lib/api"
import { useDataProfile, type DataProfile } from "../../lib/data-profile"
import { TimingStrategyWorkbenchWidget } from "./TimingStrategyWorkbench"
import type { StrategyStage } from "./strategy-workbench-types"

type EditorView = "builder" | "selection" | "python" | "yaml"
type CreateMode = "new" | "clone"

const STOCK_SELECTION_PYTHON_TEMPLATE = `def generate(context):
    """Return target stock weights using only the supplied point-in-time context."""
    ranked = []
    for candidate in context["candidates"]:
        closes = [row["close"] for row in candidate["history"] if row["close"] is not None]
        if len(closes) < 20 or closes[-20] <= 0:
            continue
        momentum = closes[-1] / closes[-20] - 1.0
        ranked.append((momentum, candidate["symbol"]))

    ranked.sort(reverse=True)
    selected = ranked[:context["limits"]["max_stocks"]]
    count = len(selected)
    weight = min(context["limits"]["max_weight"], 1.0 / count) if count else 0.0
    return {"weights": {symbol: weight for _, symbol in selected}}
`

function numeric(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function StockSelectionStrategyWorkbenchWidget({ stage }: { stage: StrategyStage }) {
  const { language } = useLanguage()
  const refreshRevision = useWorkspaceRefresh()
  const { selectedStrategy, setSelectedStrategy } = useWorkspace()
  const [profile, setProfile] = useDataProfile()
  const copy = language === "zh" ? {
    title: "策略工作台",
    description: "把因子组合、股票池、持仓与交易假设组成可执行策略，再交给回测验证。",
    factors: "个因子",
    template: "内置模板",
    local: "本地策略",
    selectStrategy: "请选择策略",
    readOnly: "内置只读",
    editable: "本地可编辑",
    draft: "未保存草稿",
    unsaved: "有未保存修改",
    builder: "可视化配置",
    selectionPreview: "选股预览",
    python: "Python 代码",
    yaml: "高级 YAML",
    overview: "策略说明",
    overviewHint: "策略 ID 与文件名保持一致；内置模板需先克隆。",
    descriptionLabel: "策略描述",
    implementation: "实现方式",
    configuredImplementation: "配置策略",
    pythonImplementation: "Python 策略",
    timeout: "单次执行超时（秒）",
    pythonTitle: "自定义选股 Python",
    pythonHint: "代码在独立子进程运行并受超时约束，但属于可信本地代码，不是恶意代码安全沙箱。只能返回候选股票的非负目标权重。",
    pythonContract: "入口必须为 generate(context)，返回 {'weights': {'股票代码': 权重}}。context 包含截至当日的候选股票历史、因子分数、当前权重和组合上限。",
    universe: "股票池与数据门槛",
    universeHint: "选择信号覆盖的股票池与所需历史长度；留空代码表示使用整个数据池。",
    pool: "数据池",
    symbols: "股票代码（逗号分隔）",
    minPrice: "最低价格",
    minHistory: "最少历史天数",
    minAmount: "最低平均成交额",
    maxStale: "最大陈旧天数",
    positiveVolume: "要求成交量大于 0",
    factorMix: "因子组合",
    factorHint: "因子先分别打分，再按方向和权重合成为一个选股分数。这里展示的是策略引用，因子检验结果尚未持久绑定。",
    addFactor: "添加注册因子",
    addCustomFactor: "添加自定义因子",
    customExpression: "自定义因子表达式",
    add: "添加",
    normalize: "权重归一化",
    factorName: "因子",
    direction: "方向",
    weight: "权重",
    winsorize: "缩尾比例",
    neutralize: "市值中性化",
    evidence: "检验依据",
    noEvidence: "未绑定检验记录",
    long: "越大越好",
    short: "越小越好",
    testFactor: "去因子工作台检验",
    selection: "选股与组合",
    coverage: "最低因子覆盖率",
    stockCount: "持股数量",
    fullSelectionWeight: "满额入选时单股权重",
    maxWeight: "单股权重上限",
    rebalance: "调仓频率",
    monthly: "每月",
    weekly: "每周",
    optimizer: "组合方式",
    equalWeight: "等权",
    portfolioHint: "把横截面分数转成可回测的持仓。当前引擎真实执行前 N 名等权组合。",
    risk: "风险控制",
    riskHint: "这些是回测引擎真正执行的硬约束：可交易性门槛、单股集中度和数据新鲜度。",
    riskCapacity: "理论最高股票仓位",
    riskCash: "至少保留现金",
    enforced: "引擎强制执行",
    execution: "交易与成本假设",
    executionHint: "信号在调仓日形成，按下一交易日价格成交，并受资金容量、成交参与率和成本约束。",
    cost: "交易成本（bps）",
    slippage: "滑点（bps）",
    impact: "冲击成本（bps）",
    price: "成交价格",
    nextOpen: "下一交易日开盘",
    nextClose: "下一交易日收盘",
    capital: "组合资金",
    participation: "最大成交参与率",
    validation: "策略校验",
    notValidated: "修改后尚未校验",
    valid: "可执行",
    invalid: "不可执行",
    validate: "校验策略",
    save: "保存本地策略",
    backtest: "用此策略去回测",
    saveBeforeBacktest: "请先校验并保存修改，再进入回测。",
    localId: "新策略 ID",
    create: "新建策略",
    clone: "克隆",
    strategies: "策略列表",
    newDraftTitle: "创建空白策略",
    cloneTitle: "克隆当前策略",
    cancel: "取消",
    createHint: "新建为空白草稿；添加至少一个因子并保存后才会写入本地。",
    invalidId: "策略 ID 需为 2–64 位小写字母、数字、下划线或连字符。",
    duplicateId: "该策略 ID 已存在。",
    remove: "删除本地策略",
    confirmRemove: "确定删除本地策略",
    confirmDiscard: "当前策略有未保存修改，确定放弃并切换吗？",
    emptyFactors: "尚未添加因子。策略至少需要一个正权重因子才能执行。",
    yamlHint: "YAML 是同一份策略的高级表示。切换视图时会由后端校验并双向同步。",
    saved: "本地策略已保存",
    cloned: "已创建本地策略",
    deleted: "本地策略已删除",
    validationPassed: "策略校验通过",
    validationFailed: "策略仍有阻断项",
    draftCreated: "空白策略草稿已创建，请添加至少一个因子后保存。",
    selectionTitle: "横截面选股结果",
    selectionHint: "按当前配置临时计算，不保存信号：股票池 → 数据过滤 → 因子排名 → 前 N 只 → 目标权重。",
    dataProfile: "数据环境",
    demoProfile: "示例数据",
    runtimeProfile: "本地数据",
    asOfDate: "截至日期",
    latestDate: "留空使用该数据环境的最新日期",
    runSelection: "生成选股预览",
    previewRunning: "正在计算...",
    previewInvalid: "请先修复策略校验中的阻断项。",
    previewReady: "选股预览已生成",
    selectedStocks: "实际入选",
    scoredStocks: "参与排名",
    eligibleStocks: "通过过滤",
    universeStocks: "初始股票池",
    cashWeight: "现金权重",
    rank: "排名",
    symbol: "股票",
    compositeScore: "综合分",
    factorCoverage: "因子覆盖",
    targetWeight: "目标权重",
    decision: "结果",
    selected: "入选",
    belowCutoff: "候选",
    factorBreakdown: "因子贡献",
    noPreview: "尚未生成选股结果。可直接预览未保存的当前配置；不会写入本地信号记录。",
    noPicks: "当前数据和过滤条件下没有股票入选。请检查数据环境、股票池、历史长度和因子覆盖率。",
    exclusions: "过滤原因",
  } : {
    title: "Strategy Workbench",
    description: "Combine factors, universe, portfolio, and execution assumptions into an executable strategy, then backtest it.",
    factors: "factors",
    template: "built-in",
    local: "local",
    selectStrategy: "Select a strategy",
    readOnly: "built-in · read only",
    editable: "local · editable",
    draft: "unsaved draft",
    unsaved: "unsaved changes",
    builder: "Visual builder",
    selectionPreview: "Selection preview",
    python: "Python code",
    yaml: "Advanced YAML",
    overview: "Strategy overview",
    overviewHint: "The strategy id matches its filename. Clone a built-in template before editing.",
    descriptionLabel: "Description",
    implementation: "Implementation",
    configuredImplementation: "Configured strategy",
    pythonImplementation: "Python strategy",
    timeout: "Per-call timeout (seconds)",
    pythonTitle: "Custom stock-selection Python",
    pythonHint: "Code runs in a timeout-bounded child process, but it is trusted local code rather than a malicious-code sandbox. It may return only non-negative weights for eligible candidates.",
    pythonContract: "Define generate(context) and return {'weights': {'SYMBOL': weight}}. Context contains point-in-time candidate history, factor scores, current weights, and portfolio limits.",
    universe: "Universe and data gates",
    universeHint: "These gates decide which stocks enter factor ranking on each rebalance date. Empty symbols use the full data pool.",
    pool: "Data pool",
    symbols: "Symbols (comma-separated)",
    minPrice: "Minimum price",
    minHistory: "Minimum history days",
    minAmount: "Minimum average amount",
    maxStale: "Maximum stale days",
    positiveVolume: "Require positive volume",
    factorMix: "Factor mix",
    factorHint: "Factors are scored separately and combined by direction and weight. This is a strategy reference; persisted factor evidence is not bound yet.",
    addFactor: "Add registered factor",
    addCustomFactor: "Add custom factor",
    customExpression: "Custom factor expression",
    add: "Add",
    normalize: "Normalize weights",
    factorName: "Factor",
    direction: "Direction",
    weight: "Weight",
    winsorize: "Winsorize",
    neutralize: "Market-cap neutralize",
    evidence: "Evidence",
    noEvidence: "No bound test record",
    long: "higher is better",
    short: "lower is better",
    testFactor: "Test in Factor Workbench",
    selection: "Selection and portfolio",
    coverage: "Minimum factor coverage",
    stockCount: "Number of stocks",
    fullSelectionWeight: "Per-stock weight at full selection",
    maxWeight: "Maximum stock weight",
    rebalance: "Rebalance frequency",
    monthly: "Monthly",
    weekly: "Weekly",
    optimizer: "Portfolio method",
    equalWeight: "Equal weight",
    portfolioHint: "Translate cross-sectional scores into backtestable holdings. The current engine actually executes an equal-weight top-N portfolio.",
    risk: "Risk controls",
    riskHint: "These are hard constraints enforced by the backtest engine: investability gates, single-name concentration, and data freshness.",
    riskCapacity: "Maximum theoretical stock exposure",
    riskCash: "Minimum residual cash",
    enforced: "Enforced by engine",
    execution: "Execution and cost assumptions",
    executionHint: "Signals form on rebalance dates, trade on the next session, and remain subject to capital, participation, and cost constraints.",
    cost: "Trading cost (bps)",
    slippage: "Slippage (bps)",
    impact: "Impact (bps)",
    price: "Execution price",
    nextOpen: "Next session open",
    nextClose: "Next session close",
    capital: "Portfolio value",
    participation: "Maximum participation rate",
    validation: "Strategy validation",
    notValidated: "Not validated after changes",
    valid: "executable",
    invalid: "blocked",
    validate: "Validate strategy",
    save: "Save local strategy",
    backtest: "Backtest this strategy",
    saveBeforeBacktest: "Validate and save changes before opening Backtest Workbench.",
    localId: "New strategy id",
    create: "New strategy",
    clone: "Clone",
    strategies: "Strategies",
    newDraftTitle: "Create blank strategy",
    cloneTitle: "Clone current strategy",
    cancel: "Cancel",
    createHint: "New starts as a blank draft. Add at least one factor and save before it is written locally.",
    invalidId: "Strategy id must be 2–64 lowercase letters, numbers, underscores, or hyphens.",
    duplicateId: "That strategy id already exists.",
    remove: "Delete local strategy",
    confirmRemove: "Delete local strategy",
    confirmDiscard: "This strategy has unsaved changes. Discard them and switch?",
    emptyFactors: "No factors yet. An executable strategy needs at least one factor with positive weight.",
    yamlHint: "YAML is the advanced representation of the same strategy. The backend validates and syncs both views when switching.",
    saved: "Local strategy saved",
    cloned: "Local strategy created",
    deleted: "Local strategy deleted",
    validationPassed: "Strategy validation passed",
    validationFailed: "Strategy still has blocking checks",
    draftCreated: "Blank strategy draft created. Add at least one factor, then save it.",
    selectionTitle: "Cross-sectional selection",
    selectionHint: "Computed temporarily from the current config and not persisted: universe → data gates → factor ranks → top N → target weights.",
    dataProfile: "Data profile",
    demoProfile: "Demo data",
    runtimeProfile: "Local data",
    asOfDate: "As-of date",
    latestDate: "Leave blank to use the latest date in this profile",
    runSelection: "Generate selection preview",
    previewRunning: "Computing...",
    previewInvalid: "Resolve the blocking strategy checks first.",
    previewReady: "Selection preview generated",
    selectedStocks: "Selected",
    scoredStocks: "Scored",
    eligibleStocks: "Eligible",
    universeStocks: "Initial universe",
    cashWeight: "Cash weight",
    rank: "Rank",
    symbol: "Symbol",
    compositeScore: "Composite",
    factorCoverage: "Factor coverage",
    targetWeight: "Target weight",
    decision: "Decision",
    selected: "Selected",
    belowCutoff: "Candidate",
    factorBreakdown: "Factor contribution",
    noPreview: "No selection result yet. Preview the current unsaved config without writing a local signal record.",
    noPicks: "No stocks passed the current data and selection gates. Check the profile, universe, history, and factor coverage.",
    exclusions: "Exclusion reasons",
  }

  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [knownStrategyIds, setKnownStrategyIds] = useState<string[]>([])
  const [strategyId, setStrategyId] = useState("")
  const [detail, setDetail] = useState<StrategyTemplateDetail | null>(null)
  const [config, setConfig] = useState<StrategyConfigPayload | null>(null)
  const [yaml, setYaml] = useState("")
  const [pythonSource, setPythonSource] = useState("")
  const [view, setView] = useState<EditorView>("builder")
  const [validation, setValidation] = useState<StrategyValidationResult | null>(null)
  const [library, setLibrary] = useState<FactorResearchLibrary | null>(null)
  const [factorToAdd, setFactorToAdd] = useState("")
  const [targetId, setTargetId] = useState("")
  const [createMode, setCreateMode] = useState<CreateMode | null>(null)
  const [isDraft, setIsDraft] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [selectionDate, setSelectionDate] = useState("")
  const [selectionResult, setSelectionResult] = useState<SignalResult | null>(null)

  const editable = Boolean(detail?.editable)
  const pythonEnabled = config?.implementation.kind === "python"
  const factorWeight = useMemo(
    () => config?.factors.reduce((total, factor) => total + factor.weight, 0) ?? 0,
    [config?.factors],
  )
  const availableFactors = useMemo(
    () => library?.factors.filter((item) => !config?.factors.some((factor) => factor.name === item.name)) ?? [],
    [config?.factors, library?.factors],
  )

  useEffect(() => {
    setView("builder")
  }, [stage])

  useEffect(() => {
    if (!availableFactors.some((factor) => factor.name === factorToAdd)) {
      setFactorToAdd(availableFactors[0]?.name ?? "")
    }
  }, [availableFactors, factorToAdd])

  async function loadStrategies(preferred?: string) {
    const allStrategies = await api.get<StrategyTemplate[]>("/strategies")
    setKnownStrategyIds(allStrategies.map((item) => item.id))
    const items = allStrategies
      .filter((item) => item.strategy_type === "stock_selection")
    setStrategies(items)
    const next = preferred && items.some((item) => item.id === preferred)
      ? preferred
      : strategyId && items.some((item) => item.id === strategyId)
        ? strategyId
        : items[0]?.id ?? ""
    setStrategyId(next)
    setIsDraft(false)
    setSelectedStrategy(next || null)
  }

  useEffect(() => {
    api.get<FactorResearchLibrary>("/factor-research/library")
      .then((result) => {
        setLibrary(result)
        setFactorToAdd(result.factors[0]?.name ?? "")
      })
      .catch((loadError: Error) => setError(loadError.message))
  }, [])

  useEffect(() => {
    if (isDraft) return
    let active = true
    api.get<StrategyTemplate[]>("/strategies")
      .then((items) => {
        if (!active) return
        setKnownStrategyIds(items.map((item) => item.id))
        const selectionStrategies = items.filter((item) => item.strategy_type === "stock_selection")
        setStrategies(selectionStrategies)
        const preferred = selectedStrategy && selectionStrategies.some((item) => item.id === selectedStrategy)
          ? selectedStrategy
          : selectionStrategies[0]?.id ?? ""
        setStrategyId(preferred)
        setSelectedStrategy(preferred || null)
      })
      .catch((loadError: Error) => active && setError(loadError.message))
    return () => { active = false }
  }, [isDraft, refreshRevision, selectedStrategy, setSelectedStrategy])

  useEffect(() => {
    if (isDraft) return
    if (!selectedStrategy || selectedStrategy === strategyId) return
    if (strategies.some((item) => item.id === selectedStrategy)) setStrategyId(selectedStrategy)
  }, [isDraft, selectedStrategy, strategies, strategyId])

  useEffect(() => {
    if (!strategyId || isDraft) return
    let active = true
    setError("")
    setMessage("")
    setDetail(null)
    setConfig(null)
    setSelectionResult(null)
    api.get<StrategyTemplateDetail>(`/strategies/${strategyId}`)
      .then(async (item) => {
        if (!active) return
        setDetail(item)
        setConfig(item.config)
        setYaml(item.yaml)
        setPythonSource(item.python_source ?? "")
        setTargetId(`${item.id}_local`)
        setDirty(false)
        setView("builder")
        const result = await api.post<StrategyValidationResult>("/strategies/validate", {
          yaml: item.yaml,
          ...(item.python_source ? { python_source: item.python_source } : {}),
        })
        if (active) setValidation(result)
      })
      .catch((loadError: Error) => active && setError(loadError.message))
    return () => { active = false }
  }, [isDraft, strategyId])

  function updateConfig(updater: (current: StrategyConfigPayload) => StrategyConfigPayload) {
    if (!editable) return
    setConfig((current) => current ? updater(current) : current)
    setDirty(true)
    setValidation(null)
    setSelectionResult(null)
    setMessage("")
  }

  function updateImplementation(kind: "configured" | "python") {
    if (!editable) return
    if (kind === "python" && !pythonSource.trim()) {
      setPythonSource(STOCK_SELECTION_PYTHON_TEMPLATE)
    }
    updateConfig((current) => ({
      ...current,
      implementation: { ...current.implementation, kind },
    }))
  }

  function updateFactor(index: number, patch: Partial<StrategyFactorConfig>) {
    updateConfig((current) => ({
      ...current,
      factors: current.factors.map((factor, factorIndex) => (
        factorIndex === index ? { ...factor, ...patch } : factor
      )),
    }))
  }

  function addFactor() {
    const registered = library?.factors.find((item) => item.name === factorToAdd)
    if (!registered || config?.factors.some((factor) => factor.name === registered.name)) return
    const remainingWeight = Math.max(0, 1 - factorWeight)
    updateConfig((current) => ({
      ...current,
      factors: [
        ...current.factors,
        {
          name: registered.name,
          source: registered.source,
          direction: "long",
          weight: remainingWeight > 0 ? Number(remainingWeight.toFixed(6)) : 0.1,
          winsorize: 0.01,
          neutralize: [],
        },
      ],
    }))
  }

  function addCustomFactor() {
    if (!config) return
    let suffix = 1
    let customName = "custom_factor"
    const existingNames = new Set(config.factors.map((factor) => factor.name))
    while (existingNames.has(customName)) {
      suffix += 1
      customName = `custom_factor_${suffix}`
    }
    const remainingWeight = Math.max(0, 1 - factorWeight)
    updateConfig((current) => ({
      ...current,
      factors: [
        ...current.factors,
        {
          name: customName,
          source: "expression",
          expression: "zscore(momentum_60d) - 0.5 * zscore(volatility_20d)",
          direction: "long",
          weight: remainingWeight > 0 ? Number(remainingWeight.toFixed(6)) : 0.1,
          winsorize: 0.01,
          neutralize: [],
        },
      ],
    }))
  }

  function normalizeWeights() {
    if (!config || factorWeight <= 0) return
    updateConfig((current) => ({
      ...current,
      factors: current.factors.map((factor) => ({
        ...factor,
        weight: Number((factor.weight / factorWeight).toFixed(6)),
      })),
    }))
  }

  async function requestValidation(sourceView: EditorView = view): Promise<StrategyValidationResult> {
    const representation = sourceView === "yaml" ? { yaml } : { config }
    const body = {
      ...representation,
      ...(pythonSource.trim() ? { python_source: pythonSource } : {}),
    }
    const result = await api.post<StrategyValidationResult>("/strategies/validate", body)
    setValidation(result)
    setConfig(result.config)
    setYaml(result.normalized_yaml)
    if (result.config.implementation.kind === "python" && result.python_source != null) {
      setPythonSource(result.python_source)
    }
    return result
  }

  async function validate() {
    setBusy(true)
    setError("")
    try {
      const result = await requestValidation()
      setMessage(result.valid ? copy.validationPassed : copy.validationFailed)
    } catch (validationError) {
      setValidation(null)
      setError(validationError instanceof Error ? validationError.message : String(validationError))
    } finally {
      setBusy(false)
    }
  }

  async function switchView(next: EditorView) {
    if (next === view) return
    setBusy(true)
    setError("")
    try {
      await requestValidation(view)
      setView(next)
    } catch (switchError) {
      setError(switchError instanceof Error ? switchError.message : String(switchError))
    } finally {
      setBusy(false)
    }
  }

  async function previewSelection() {
    if (!config) return
    setBusy(true)
    setError("")
    setMessage("")
    try {
      const result = await requestValidation("selection")
      if (!result.valid) {
        setSelectionResult(null)
        setMessage(copy.previewInvalid)
        return
      }
      const preview = await api.post<SignalResult>("/strategies/selection-preview", {
        config: result.config,
        ...(result.config.implementation.kind === "python" ? { python_source: pythonSource } : {}),
        profile,
        ...(selectionDate ? { as_of_date: selectionDate } : {}),
      })
      setSelectionResult(preview)
      setMessage(copy.previewReady)
    } catch (previewError) {
      setSelectionResult(null)
      setError(previewError instanceof Error ? previewError.message : String(previewError))
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    if (!detail?.editable) return
    setBusy(true)
    setError("")
    try {
      const result = await requestValidation()
      if (!result.valid) {
        setMessage(copy.validationFailed)
        return
      }
      const saved = await api.put<StrategyTemplateDetail>(`/strategies/${detail.id}`, {
        yaml: result.normalized_yaml,
        ...(result.config.implementation.kind === "python" ? { python_source: pythonSource } : {}),
      })
      setDetail(saved)
      setConfig(saved.config)
      setYaml(saved.yaml)
      setPythonSource(saved.python_source ?? "")
      setDirty(false)
      setMessage(copy.saved)
      await loadStrategies(saved.id)
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError))
    } finally {
      setBusy(false)
    }
  }

  async function clone() {
    setBusy(true)
    setError("")
    try {
      const saved = await api.post<StrategyTemplateDetail>(
        `/strategies/${strategyId}/clone`,
        { target_id: targetId },
      )
      setMessage(copy.cloned)
      setCreateMode(null)
      await loadStrategies(saved.id)
    } catch (cloneError) {
      setError(cloneError instanceof Error ? cloneError.message : String(cloneError))
    } finally {
      setBusy(false)
    }
  }

  async function createDraft() {
    if (dirty && !window.confirm(copy.confirmDiscard)) return
    const normalized = targetId.trim().toLowerCase()
    if (!/^[a-z0-9][a-z0-9_-]{1,63}$/.test(normalized)) {
      setError(copy.invalidId)
      return
    }
    if (knownStrategyIds.includes(normalized)) {
      setError(copy.duplicateId)
      return
    }
    setBusy(true)
    setError("")
    try {
      const result = await api.post<StrategyValidationResult>("/strategies/validate", {
        config: { name: normalized },
      })
      const draft: StrategyTemplateDetail = {
        id: normalized,
        strategy_type: "stock_selection",
        name: normalized,
        description: "",
        path: "",
        factors: [],
        warnings: result.warnings,
        built_in: false,
        editable: true,
        source: "local",
        implementation: "configured",
        yaml: result.normalized_yaml,
        config: result.config,
        python_source: null,
        python_source_sha256: null,
      }
      setIsDraft(true)
      setTargetId(normalized)
      setStrategyId(normalized)
      setDetail(draft)
      setConfig(result.config)
      setYaml(result.normalized_yaml)
      setPythonSource("")
      setValidation(result)
      setDirty(true)
      setSelectionResult(null)
      setView("builder")
      setCreateMode(null)
      setMessage(copy.draftCreated)
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : String(createError))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!detail?.editable || !window.confirm(`${copy.confirmRemove} ${detail.id}?`)) return
    setBusy(true)
    setError("")
    try {
      await api.delete<void>(`/strategies/${detail.id}`)
      setMessage(copy.deleted)
      setDetail(null)
      setConfig(null)
      setYaml("")
      setPythonSource("")
      await loadStrategies()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : String(deleteError))
    } finally {
      setBusy(false)
    }
  }

  function openFactorWorkbench() {
    window.dispatchEvent(new CustomEvent("alphalab:openWidget", {
      detail: { widgetId: "factor.workbench", mode: "factor" },
    }))
  }

  function selectStrategy(nextStrategyId: string) {
    if (nextStrategyId === strategyId) return
    if (dirty && !window.confirm(copy.confirmDiscard)) return
    setCreateMode(null)
    setIsDraft(false)
    setStrategyId(nextStrategyId)
    setSelectedStrategy(nextStrategyId)
  }

  function openCreatePanel(mode: CreateMode) {
    setError("")
    setMessage("")
    setCreateMode(mode)
    if (mode === "clone" && detail) {
      setTargetId(`${detail.id}_local`)
      return
    }
    let candidate = "my_strategy"
    let suffix = 2
    while (knownStrategyIds.includes(candidate)) {
      candidate = `my_strategy_${suffix}`
      suffix += 1
    }
    setTargetId(candidate)
  }

  function openBacktestWorkbench() {
    if (!detail || dirty || !validation?.valid) {
      setMessage(copy.saveBeforeBacktest)
      return
    }
    setSelectedStrategy(detail.id)
    window.dispatchEvent(new CustomEvent("alphalab:openWidget", {
      detail: { widgetId: "backtest.workbench", mode: "backtest" },
    }))
  }

  return (
    <div className="strategy-workbench">
      <header className="strategy-workbench-heading">
        <div>
          <h2>{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
        <FileCode2 size={18} />
      </header>

      {(error || message) && (
        <div className={error ? "strategy-flash error" : "strategy-flash"}>
          {error ? <CircleAlert size={14} /> : <CheckCircle2 size={14} />}
          <span>{error || message}</span>
        </div>
      )}

      <div className="strategy-workbench-layout">
        <aside className="strategy-sidebar">
          <div className="strategy-sidebar-heading">
            <strong>{copy.strategies}</strong>
            <button type="button" title={copy.create} onClick={() => openCreatePanel("new")}>
              <Plus size={13} /> {copy.create}
            </button>
          </div>
          {createMode && (
            <form
              className="strategy-create-panel"
              onSubmit={(event) => {
                event.preventDefault()
                if (createMode === "new") void createDraft()
                else void clone()
              }}
            >
              <div>
                <strong>{createMode === "new" ? copy.newDraftTitle : copy.cloneTitle}</strong>
                <button type="button" title={copy.cancel} onClick={() => setCreateMode(null)}><X size={13} /></button>
              </div>
              <label htmlFor="strategy-target-id">{copy.localId}</label>
              <input
                id="strategy-target-id"
                autoFocus
                value={targetId}
                onChange={(event) => setTargetId(event.target.value)}
              />
              <button type="submit" className="primary" disabled={busy || !targetId}>
                {createMode === "new" ? <Plus size={13} /> : <Copy size={13} />}
                {createMode === "new" ? copy.create : copy.clone}
              </button>
              {createMode === "new" && <small>{copy.createHint}</small>}
            </form>
          )}
          <div className="strategy-list-scroll">
            {isDraft && detail && (
              <button type="button" className="active">
                <strong>{detail.name}</strong>
                <span>{copy.draft}</span>
              </button>
            )}
            {strategies.map((strategy) => (
              <button
                key={strategy.id}
                type="button"
                className={strategy.id === strategyId ? "active" : ""}
                onClick={() => selectStrategy(strategy.id)}
              >
                <strong>{strategy.name}</strong>
                <span>{strategy.implementation === "python" ? "Python" : `${strategy.factors.length} ${copy.factors}`} · {strategy.built_in ? copy.template : copy.local}</span>
              </button>
            ))}
          </div>
        </aside>

        <main className="strategy-main">
          <div className="strategy-detail-bar">
            <div>
              <strong>{detail?.name ?? copy.selectStrategy}</strong>
              <span>{detail?.description}</span>
            </div>
            <div className="strategy-detail-actions">
              <button type="button" className="strategy-clone-trigger" onClick={() => openCreatePanel("clone")} disabled={busy || isDraft || !detail}>
                <Copy size={13} /> {copy.clone}
              </button>
              <div className="strategy-state-pills">
                {dirty && <span className="warning">{copy.unsaved}</span>}
                <span className={detail?.editable ? "editable" : ""}>{isDraft ? copy.draft : detail?.built_in ? copy.readOnly : copy.editable}</span>
              </div>
            </div>
          </div>

          <div className="strategy-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={view === "builder"} onClick={() => void switchView("builder")}>
              <ListChecks size={14} /> {copy.builder}
            </button>
            <button type="button" role="tab" aria-selected={view === "selection"} onClick={() => void switchView("selection")}>
              <Target size={14} /> {copy.selectionPreview}
            </button>
            {pythonEnabled && (
              <button type="button" role="tab" aria-selected={view === "python"} onClick={() => void switchView("python")}>
                <Code2 size={14} /> {copy.python}
              </button>
            )}
            <button type="button" role="tab" aria-selected={view === "yaml"} onClick={() => void switchView("yaml")}>
              <Braces size={14} /> {copy.yaml}
            </button>
          </div>

          <div className="strategy-editor-scroll">
            {view === "builder" && config && (
              <div className="strategy-builder">
                {stage === "signal" && <section className="strategy-section strategy-section-wide">
                  <div className="strategy-section-heading">
                    <div><h3>{copy.overview}</h3><p>{copy.overviewHint}</p></div>
                  </div>
                  <div className="strategy-field-grid two">
                    <label><span>ID</span><input value={config.name} readOnly /></label>
                    <label><span>{copy.descriptionLabel}</span><input value={config.description} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, description: event.target.value }))} /></label>
                    <label><span>{copy.implementation}</span><select value={config.implementation.kind} disabled={!editable} onChange={(event) => updateImplementation(event.target.value as "configured" | "python")}><option value="configured">{copy.configuredImplementation}</option><option value="python">{copy.pythonImplementation}</option></select></label>
                    <label><span>{copy.timeout}</span><input type="number" min="0.1" max="30" step="0.5" value={config.implementation.timeout_seconds} disabled={!editable || !pythonEnabled} onChange={(event) => updateConfig((current) => ({ ...current, implementation: { ...current.implementation, timeout_seconds: numeric(event.target.value) } }))} /></label>
                  </div>
                </section>}

                {stage === "signal" && <section className="strategy-section strategy-section-wide">
                  <div className="strategy-section-heading">
                    <div><h3>{copy.universe}</h3><p>{copy.universeHint}</p></div>
                  </div>
                  <div className="strategy-field-grid three">
                    <label><span>{copy.pool}</span><input value={config.universe.pool} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, universe: { ...current.universe, pool: event.target.value } }))} /></label>
                    <label className="span-two"><span>{copy.symbols}</span><input placeholder="000001.XSHE, 600000.XSHG" value={config.universe.symbols.join(", ")} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, universe: { ...current.universe, symbols: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) } }))} /></label>
                    <label><span>{copy.minHistory}</span><input type="number" min="2" step="1" value={config.universe.min_history_days} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, universe: { ...current.universe, min_history_days: numeric(event.target.value) } }))} /></label>
                  </div>
                </section>}

                {stage === "signal" && <section className="strategy-section strategy-section-wide">
                  <div className="strategy-section-heading factor-heading">
                    <div><h3>{copy.factorMix}</h3><p>{copy.factorHint}</p></div>
                    <button type="button" className="strategy-link-button" onClick={openFactorWorkbench}><FlaskConical size={13} />{copy.testFactor}</button>
                  </div>
                  <div className="strategy-factor-toolbar">
                    <select aria-label={copy.addFactor} value={factorToAdd} disabled={!editable || availableFactors.length === 0} onChange={(event) => setFactorToAdd(event.target.value)}>
                      {availableFactors.map((factor) => <option key={factor.name} value={factor.name}>{factor.name} · {factor.source}</option>)}
                    </select>
                    <button type="button" onClick={addFactor} disabled={!editable || !factorToAdd || availableFactors.length === 0}><Plus size={13} />{copy.add}</button>
                    <button type="button" onClick={addCustomFactor} disabled={!editable}><Sigma size={13} />{copy.addCustomFactor}</button>
                    <button type="button" onClick={normalizeWeights} disabled={!editable || factorWeight <= 0}>{copy.normalize}</button>
                    <span>Σ {factorWeight.toFixed(4)}</span>
                  </div>
                  {config.factors.length === 0 ? <div className="strategy-empty-row">{copy.emptyFactors}</div> : (
                    <div className="strategy-factor-table">
                      <div className="strategy-factor-table-head">
                        <span>{copy.factorName}</span><span>{copy.direction}</span><span>{copy.weight}</span><span>{copy.winsorize}</span><span>{copy.neutralize}</span><span>{copy.evidence}</span><span />
                      </div>
                      {config.factors.map((factor, index) => (
                        <Fragment key={`${factor.source}-${index}`}>
                        <div className="strategy-factor-row">
                          <div className="strategy-factor-name">
                            {factor.source === "expression"
                              ? <input aria-label={copy.factorName} value={factor.name} disabled={!editable} onChange={(event) => updateFactor(index, { name: event.target.value })} />
                              : <strong>{factor.name}</strong>}
                            <span>{factor.source}</span>
                          </div>
                          <select value={factor.direction} disabled={!editable} onChange={(event) => updateFactor(index, { direction: event.target.value as "long" | "short" })}><option value="long">{copy.long}</option><option value="short">{copy.short}</option></select>
                          <input aria-label={`${factor.name} ${copy.weight}`} type="number" min="0" step="0.05" value={factor.weight} disabled={!editable} onChange={(event) => updateFactor(index, { weight: numeric(event.target.value) })} />
                          <input aria-label={`${factor.name} ${copy.winsorize}`} type="number" min="0" max="0.249" step="0.005" value={factor.winsorize} disabled={!editable} onChange={(event) => updateFactor(index, { winsorize: numeric(event.target.value) })} />
                          <label className="factor-neutralize-check"><input type="checkbox" checked={factor.neutralize.includes("market_cap")} disabled={!editable} onChange={(event) => updateFactor(index, { neutralize: event.target.checked ? ["market_cap"] : [] })} /><span>{copy.neutralize}</span></label>
                          <span className="strategy-evidence">{copy.noEvidence}</span>
                          <button type="button" className="strategy-remove-factor" title={copy.remove} disabled={!editable} onClick={() => updateConfig((current) => ({ ...current, factors: current.factors.filter((_, factorIndex) => factorIndex !== index) }))}><X size={13} /></button>
                        </div>
                        {factor.source === "expression" && (
                          <label className="strategy-factor-expression">
                            <span>{copy.customExpression}</span>
                            <textarea value={factor.expression ?? ""} disabled={!editable} spellCheck={false} onChange={(event) => updateFactor(index, { expression: event.target.value })} />
                          </label>
                        )}
                        </Fragment>
                      ))}
                    </div>
                  )}
                </section>}

                {stage === "portfolio" && <section className="strategy-section strategy-section-wide">
                  <div className="strategy-section-heading"><div><h3>{copy.selection}</h3><p>{copy.portfolioHint}</p></div></div>
                  <div className="strategy-field-grid two">
                    <label><span>{copy.coverage}</span><input type="number" min="0" max="1" step="0.05" value={config.selection.min_factor_coverage} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, selection: { ...current.selection, min_factor_coverage: numeric(event.target.value) } }))} /></label>
                    <label><span>{copy.stockCount}</span><input type="number" min="1" step="1" value={config.selection.n_stocks} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, selection: { ...current.selection, n_stocks: numeric(event.target.value) } }))} /></label>
                    <label><span>{copy.rebalance}</span><select value={config.portfolio.rebalance_freq} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, portfolio: { ...current.portfolio, rebalance_freq: event.target.value as "monthly" | "weekly" } }))}><option value="monthly">{copy.monthly}</option><option value="weekly">{copy.weekly}</option></select></label>
                    <label><span>{copy.optimizer}</span><select value={config.portfolio.optimizer} disabled><option value="equal_weight">{copy.equalWeight}</option></select></label>
                  </div>
                  <div className="strategy-stage-audit">
                    <div><span>{copy.stockCount}</span><strong>{config.selection.n_stocks}</strong></div>
                    <div><span>{copy.fullSelectionWeight}</span><strong>{config.selection.n_stocks > 0 ? `${(Math.min(1 / config.selection.n_stocks, config.portfolio.max_weight) * 100).toFixed(1)}%` : "—"}</strong></div>
                    <div><span>{copy.rebalance}</span><strong>{config.portfolio.rebalance_freq === "monthly" ? copy.monthly : copy.weekly}</strong></div>
                  </div>
                </section>}

                {stage === "risk" && <section className="strategy-section strategy-section-wide">
                  <div className="strategy-section-heading"><div><h3>{copy.risk}</h3><p>{copy.riskHint}</p></div><span className="strategy-enforced-pill"><ShieldCheck size={12} />{copy.enforced}</span></div>
                  <div className="strategy-field-grid three">
                    <label><span>{copy.maxWeight}</span><input type="number" min="0.001" max="1" step="0.01" value={config.portfolio.max_weight} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, portfolio: { ...current.portfolio, max_weight: numeric(event.target.value) } }))} /></label>
                    <label><span>{copy.minPrice}</span><input type="number" min="0" step="0.01" value={config.universe.min_price} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, universe: { ...current.universe, min_price: numeric(event.target.value) } }))} /></label>
                    <label><span>{copy.minAmount}</span><input type="number" min="0" step="1000" value={config.universe.min_average_amount} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, universe: { ...current.universe, min_average_amount: numeric(event.target.value) } }))} /></label>
                    <label><span>{copy.maxStale}</span><input type="number" min="0" step="1" value={config.universe.max_stale_days} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, universe: { ...current.universe, max_stale_days: numeric(event.target.value) } }))} /></label>
                    <label className="strategy-checkbox"><input type="checkbox" checked={config.universe.require_positive_volume} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, universe: { ...current.universe, require_positive_volume: event.target.checked } }))} /><span>{copy.positiveVolume}</span></label>
                  </div>
                  <div className="strategy-stage-audit">
                    <div><span>{copy.riskCapacity}</span><strong>{`${(Math.min(1, config.selection.n_stocks * config.portfolio.max_weight) * 100).toFixed(1)}%`}</strong></div>
                    <div><span>{copy.riskCash}</span><strong>{`${(Math.max(0, 1 - config.selection.n_stocks * config.portfolio.max_weight) * 100).toFixed(1)}%`}</strong></div>
                    <div><span>{copy.maxWeight}</span><strong>{`${(config.portfolio.max_weight * 100).toFixed(1)}%`}</strong></div>
                  </div>
                </section>}

                {stage === "execution" && <section className="strategy-section strategy-section-wide">
                  <div className="strategy-section-heading"><div><h3>{copy.execution}</h3><p>{copy.executionHint}</p></div></div>
                  <div className="strategy-field-grid two">
                    <label><span>{copy.cost}</span><input type="number" min="0" step="1" value={config.execution.cost_bps} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, execution: { ...current.execution, cost_bps: numeric(event.target.value) } }))} /></label>
                    <label><span>{copy.slippage}</span><input type="number" min="0" step="1" value={config.execution.slippage_bps} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, execution: { ...current.execution, slippage_bps: numeric(event.target.value) } }))} /></label>
                    <label><span>{copy.impact}</span><input type="number" min="0" step="1" value={config.execution.impact_bps} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, execution: { ...current.execution, impact_bps: numeric(event.target.value) } }))} /></label>
                    <label><span>{copy.price}</span><select value={config.execution.execution_price} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, execution: { ...current.execution, execution_price: event.target.value as "next_open" | "next_close" } }))}><option value="next_open">{copy.nextOpen}</option><option value="next_close">{copy.nextClose}</option></select></label>
                    <label><span>{copy.capital}</span><input type="number" min="1" step="10000" value={config.execution.portfolio_value} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, execution: { ...current.execution, portfolio_value: numeric(event.target.value) } }))} /></label>
                    <label><span>{copy.participation}</span><input type="number" min="0.001" max="1" step="0.01" value={config.execution.max_participation_rate} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, execution: { ...current.execution, max_participation_rate: numeric(event.target.value) } }))} /></label>
                  </div>
                </section>}

                <section className="strategy-section strategy-section-wide">
                  <div className="strategy-section-heading"><div><h3>{copy.validation}</h3><p>{validation ? (validation.valid ? copy.valid : copy.invalid) : copy.notValidated}</p></div></div>
                  {validation ? (
                    <div className="strategy-checks">
                      {validation.checks.map((check, index) => (
                        <div key={`${check.code}-${index}`} className={`strategy-check ${check.status}`}>
                          {check.status === "passed" ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}
                          <span>{check.message}</span>
                        </div>
                      ))}
                    </div>
                  ) : <div className="strategy-empty-row">{copy.notValidated}</div>}
                </section>
              </div>
            )}

            {view === "selection" && config && (
              <div className="strategy-selection-view">
                <section className="strategy-selection-header">
                  <div>
                    <h3>{copy.selectionTitle}</h3>
                    <p>{copy.selectionHint}</p>
                  </div>
                  <div className="strategy-selection-controls">
                    <label>
                      <span>{copy.dataProfile}</span>
                      <select value={profile} onChange={(event) => { setProfile(event.target.value as DataProfile); setSelectionResult(null) }}>
                        <option value="demo">{copy.demoProfile}</option>
                        <option value="runtime">{copy.runtimeProfile}</option>
                      </select>
                    </label>
                    <label>
                      <span>{copy.asOfDate}</span>
                      <input type="date" value={selectionDate} title={copy.latestDate} onChange={(event) => { setSelectionDate(event.target.value); setSelectionResult(null) }} />
                    </label>
                    <button type="button" className="icon-text-command primary" onClick={() => void previewSelection()} disabled={busy || (!pythonEnabled && config.factors.length === 0)}>
                      <Target /> {busy ? copy.previewRunning : copy.runSelection}
                    </button>
                  </div>
                </section>

                {!selectionResult ? (
                  <div className="strategy-selection-empty"><Target size={20} /><span>{copy.noPreview}</span></div>
                ) : (
                  <>
                    <div className="strategy-selection-context">
                      <strong>{selectionResult.strategy_id}</strong>
                      <span>{selectionResult.selection.as_of_date}</span>
                      <span>{selectionResult.profile === "demo" ? copy.demoProfile : copy.runtimeProfile}</span>
                      <span>Top {selectionResult.selection.requested_count}</span>
                    </div>
                    <div className="strategy-selection-kpis">
                      <div><span>{copy.selectedStocks}</span><strong>{selectionResult.selection.selected_count}</strong></div>
                      <div><span>{copy.scoredStocks}</span><strong>{selectionResult.selection.scored_count}</strong></div>
                      <div><span>{copy.eligibleStocks}</span><strong>{selectionResult.selection.eligible_count}</strong></div>
                      <div><span>{copy.universeStocks}</span><strong>{selectionResult.selection.universe_size}</strong></div>
                      <div><span>{copy.cashWeight}</span><strong>{(selectionResult.selection.cash_weight * 100).toFixed(1)}%</strong></div>
                    </div>
                    {Object.keys(selectionResult.selection.exclusions).length > 0 && (
                      <div className="strategy-selection-exclusions">
                        <strong>{copy.exclusions}</strong>
                        {Object.entries(selectionResult.selection.exclusions).map(([reason, count]) => (
                          <span key={reason}>{reason.replace(/_/g, " ")} · {count}</span>
                        ))}
                      </div>
                    )}
                    {selectionResult.selection.rows.length === 0 ? (
                      <div className="strategy-selection-empty warning"><CircleAlert size={20} /><span>{copy.noPicks}</span></div>
                    ) : (
                      <div className="strategy-selection-table-wrap">
                        <table className="strategy-selection-table">
                          <thead>
                            <tr>
                              <th>{copy.rank}</th>
                              <th>{copy.symbol}</th>
                              <th>{copy.compositeScore}</th>
                              <th>{copy.factorCoverage}</th>
                              <th>{copy.targetWeight}</th>
                              <th>{copy.decision}</th>
                              <th>{copy.factorBreakdown}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {selectionResult.selection.rows.map((row) => (
                              <tr key={row.symbol} className={row.selected ? "selected" : ""}>
                                <td>{row.rank}</td>
                                <td><strong>{row.symbol}</strong></td>
                                <td>{row.composite_score.toFixed(4)}</td>
                                <td>{(row.factor_coverage * 100).toFixed(0)}%</td>
                                <td>{row.selected ? `${(row.target_weight * 100).toFixed(2)}%` : "—"}</td>
                                <td><span className={row.selected ? "selection-decision selected" : "selection-decision"}>{row.selected ? copy.selected : copy.belowCutoff}</span></td>
                                <td>
                                  <div className="strategy-factor-breakdown">
                                    {selectionResult.selection.factor_names.map((name) => (
                                      <span key={name}>{name} {row.factor_scores[name] == null ? "—" : row.factor_scores[name]?.toFixed(3)}</span>
                                    ))}
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {view === "python" && pythonEnabled && (
              <div className="strategy-yaml-view strategy-python-view">
                <div className="strategy-python-notice">
                  <CircleAlert size={15} />
                  <div><strong>{copy.pythonTitle}</strong><p>{copy.pythonHint}</p><p>{copy.pythonContract}</p></div>
                </div>
                <textarea
                  className="code-view code-editor"
                  aria-label={copy.python}
                  value={pythonSource}
                  readOnly={!editable}
                  spellCheck={false}
                  onChange={(event) => {
                    setPythonSource(event.target.value)
                    setDirty(true)
                    setValidation(null)
                    setSelectionResult(null)
                    setMessage("")
                  }}
                />
              </div>
            )}

            {view === "yaml" && (
              <div className="strategy-yaml-view">
                <p>{copy.yamlHint}</p>
                <textarea className="code-view code-editor" aria-label={copy.yaml} value={yaml} readOnly={!editable} spellCheck={false} onChange={(event) => { setYaml(event.target.value); setDirty(true); setValidation(null); setSelectionResult(null); setMessage("") }} />
              </div>
            )}
          </div>

          <footer className="strategy-actions">
            <button type="button" className="icon-text-command" onClick={validate} disabled={busy || (!yaml && !config)}><CheckCircle2 />{copy.validate}</button>
            <button type="button" className="icon-text-command primary" onClick={save} disabled={busy || !editable || !dirty}><Save />{copy.save}</button>
            <button type="button" className="icon-text-command strategy-backtest-button" onClick={openBacktestWorkbench} disabled={busy || !detail}><Play />{copy.backtest}</button>
            <button type="button" className="icon-command danger" title={copy.remove} onClick={remove} disabled={busy || isDraft || !editable}><Trash2 /></button>
          </footer>
        </main>
      </div>
    </div>
  )
}

export function StrategyWorkbenchWidget() {
  const { language } = useLanguage()
  const [strategyDomain, setStrategyDomain] = useState<"stock_selection" | "market_timing">(() => {
    const requested = typeof window !== "undefined"
      ? new URLSearchParams(window.location.search).get("strategyType")
      : null
    return requested === "market_timing" ? requested : "stock_selection"
  })
  const [stage, setStage] = useState<StrategyStage>("signal")
  const stages: Array<{ id: StrategyStage; title: string; description: string; icon: typeof Workflow }> = language === "zh"
    ? [
        { id: "signal", title: "信号设计", description: "研究买什么或何时持有", icon: Workflow },
        { id: "portfolio", title: "组合构建", description: "把分数转成权重与仓位", icon: Layers3 },
        { id: "risk", title: "风险控制", description: "设置引擎强制执行的边界", icon: ShieldCheck },
        { id: "execution", title: "交易执行", description: "定义成交、容量与成本", icon: Play },
      ]
    : [
        { id: "signal", title: "Signal design", description: "Research what or when to own", icon: Workflow },
        { id: "portfolio", title: "Portfolio construction", description: "Translate scores into weights", icon: Layers3 },
        { id: "risk", title: "Risk controls", description: "Set engine-enforced boundaries", icon: ShieldCheck },
        { id: "execution", title: "Trade execution", description: "Define fills, capacity, and costs", icon: Play },
      ]
  const activeStage = stages.find((item) => item.id === stage) ?? stages[0]

  return (
    <div className="strategy-domain-shell">
      <header className="strategy-workflow-header">
        <div className="strategy-signal-switch" role="group" aria-label={language === "zh" ? "信号类型" : "Signal type"}>
          <span>{language === "zh" ? "信号类型" : "Signal type"}</span>
          <button type="button" className={strategyDomain === "stock_selection" ? "active" : ""} onClick={() => setStrategyDomain("stock_selection")}><Target size={14} />{language === "zh" ? "选股信号" : "Stock selection"}</button>
          <button type="button" className={strategyDomain === "market_timing" ? "active" : ""} onClick={() => setStrategyDomain("market_timing")}><FlaskConical size={14} />{language === "zh" ? "择时信号" : "Market timing"}</button>
        </div>
        <nav className="strategy-stage-tabs" aria-label={language === "zh" ? "研究流程" : "Research workflow"}>
          {stages.map((item, index) => {
            const Icon = item.icon
            return <button key={item.id} type="button" className={stage === item.id ? "active" : ""} aria-current={stage === item.id ? "step" : undefined} onClick={() => setStage(item.id)}><small>{index + 1}</small><Icon size={14} /><span>{item.title}</span></button>
          })}
        </nav>
        <div className="strategy-stage-context"><strong>{activeStage.title}</strong><span>{activeStage.description}</span></div>
      </header>
      {strategyDomain === "stock_selection"
        ? <StockSelectionStrategyWorkbenchWidget stage={stage} />
        : <TimingStrategyWorkbenchWidget stage={stage} />}
    </div>
  )
}
