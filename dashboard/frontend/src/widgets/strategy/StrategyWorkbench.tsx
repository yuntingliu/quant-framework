import { useEffect, useMemo, useState } from "react"
import {
  Activity,
  ArrowRight,
  Braces,
  CalendarDays,
  CheckCircle2,
  Code2,
  Gauge,
  Layers3,
  Plus,
  Play,
  Save,
  Settings2,
  ShieldCheck,
  Trash2,
  WalletCards,
  Workflow,
  type LucideIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk, type SdkEntrypoint, type SdkParameter } from "@/contexts/StrategySdkContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

type StageId = "selection" | "allocation" | "risk" | "execution"
type EditorMode = "visual" | "function" | "module" | "preview"
type PreviewOperation = "signal" | "portfolio" | "execution"

interface StageDefinition {
  id: StageId
  title: string
  subtitle: string
  description: string
  kinds: SdkEntrypoint["kind"][]
  icon: LucideIcon
  previewOperation: PreviewOperation
  previewLabel: string
  input: string
  output: string
}

interface EntrypointSourcePayload {
  project_id: string
  entrypoint_id: string
  source_sha256: string
  source: string
}

interface FactorBlendMetadata {
  mode?: "single" | "structured" | "custom"
  weights?: Record<string, number>
  normalization?: "raw" | "rank" | "zscore"
  parameters?: Record<string, Record<string, unknown>>
  factor_ids?: string[]
  source?: string
}

interface FactorWeightDraft {
  factorId: string
  weight: string
}

const STAGES: StageDefinition[] = [
  {
    id: "selection",
    title: "选股与调仓",
    subtitle: "什么时候计算，选择哪些标的",
    description: "按照调仓日调用因子和过滤条件，形成当期入选证券与评分。",
    kinds: ["signal", "schedule"],
    icon: CalendarDays,
    previewOperation: "signal",
    previewLabel: "预览本期选股",
    input: "标的池、因子分数、策略状态",
    output: "入选证券与横截面评分",
  },
  {
    id: "allocation",
    title: "仓位分配",
    subtitle: "选中以后分别买多少",
    description: "把入选证券转换为目标权重，也可以配置现金或防御资产。",
    kinds: ["portfolio"],
    icon: WalletCards,
    previewOperation: "portfolio",
    previewLabel: "预览目标仓位",
    input: "入选证券、评分、当前状态",
    output: "目标持仓权重",
  },
  {
    id: "risk",
    title: "持有期风控",
    subtitle: "持仓以后每天如何处理",
    description: "处理止损、均线门控、利润锁定、减仓和禁止买回等有状态规则。",
    kinds: ["event"],
    icon: ShieldCheck,
    previewOperation: "execution",
    previewLabel: "预览风控后计划",
    input: "实际持仓、每日行情、共享 State",
    output: "保持仓位或覆盖目标仓位",
  },
  {
    id: "execution",
    title: "成交执行",
    subtitle: "什么时候成交，成本如何计算",
    description: "声明次日开盘/收盘、佣金、滑点和参与率；撮合与账户安全仍由核心引擎负责。",
    kinds: ["execution"],
    icon: Gauge,
    previewOperation: "execution",
    previewLabel: "预览成交计划",
    input: "最终目标权重",
    output: "成交策略与核心撮合约束",
  },
]

const PARAMETER_LABELS: Record<string, string> = {
  top_n: "选取数量",
  minimum_momentum: "最低动量",
  max_weight: "单标的最大权重",
  defensive_symbol: "防御资产",
  ma_window: "均线窗口",
  profit_trigger: "利润锁定触发",
  trailing_drawdown: "最高点回吐阈值",
  volatility_threshold: "高波动阈值",
  reduction_ratio: "减仓比例",
  allow_reentry: "允许当月买回",
  activation: "成交时点",
  commission_rate: "佣金率",
  slippage_rate: "滑点率",
  max_participation_rate: "最大成交参与率",
  market_impact_rate: "冲击成本率",
}

const EVENT_LABELS: Record<string, string> = {
  session_open: "每日开盘规则",
  session_close: "每日收盘规则",
  decision: "目标仓位生成后",
  fill: "成交后处理",
  rejection: "拒单后处理",
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function parameterValue(parameter: SdkParameter, raw: string): unknown {
  if (typeof parameter.default === "boolean") return raw === "true"
  if (typeof parameter.default === "number") return Number(raw)
  if (parameter.default === null && raw === "None") return null
  return raw
}

function parameterLabel(parameter: SdkParameter) {
  return parameter.label || PARAMETER_LABELS[parameter.name] || parameter.name.replace(/_/g, " ")
}

function displayParameter(parameter: SdkParameter) {
  if (typeof parameter.default === "boolean") return parameter.default ? "启用" : "关闭"
  if (typeof parameter.default === "number" && /rate|weight|drawdown|momentum|volatility|profit|exposure|ratio|threshold/.test(parameter.name)) {
    return `${(parameter.default * 100).toFixed(2)}%`
  }
  return String(parameter.default ?? "None")
}

function scheduleLabel(schedule: Record<string, string> | undefined) {
  if (!schedule || schedule.mode !== "structured") return "自定义 Python 调度"
  const frequency = { daily: "每日", weekly: "每周", monthly: "每月" }[schedule.frequency] ?? schedule.frequency
  const selector = schedule.frequency === "daily" ? "" : schedule.selector === "first_trading_day" ? "首个交易日" : "最后交易日"
  const at = schedule.at === "open" ? "开盘" : "收盘"
  return [frequency, selector, at].filter(Boolean).join(" · ")
}

function entrypointBusinessLabel(entrypoint: SdkEntrypoint) {
  if (entrypoint.label) return entrypoint.label
  if (entrypoint.kind === "signal") return "选股模型"
  if (entrypoint.kind === "schedule") return "自定义调仓日"
  if (entrypoint.kind === "portfolio") return "目标仓位模型"
  if (entrypoint.kind === "event") return EVENT_LABELS[entrypoint.event || ""] || "持有期处理规则"
  if (entrypoint.kind === "execution") return "成交模型"
  return entrypoint.id
}

function entrypointTypeLabel(entrypoint: SdkEntrypoint) {
  return ({ signal: "选股", schedule: "调度", portfolio: "仓位", event: "风控", execution: "成交" } as Record<string, string>)[entrypoint.kind] || entrypoint.kind
}

function ParameterEditor({ entrypoint, parameter, locked, onError }: {
  entrypoint: SdkEntrypoint
  parameter: SdkParameter
  locked: boolean
  onError: (message: string) => void
}) {
  const sdk = useStrategySdk()
  const [value, setValue] = useState(String(parameter.default))
  const [busy, setBusy] = useState(false)
  useEffect(() => setValue(String(parameter.default)), [parameter.default])
  const changed = value !== String(parameter.default)
  const editable = parameter.editable && Boolean(sdk.project?.editable) && !locked
  const boundedNumber = typeof parameter.default === "number" && parameter.minimum !== null && parameter.maximum !== null

  async function apply() {
    setBusy(true); onError("")
    try {
      await sdk.structuredEdit({
        operation: "parameter",
        entrypoint_id: entrypoint.id,
        parameter: parameter.name,
        value: parameterValue(parameter, value),
      })
    } catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  return (
    <div className={`strategy-parameter-card ${changed ? "changed" : ""}`}>
      <div className="strategy-parameter-heading">
        <div><strong>{parameterLabel(parameter)}</strong><small>{parameter.description || `Python 参数：${parameter.name}`}</small></div>
        <span>{displayParameter(parameter)}</span>
      </div>
      {typeof parameter.default === "boolean" ? (
        <div className="strategy-segmented-control">
          <button type="button" className={value === "true" ? "active" : ""} disabled={!editable} onClick={() => setValue("true")}>启用</button>
          <button type="button" className={value === "false" ? "active" : ""} disabled={!editable} onClick={() => setValue("false")}>关闭</button>
        </div>
      ) : parameter.name === "activation" ? (
        <select value={value} disabled={!editable} onChange={(event) => setValue(event.target.value)}>
          <option value="next_session_open">下一交易日开盘</option>
          <option value="next_session_close">下一交易日收盘</option>
        </select>
      ) : boundedNumber ? (
        <div className="strategy-range-control">
          <input type="range" min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} step={parameter.step ?? "any"} value={value} disabled={!editable} onChange={(event) => setValue(event.target.value)} />
          <Input type="number" min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} step={parameter.step ?? undefined} value={value} disabled={!editable} onChange={(event) => setValue(event.target.value)} />
        </div>
      ) : (
        <Input type={typeof parameter.default === "number" ? "number" : "text"} min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} step={parameter.step ?? undefined} value={value} disabled={!editable} onChange={(event) => setValue(event.target.value)} />
      )}
      <div className="strategy-parameter-footer">
        <code>{parameter.name}</code>
        {parameter.minimum !== null || parameter.maximum !== null ? <small>{parameter.minimum ?? "−∞"} ～ {parameter.maximum ?? "+∞"}</small> : <span />}
        <Button size="sm" variant="outline" disabled={!editable || busy || !changed} onClick={() => void apply()}>{busy ? "写入中" : "应用到 Python"}</Button>
      </div>
    </div>
  )
}

function ScheduleEditor({ entrypoint, locked, onError }: {
  entrypoint: SdkEntrypoint
  locked: boolean
  onError: (message: string) => void
}) {
  const sdk = useStrategySdk()
  const schedule = entrypoint.metadata.schedule as Record<string, string> | undefined
  const [frequency, setFrequency] = useState(schedule?.frequency || "monthly")
  const [selector, setSelector] = useState(schedule?.selector || "last_trading_day")
  const [at, setAt] = useState(schedule?.at || "close")
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    setFrequency(schedule?.frequency || "monthly")
    setSelector(schedule?.selector || "last_trading_day")
    setAt(schedule?.at || "close")
  }, [schedule?.frequency, schedule?.selector, schedule?.at])
  if (!schedule) return null
  if (schedule.mode !== "structured") return <div className="strategy-custom-notice"><Braces size={16} /><div><strong>调仓时间由 Python 自定义</strong><span>这个 schedule 不能安全投影为普通频率控件，请在“自定义当前环节”中修改。</span></div></div>
  const changed = frequency !== schedule.frequency || selector !== schedule.selector || at !== schedule.at
  const editable = Boolean(sdk.project?.editable) && !locked

  async function apply() {
    setBusy(true); onError("")
    try {
      await sdk.structuredEdit({ operation: "schedule", entrypoint_id: entrypoint.id, frequency, selector: frequency === "daily" ? "every" : selector, at })
    } catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  return (
    <section className="strategy-schedule-card">
      <header><div><CalendarDays size={16} /><span><strong>重新计算时间</strong><small>直接修改 @signal 的 schedule</small></span></div><Badge variant="outline">{scheduleLabel(schedule)}</Badge></header>
      <div className="strategy-schedule-grid">
        <label><span>频率</span><select value={frequency} disabled={!editable} onChange={(event) => setFrequency(event.target.value)}><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></select></label>
        <label><span>交易日</span><select value={frequency === "daily" ? "every" : selector} disabled={!editable || frequency === "daily"} onChange={(event) => setSelector(event.target.value)}><option value="every">每个交易日</option><option value="first_trading_day">首个交易日</option><option value="last_trading_day">最后交易日</option></select></label>
        <label><span>计算时点</span><select value={at} disabled={!editable} onChange={(event) => setAt(event.target.value)}><option value="open">开盘</option><option value="close">收盘</option></select></label>
        <Button disabled={!editable || busy || !changed} onClick={() => void apply()}>{busy ? "写入中" : "应用调仓时间"}</Button>
      </div>
    </section>
  )
}

function MultiFactorEditor({ entrypoint, factors, locked, onError, onOpenCode }: {
  entrypoint: SdkEntrypoint
  factors: SdkEntrypoint[]
  locked: boolean
  onError: (message: string) => void
  onOpenCode: () => void
}) {
  const sdk = useStrategySdk()
  const blend = (entrypoint.metadata.factor_blend ?? {}) as FactorBlendMetadata
  const blendKey = JSON.stringify(blend)
  const [rows, setRows] = useState<FactorWeightDraft[]>([])
  const [normalization, setNormalization] = useState<"raw" | "rank" | "zscore">("rank")
  const [addFactorId, setAddFactorId] = useState("")
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const projected = Object.entries(blend.weights ?? {}).map(([factorId, weight]) => ({ factorId, weight: String(weight) }))
    setRows(projected)
    setNormalization(blend.normalization ?? "rank")
  }, [blendKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const supported = blend.mode === "single" || blend.mode === "structured"
  const editable = supported && Boolean(sdk.project?.editable) && !locked
  const factorById = Object.fromEntries(factors.map((factor) => [factor.id, factor]))
  const available = factors.filter((factor) => !rows.some((row) => row.factorId === factor.id))
  const selectedToAdd = available.some((factor) => factor.id === addFactorId) ? addFactorId : available[0]?.id ?? ""
  const parsed = rows.map((row) => ({ ...row, numeric: Number(row.weight) }))
  const valid = rows.length > 0 && parsed.every((row) => row.weight !== "" && Number.isFinite(row.numeric) && Math.abs(row.numeric) > 1e-12)
  const projectedWeights = Object.fromEntries(parsed.filter((row) => Number.isFinite(row.numeric)).map((row) => [row.factorId, row.numeric]))
  const baselineWeights = blend.weights ?? {}
  const orderedKey = (weights: Record<string, number>) => JSON.stringify(Object.entries(weights).sort(([left], [right]) => left.localeCompare(right)))
  const changed = supported && (orderedKey(projectedWeights) !== orderedKey(baselineWeights) || normalization !== blend.normalization)
  const total = parsed.reduce((sum, row) => sum + (Number.isFinite(row.numeric) ? Math.abs(row.numeric) : 0), 0)

  function updateWeight(factorId: string, value: string) {
    setRows((current) => current.map((row) => row.factorId === factorId ? { ...row, weight: value } : row))
  }

  function setDirection(row: FactorWeightDraft, direction: 1 | -1) {
    const magnitude = Math.abs(Number(row.weight)) || 1
    updateWeight(row.factorId, String(magnitude * direction))
  }

  function setMagnitude(row: FactorWeightDraft, value: string) {
    if (value === "") { updateWeight(row.factorId, ""); return }
    const direction = Number(row.weight) < 0 ? -1 : 1
    updateWeight(row.factorId, String(Math.abs(Number(value)) * direction))
  }

  function addFactor() {
    if (!selectedToAdd) return
    setRows((current) => [...current, { factorId: selectedToAdd, weight: "1" }])
    if (blend.mode === "single") setNormalization("rank")
    setAddFactorId("")
  }

  async function apply() {
    setBusy(true); onError("")
    try {
      await sdk.structuredEdit({
        operation: "factor_blend",
        entrypoint_id: entrypoint.id,
        factor_weights: projectedWeights,
        normalization,
      })
    } catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!supported) {
    const detected = blend.factor_ids ?? []
    return <section className="strategy-factor-blend-card custom"><header><div><Layers3 size={16} /><span><strong>多因子选股</strong><small>因子评分与合成方式</small></span></div><Badge variant="outline">自定义 Python</Badge></header><div className="strategy-factor-custom"><div><Braces size={17} /><span><strong>当前是自定义因子组合</strong><small>{detected.length ? `检测到：${detected.join("、")}` : "信号函数没有可安全投影的标准因子调用。"} 无代码界面不会猜测或改写公式。</small></span></div><Button size="sm" variant="outline" onClick={onOpenCode}>编辑选股 Python</Button></div></section>
  }

  return (
    <section className="strategy-factor-blend-card">
      <header><div><Layers3 size={16} /><span><strong>多因子选股</strong><small>正权重偏好高值，负权重偏好低值；最终仍是当前信号函数里的 Python。</small></span></div><Badge variant={blend.mode === "single" ? "outline" : "secondary"}>{blend.mode === "single" ? "单因子，可扩展" : `${rows.length} 因子`}</Badge></header>
      <div className="strategy-factor-toolbar">
        <label><span>合成前标准化</span><select value={normalization} disabled={!editable} onChange={(event) => setNormalization(event.target.value as "raw" | "rank" | "zscore")}><option value="rank">横截面排名（推荐）</option><option value="zscore">Z-score 标准化</option><option value="raw">原始值</option></select></label>
        <div><select aria-label="添加因子" value={selectedToAdd} disabled={!editable || !available.length} onChange={(event) => setAddFactorId(event.target.value)}>{available.length ? available.map((factor) => <option value={factor.id} key={factor.id}>{factor.label || factor.id}</option>) : <option value="">因子已全部加入</option>}</select><Button size="sm" variant="outline" disabled={!editable || !selectedToAdd} onClick={addFactor}><Plus />添加因子</Button></div>
      </div>
      <div className="strategy-factor-rows">
        {rows.map((row) => {
          const factor = factorById[row.factorId]
          const numeric = Number(row.weight)
          const share = total > 0 && Number.isFinite(numeric) ? Math.abs(numeric) / total : 0
          const overrides = Object.keys(blend.parameters?.[row.factorId] ?? {}).length
          return <article key={row.factorId} className="strategy-factor-row"><div className="strategy-factor-identity"><span className="strategy-factor-avatar">{(factor?.label || row.factorId).slice(0, 1).toUpperCase()}</span><span><strong>{factor?.label || row.factorId}</strong><small><code>{row.factorId}</code>{overrides ? ` · ${overrides} 个调用参数` : " · 使用因子默认参数"}</small></span></div><div className="strategy-factor-direction"><button type="button" className={numeric >= 0 ? "active" : ""} disabled={!editable} onClick={() => setDirection(row, 1)}>高值优先</button><button type="button" className={numeric < 0 ? "active inverse" : ""} disabled={!editable} onClick={() => setDirection(row, -1)}>低值优先</button></div><label className="strategy-factor-weight"><span>权重强度</span><Input type="number" min="0.01" step="0.05" value={row.weight === "" ? "" : Math.abs(numeric)} disabled={!editable} onChange={(event) => setMagnitude(row, event.target.value)} /></label><div className="strategy-factor-share"><span>{(share * 100).toFixed(1)}%</span><i><b className={numeric < 0 ? "inverse" : ""} style={{ width: `${share * 100}%` }} /></i></div><button className="strategy-factor-remove" type="button" aria-label={`移除 ${factor?.label || row.factorId}`} disabled={!editable || rows.length === 1} onClick={() => setRows((current) => current.filter((item) => item.factorId !== row.factorId))}><Trash2 size={14} /></button></article>
        })}
      </div>
      <footer><span><code>context.combine_factors(...)</code>{blend.mode === "single" ? " · 应用后转换为标准多因子调用，并保留现有过滤逻辑。" : " · 只改 weights 与 normalization。"}</span><Button disabled={!editable || busy || !valid || !changed} onClick={() => void apply()}>{busy ? "写入中" : "应用多因子配置"}</Button></footer>
    </section>
  )
}

function PreviewPanel({ preview }: { preview: Record<string, unknown> | null }) {
  if (!preview) return <div className="analytics-empty">运行预览后，这里会显示入选证券、目标权重、风控结果和成交策略。</div>
  const result = asRecord(preview.result)
  const signal = asRecord(result.signal)
  const decision = asRecord(result.decision)
  const policy = asRecord(result.execution_policy)
  const scores = asRecord(signal.scores)
  const weights = asRecord(decision.target_weights)
  const selected = Array.isArray(signal.selected) ? signal.selected.map(String) : []
  const invoked = Array.isArray(result.invoked) ? result.invoked.map(String) : []
  const universe = Array.isArray(result.universe) ? result.universe : []
  const gross = Object.values(weights).reduce<number>((sum, value) => sum + (typeof value === "number" ? value : 0), 0)
  const ranked = Object.entries(scores).filter((item): item is [string, number] => typeof item[1] === "number").sort((left, right) => right[1] - left[1])
  const weighted = Object.entries(weights).filter((item): item is [string, number] => typeof item[1] === "number").sort((left, right) => right[1] - left[1])

  return (
    <div className="strategy-preview-dashboard">
      <div className="stage-kpi-grid four">
        <div><span>预览日期</span><strong>{String(result.as_of || "—").slice(0, 10)}</strong></div>
        <div><span>可选证券</span><strong>{universe.length}</strong></div>
        <div><span>最终入选</span><strong>{selected.length}</strong></div>
        <div><span>目标总仓位</span><strong>{(gross * 100).toFixed(1)}%</strong></div>
      </div>
      <div className="strategy-preview-grid">
        <section><header><strong>本期选股与得分</strong><Badge variant="outline">{ranked.length} 个评分</Badge></header>{ranked.length ? <div className="analytics-table-wrap"><table className="analytics-table compact"><thead><tr><th>排名</th><th>证券</th><th>分数</th><th>入选</th></tr></thead><tbody>{ranked.slice(0, 15).map(([symbol, score], index) => <tr key={symbol}><td>{index + 1}</td><td><code>{symbol}</code></td><td>{score.toFixed(4)}</td><td>{selected.includes(symbol) ? <CheckCircle2 className="strategy-preview-check" size={14} /> : ""}</td></tr>)}</tbody></table></div> : <div className="analytics-empty">这个预览阶段尚未输出选股结果。</div>}</section>
        <section><header><strong>目标仓位</strong><Badge variant="outline">{weighted.length} 个持仓</Badge></header>{weighted.length ? <div className="strategy-weight-bars">{weighted.map(([symbol, weight]) => <div key={symbol}><span><code>{symbol}</code><strong>{(weight * 100).toFixed(2)}%</strong></span><i><b style={{ width: `${Math.min(100, Math.max(0, weight * 100))}%` }} /></i></div>)}</div> : <div className="analytics-empty">继续预览仓位或成交阶段后显示目标权重。</div>}</section>
      </div>
      <section className="strategy-preview-policy"><header><strong>成交与调用链</strong><span>{invoked.join(" → ") || "—"}</span></header><div>{Object.entries(policy).length ? Object.entries(policy).map(([key, value]) => <div key={key}><span>{PARAMETER_LABELS[key] || key.replace(/_/g, " ")}</span><strong>{typeof value === "number" && key.includes("rate") ? `${(value * 100).toFixed(3)}%` : String(value)}</strong></div>) : <div><span>执行策略</span><strong>当前预览阶段尚未调用</strong></div>}</div></section>
      {Object.keys(asRecord(result.state)).length ? <details className="strategy-technical-details"><summary>查看策略 State</summary><pre>{JSON.stringify(result.state, null, 2)}</pre></details> : null}
    </div>
  )
}

export function StrategyWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const project = sdk.project
  const [source, setSource] = useState("")
  const [stageId, setStageId] = useState<StageId>("selection")
  const [selectedEntrypoint, setSelectedEntrypoint] = useState("")
  const [mode, setMode] = useState<EditorMode>("visual")
  const [functionSource, setFunctionSource] = useState("")
  const [baseFunctionSource, setBaseFunctionSource] = useState("")
  const [functionLoading, setFunctionLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)

  const strategyEntrypoints = useMemo(
    () => project?.inspection.entrypoints.filter((item) => item.kind !== "factor" && item.kind !== "universe") ?? [],
    [project?.inspection.entrypoints],
  )
  const factorEntrypoints = useMemo(
    () => project?.inspection.entrypoints.filter((item) => item.kind === "factor") ?? [],
    [project?.inspection.entrypoints],
  )
  const stageGroups = useMemo(() => Object.fromEntries(STAGES.map((stage) => [stage.id, strategyEntrypoints.filter((item) => stage.kinds.includes(item.kind)).sort((left, right) => stage.kinds.indexOf(left.kind) - stage.kinds.indexOf(right.kind))])) as Record<StageId, SdkEntrypoint[]>, [strategyEntrypoints])
  const activeStage = STAGES.find((stage) => stage.id === stageId) ?? STAGES[0]
  const stageEntrypoints = stageGroups[activeStage.id]
  const activeEntrypoint = stageEntrypoints.find((item) => item.id === selectedEntrypoint) ?? stageEntrypoints[0]
  const localDirty = Boolean(project && source !== project.draft_source)
  const functionDirty = functionSource !== baseFunctionSource
  const requiresFreeze = Boolean(project?.dirty || localDirty || functionDirty)
  const editLocked = localDirty || functionDirty

  useEffect(() => setSource(project?.draft_source ?? ""), [project?.id, project?.draft_source_sha256])
  useEffect(() => setPreview(null), [project?.id, project?.current_revision, project?.draft_source_sha256])
  useEffect(() => {
    if (activeEntrypoint && activeEntrypoint.id !== selectedEntrypoint) setSelectedEntrypoint(activeEntrypoint.id)
    if (!activeEntrypoint) setSelectedEntrypoint("")
  }, [activeEntrypoint, selectedEntrypoint])
  useEffect(() => {
    if (!project || !activeEntrypoint) { setFunctionSource(""); setBaseFunctionSource(""); return }
    let current = true
    setFunctionLoading(true)
    void api.get<EntrypointSourcePayload>(`/strategy/projects/${project.id}/entrypoints/${encodeURIComponent(activeEntrypoint.id)}/source`).then((payload) => {
      if (!current) return
      setFunctionSource(payload.source); setBaseFunctionSource(payload.source)
    }).catch((reason: Error) => { if (current) setError(reason.message) }).finally(() => { if (current) setFunctionLoading(false) })
    return () => { current = false }
  }, [project?.id, project?.draft_source_sha256, activeEntrypoint?.id])

  async function chooseStage(next: StageId) {
    if (next === stageId) return
    if (functionDirty && !await confirm({ title: "放弃当前环节的未保存代码？", description: "切换环节会重新读取对应 Python 函数。尚未保存的函数修改将丢失。", confirmText: "放弃并切换" })) return
    setFunctionSource(baseFunctionSource); setStageId(next); setSelectedEntrypoint(""); setPreview(null); setMode("visual")
  }

  async function chooseEntrypoint(id: string) {
    if (id === activeEntrypoint?.id) return
    if (functionDirty && !await confirm({ title: "放弃当前函数的未保存代码？", description: "切换规则会重新读取另一段 Python。", confirmText: "放弃并切换" })) return
    setFunctionSource(baseFunctionSource); setSelectedEntrypoint(id)
  }

  async function saveDraft() {
    if (!project?.editable || !localDirty || functionDirty) return
    setBusy(true); setError("")
    try { await sdk.updateDraft(source) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function saveFunction() {
    if (!project?.editable || !activeEntrypoint || !functionDirty || localDirty) return
    setBusy(true); setError("")
    try {
      await sdk.structuredEdit({ operation: "replace_function", entrypoint_id: activeEntrypoint.id, function_source: functionSource })
      setBaseFunctionSource(functionSource)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function freezeRevision() {
    if (!project?.editable || localDirty || functionDirty) return
    if (!await confirm({ title: "验证并冻结策略版本", description: "系统将导入完整 Python 并探测选股、仓位、持有期事件和成交合同。成功后形成不可变 revision。", confirmText: "验证并冻结" })) return
    setBusy(true); setError("")
    try { await sdk.saveRevision() }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function runPreview(operation: PreviewOperation) {
    if (!project || requiresFreeze) return
    if (!await confirm({ title: activeStage.previewLabel, description: `将运行 ${project.id}@${project.current_revision}，源码哈希 ${project.current_package.source_sha256.slice(0, 16)}。本机 Python 不是安全沙箱。`, confirmText: "运行预览" })) return
    setBusy(true); setError("")
    try {
      setPreview(await api.post<Record<string, unknown>>(`/strategy/projects/${project.id}/preview`, { operation, profile: project.profile, revision: project.current_revision, confirm_python_execution: true }))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project) return <Widget title="策略工作台" loading={sdk.loading} error={sdk.error}><span /></Widget>
  const signalEntrypoint = stageEntrypoints.find((item) => item.kind === "signal")

  return (
    <Widget headerless>
      <div className="strategy-business-workbench">
        <section className="backtest-run-setup pipeline-stage-setup strategy-project-bar">
          <div className="backtest-run-controls pipeline-stage-controls">
            <div className="pipeline-pinned-component"><span>策略项目</span><strong>{project.name}</strong></div>
            <div className="pipeline-pinned-component"><span>当前版本</span><strong>r{project.current_revision} · {project.draft_source_sha256.slice(0, 12)}</strong></div>
            <div className="pipeline-stage-actions">
              <Badge variant={requiresFreeze ? "destructive" : "secondary"}>{functionDirty ? "当前函数未保存" : localDirty ? "完整源码未保存" : project.dirty ? "草稿未冻结" : "可复现"}</Badge>
              <button className="primary-command" type="button" disabled={!project.editable || busy || !project.dirty || localDirty || functionDirty} onClick={() => void freezeRevision()}>冻结新版本</button>
            </div>
          </div>
        </section>
        {error ? <div className="workbench-message error strategy-workbench-error">{error}</div> : null}

        <section className="strategy-business-flow" aria-label="交易策略流程">
          {STAGES.map((stage, index) => {
            const Icon = stage.icon
            const items = stageGroups[stage.id]
            const signal = items.find((item) => item.kind === "signal")
            const blend = signal?.metadata.factor_blend as FactorBlendMetadata | undefined
            const factorCount = Object.keys(blend?.weights ?? {}).length
            const summary = stage.id === "selection" ? `${factorCount || "自定义"} 因子 · ${scheduleLabel(signal?.metadata.schedule as Record<string, string> | undefined)}` : items.map(entrypointBusinessLabel).join("、") || "尚未配置"
            const structuredSchedule = signal?.metadata.schedule as Record<string, string> | undefined
            const projected = items.reduce((count, item) => count + item.parameters.filter((parameter) => parameter.editable).length, 0) + (structuredSchedule?.mode === "structured" ? 1 : 0) + ((blend?.mode === "single" || blend?.mode === "structured") ? factorCount + 1 : 0)
            return <div className="strategy-flow-step" key={stage.id}>
              <button type="button" className={stageId === stage.id ? "active" : ""} onClick={() => void chooseStage(stage.id)}>
                <header><span className="strategy-flow-number">{index + 1}</span><Icon size={18} /><Badge variant={items.length ? "secondary" : "outline"}>{items.length ? "已配置" : "可选"}</Badge></header>
                <strong>{stage.title}</strong><small>{stage.subtitle}</small>
                <footer><span>{summary}</span><em>{projected ? `${projected} 个无代码参数` : items.length ? "自定义 Python" : "未添加规则"}</em></footer>
              </button>
              {index < STAGES.length - 1 ? <ArrowRight className="strategy-flow-arrow" size={18} /> : null}
            </div>
          })}
        </section>

        <div className="strategy-authoring-layout">
          <aside className="strategy-stage-sidebar">
            <div className="strategy-stage-intro"><activeStage.icon size={22} /><div><span>第 {STAGES.indexOf(activeStage) + 1} 步</span><strong>{activeStage.title}</strong><p>{activeStage.description}</p></div></div>
            <div className="strategy-contract-flow"><div><span>输入</span><strong>{activeStage.input}</strong></div><ArrowRight size={14} /><div><span>输出</span><strong>{activeStage.output}</strong></div></div>
            <div className="strategy-stage-rules-heading"><strong>本环节规则</strong><span>{stageEntrypoints.length}</span></div>
            <div className="strategy-stage-rule-list">
              {stageEntrypoints.map((entrypoint) => <button key={entrypoint.id} type="button" className={activeEntrypoint?.id === entrypoint.id ? "active" : ""} onClick={() => void chooseEntrypoint(entrypoint.id)}><span><strong>{entrypointBusinessLabel(entrypoint)}</strong><small>{entrypointTypeLabel(entrypoint)}{entrypoint.event ? ` · ${EVENT_LABELS[entrypoint.event] || entrypoint.event}` : ""}</small></span><em>{entrypoint.parameters.length} 参数</em></button>)}
            </div>
            {!stageEntrypoints.length ? <div className="strategy-empty-stage"><ShieldCheck size={20} /><strong>这个环节尚未配置</strong><p>{activeStage.id === "risk" ? "没有持有期事件时，仓位会一直保持到下一次调仓。可在完整 Python 中添加 @on_event。" : "请在完整 Python 中添加对应的 SDK 函数。"}</p><Button size="sm" variant="outline" onClick={() => setMode("module")}><Code2 />打开完整 Python</Button></div> : null}
            {activeEntrypoint ? <details className="strategy-technical-details"><summary>Python 接口信息</summary><dl><div><dt>注册类型</dt><dd>@{activeEntrypoint.kind}</dd></div><div><dt>公开 ID</dt><dd>{activeEntrypoint.id}</dd></div><div><dt>函数名</dt><dd>{activeEntrypoint.function}</dd></div><div><dt>源码位置</dt><dd>L{activeEntrypoint.line}</dd></div></dl></details> : null}
          </aside>

          <main className="strategy-authoring-main">
            <header className="strategy-authoring-header">
              <div><Workflow size={18} /><span><strong>{activeEntrypoint ? entrypointBusinessLabel(activeEntrypoint) : activeStage.title}</strong><small>无代码和 Python 编辑的是同一个策略函数</small></span></div>
              <nav aria-label="策略编辑方式"><button type="button" className={mode === "visual" ? "active" : ""} onClick={() => setMode("visual")}><Settings2 size={14} />无代码配置</button><button type="button" className={mode === "function" ? "active" : ""} disabled={!activeEntrypoint} onClick={() => setMode("function")}><Braces size={14} />自定义当前环节</button><button type="button" className={mode === "module" ? "active" : ""} onClick={() => setMode("module")}><Code2 size={14} />完整 Python</button><button type="button" className={mode === "preview" ? "active" : ""} onClick={() => setMode("preview")}><Play size={14} />可视化预览</button></nav>
            </header>

            <div className="strategy-authoring-content">
              {mode === "visual" ? <div className="strategy-visual-editor">
                {editLocked ? <div className="workbench-message warning">{functionDirty ? "当前环节有未保存 Python；保存或放弃后才能使用无代码控件。" : "完整模块有未保存修改；保存或放弃后才能使用无代码控件。"}</div> : null}
                {signalEntrypoint ? <ScheduleEditor entrypoint={signalEntrypoint} locked={editLocked} onError={setError} /> : null}
                {signalEntrypoint ? <MultiFactorEditor entrypoint={signalEntrypoint} factors={factorEntrypoints} locked={editLocked} onError={setError} onOpenCode={() => { setSelectedEntrypoint(signalEntrypoint.id); setMode("function") }} /> : null}
                {activeEntrypoint ? <>
                  <div className="strategy-visual-summary"><div><Activity size={17} /><span><strong>当前逻辑</strong><small>{entrypointBusinessLabel(activeEntrypoint)}</small></span></div><div>{activeEntrypoint.parameters.slice(0, 4).map((parameter) => <span key={parameter.name}><small>{parameterLabel(parameter)}</small><strong>{displayParameter(parameter)}</strong></span>)}</div></div>
                  <section><div className="backtest-section-heading"><div><strong>可视化参数</strong><span>每次应用只修改这个函数中对应的关键字默认值。</span></div><Badge variant="outline">{activeEntrypoint.parameters.filter((item) => item.editable).length} 个可编辑</Badge></div>
                    {activeEntrypoint.parameters.length ? <div className="strategy-parameter-grid">{activeEntrypoint.parameters.map((parameter) => <ParameterEditor key={parameter.name} entrypoint={activeEntrypoint} parameter={parameter} locked={editLocked} onError={setError} />)}</div> : <div className="strategy-custom-notice"><Braces size={16} /><div><strong>这段逻辑目前完全由 Python 定义</strong><span>如需让参数出现在这里，请把它声明成带字面量默认值的 keyword-only 参数。</span></div></div>}
                  </section>
                  <div className="strategy-sdk-boundary"><ShieldCheck size={16} /><div><strong>核心安全边界不会被自定义代码绕过</strong><span>证券有效性、可交易性、价格、流动性、现金、费用和账户记账仍由事件引擎验证。</span></div></div>
                </> : <div className="analytics-empty">当前环节没有已注册函数。可以从“完整 Python”添加自定义实现。</div>}
              </div> : null}

              {mode === "function" ? <div className="strategy-code-editor-view">
                <div className="strategy-code-note"><Braces size={17} /><div><strong>只编辑当前环节</strong><span>保存时按 entrypoint ID 精确替换这一个注册函数，其他因子和交易环节保持不变。</span></div><Badge variant={functionDirty ? "destructive" : "secondary"}>{functionDirty ? "未保存" : "已同步"}</Badge></div>
                {functionLoading ? <div className="analytics-empty">正在读取函数源码…</div> : <Textarea className="pipeline-code-editor strategy-function-editor font-mono text-xs leading-5" spellCheck={false} value={functionSource} disabled={!project.editable || localDirty} onChange={(event) => setFunctionSource(event.target.value)} />}
                <div className="strategy-code-actions"><span>{localDirty ? "请先处理完整模块中的未保存修改。" : "保存后系统会重新解析参数，并自动回填无代码界面。"}</span><Button variant="outline" disabled={!functionDirty || busy} onClick={() => setFunctionSource(baseFunctionSource)}>放弃修改</Button><Button disabled={!project.editable || !functionDirty || localDirty || busy} onClick={() => void saveFunction()}><Save />保存当前函数</Button></div>
              </div> : null}

              {mode === "module" ? <div className="strategy-code-editor-view">
                <div className="strategy-code-note"><Code2 size={17} /><div><strong>完整 canonical module</strong><span>适合新增持有期事件、辅助函数或完全自定义逻辑；保存后所有工作台都会从这里重新投影。</span></div><Badge variant={localDirty ? "destructive" : "outline"}>{localDirty ? "未保存" : project.draft_source_sha256.slice(0, 12)}</Badge></div>
                {functionDirty ? <div className="workbench-message warning">当前环节代码尚未保存。为避免互相覆盖，完整模块暂时只读。</div> : null}
                <Textarea className="pipeline-code-editor strategy-function-editor font-mono text-xs leading-5" spellCheck={false} value={source} disabled={!project.editable || functionDirty} onChange={(event) => setSource(event.target.value)} />
                {project.inspection.warnings.map((warning) => <div key={`${warning.line}-${warning.message}`} className="workbench-message warning">L{warning.line}: {warning.message}</div>)}
                <div className="strategy-code-actions"><span>完整源码保存为同一项目草稿，不会创建第二套策略。</span><Button variant="outline" disabled={!localDirty || busy} onClick={() => setSource(project.draft_source)}>放弃修改</Button><Button disabled={!project.editable || !localDirty || functionDirty || busy} onClick={() => void saveDraft()}><Save />保存完整草稿</Button></div>
              </div> : null}

              {mode === "preview" ? <div className="strategy-preview-view">
                <div className="strategy-preview-toolbar"><div><Play size={17} /><span><strong>{activeStage.previewLabel}</strong><small>调用当前冻结 revision，与回测使用同一函数和事件顺序。</small></span></div><Button disabled={busy || requiresFreeze} onClick={() => void runPreview(activeStage.previewOperation)}><Play />{busy ? "运行中" : activeStage.previewLabel}</Button></div>
                {requiresFreeze ? <div className="workbench-message warning">请先保存代码并冻结新版本；预览不会运行可变草稿。</div> : null}
                <PreviewPanel preview={preview} />
              </div> : null}
            </div>
          </main>
        </div>
      </div>
    </Widget>
  )
}
