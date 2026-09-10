import { Fragment, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import {
  CalendarDays,
  CheckCircle2,
  Code2,
  Gauge,
  Plus,
  Play,
  Save,
  Settings2,
  ShieldCheck,
  Trash2,
  WalletCards,
  type LucideIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PythonEditor } from "@/components/python"
import { ProjectStrategies } from "./ProjectStrategies"
import { useStrategySdk, type SdkEntrypoint, type SdkParameter } from "@/contexts/StrategySdkContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

type StageId = "selection" | "allocation" | "risk" | "execution"
type WorkspaceView = "author" | "preview"
type PreviewOperation = "signal" | "portfolio" | "execution"

interface StageDefinition {
  id: StageId
  title: string
  kinds: SdkEntrypoint["kind"][]
  icon: LucideIcon
}

interface FactorBlendMetadata {
  mode?: "single" | "structured" | "custom"
  weights?: Record<string, number>
  normalization?: "raw" | "rank" | "zscore"
}

interface FactorWeightDraft {
  factorId: string
  weight: string
}

interface VisualDraft {
  edits: Array<Record<string, unknown>>
  valid: boolean
}

interface VisualEditPreview {
  project_id: string
  base_source_sha256: string
  source: string
  source_sha256: string
}

const STAGES: StageDefinition[] = [
  {
    id: "selection",
    title: "选股与调仓",
    kinds: ["signal", "schedule"],
    icon: CalendarDays,
  },
  {
    id: "allocation",
    title: "仓位分配",
    kinds: ["portfolio"],
    icon: WalletCards,
  },
  {
    id: "risk",
    title: "持有期风控",
    kinds: ["event"],
    icon: ShieldCheck,
  },
  {
    id: "execution",
    title: "成交执行",
    kinds: ["execution_data_fill", "execution"],
    icon: Gauge,
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
  main_board_limit_rate: "主板涨跌幅",
  main_board_st_limit_rate_before_change: "主板 ST 调整前涨跌幅",
  main_board_st_limit_rate: "主板 ST 当前涨跌幅",
  main_board_st_change_date: "主板 ST 规则切换日",
  star_market_limit_rate: "科创板涨跌幅",
  chinext_limit_rate: "创业板涨跌幅",
  beijing_limit_rate: "北交所涨跌幅",
  etf_limit_rate: "ETF 涨跌幅",
  ipo_unlimited_sessions: "沪深新股无涨跌停交易日",
  beijing_ipo_unlimited_sessions: "北交所新股无涨跌停交易日",
  state_lookback_sessions: "状态补齐向前查找交易日数",
  fill_unknown_suspension_as_tradable: "无历史停牌状态时按可交易补齐",
  reference_price_lookback_sessions: "未复权参考价向前查找交易日数",
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

function parameterDraftValue(parameter: SdkParameter) {
  return parameter.default === null ? "None" : String(parameter.default)
}

function parameterLabel(parameter: SdkParameter) {
  return parameter.label || PARAMETER_LABELS[parameter.name] || parameter.name.replace(/_/g, " ")
}

function entrypointBusinessLabel(entrypoint: SdkEntrypoint) {
  if (entrypoint.label) return entrypoint.label
  if (entrypoint.kind === "signal") return "选股模型"
  if (entrypoint.kind === "schedule") return "自定义调仓日"
  if (entrypoint.kind === "portfolio") return "目标仓位模型"
  if (entrypoint.kind === "event") return EVENT_LABELS[entrypoint.event || ""] || "持有期处理规则"
  if (entrypoint.kind === "execution_data_fill") return "缺失交易状态补齐"
  if (entrypoint.kind === "execution") return "成交模型"
  return entrypoint.id
}

function ParameterControl({ parameter, value, disabled, onChange }: {
  parameter: SdkParameter
  value: string
  disabled: boolean
  onChange: (value: string) => void
}) {
  const boundedNumber = typeof parameter.default === "number" && parameter.minimum !== null && parameter.maximum !== null
  if (typeof parameter.default === "boolean") {
    return <div className="strategy-segmented-control"><button type="button" className={value === "true" ? "active" : ""} disabled={disabled} onClick={() => onChange("true")}>启用</button><button type="button" className={value === "false" ? "active" : ""} disabled={disabled} onClick={() => onChange("false")}>关闭</button></div>
  }
  if (parameter.name === "activation") {
    return <select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}><option value="next_session_open">下一交易日开盘</option><option value="next_session_close">下一交易日收盘</option></select>
  }
  if (boundedNumber) {
    return <div className="strategy-range-control"><input type="range" min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} step={parameter.step ?? "any"} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} /><Input type="number" min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} step={parameter.step ?? undefined} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} /></div>
  }
  return <Input type={typeof parameter.default === "number" ? "number" : "text"} min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} step={parameter.step ?? undefined} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} />
}

function StrategyVisualEditor({ stageGroups, factors, locked, onError, onDraftChange }: {
  stageGroups: Record<StageId, SdkEntrypoint[]>
  factors: SdkEntrypoint[]
  locked: boolean
  onError: (message: string) => void
  onDraftChange: (draft: VisualDraft) => void
}) {
  const sdk = useStrategySdk()
  const strategyEntrypoints = STAGES.flatMap((stage) => stageGroups[stage.id])
  const signalEntrypoint = stageGroups.selection.find((item) => item.kind === "signal")
  const schedule = signalEntrypoint?.metadata.schedule as Record<string, string> | undefined
  const blend = (signalEntrypoint?.metadata.factor_blend ?? {}) as FactorBlendMetadata
  const projectionVersion = `${sdk.project?.id ?? ""}:${sdk.project?.strategy_id ?? "main"}:${sdk.project?.draft_source_sha256 ?? ""}`
  const scheduleKey = `${projectionVersion}:${JSON.stringify(schedule ?? {})}`
  const blendKey = `${projectionVersion}:${JSON.stringify(blend)}`
  const parameterKey = `${projectionVersion}:${JSON.stringify(strategyEntrypoints.map((entrypoint) => [entrypoint.id, entrypoint.parameters.map((item) => [item.name, item.default])]))}`
  const [frequency, setFrequency] = useState(schedule?.frequency || "monthly")
  const [selector, setSelector] = useState(schedule?.selector || "last_trading_day")
  const [at, setAt] = useState(schedule?.at || "close")
  const [rows, setRows] = useState<FactorWeightDraft[]>(() => Object.entries(blend.weights ?? {}).map(([factorId, weight]) => ({ factorId, weight: String(weight) })))
  const [normalization, setNormalization] = useState<"raw" | "rank" | "zscore">(blend.normalization ?? "rank")
  const [addFactorId, setAddFactorId] = useState("")
  const [parameterValues, setParameterValues] = useState<Record<string, Record<string, string>>>(() => Object.fromEntries(strategyEntrypoints.map((entrypoint) => [entrypoint.id, Object.fromEntries(entrypoint.parameters.map((parameter) => [parameter.name, parameterDraftValue(parameter)]))])))
  const [expandedStages, setExpandedStages] = useState<StageId[]>(["selection"])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setFrequency(schedule?.frequency || "monthly")
    setSelector(schedule?.selector || "last_trading_day")
    setAt(schedule?.at || "close")
  }, [scheduleKey]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const projected = Object.entries(blend.weights ?? {}).map(([factorId, weight]) => ({ factorId, weight: String(weight) }))
    setRows(projected)
    setNormalization(blend.normalization ?? "rank")
  }, [blendKey]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setParameterValues(Object.fromEntries(strategyEntrypoints.map((entrypoint) => [entrypoint.id, Object.fromEntries(entrypoint.parameters.map((parameter) => [parameter.name, parameterDraftValue(parameter)]))])))
  }, [parameterKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const scheduleSupported = schedule?.mode === "structured"
  const blendSupported = blend.mode === "single" || blend.mode === "structured"
  const editable = Boolean(sdk.project?.editable) && !locked
  const factorById = Object.fromEntries(factors.map((factor) => [factor.id, factor]))
  const available = factors.filter((factor) => !rows.some((row) => row.factorId === factor.id))
  const selectedToAdd = available.some((factor) => factor.id === addFactorId) ? addFactorId : available[0]?.id ?? ""
  const parsed = rows.map((row) => ({ ...row, numeric: Number(row.weight) }))
  const factorValuesValid = !blendSupported || rows.length > 0 && parsed.every((row) => row.weight !== "" && Number.isFinite(row.numeric) && Math.abs(row.numeric) > 1e-12)
  const projectedWeights = Object.fromEntries(parsed.filter((row) => Number.isFinite(row.numeric)).map((row) => [row.factorId, row.numeric]))
  const baselineWeights = blend.weights ?? {}
  const orderedKey = (weights: Record<string, number>) => JSON.stringify(Object.entries(weights).sort(([left], [right]) => left.localeCompare(right)))
  const scheduleChanged = Boolean(scheduleSupported && (frequency !== schedule.frequency || (frequency === "daily" ? "every" : selector) !== schedule.selector || at !== schedule.at))
  const blendChanged = Boolean(blendSupported && (orderedKey(projectedWeights) !== orderedKey(baselineWeights) || normalization !== blend.normalization))
  const changedParameters = strategyEntrypoints.flatMap((entrypoint) => entrypoint.parameters.filter((parameter) => parameter.editable && parameterValues[entrypoint.id]?.[parameter.name] !== parameterDraftValue(parameter)).map((parameter) => ({ entrypoint, parameter })))
  const parameterValuesValid = changedParameters.every(({ entrypoint, parameter }) => typeof parameter.default !== "number" || parameterValues[entrypoint.id]?.[parameter.name]?.trim() !== "" && Number.isFinite(Number(parameterValues[entrypoint.id]?.[parameter.name])))
  const changed = scheduleChanged || blendChanged || changedParameters.length > 0
  const total = parsed.reduce((sum, row) => sum + (Number.isFinite(row.numeric) ? Math.abs(row.numeric) : 0), 0)
  const pendingEdits: Array<Record<string, unknown>> = []
  if (scheduleChanged && signalEntrypoint) pendingEdits.push({ operation: "schedule", entrypoint_id: signalEntrypoint.id, frequency, selector: frequency === "daily" ? "every" : selector, at })
  if (blendChanged && signalEntrypoint) pendingEdits.push({ operation: "factor_blend", entrypoint_id: signalEntrypoint.id, factor_weights: projectedWeights, normalization })
  for (const { entrypoint, parameter } of changedParameters) pendingEdits.push({ operation: "parameter", entrypoint_id: entrypoint.id, parameter: parameter.name, value: parameterValue(parameter, parameterValues[entrypoint.id][parameter.name]) })
  const draftValid = factorValuesValid && parameterValuesValid
  const draftKey = JSON.stringify(pendingEdits)
  useEffect(() => onDraftChange({ edits: pendingEdits, valid: draftValid }), [draftKey, draftValid, onDraftChange]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => onDraftChange({ edits: [], valid: true }), [onDraftChange])

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

  function toggleStage(stageId: StageId) {
    setExpandedStages((current) => current.includes(stageId) ? current.filter((item) => item !== stageId) : [...current, stageId])
  }

  async function apply() {
    setBusy(true); onError("")
    try {
      await sdk.structuredEdit({ operation: "batch", edits: pendingEdits })
    } catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  return <section className="strategy-all-settings">
    <div className="strategy-stage-sections">{STAGES.map((stage) => {
      const Icon = stage.icon
      const entrypoints = stageGroups[stage.id]
      const expanded = expandedStages.includes(stage.id)
      return <article className={`strategy-stage-section ${expanded ? "expanded" : ""}`} key={stage.id}>
        <button type="button" className="strategy-stage-section-toggle" onClick={() => toggleStage(stage.id)}><span><Icon size={17} /><strong>{stage.title}</strong></span><span className="strategy-stage-chevron">⌄</span></button>
        {expanded ? <div className="strategy-settings-table-wrap"><table className="strategy-settings-table"><thead><tr><th>设置项</th><th>配置</th><th>操作</th></tr></thead><tbody>
          {stage.id === "selection" && schedule ? <tr className="strategy-settings-group"><th colSpan={3}>调仓时间</th></tr> : null}
          {stage.id === "selection" && scheduleSupported ? <>
            <tr><td>频率</td><td colSpan={2}><select value={frequency} disabled={!editable} onChange={(event) => setFrequency(event.target.value)}><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></select></td></tr>
            <tr><td>交易日</td><td colSpan={2}><select value={frequency === "daily" ? "every" : selector} disabled={!editable || frequency === "daily"} onChange={(event) => setSelector(event.target.value)}><option value="every">每个交易日</option><option value="first_trading_day">首个交易日</option><option value="last_trading_day">最后交易日</option></select></td></tr>
            <tr><td>计算时点</td><td colSpan={2}><select value={at} disabled={!editable} onChange={(event) => setAt(event.target.value)}><option value="open">开盘</option><option value="close">收盘</option></select></td></tr>
          </> : stage.id === "selection" && schedule && signalEntrypoint ? <tr><td>调仓时间</td><td colSpan={2}>自定义 Python</td></tr> : null}
          {stage.id === "selection" && signalEntrypoint ? <tr className="strategy-settings-group"><th colSpan={3}>因子选股</th></tr> : null}
          {stage.id === "selection" && blendSupported ? <>
            <tr><td>合成前标准化</td><td colSpan={2}><select value={normalization} disabled={!editable} onChange={(event) => setNormalization(event.target.value as "raw" | "rank" | "zscore")}><option value="rank">横截面排名</option><option value="zscore">Z-score 标准化</option><option value="raw">原始值</option></select></td></tr>
            {rows.map((row) => {
              const factor = factorById[row.factorId]
              const numeric = Number(row.weight)
              const share = total > 0 && Number.isFinite(numeric) ? Math.abs(numeric) / total : 0
              return <tr key={row.factorId}><td><div className="strategy-factor-identity"><span className="strategy-factor-avatar">{(factor?.label || row.factorId).slice(0, 1).toUpperCase()}</span><strong>{factor?.label || row.factorId}</strong></div></td><td><div className="strategy-factor-config"><div className="strategy-factor-direction"><button type="button" className={numeric >= 0 ? "active" : ""} disabled={!editable} onClick={() => setDirection(row, 1)}>高值优先</button><button type="button" className={numeric < 0 ? "active inverse" : ""} disabled={!editable} onClick={() => setDirection(row, -1)}>低值优先</button></div><label className="strategy-factor-weight"><span>权重</span><Input type="number" min="0.01" step="0.05" value={row.weight === "" ? "" : Math.abs(numeric)} disabled={!editable} onChange={(event) => setMagnitude(row, event.target.value)} /></label></div></td><td><div className="strategy-factor-row-actions"><div className="strategy-factor-share"><span>{(share * 100).toFixed(1)}%</span><i><b className={numeric < 0 ? "inverse" : ""} style={{ width: `${share * 100}%` }} /></i></div><button className="strategy-factor-remove" type="button" aria-label={`移除 ${factor?.label || row.factorId}`} disabled={!editable || rows.length === 1} onClick={() => setRows((current) => current.filter((item) => item.factorId !== row.factorId))}><Trash2 size={14} /></button></div></td></tr>
            })}
            <tr><td>添加因子</td><td><select aria-label="添加因子" value={selectedToAdd} disabled={!editable || !available.length} onChange={(event) => setAddFactorId(event.target.value)}>{available.length ? available.map((factor) => <option value={factor.id} key={factor.id}>{factor.label || factor.id}</option>) : <option value="">因子已全部加入</option>}</select></td><td><Button size="sm" variant="outline" disabled={!editable || !selectedToAdd} onClick={addFactor}><Plus />添加</Button></td></tr>
          </> : stage.id === "selection" && signalEntrypoint ? <tr><td>因子组合</td><td colSpan={2}>自定义 Python</td></tr> : null}
          {entrypoints.map((entrypoint) => <Fragment key={entrypoint.id}>
            <tr className="strategy-settings-group"><th colSpan={3}>{entrypointBusinessLabel(entrypoint)}</th></tr>
            {entrypoint.parameters.map((parameter) => <tr key={`${entrypoint.id}.${parameter.name}`}><td><strong>{parameterLabel(parameter)}</strong></td><td colSpan={2}><ParameterControl parameter={parameter} value={parameterValues[entrypoint.id]?.[parameter.name] ?? parameterDraftValue(parameter)} disabled={!editable || !parameter.editable} onChange={(value) => setParameterValues((current) => ({ ...current, [entrypoint.id]: { ...current[entrypoint.id], [parameter.name]: value } }))} /></td></tr>)}
            {entrypoint.kind === "portfolio" && entrypoint.parameters.some((parameter) => parameter.name === "max_weight") ? <tr><td colSpan={3} className="text-xs text-muted-foreground">权重用小数填写：0.10 = 10%。默认等权配置取「1 ÷ 实际入选数量」与单标的最大权重的较小值，未分配部分留作现金。</td></tr> : null}
            {!entrypoint.parameters.length ? <tr><td>实现方式</td><td colSpan={2}>自定义 Python</td></tr> : null}
          </Fragment>)}
          {!entrypoints.length ? stage.id === "risk" ? <tr><td>实现方式</td><td colSpan={2}>自定义 Python</td></tr> : <tr><td colSpan={3}>当前环节尚未配置</td></tr> : null}
        </tbody></table></div> : null}
      </article>
    })}</div>
    <footer><Button disabled={!editable || busy || !changed || !factorValuesValid || !parameterValuesValid} onClick={() => void apply()}><Save />{busy ? "应用中" : "应用全部设置"}</Button></footer>
  </section>
}

function PreviewPanel({ preview }: { preview: Record<string, unknown> | null }) {
  if (!preview) return <div className="analytics-empty">运行预览后，这里会显示入选证券、目标权重、风控结果和成交策略。</div>
  const result = asRecord(preview.result)
  const signal = asRecord(result.signal)
  const decision = asRecord(result.decision)
  const policy = asRecord(result.execution_policy)
  const scores = asRecord(signal.scores)
  const weights = asRecord(decision.target_weights)
  const hasDecision = decision.target_weights !== undefined
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
        <div><span>目标总仓位</span><strong>{hasDecision ? `${(gross * 100).toFixed(1)}%` : "—"}</strong></div>
      </div>
      {hasDecision ? <p className="text-xs text-muted-foreground">目标现金比例：{((1 - gross) * 100).toFixed(2)}%。目标仓位由仓位分配函数计算；默认等权配置受单标的最大权重约束。</p> : null}
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
  const [view, setView] = useState<WorkspaceView>("author")
  const [visualDraft, setVisualDraft] = useState<VisualDraft>({ edits: [], valid: true })
  const [sourcePreview, setSourcePreview] = useState<VisualEditPreview | null>(null)
  const [loadingSourcePreview, setLoadingSourcePreview] = useState(false)
  const [visualPaneWidth, setVisualPaneWidth] = useState(42)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const splitPane = useRef<HTMLDivElement>(null)

  const strategyEntrypoints = useMemo(
    () => project?.inspection.entrypoints.filter((item) => item.kind !== "factor" && item.kind !== "universe") ?? [],
    [project?.inspection.entrypoints],
  )
  const factorEntrypoints = useMemo(
    () => project?.inspection.entrypoints.filter((item) => item.kind === "factor") ?? [],
    [project?.inspection.entrypoints],
  )
  const stageGroups = useMemo(() => Object.fromEntries(STAGES.map((stage) => [stage.id, strategyEntrypoints.filter((item) => stage.kinds.includes(item.kind)).sort((left, right) => stage.kinds.indexOf(left.kind) - stage.kinds.indexOf(right.kind))])) as Record<StageId, SdkEntrypoint[]>, [strategyEntrypoints])
  const projectId = project?.id ?? ""
  const selectedStrategyId = project?.strategy_id ?? "main"
  const projectHash = project?.draft_source_sha256 ?? ""
  const moduleDirty = Boolean(project && source !== project.strategy_source)
  const visualDirty = visualDraft.edits.length > 0
  const visualDraftKey = JSON.stringify(visualDraft.edits)
  const hasUnsavedChanges = Boolean(project?.dirty || moduleDirty || visualDirty)
  const editLocked = moduleDirty
  const needsDefaultMigration = Boolean(
    project?.editable
    && !project.inspection.entrypoints.some((item) => item.kind === "execution_data_fill"),
  )

  useEffect(() => setSource(project?.strategy_source ?? ""), [project?.id, project?.strategy_id, project?.strategy_source, project?.draft_source_sha256])
  useEffect(() => setPreview(null), [project?.id, project?.strategy_id, project?.current_revision, project?.draft_source_sha256])
  useEffect(() => {
    if (!projectId || !visualDirty || !visualDraft.valid) {
      setSourcePreview(null)
      setLoadingSourcePreview(false)
      return
    }
    let current = true
    setSourcePreview(null)
    setLoadingSourcePreview(true)
    const timer = window.setTimeout(() => {
      void api.post<VisualEditPreview>(sdk.strategyUrl(`/edits/preview`), {
        edits: visualDraft.edits,
        expected_source_sha256: projectHash,
      }).then((payload) => {
        if (current && payload.base_source_sha256 === projectHash) setSourcePreview(payload)
      }).catch((reason: Error) => {
        if (current) setError(reason.message)
      }).finally(() => {
        if (current) setLoadingSourcePreview(false)
      })
    }, 250)
    return () => { current = false; window.clearTimeout(timer) }
  }, [projectHash, projectId, selectedStrategyId, visualDraft.valid, visualDraftKey, visualDirty]) // eslint-disable-line react-hooks/exhaustive-deps
  function chooseView(next: WorkspaceView) {
    if (next === view) return
    if (visualDirty || moduleDirty) {
      setError("当前修改尚未保存，请先应用或保存。")
      return
    }
    setError("")
    setView(next)
  }

  function beginResize(event: ReactPointerEvent<HTMLButtonElement>) {
    const container = splitPane.current
    if (!container) return
    event.preventDefault()
    const move = (pointer: PointerEvent) => {
      const bounds = container.getBoundingClientRect()
      const width = ((pointer.clientX - bounds.left) / bounds.width) * 100
      setVisualPaneWidth(Math.min(65, Math.max(28, width)))
    }
    const stop = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", stop)
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", stop)
  }

  async function saveDraft(nextSource = source) {
    if (!project?.editable || (nextSource === project.strategy_source && !project.dirty)) return
    setBusy(true); setError("")
    try { await sdk.updateDraft(nextSource) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function migrateDefault() {
    if (!project?.editable || hasUnsavedChanges) return
    if (!await confirm({
      title: "迁移默认项目基础组件",
      description: "将用最新 sdk-v1-default 更新标的池和交易状态补齐函数，并保存为新版本；历史回测版本保持不变。",
      confirmText: "迁移并保存",
    })) return
    setBusy(true); setError("")
    try {
      await api.post(sdk.strategyUrl(`/default-migration`), {
        expected_source_sha256: projectHash,
        confirm_write: true,
        confirm_python_execution: true,
      })
      await sdk.refresh(project.id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function runPreview(operation: PreviewOperation) {
    if (!project || hasUnsavedChanges) return
    const previewLabel = operation === "signal" ? "预览本期选股" : operation === "portfolio" ? "预览目标仓位" : "预览完整策略"
    if (!await confirm({ title: previewLabel, description: "将运行当前已保存策略。本机 Python 不是安全沙箱。", confirmText: "运行预览" })) return
    setBusy(true); setError("")
    try {
      setPreview(await api.post<Record<string, unknown>>(sdk.strategyUrl(`/preview`), { operation, profile: project.profile, revision: project.current_revision, confirm_python_execution: true }))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project) return <Widget title="策略工作台" loading={sdk.loading} error={sdk.error}><span /></Widget>

  const moduleDisplaySource = visualDirty && sourcePreview ? sourcePreview.source : source
  const codeReadOnly = !project.editable || visualDirty
  const sourcePreviewStatus = visualDirty
    ? !visualDraft.valid
      ? "左侧设置有误"
      : loadingSourcePreview
        ? "正在生成"
        : sourcePreview
          ? "待应用预览"
          : "等待预览"
    : null

  return (
    <Widget headerless className="strategy-workbench-widget">
      <div className="strategy-business-workbench">
        <ProjectStrategies key={project.id} locked={busy || hasUnsavedChanges} onError={setError} onBusy={setBusy} />
        {error ? <div className="workbench-message error strategy-workbench-error">{error}</div> : null}
        <main className="strategy-authoring-main strategy-authoring-main-single">
          <header className="strategy-authoring-header">
            <nav aria-label="策略工作区"><button type="button" className={view === "author" ? "active" : ""} onClick={() => chooseView("author")}><Settings2 size={14} />策略编辑</button><button type="button" className={view === "preview" ? "active" : ""} onClick={() => chooseView("preview")}><Play size={14} />可视化预览</button></nav>
          </header>

          <div className={`strategy-authoring-content ${view === "author" ? "strategy-authoring-content-split" : ""}`}>
            {view === "author" ? <div
              ref={splitPane}
              className="strategy-split-authoring"
              style={{ gridTemplateColumns: `${visualPaneWidth}% 0.4rem minmax(0, 1fr)` }}
            >
              <section className="strategy-visual-pane">
                <header className="strategy-pane-header"><div><Settings2 size={16} /><strong>可视化配置</strong></div>{visualDirty ? <Badge variant={visualDraft.valid ? "secondary" : "destructive"}>待应用</Badge> : null}</header>
                <div className="strategy-pane-scroll">
                  {needsDefaultMigration ? <div className="workbench-message warning">这个旧项目还没有默认的交易状态补齐函数。<Button variant="outline" disabled={busy || hasUnsavedChanges} onClick={() => void migrateDefault()}>迁移最新默认项目组件</Button></div> : null}
                  {editLocked ? <div className="workbench-message warning">右侧 Python 有未保存修改；保存后才能调整左侧设置。</div> : null}
                  <StrategyVisualEditor
                    key={`${project.id}:${project.strategy_id}`}
                    stageGroups={stageGroups}
                    factors={factorEntrypoints}
                    locked={editLocked}
                    onError={setError}
                    onDraftChange={setVisualDraft}
                  />
                </div>
              </section>

              <button
                type="button"
                className="strategy-split-divider"
                role="separator"
                aria-orientation="vertical"
                aria-label="调整可视化与 Python 面板宽度"
                aria-valuemin={28}
                aria-valuemax={65}
                aria-valuenow={Math.round(visualPaneWidth)}
                onPointerDown={beginResize}
                onKeyDown={(event) => {
                  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
                  event.preventDefault()
                  setVisualPaneWidth((current) => Math.min(65, Math.max(28, current + (event.key === "ArrowLeft" ? -2 : 2))))
                }}
              />

              <section className="strategy-code-pane">
                <header className="strategy-pane-header strategy-code-pane-header">
                  <div><Code2 size={16} /><strong title={project.strategy_path}>{project.strategy_id}.py</strong>{sourcePreviewStatus ? <Badge variant={!visualDraft.valid ? "destructive" : "secondary"}>{sourcePreviewStatus}</Badge> : <Badge variant={moduleDirty ? "destructive" : "secondary"}>{moduleDirty ? "未保存" : "已同步"}</Badge>}</div>
                </header>
                <div className="strategy-pane-scroll strategy-code-pane-scroll">
                  <div className="strategy-code-editor-view">
                    <PythonEditor
                      className="strategy-code-python-editor"
                      kind="strategy"
                      documentId={visualDirty ? `${project.id}.${project.strategy_id}.visual.strategy` : `${project.id}.${project.strategy_id}.strategy`}
                      value={moduleDisplaySource}
                      version={visualDirty ? sourcePreview?.source_sha256 ?? `${projectHash}:visual-pending` : projectHash}
                      disabled={codeReadOnly}
                      height="100%"
                      factors={factorEntrypoints.map((factor) => ({ id: factor.id, label: factor.label }))}
                      parameters={project.inspection.entrypoints.flatMap((entrypoint) => entrypoint.parameters)}
                      onChange={visualDirty ? () => undefined : setSource}
                      onSave={visualDirty ? undefined : (nextSource) => saveDraft(nextSource)}
                    />
                    {visualDirty ? <div className="workbench-message info strategy-code-preview-message">左侧设置尚未应用，右侧为只读 Python 预览。</div> : <div className="strategy-code-actions"><Button disabled={!project.editable || (!moduleDirty && !project.dirty) || busy} onClick={() => void saveDraft()}><Save />保存</Button></div>}
                  </div>
                </div>
              </section>
            </div> : null}

            {view === "preview" ? <div className="strategy-preview-view">
                <div className="strategy-preview-toolbar"><div><Play size={17} /><strong>策略预览</strong></div><div className="strategy-preview-actions"><Button variant="outline" disabled={busy || hasUnsavedChanges} onClick={() => void runPreview("signal")}>预览选股</Button><Button variant="outline" disabled={busy || hasUnsavedChanges} onClick={() => void runPreview("portfolio")}>预览仓位</Button><Button disabled={busy || hasUnsavedChanges} onClick={() => void runPreview("execution")}><Play />{busy ? "运行中" : "预览完整策略"}</Button></div></div>
                {hasUnsavedChanges ? <div className="workbench-message warning">请先保存代码，再运行预览。</div> : null}
                <PreviewPanel preview={preview} />
              </div> : null}
          </div>
        </main>
      </div>
    </Widget>
  )
}
