import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { CalendarDays, RefreshCw } from "lucide-react"

import { CandlestickChart, HorizontalBarChart, RiskPieChart } from "@/components/charts"
import type { CandlestickMarker } from "@/components/charts/CandlestickChart"
import { SymbolCombobox, type SymbolOption } from "@/components/shared/SymbolCombobox"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { usePipelineStageRun } from "@/hooks/use-pipeline-stage-run"
import {
  api,
  type MarketBar,
  type MarketInstrument,
  type PipelinePreview,
  type PythonPipelineStage,
} from "@/lib/api"
import type { DataProfile } from "@/lib/data-profile"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"

type UnknownRecord = Record<string, unknown>

const EMPTY_INDICATORS: [] = []

function record(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRecord
    : {}
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

function numbers(value: unknown): Record<string, number> {
  return Object.fromEntries(
    Object.entries(record(value))
      .map(([key, item]) => [key, Number(item)] as const)
      .filter(([, item]) => Number.isFinite(item)),
  )
}

function numeric(value: unknown, fallback = 0): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function stageOutput(preview: PipelinePreview | null, stage: PythonPipelineStage): UnknownRecord {
  return record(preview?.stage_outputs?.[stage])
}

function useStagePanel(stage: PythonPipelineStage) {
  const { selectedStrategy } = useWorkspace()
  const run = usePipelineStageRun(selectedStrategy, stage)
  return {
    ...run,
    output: stageOutput(run.preview, stage),
  }
}

function PanelToolbar({
  signalDate,
  profile,
  running,
}: {
  signalDate?: string | null
  profile: DataProfile
  running: boolean
}) {
  return (
    <div className="stage-panel-toolbar">
      <span>
        {running ? <RefreshCw className="spin" size={12} /> : <CalendarDays size={12} />}
        {profile === "runtime" ? "本地 RQ" : "演示数据"} · {running ? "正在生成…" : signalDate || "尚未生成"}
      </span>
    </div>
  )
}

function StagePanel({
  signalDate,
  profile,
  running,
  error,
  children,
}: {
  signalDate?: string | null
  profile: DataProfile
  running: boolean
  error?: Error | null
  children: React.ReactNode
}) {
  return (
    <Widget headerless className="pipeline-stage-panel">
      <PanelToolbar signalDate={signalDate} profile={profile} running={running} />
      {error ? <div className="workbench-message error">{error.message}</div> : null}
      <div className="stage-panel-body">{children}</div>
    </Widget>
  )
}

function EmptyRunState({ stale = false }: { stale?: boolean }) {
  return (
    <div className="analytics-empty">
      {stale ? "项目、数据环境或数据截至日已变化，请重新运行当前阶段" : "当前上下文没有有效结果，请运行当前阶段"}
    </div>
  )
}

function useInstrumentOptions(profile: DataProfile) {
  return useQuery({
    queryKey: ["data", "market", "symbols", profile],
    queryFn: () => api.get<{ symbols: string[]; instruments?: MarketInstrument[] }>(
      `/data/market/symbols?profile=${profile}`,
    ),
    staleTime: Infinity,
    select: (payload): SymbolOption[] => payload.instruments?.length
      ? payload.instruments
      : payload.symbols.map((symbol) => ({ symbol, name: null })),
  })
}

function useStageBars(profile: DataProfile, symbol: string, end: string | null) {
  const start = useMemo(() => {
    if (!end) return ""
    const value = new Date(`${end}T00:00:00`)
    value.setFullYear(value.getFullYear() - 1)
    return value.toISOString().slice(0, 10)
  }, [end])
  return useQuery({
    queryKey: ["data", "market", "bars", profile, symbol, start, end],
    queryFn: () => {
      const params = new URLSearchParams({
        profile,
        symbol,
        start,
        end: end ?? "",
      })
      return api.get<{ rows: MarketBar[] }>(`/data/market/bars?${params.toString()}`)
    },
    enabled: Boolean(symbol && start && end),
    staleTime: Infinity,
  })
}

function symbolLabel(symbol: string, names: Map<string, string | null>): string {
  const name = names.get(symbol)
  return name ? `${symbol} ${name}` : symbol
}

export function UniverseMembersWidget() {
  const panel = useStagePanel("universe")
  const { selectedSymbol, setSelectedSymbol } = useWorkspace()
  const instruments = useInstrumentOptions(panel.profile)
  const [query, setQuery] = useState("")
  const symbols = strings(panel.output.symbols)
  const names = useMemo(
    () => new Map((instruments.data ?? []).map((item) => [item.symbol, item.name ?? null])),
    [instruments.data],
  )
  const visible = symbols.filter((symbol) => {
    const needle = query.trim().toUpperCase()
    return !needle || symbolLabel(symbol, names).toUpperCase().includes(needle)
  })
  const eligibleCount = numeric(panel.preview?.diagnostics.eligible_count, symbols.length)

  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid">
            <div><span>基础合格标的</span><strong>{eligibleCount}</strong></div>
            <div><span>当前标的池</span><strong>{symbols.length}</strong></div>
          </div>
          <label className="stage-filter"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索代码或名称" /></label>
          <div className="stage-symbol-grid">
            {visible.map((symbol) => (
              <button key={symbol} type="button" className={selectedSymbol === symbol ? "active" : ""} onClick={() => setSelectedSymbol(symbol)}>
                <strong>{symbol}</strong><span>{names.get(symbol) || "—"}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </StagePanel>
  )
}

export function SelectionRankingWidget() {
  const panel = useStagePanel("selection")
  const { selectedSymbol, setSelectedSymbol } = useWorkspace()
  const instruments = useInstrumentOptions(panel.profile)
  const selected = new Set(strings(panel.output.selected))
  const scores = numbers(panel.output.scores)
  const names = useMemo(
    () => new Map((instruments.data ?? []).map((item) => [item.symbol, item.name ?? null])),
    [instruments.data],
  )
  const rows = Object.entries(scores).sort((left, right) => right[1] - left[1])

  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid">
            <div><span>选股池</span><strong>{selected.size}</strong></div>
            <div><span>参与排名</span><strong>{rows.length}</strong></div>
          </div>
          <div className="stage-table-wrap">
            <table className="stage-table">
              <thead><tr><th>#</th><th>证券</th><th>得分</th><th>结果</th></tr></thead>
              <tbody>{rows.map(([symbol, score], index) => (
                <tr key={symbol} className={selectedSymbol === symbol ? "active" : ""} onClick={() => setSelectedSymbol(symbol)}>
                  <td>{index + 1}</td><td><strong>{symbol}</strong><small>{names.get(symbol) || ""}</small></td>
                  <td>{formatNumber(score, 3)}</td><td>{selected.has(symbol) ? <em>入选</em> : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
    </StagePanel>
  )
}

function SecurityChartWidget({ mode }: { mode: "universe" | "selection" }) {
  const panel = useStagePanel(mode)
  const { selectedSymbol, setSelectedSymbol } = useWorkspace()
  const instruments = useInstrumentOptions(panel.profile)
  const universe = strings(stageOutput(panel.preview, "universe").symbols)
  const selection = strings(stageOutput(panel.preview, "selection").selected)
  const candidates = mode === "universe" ? universe : (selection.length ? selection : universe)
  const symbol = selectedSymbol && candidates.includes(selectedSymbol) ? selectedSymbol : candidates[0] ?? ""
  const end = panel.preview?.signal_date ?? null
  const bars = useStageBars(panel.profile, symbol, end)
  const options = useMemo(() => {
    const names = new Map((instruments.data ?? []).map((item) => [item.symbol, item.name]))
    return candidates.map((item) => ({ symbol: item, name: names.get(item) ?? null }))
  }, [candidates, instruments.data])
  const markers = useMemo<CandlestickMarker[]>(
    () => end ? [{ time: end, position: "aboveBar", shape: "circle", color: "#2962ff", text: mode === "selection" ? "入选" : "池内" }] : [],
    [end, mode],
  )
  const rows = useMemo(() => bars.data?.rows ?? [], [bars.data?.rows])

  return (
    <StagePanel signalDate={end} profile={panel.profile} running={panel.isRunning} error={panel.error ?? bars.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-chart-toolbar">
            <SymbolCombobox symbols={options} value={symbol} onChange={setSelectedSymbol} ariaLabel="图表证券" />
          </div>
          {bars.isLoading ? <div className="analytics-empty">正在加载行情…</div> : null}
          {!bars.isLoading && !rows.length ? <div className="analytics-empty">该证券没有可用行情</div> : null}
          {rows.length ? <div className="stage-kline-frame"><CandlestickChart rows={rows} selectedIndicators={EMPTY_INDICATORS} markers={markers} height={340} /></div> : null}
        </>
      )}
    </StagePanel>
  )
}

export const UniverseChartWidget = () => <SecurityChartWidget mode="universe" />
export const SelectionChartWidget = () => <SecurityChartWidget mode="selection" />

export function TimingReferenceWidget() {
  const panel = useStagePanel("timing")
  const timing = panel.output
  const rows = useMemo(() => {
    let wealth = 1
    return (panel.preview?.timing_reference ?? []).map((point) => {
      wealth *= 1 + numeric(point.value)
      return { date: point.date, wealth }
    })
  }, [panel.preview?.timing_reference])

  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-chart-toolbar">
            <strong>MKT 市场累计净值</strong>
            <span className="status-pill">当前仓位 {formatPercent(numeric(timing.exposure), 0)}</span>
          </div>
          {rows.length ? (
            <div className="stage-history-chart">
              <ResponsiveContainer width="100%" height={340}>
                <AreaChart data={rows}>
                  <defs><linearGradient id="timingMarket" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#2962ff" stopOpacity={0.32}/><stop offset="95%" stopColor="#2962ff" stopOpacity={0.03}/></linearGradient></defs>
                  <CartesianGrid stroke="hsl(var(--border))" opacity={0.45}/>
                  <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={30}/>
                  <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10 }} tickFormatter={(value) => formatNumber(Number(value), 2)}/>
                  <Tooltip formatter={(value) => formatNumber(Number(value), 3)}/>
                  <Area type="monotone" dataKey="wealth" stroke="#2962ff" fill="url(#timingMarket)" isAnimationActive={false}/>
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : <div className="analytics-empty">当前数据源没有 MKT 市场序列</div>}
        </>
      )}
    </StagePanel>
  )
}

export function TimingResultWidget() {
  const panel = useStagePanel("timing")
  const selection = strings(stageOutput(panel.preview, "selection").selected)
  const exposure = numeric(panel.output.exposure)
  const signal = String(panel.output.signal ?? "—")
  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid">
            <div><span>市场仓位</span><strong>{formatPercent(exposure, 0)}</strong></div>
            <div><span>选股池</span><strong>{selection.length}</strong></div>
          </div>
          <div className="stage-settings-grid">
            <div><span>择时信号</span><strong>{signal}</strong></div>
            <div><span>作用方式</span><strong>覆盖选股池仓位</strong></div>
          </div>
          <div className="analytics-empty compact">择时只调整市场仓位，不重新选择股票；组合阶段会在当前选股池内分配权重。</div>
        </>
      )}
    </StagePanel>
  )
}

export function PortfolioWeightsWidget() {
  const panel = useStagePanel("portfolio")
  const weights = numbers(panel.output.weights)
  const rows = Object.entries(weights).sort((left, right) => right[1] - left[1]).slice(0, 20)
  const gross = Object.values(weights).reduce((sum, item) => sum + Math.abs(item), 0)
  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState /> : <><div className="stage-kpi-grid"><div><span>组合标的</span><strong>{Object.keys(weights).length}</strong></div><div><span>计划仓位</span><strong>{formatPercent(gross, 1)}</strong></div></div><HorizontalBarChart data={rows.map(([name, value]) => ({ name, value }))} height={Math.max(220, rows.length * 28)} /></>}
    </StagePanel>
  )
}

export function PortfolioSummaryWidget() {
  const panel = useStagePanel("portfolio")
  const weights = numbers(panel.output.weights)
  const selection = strings(stageOutput(panel.preview, "selection").selected)
  const exposure = numeric(stageOutput(panel.preview, "timing").exposure, 1)
  const gross = Object.values(weights).reduce((sum, item) => sum + Math.abs(item), 0)
  const largest = Math.max(0, ...Object.values(weights))
  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <div className="stage-settings-grid">
          <div><span>输入选股池</span><strong>{selection.length} 只</strong></div>
          <div><span>择时覆盖</span><strong>{formatPercent(exposure, 0)}</strong></div>
          <div><span>组合总仓位</span><strong>{formatPercent(gross, 1)}</strong></div>
          <div><span>最大单股</span><strong>{formatPercent(largest, 1)}</strong></div>
        </div>
      )}
    </StagePanel>
  )
}

export function RiskLimitsWidget() {
  const panel = useStagePanel("risk")
  const before = numbers(stageOutput(panel.preview, "portfolio").weights)
  const after = numbers(panel.output.weights)
  const symbols = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .sort((left, right) => (after[right] ?? 0) - (after[left] ?? 0))
  return <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>{!panel.preview ? <EmptyRunState /> : <div className="stage-table-wrap"><table className="stage-table"><thead><tr><th>证券</th><th>约束前</th><th>约束后</th><th>变化</th></tr></thead><tbody>{symbols.map((symbol) => <tr key={symbol}><td><strong>{symbol}</strong></td><td>{formatPercent(before[symbol] ?? 0, 2)}</td><td>{formatPercent(after[symbol] ?? 0, 2)}</td><td className={(after[symbol] ?? 0) < (before[symbol] ?? 0) ? "negative" : ""}>{formatPercent((after[symbol] ?? 0) - (before[symbol] ?? 0), 2)}</td></tr>)}</tbody></table></div>}</StagePanel>
}

export function RiskExposureWidget() {
  const panel = useStagePanel("risk")
  const gross = numeric(panel.output.gross_exposure, Object.values(numbers(panel.output.weights)).reduce((sum, item) => sum + Math.abs(item), 0))
  const largest = Math.max(0, ...Object.values(numbers(panel.output.weights)))
  return <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>{!panel.preview ? <EmptyRunState /> : <><div className="stage-kpi-grid"><div><span>风险后仓位</span><strong>{formatPercent(gross, 1)}</strong></div><div><span>最大单股</span><strong>{formatPercent(largest, 1)}</strong></div></div><RiskPieChart height={260} innerRadius={55} showLabels={false} data={[{ name: "股票仓位", value: gross, color: "#2962ff" }, { name: "现金", value: Math.max(0, 1 - gross), color: "#9aa4b2" }]} /></>}</StagePanel>
}

export function ExecutionSettingsWidget() {
  const panel = useStagePanel("execution")
  const settings = record(panel.output.execution)
  const labels: Record<string, string> = { rebalance_freq: "调仓频率", execution_price: "成交价格", cost_bps: "佣金 bps", slippage_bps: "滑点 bps", impact_bps: "冲击 bps", max_participation_rate: "最大成交占比", portfolio_value: "组合资金" }
  return <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>{!panel.preview ? <EmptyRunState /> : <div className="stage-settings-grid">{Object.entries(settings).map(([key, value]) => <div key={key}><span>{labels[key] ?? key}</span><strong>{key.endsWith("_rate") ? formatPercent(numeric(value), 1) : String(value)}</strong></div>)}</div>}</StagePanel>
}

export function ExecutionTargetsWidget() {
  const panel = useStagePanel("execution")
  const weights = numbers(stageOutput(panel.preview, "risk").weights)
  const rows = Object.entries(weights).sort((left, right) => right[1] - left[1])
  const gross = Object.values(weights).reduce((sum, item) => sum + Math.abs(item), 0)
  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid"><div><span>目标标的</span><strong>{rows.length}</strong></div><div><span>目标总仓位</span><strong>{formatPercent(gross, 1)}</strong></div></div>
          <div className="stage-table-wrap"><table className="stage-table"><thead><tr><th>证券</th><th>风险后目标权重</th></tr></thead><tbody>{rows.map(([symbol, weight]) => <tr key={symbol}><td><strong>{symbol}</strong></td><td>{formatPercent(weight, 2)}</td></tr>)}</tbody></table></div>
          <div className="analytics-empty compact">实际换手、成交限制和成本需要完整回测结合上一期持仓计算。</div>
        </>
      )}
    </StagePanel>
  )
}
