import { useEffect, useMemo, useState } from "react"
import {
  Activity,
  Braces,
  CheckCircle2,
  CircleAlert,
  Code2,
  Copy,
  Gauge,
  Play,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react"
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { useLanguage } from "../../contexts/LanguageContext"
import { useWorkspace } from "../../contexts/WorkspaceContext"
import { useWorkspaceRefresh } from "../../hooks/useWorkspaceRefresh"
import {
  api,
  type DataManifest,
  type ProviderStatus,
  type StrategyTemplate,
  type TimingResearchResult,
  type TimingSignalConfig,
  type TimingStrategyConfigPayload,
  type TimingStrategyTemplateDetail,
  type TimingStrategyValidationResult,
} from "../../lib/api"
import { CHART_COLORS } from "../../lib/constants"
import { useDataProfile } from "../../lib/data-profile"

type TimingView = "builder" | "research" | "python" | "yaml"
type CreateMode = "new" | "clone"

const TIMING_PYTHON_TEMPLATE = `def generate(context):
    """Return a market exposure using only MKT returns available as of the signal date."""
    values = [row["value"] for row in context["market_returns"]]
    momentum = 1.0
    for value in values[-12:]:
        momentum *= 1.0 + value
    exposure = context["limits"]["max_exposure"] if momentum >= 1.0 else context["limits"]["min_exposure"]
    return {"market_exposure": exposure}
`

function numberValue(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function percent(value: number | null | undefined, digits = 1): string {
  return value == null ? "—" : `${(value * 100).toFixed(digits)}%`
}

export function TimingStrategyWorkbenchWidget() {
  const { language } = useLanguage()
  const refreshRevision = useWorkspaceRefresh()
  const { selectedStrategy, setSelectedStrategy } = useWorkspace()
  const [profile, setProfile] = useDataProfile()
  const copy = language === "zh" ? {
    title: "择时策略研究",
    description: "用市场时序信号决定整体仓位；信号在月末形成，下一期才应用，避免前视偏差。",
    strategies: "择时策略",
    create: "新建策略",
    newDraft: "创建空白择时策略",
    clone: "克隆当前模板",
    strategyId: "新策略 ID",
    cancel: "取消",
    builtIn: "内置模板 · 只读",
    local: "本地策略 · 可编辑",
    draft: "未保存草稿",
    unsaved: "有未保存修改",
    builder: "信号与仓位",
    research: "择时研究",
    python: "Python 代码",
    yaml: "高级 YAML",
    overview: "策略说明",
    descriptionLabel: "策略描述",
    implementation: "实现方式",
    configuredImplementation: "配置信号",
    pythonImplementation: "Python 策略",
    timeout: "批量执行超时（秒）",
    pythonTitle: "自定义择时 Python",
    pythonHint: "代码在独立子进程批量计算各月信号并受超时约束，但属于可信本地代码，不是恶意代码安全沙箱。",
    pythonContract: "入口必须为 generate(context)，返回 {'market_exposure': 数值}，且必须落在当前配置的仓位上下限内。context 只包含截至信号日的 MKT 历史、仓位上下限和 metadata。",
    market: "择时对象",
    marketHint: "当前基础版直接对 MKT 市场因子做月频仓位管理，不等同于个股选股。",
    marketFactor: "市场收益序列",
    frequency: "决策频率",
    monthly: "月频",
    signals: "择时信号",
    signalHint: "每个信号输出 0–1 分数，加权后映射为整体市场仓位。",
    addSignal: "添加信号",
    normalize: "权重归一化",
    kind: "信号",
    weight: "权重",
    window: "回看月数",
    threshold: "阈值",
    trend: "趋势",
    momentum: "动量",
    volatility_control: "波动率控制",
    noSignals: "尚未添加信号。至少需要一个正权重信号。",
    position: "仓位映射",
    minExposure: "最低市场仓位",
    maxExposure: "最高市场仓位",
    execution: "换仓成本",
    cost: "交易成本（bps）",
    slippage: "滑点（bps）",
    validation: "策略校验",
    validate: "校验策略",
    save: "保存本地策略",
    backtest: "用此策略去回测",
    remove: "删除本地策略",
    researchTitle: "历史择时表现",
    researchHint: "这里可直接研究未保存配置，不产生回测记录，也不会生成个股订单。",
    dataProfile: "数据环境",
    demo: "示例数据",
    runtime: "本地数据",
    start: "开始日期",
    end: "结束日期",
    run: "运行择时研究",
    running: "计算中...",
    latestExposure: "当前建议仓位",
    averageExposure: "历史平均仓位",
    totalReturn: "策略总收益",
    sharpe: "夏普比率",
    maxDrawdown: "最大回撤",
    signalDate: "信号日期",
    score: "综合信号",
    exposure: "建议仓位",
    signalDetail: "信号分项",
    noResearch: "尚未运行择时研究。设置区间后可直接检验当前配置。",
    saved: "择时策略已保存",
    cloned: "已创建本地择时策略",
    deleted: "择时策略已删除",
    validated: "策略校验通过",
    invalid: "策略仍有阻断项",
    saveFirst: "请先校验并保存当前修改，再进入回测。",
    invalidId: "策略 ID 需为 2–64 位小写字母、数字、下划线或连字符。",
    duplicateId: "该策略 ID 已存在。",
    confirmDiscard: "当前策略有未保存修改，确定放弃吗？",
    confirmDelete: "确定删除本地择时策略",
    yamlHint: "YAML 与可视化配置是同一份择时策略；切换时由后端校验并同步。",
  } : {
    title: "Market-timing research",
    description: "Use market time-series signals to set aggregate exposure. Signals form at month-end and apply one period later.",
    strategies: "Timing strategies",
    create: "New strategy",
    newDraft: "Create blank timing strategy",
    clone: "Clone current template",
    strategyId: "New strategy ID",
    cancel: "Cancel",
    builtIn: "built-in · read only",
    local: "local · editable",
    draft: "unsaved draft",
    unsaved: "unsaved changes",
    builder: "Signals & exposure",
    research: "Timing research",
    python: "Python code",
    yaml: "Advanced YAML",
    overview: "Strategy overview",
    descriptionLabel: "Description",
    implementation: "Implementation",
    configuredImplementation: "Configured signals",
    pythonImplementation: "Python strategy",
    timeout: "Batch timeout (seconds)",
    pythonTitle: "Custom market-timing Python",
    pythonHint: "Code evaluates monthly signals in a timeout-bounded child process, but it is trusted local code rather than a malicious-code sandbox.",
    pythonContract: "Define generate(context) and return {'market_exposure': number} within the configured exposure limits. Context contains only point-in-time MKT history, exposure limits, and metadata.",
    market: "Timed market",
    marketHint: "The barebone version manages monthly exposure to MKT. It is separate from cross-sectional stock selection.",
    marketFactor: "Market return series",
    frequency: "Decision frequency",
    monthly: "Monthly",
    signals: "Timing signals",
    signalHint: "Each signal emits a 0–1 score. Their weighted score maps to aggregate market exposure.",
    addSignal: "Add signal",
    normalize: "Normalize weights",
    kind: "Signal",
    weight: "Weight",
    window: "Lookback months",
    threshold: "Threshold",
    trend: "Trend",
    momentum: "Momentum",
    volatility_control: "Volatility control",
    noSignals: "No signals yet. Add at least one signal with positive weight.",
    position: "Exposure mapping",
    minExposure: "Minimum exposure",
    maxExposure: "Maximum exposure",
    execution: "Turnover costs",
    cost: "Trading cost (bps)",
    slippage: "Slippage (bps)",
    validation: "Strategy validation",
    validate: "Validate strategy",
    save: "Save local strategy",
    backtest: "Backtest this strategy",
    remove: "Delete local strategy",
    researchTitle: "Historical timing behavior",
    researchHint: "Research the current unsaved config without persisting a backtest or creating stock orders.",
    dataProfile: "Data profile",
    demo: "Demo data",
    runtime: "Local data",
    start: "Start date",
    end: "End date",
    run: "Run timing research",
    running: "Computing...",
    latestExposure: "Current suggested exposure",
    averageExposure: "Average historical exposure",
    totalReturn: "Strategy total return",
    sharpe: "Sharpe ratio",
    maxDrawdown: "Maximum drawdown",
    signalDate: "Signal date",
    score: "Combined score",
    exposure: "Suggested exposure",
    signalDetail: "Signal components",
    noResearch: "No timing research yet. Choose a range and evaluate the current configuration.",
    saved: "Timing strategy saved",
    cloned: "Local timing strategy created",
    deleted: "Timing strategy deleted",
    validated: "Strategy validation passed",
    invalid: "Strategy still has blocking checks",
    saveFirst: "Validate and save the current changes before opening Backtest Workbench.",
    invalidId: "Strategy ID must be 2–64 lowercase letters, numbers, underscores, or hyphens.",
    duplicateId: "That strategy ID already exists.",
    confirmDiscard: "Discard the unsaved timing changes?",
    confirmDelete: "Delete local timing strategy",
    yamlHint: "YAML and the visual builder represent the same timing strategy. The backend validates and synchronizes them.",
  }

  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [knownStrategyIds, setKnownStrategyIds] = useState<string[]>([])
  const [strategyId, setStrategyId] = useState("")
  const [detail, setDetail] = useState<TimingStrategyTemplateDetail | null>(null)
  const [config, setConfig] = useState<TimingStrategyConfigPayload | null>(null)
  const [yaml, setYaml] = useState("")
  const [pythonSource, setPythonSource] = useState("")
  const [view, setView] = useState<TimingView>("builder")
  const [validation, setValidation] = useState<TimingStrategyValidationResult | null>(null)
  const [researchResult, setResearchResult] = useState<TimingResearchResult | null>(null)
  const [targetId, setTargetId] = useState("my_timing_strategy")
  const [createMode, setCreateMode] = useState<CreateMode | null>(null)
  const [isDraft, setIsDraft] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")

  const editable = Boolean(detail?.editable)
  const pythonEnabled = config?.implementation.kind === "python"
  const totalWeight = useMemo(
    () => config?.signals.reduce((total, signal) => total + signal.weight, 0) ?? 0,
    [config?.signals],
  )

  async function fetchTimingStrategies(): Promise<StrategyTemplate[]> {
    const allStrategies = await api.get<StrategyTemplate[]>("/strategies")
    setKnownStrategyIds(allStrategies.map((item) => item.id))
    return allStrategies
      .filter((item) => item.strategy_type === "market_timing")
  }

  async function loadStrategies(preferred?: string) {
    const items = await fetchTimingStrategies()
    setStrategies(items)
    const next = preferred && items.some((item) => item.id === preferred)
      ? preferred
      : strategyId && items.some((item) => item.id === strategyId)
        ? strategyId
        : items[0]?.id ?? ""
    setStrategyId(next)
    setSelectedStrategy(next || null)
    setIsDraft(false)
  }

  useEffect(() => {
    if (isDraft) return
    let active = true
    fetchTimingStrategies()
      .then((items) => {
        if (!active) return
        setStrategies(items)
        const preferred = selectedStrategy && items.some((item) => item.id === selectedStrategy)
          ? selectedStrategy
          : items[0]?.id ?? ""
        setStrategyId(preferred)
        setSelectedStrategy(preferred || null)
      })
      .catch((loadError: Error) => active && setError(loadError.message))
    return () => { active = false }
  }, [isDraft, refreshRevision, selectedStrategy, setSelectedStrategy])

  useEffect(() => {
    if (!strategyId || isDraft) return
    let active = true
    setError("")
    setMessage("")
    setResearchResult(null)
    api.get<TimingStrategyTemplateDetail>(`/strategies/${strategyId}`)
      .then(async (item) => {
        if (!active) return
        setDetail(item)
        setConfig(item.config)
        setYaml(item.yaml)
        setPythonSource(item.python_source ?? "")
        setTargetId(`${item.id}_local`)
        setDirty(false)
        setView("builder")
        const result = await api.post<TimingStrategyValidationResult>("/strategies/validate", {
          yaml: item.yaml,
          ...(item.python_source ? { python_source: item.python_source } : {}),
        })
        if (active) setValidation(result)
      })
      .catch((loadError: Error) => active && setError(loadError.message))
    return () => { active = false }
  }, [isDraft, strategyId])

  useEffect(() => {
    let active = true
    Promise.all([
      api.get<DataManifest>("/data/manifest"),
      api.get<ProviderStatus>("/data/providers"),
    ]).then(([manifest, providers]) => {
      if (!active) return
      if (profile === "runtime") {
        setStartDate(providers.profiles.runtime.latest_date ? providers.runtime.datasets.find((item) => item.id === "rq.bars")?.date_start ?? "" : "")
        setEndDate(providers.profiles.runtime.latest_date ?? "")
      } else {
        setStartDate(manifest.sample_start)
        setEndDate(manifest.cutoff_date)
      }
    }).catch((loadError: Error) => active && setError(loadError.message))
    return () => { active = false }
  }, [profile])

  function updateConfig(updater: (current: TimingStrategyConfigPayload) => TimingStrategyConfigPayload) {
    if (!editable) return
    setConfig((current) => current ? updater(current) : current)
    setDirty(true)
    setValidation(null)
    setResearchResult(null)
    setMessage("")
  }

  function updateImplementation(kind: "configured" | "python") {
    if (!editable) return
    if (kind === "python" && !pythonSource.trim()) {
      setPythonSource(TIMING_PYTHON_TEMPLATE)
    }
    updateConfig((current) => ({
      ...current,
      implementation: { ...current.implementation, kind },
    }))
  }

  function updateSignal(index: number, patch: Partial<TimingSignalConfig>) {
    updateConfig((current) => ({
      ...current,
      signals: current.signals.map((signal, signalIndex) => (
        signalIndex === index ? { ...signal, ...patch } : signal
      )),
    }))
  }

  function addSignal() {
    updateConfig((current) => ({
      ...current,
      signals: [...current.signals, { kind: "trend", weight: 0.25, window: 6, threshold: 0 }],
    }))
  }

  function normalizeSignals() {
    if (totalWeight <= 0) return
    updateConfig((current) => ({
      ...current,
      signals: current.signals.map((signal) => ({
        ...signal,
        weight: Number((signal.weight / totalWeight).toFixed(6)),
      })),
    }))
  }

  async function requestValidation(sourceView: TimingView = view) {
    const representation = sourceView === "yaml" ? { yaml } : { config }
    const body = {
      ...representation,
      ...(pythonSource.trim() ? { python_source: pythonSource } : {}),
    }
    const result = await api.post<TimingStrategyValidationResult>("/strategies/validate", body)
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
      setMessage(result.valid ? copy.validated : copy.invalid)
    } catch (validationError) {
      setError(validationError instanceof Error ? validationError.message : String(validationError))
    } finally {
      setBusy(false)
    }
  }

  async function switchView(next: TimingView) {
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

  async function runResearch() {
    if (!config || !startDate || !endDate) return
    setBusy(true)
    setError("")
    try {
      const result = await requestValidation(view)
      if (!result.valid) {
        setMessage(copy.invalid)
        return
      }
      const research = await api.post<TimingResearchResult>("/strategies/timing-research", {
        config: result.config,
        ...(result.config.implementation.kind === "python" ? { python_source: pythonSource } : {}),
        profile,
        start_date: startDate,
        end_date: endDate,
      })
      setResearchResult(research)
      setView("research")
    } catch (researchError) {
      setError(researchError instanceof Error ? researchError.message : String(researchError))
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
        setMessage(copy.invalid)
        return
      }
      const saved = await api.put<TimingStrategyTemplateDetail>(`/strategies/${detail.id}`, {
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
      const saved = await api.post<TimingStrategyTemplateDetail>(`/strategies/${strategyId}/clone`, { target_id: targetId })
      setCreateMode(null)
      setMessage(copy.cloned)
      await loadStrategies(saved.id)
    } catch (cloneError) {
      setError(cloneError instanceof Error ? cloneError.message : String(cloneError))
    } finally {
      setBusy(false)
    }
  }

  async function createDraft() {
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
      const result = await api.post<TimingStrategyValidationResult>("/strategies/validate", {
        config: { strategy_type: "market_timing", name: normalized },
      })
      const draft: TimingStrategyTemplateDetail = {
        id: normalized,
        strategy_type: "market_timing",
        name: normalized,
        description: "",
        path: "",
        factors: [],
        signals: result.signals,
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
      setDetail(draft)
      setConfig(result.config)
      setYaml(result.normalized_yaml)
      setPythonSource("")
      setValidation(result)
      setStrategyId(normalized)
      setSelectedStrategy(normalized)
      setIsDraft(true)
      setDirty(true)
      setCreateMode(null)
      setView("builder")
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : String(createError))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!detail?.editable || !window.confirm(`${copy.confirmDelete} ${detail.id}?`)) return
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

  function selectStrategy(next: string) {
    if (next === strategyId) return
    if (dirty && !window.confirm(copy.confirmDiscard)) return
    setCreateMode(null)
    setIsDraft(false)
    setStrategyId(next)
    setSelectedStrategy(next)
  }

  function openCreatePanel(mode: CreateMode) {
    setCreateMode(mode)
    setError("")
    let candidate = mode === "clone" && detail ? `${detail.id}_local` : "my_timing_strategy"
    let suffix = 2
    while (knownStrategyIds.includes(candidate)) {
      candidate = `my_timing_strategy_${suffix}`
      suffix += 1
    }
    setTargetId(candidate)
  }

  function openBacktest() {
    if (!detail || dirty || !validation?.valid) {
      setMessage(copy.saveFirst)
      return
    }
    setSelectedStrategy(detail.id)
    window.dispatchEvent(new CustomEvent("alphalab:openWidget", {
      detail: { widgetId: "backtest.workbench", mode: "backtest" },
    }))
  }

  return (
    <div className="strategy-workbench timing-strategy-workbench">
      <header className="strategy-workbench-heading">
        <div><h2>{copy.title}</h2><p>{copy.description}</p></div>
        <Activity size={18} />
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
            <button type="button" onClick={() => openCreatePanel("new")}><Plus size={13} /> {copy.create}</button>
          </div>
          {createMode && (
            <form className="strategy-create-panel" onSubmit={(event) => { event.preventDefault(); void (createMode === "new" ? createDraft() : clone()) }}>
              <div><strong>{createMode === "new" ? copy.newDraft : copy.clone}</strong><button type="button" title={copy.cancel} onClick={() => setCreateMode(null)}><X size={13} /></button></div>
              <label htmlFor="timing-strategy-id">{copy.strategyId}</label>
              <input id="timing-strategy-id" value={targetId} onChange={(event) => setTargetId(event.target.value)} />
              <button type="submit" className="primary" disabled={busy || !targetId}>{createMode === "new" ? <Plus size={13} /> : <Copy size={13} />}{createMode === "new" ? copy.create : copy.clone}</button>
            </form>
          )}
          <div className="strategy-list-scroll">
            {isDraft && detail && <button type="button" className="active"><strong>{detail.name}</strong><span>{copy.draft}</span></button>}
            {strategies.map((strategy) => (
              <button key={strategy.id} type="button" className={!isDraft && strategy.id === strategyId ? "active" : ""} onClick={() => selectStrategy(strategy.id)}>
                <strong>{strategy.name}</strong>
                <span>{strategy.built_in ? copy.builtIn : copy.local} · {strategy.implementation === "python" ? "Python" : `${strategy.signals?.length ?? 0} signals`}</span>
              </button>
            ))}
          </div>
        </aside>

        <main className="strategy-main">
          <div className="strategy-detail-bar">
            <div><strong>{detail?.name ?? "—"}</strong><span>{detail?.description || detail?.id || ""}</span></div>
            <div className="strategy-detail-actions">
              <div className="strategy-state-pills">
                <span className={detail?.editable ? "editable" : ""}>{isDraft ? copy.draft : detail?.editable ? copy.local : copy.builtIn}</span>
                {dirty && <span className="warning">{copy.unsaved}</span>}
              </div>
              {detail?.built_in && <button type="button" className="strategy-clone-trigger" onClick={() => openCreatePanel("clone")}><Copy size={13} />{copy.clone}</button>}
            </div>
          </div>

          <div className="strategy-tabs" role="tablist">
            <button type="button" aria-selected={view === "builder"} onClick={() => void switchView("builder")}><Gauge size={14} />{copy.builder}</button>
            <button type="button" aria-selected={view === "research"} onClick={() => void switchView("research")}><Activity size={14} />{copy.research}</button>
            {pythonEnabled && <button type="button" aria-selected={view === "python"} onClick={() => void switchView("python")}><Code2 size={14} />{copy.python}</button>}
            <button type="button" aria-selected={view === "yaml"} onClick={() => void switchView("yaml")}><Braces size={14} />{copy.yaml}</button>
          </div>

          <div className="strategy-editor-scroll">
            {view === "builder" && config && (
              <div className="strategy-builder">
                <section className="strategy-section strategy-section-wide">
                  <div className="strategy-section-heading"><div><h3>{copy.overview}</h3></div></div>
                  <div className="strategy-field-grid">
                    <label><span>{copy.descriptionLabel}</span><input value={config.description} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, description: event.target.value }))} /></label>
                    <label><span>{copy.implementation}</span><select value={config.implementation.kind} disabled={!editable} onChange={(event) => updateImplementation(event.target.value as "configured" | "python")}><option value="configured">{copy.configuredImplementation}</option><option value="python">{copy.pythonImplementation}</option></select></label>
                    <label><span>{copy.timeout}</span><input type="number" min="0.1" max="30" step="0.5" value={config.implementation.timeout_seconds} disabled={!editable || !pythonEnabled} onChange={(event) => updateConfig((current) => ({ ...current, implementation: { ...current.implementation, timeout_seconds: numberValue(event.target.value) } }))} /></label>
                  </div>
                </section>
                <section className="strategy-section">
                  <div className="strategy-section-heading"><div><h3>{copy.market}</h3><p>{copy.marketHint}</p></div></div>
                  <div className="strategy-field-grid two">
                    <label><span>{copy.marketFactor}</span><input value={config.market_factor} disabled /></label>
                    <label><span>{copy.frequency}</span><input value={copy.monthly} disabled /></label>
                  </div>
                </section>
                <section className="strategy-section">
                  <div className="strategy-section-heading"><div><h3>{copy.position}</h3></div></div>
                  <div className="strategy-field-grid two">
                    <label><span>{copy.minExposure}</span><input type="number" min="0" max="1" step="0.05" value={config.position.min_exposure} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, position: { ...current.position, min_exposure: numberValue(event.target.value) } }))} /></label>
                    <label><span>{copy.maxExposure}</span><input type="number" min="0" max="1" step="0.05" value={config.position.max_exposure} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, position: { ...current.position, max_exposure: numberValue(event.target.value) } }))} /></label>
                  </div>
                </section>
                {!pythonEnabled && <section className="strategy-section strategy-section-wide timing-signal-section">
                  <div className="strategy-section-heading"><div><h3>{copy.signals}</h3><p>{copy.signalHint}</p></div></div>
                  <div className="strategy-factor-toolbar">
                    <button type="button" onClick={addSignal} disabled={!editable}><Plus size={13} />{copy.addSignal}</button>
                    <button type="button" onClick={normalizeSignals} disabled={!editable || totalWeight <= 0}>{copy.normalize}</button>
                    <span>Σ {totalWeight.toFixed(4)}</span>
                  </div>
                  {config.signals.length === 0 ? <div className="strategy-empty-row">{copy.noSignals}</div> : (
                    <div className="timing-signal-table">
                      <div className="timing-signal-row timing-signal-head"><span>{copy.kind}</span><span>{copy.weight}</span><span>{copy.window}</span><span>{copy.threshold}</span><span /></div>
                      {config.signals.map((signal, index) => (
                        <div className="timing-signal-row" key={`${signal.kind}-${index}`}>
                          <select value={signal.kind} disabled={!editable} onChange={(event) => {
                            const kind = event.target.value as TimingSignalConfig["kind"]
                            updateSignal(index, { kind, threshold: kind === "volatility_control" ? 0.15 : 0 })
                          }}><option value="trend">{copy.trend}</option><option value="momentum">{copy.momentum}</option><option value="volatility_control">{copy.volatility_control}</option></select>
                          <input type="number" min="0" step="0.05" value={signal.weight} disabled={!editable} onChange={(event) => updateSignal(index, { weight: numberValue(event.target.value) })} />
                          <input type="number" min="2" max="120" step="1" value={signal.window} disabled={!editable} onChange={(event) => updateSignal(index, { window: numberValue(event.target.value) })} />
                          <input type="number" step="0.01" value={signal.threshold} disabled={!editable} title={signal.kind === "volatility_control" ? "annual volatility target" : copy.threshold} onChange={(event) => updateSignal(index, { threshold: numberValue(event.target.value) })} />
                          <button type="button" className="strategy-remove-factor" disabled={!editable} onClick={() => updateConfig((current) => ({ ...current, signals: current.signals.filter((_, signalIndex) => signalIndex !== index) }))}><Trash2 size={13} /></button>
                        </div>
                      ))}
                    </div>
                  )}
                </section>}
                <section className="strategy-section">
                  <div className="strategy-section-heading"><div><h3>{copy.execution}</h3></div></div>
                  <div className="strategy-field-grid two">
                    <label><span>{copy.cost}</span><input type="number" min="0" step="1" value={config.execution.cost_bps} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, execution: { ...current.execution, cost_bps: numberValue(event.target.value) } }))} /></label>
                    <label><span>{copy.slippage}</span><input type="number" min="0" step="1" value={config.execution.slippage_bps} disabled={!editable} onChange={(event) => updateConfig((current) => ({ ...current, execution: { ...current.execution, slippage_bps: numberValue(event.target.value) } }))} /></label>
                  </div>
                </section>
                <section className="strategy-section">
                  <div className="strategy-section-heading"><div><h3>{copy.validation}</h3></div></div>
                  <div className="strategy-checks">
                    {validation?.checks.map((check) => <div className={`strategy-check ${check.status}`} key={check.code}>{check.status === "passed" ? <CheckCircle2 size={13} /> : <CircleAlert size={13} />}<span>{check.message}</span></div>)}
                  </div>
                </section>
              </div>
            )}

            {view === "research" && (
              <div className="strategy-selection-view timing-research-view">
                <div className="strategy-selection-header">
                  <div><h3>{copy.researchTitle}</h3><p>{copy.researchHint}</p></div>
                  <div className="strategy-selection-controls">
                    <label><span>{copy.dataProfile}</span><select value={profile} onChange={(event) => setProfile(event.target.value as "demo" | "runtime")}><option value="demo">{copy.demo}</option><option value="runtime">{copy.runtime}</option></select></label>
                    <label><span>{copy.start}</span><input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
                    <label><span>{copy.end}</span><input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
                    <button type="button" className="icon-text-command primary" onClick={() => void runResearch()} disabled={busy || !startDate || !endDate}><Play />{busy ? copy.running : copy.run}</button>
                  </div>
                </div>
                {!researchResult ? <div className="strategy-selection-empty"><Activity size={18} />{copy.noResearch}</div> : <>
                  <div className="strategy-selection-kpis">
                    <div><span>{copy.latestExposure}</span><strong>{percent(researchResult.diagnostics.latest_exposure, 0)}</strong></div>
                    <div><span>{copy.averageExposure}</span><strong>{percent(researchResult.diagnostics.average_exposure, 0)}</strong></div>
                    <div><span>{copy.totalReturn}</span><strong>{percent(researchResult.metrics.total_return)}</strong></div>
                    <div><span>{copy.sharpe}</span><strong>{researchResult.metrics.sharpe?.toFixed(2) ?? "—"}</strong></div>
                    <div><span>{copy.maxDrawdown}</span><strong>{percent(researchResult.metrics.max_drawdown)}</strong></div>
                  </div>
                  <section className="strategy-section timing-research-chart">
                    <ResponsiveContainer width="100%" height={280}>
                      <LineChart data={researchResult.series} margin={{ top: 8, right: 22, bottom: 4, left: 4 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} />
                        <XAxis dataKey="date" minTickGap={28} />
                        <YAxis yAxisId="equity" domain={["auto", "auto"]} />
                        <YAxis yAxisId="exposure" orientation="right" domain={[0, 1]} tickFormatter={(value) => `${Number(value) * 100}%`} />
                        <Tooltip formatter={(value, name) => name === "exposure" ? percent(Number(value), 0) : Number(value).toFixed(3)} />
                        <Legend />
                        <Line yAxisId="equity" type="monotone" dataKey="strategy" stroke={CHART_COLORS[0]} dot={false} strokeWidth={2} />
                        <Line yAxisId="equity" type="monotone" dataKey="benchmark" stroke={CHART_COLORS[1]} dot={false} />
                        <Line yAxisId="exposure" type="stepAfter" dataKey="exposure" stroke={CHART_COLORS[3]} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </section>
                  <div className="strategy-selection-table-wrap">
                    <table className="strategy-selection-table timing-signal-history"><thead><tr><th>{copy.signalDate}</th><th>{copy.score}</th><th>{copy.exposure}</th><th>{copy.signalDetail}</th></tr></thead><tbody>
                      {researchResult.signals.slice(-18).reverse().map((row) => <tr key={row.date}><td>{row.date}</td><td>{row.combined_score.toFixed(3)}</td><td><strong>{percent(row.exposure, 0)}</strong></td><td><div className="strategy-factor-breakdown">{Object.entries(row.signals).map(([name, value]) => <span key={name}>{name} {value.toFixed(2)}</span>)}</div></td></tr>)}
                    </tbody></table>
                  </div>
                </>}
              </div>
            )}

            {view === "python" && pythonEnabled && <div className="strategy-yaml-view strategy-python-view">
              <div className="strategy-python-notice"><CircleAlert size={15} /><div><strong>{copy.pythonTitle}</strong><p>{copy.pythonHint}</p><p>{copy.pythonContract}</p></div></div>
              <textarea className="code-view code-editor" aria-label={copy.python} value={pythonSource} readOnly={!editable} spellCheck={false} onChange={(event) => { setPythonSource(event.target.value); setDirty(true); setValidation(null); setResearchResult(null); setMessage("") }} />
            </div>}

            {view === "yaml" && <div className="strategy-yaml-view"><p>{copy.yamlHint}</p><textarea className="code-view code-editor" value={yaml} readOnly={!editable} spellCheck={false} onChange={(event) => { setYaml(event.target.value); setDirty(true); setValidation(null); setResearchResult(null) }} /></div>}
          </div>

          <footer className="strategy-actions">
            <button type="button" className="icon-text-command" onClick={() => void validate()} disabled={busy || (!config && !yaml)}><CheckCircle2 />{copy.validate}</button>
            <button type="button" className="icon-text-command primary" onClick={() => void save()} disabled={busy || !editable || !dirty}><Save />{copy.save}</button>
            <button type="button" className="icon-text-command strategy-backtest-button" onClick={openBacktest} disabled={busy || !detail}><Play />{copy.backtest}</button>
            <button type="button" className="icon-command danger" title={copy.remove} onClick={() => void remove()} disabled={busy || isDraft || !editable}><Trash2 /></button>
          </footer>
        </main>
      </div>
    </div>
  )
}
