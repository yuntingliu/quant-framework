import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { CalendarDays, Play, RefreshCw } from "lucide-react"

import { CandlestickChart, HorizontalBarChart, RiskPieChart } from "@/components/charts"
import type { CandlestickMarker } from "@/components/charts/CandlestickChart"
import { SymbolCombobox, type SymbolOption } from "@/components/shared/SymbolCombobox"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { usePipelineStageRun } from "@/hooks/use-pipeline-stage-run"
import {
  api,
  type MarketBar,
  type MarketInstrument,
  type PipelineAnalysisPoint,
  type PipelinePreview,
  type PythonPipelineStage,
} from "@/lib/api"
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

function pointOutput(point: PipelineAnalysisPoint, stage: PythonPipelineStage): UnknownRecord {
  return record(point.stage_outputs?.[stage])
}

function useStagePanel(stage: PythonPipelineStage) {
  const { selectedStrategy } = useWorkspace()
  const run = usePipelineStageRun(selectedStrategy, stage)
  return {
    ...run,
    output: stageOutput(run.preview, stage),
    points: run.analysis?.points ?? [],
  }
}

function PanelToolbar({
  signalDate,
  running,
  onRun,
}: {
  signalDate?: string | null
  running: boolean
  onRun: () => void
}) {
  return (
    <div className="stage-panel-toolbar">
      <span><CalendarDays size={12} />{signalDate || "尚未运行"}</span>
      <button type="button" onClick={onRun} disabled={running}>
        {running ? <RefreshCw className="spin" size={13} /> : <Play size={13} />}
        {running ? "运行中…" : "运行"}
      </button>
    </div>
  )
}

function StagePanel({
  signalDate,
  running,
  error,
  onRun,
  children,
}: {
  signalDate?: string | null
  running: boolean
  error?: Error | null
  onRun: () => Promise<unknown>
  children: React.ReactNode
}) {
  return (
    <Widget headerless className="pipeline-stage-panel">
      <PanelToolbar
        signalDate={signalDate}
        running={running}
        onRun={() => { void onRun().catch(() => undefined) }}
      />
      {error ? <div className="workbench-message error">{error.message}</div> : null}
      <div className="stage-panel-body">{children}</div>
    </Widget>
  )
}

function EmptyRunState() {
  return <div className="analytics-empty">运行当前策略后显示真实阶段结果</div>
}

function useInstrumentOptions() {
  return useQuery({
    queryKey: ["data", "market", "symbols", "demo"],
    queryFn: () => api.get<{ symbols: string[]; instruments?: MarketInstrument[] }>(
      "/data/market/symbols?profile=demo",
    ),
    staleTime: Infinity,
    select: (payload): SymbolOption[] => payload.instruments?.length
      ? payload.instruments
      : payload.symbols.map((symbol) => ({ symbol, name: null })),
  })
}

function useStageBars(symbol: string, end: string | null) {
  const start = useMemo(() => {
    if (!end) return ""
    const value = new Date(`${end}T00:00:00`)
    value.setFullYear(value.getFullYear() - 1)
    return value.toISOString().slice(0, 10)
  }, [end])
  return useQuery({
    queryKey: ["data", "market", "bars", "demo", symbol, start, end],
    queryFn: () => {
      const params = new URLSearchParams({
        profile: "demo",
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
  const instruments = useInstrumentOptions()
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
  const eligibleHistory = panel.points.map((point) => strings(pointOutput(point, "universe").symbols).length)
  const minimum = eligibleHistory.length ? Math.min(...eligibleHistory) : 0

  return (
    <StagePanel signalDate={panel.preview?.signal_date} running={panel.isRunning} error={panel.error} onRun={panel.run}>
      {!panel.preview ? <EmptyRunState /> : (
        <>
          <div className="stage-kpi-grid">
            <div><span>当前标的</span><strong>{symbols.length}</strong></div>
            <div><span>近12月最少</span><strong>{minimum || "—"}</strong></div>
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
  const instruments = useInstrumentOptions()
  const selected = new Set(strings(panel.output.selected))
  const scores = numbers(panel.output.scores)
  const names = useMemo(
    () => new Map((instruments.data ?? []).map((item) => [item.symbol, item.name ?? null])),
    [instruments.data],
  )
  const rows = Object.entries(scores).sort((left, right) => right[1] - left[1])

  return (
    <StagePanel signalDate={panel.preview?.signal_date} running={panel.isRunning} error={panel.error} onRun={panel.run}>
      {!panel.preview ? <EmptyRunState /> : (
        <>
          <div className="stage-kpi-grid">
            <div><span>入选</span><strong>{selected.size}</strong></div>
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

function alignMarkers(rows: MarketBar[], markers: CandlestickMarker[]): CandlestickMarker[] {
  const dates = rows.map((row) => row.date).sort()
  return markers.flatMap((marker) => {
    const date = [...dates].reverse().find((item) => item <= marker.time)
    return date ? [{ ...marker, time: date }] : []
  })
}

function SecurityChartWidget({ mode }: { mode: "universe" | "selection" | "timing" }) {
  const panel = useStagePanel(mode)
  const { selectedSymbol, setSelectedSymbol } = useWorkspace()
  const instruments = useInstrumentOptions()
  const universe = strings(stageOutput(panel.preview, "universe").symbols)
  const selection = strings(stageOutput(panel.preview, "selection").selected)
  const candidates = mode === "universe" ? universe : (selection.length ? selection : universe)
  const symbol = selectedSymbol && candidates.includes(selectedSymbol) ? selectedSymbol : candidates[0] ?? ""
  const end = panel.preview?.signal_date ?? null
  const bars = useStageBars(symbol, end)
  const options = useMemo(() => {
    const names = new Map((instruments.data ?? []).map((item) => [item.symbol, item.name]))
    return candidates.map((item) => ({ symbol: item, name: names.get(item) ?? null }))
  }, [candidates, instruments.data])
  const rawMarkers = useMemo<CandlestickMarker[]>(() => {
    if (mode !== "timing") {
      return end ? [{ time: end, position: "aboveBar", shape: "circle", color: "#2962ff", text: "当前信号" }] : []
    }
    let previous: number | null = null
    return panel.points.flatMap((point) => {
      const exposure = numeric(pointOutput(point, "timing").exposure)
      const changed = previous === null || Math.abs(exposure - previous) > 1e-8
      const rising = previous === null ? exposure > 0 : exposure > previous
      previous = exposure
      if (!changed) return []
      return [{
        time: point.entry_date,
        position: rising ? "belowBar" : "aboveBar",
        shape: rising ? "arrowUp" : "arrowDown",
        color: rising ? "#20a36a" : "#e05260",
        text: `${rising ? "加仓" : "减仓"} ${formatPercent(exposure, 0)}`,
      } satisfies CandlestickMarker]
    })
  }, [end, mode, panel.points])
  const rows = useMemo(() => bars.data?.rows ?? [], [bars.data?.rows])
  const markers = useMemo(() => alignMarkers(rows, rawMarkers), [rawMarkers, rows])
  const timing = stageOutput(panel.preview, "timing")

  return (
    <StagePanel signalDate={end} running={panel.isRunning} error={panel.error ?? bars.error} onRun={panel.run}>
      {!panel.preview ? <EmptyRunState /> : (
        <>
          <div className="stage-chart-toolbar">
            <SymbolCombobox symbols={options} value={symbol} onChange={setSelectedSymbol} ariaLabel="图表证券" />
            {mode === "timing" ? <span className="status-pill">当前仓位 {formatPercent(numeric(timing.exposure), 0)}</span> : null}
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
export const TimingChartWidget = () => <SecurityChartWidget mode="timing" />

export function TimingEventsWidget() {
  const panel = useStagePanel("timing")
  const rows = panel.points.map((point) => ({
    date: point.signal_date,
    exposure: numeric(pointOutput(point, "timing").exposure),
    signal: String(pointOutput(point, "timing").signal ?? "—"),
  }))
  return (
    <StagePanel signalDate={panel.preview?.signal_date} running={panel.isRunning} error={panel.error} onRun={panel.run}>
      {!panel.preview ? <EmptyRunState /> : (
        <>
          <div className="stage-history-chart">
            <ResponsiveContainer width="100%" height={190}>
              <AreaChart data={rows}><defs><linearGradient id="timingExposure" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#2962ff" stopOpacity={0.35}/><stop offset="95%" stopColor="#2962ff" stopOpacity={0.03}/></linearGradient></defs><CartesianGrid stroke="hsl(var(--border))" opacity={0.45}/><XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={30}/><YAxis domain={[0, 1]} tickFormatter={(value) => `${value * 100}%`} tick={{ fontSize: 10 }}/><Tooltip formatter={(value) => formatPercent(Number(value), 0)}/><Area type="stepAfter" dataKey="exposure" stroke="#2962ff" fill="url(#timingExposure)" isAnimationActive={false}/></AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="stage-table-wrap compact"><table className="stage-table"><thead><tr><th>信号日</th><th>仓位</th><th>信号</th></tr></thead><tbody>{[...rows].reverse().map((row) => <tr key={row.date}><td>{row.date}</td><td>{formatPercent(row.exposure, 0)}</td><td>{row.signal}</td></tr>)}</tbody></table></div>
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
    <StagePanel signalDate={panel.preview?.signal_date} running={panel.isRunning} error={panel.error} onRun={panel.run}>
      {!panel.preview ? <EmptyRunState /> : <><div className="stage-kpi-grid"><div><span>组合标的</span><strong>{Object.keys(weights).length}</strong></div><div><span>计划仓位</span><strong>{formatPercent(gross, 1)}</strong></div></div><HorizontalBarChart data={rows.map(([name, value]) => ({ name, value }))} height={Math.max(220, rows.length * 28)} /></>}
    </StagePanel>
  )
}

export function PortfolioHistoryWidget() {
  const panel = useStagePanel("portfolio")
  const rows = panel.points.map((point) => {
    const weights = numbers(pointOutput(point, "portfolio").weights)
    return { date: point.signal_date, names: Object.keys(weights).length, gross: Object.values(weights).reduce((sum, item) => sum + Math.abs(item), 0), max: Math.max(0, ...Object.values(weights)) }
  })
  return <StagePanel signalDate={panel.preview?.signal_date} running={panel.isRunning} error={panel.error} onRun={panel.run}>{!panel.preview ? <EmptyRunState /> : <div className="stage-table-wrap"><table className="stage-table"><thead><tr><th>信号日</th><th>标的数</th><th>总仓位</th><th>最大权重</th></tr></thead><tbody>{[...rows].reverse().map((row) => <tr key={row.date}><td>{row.date}</td><td>{row.names}</td><td>{formatPercent(row.gross, 1)}</td><td>{formatPercent(row.max, 1)}</td></tr>)}</tbody></table></div>}</StagePanel>
}

export function RiskLimitsWidget() {
  const panel = useStagePanel("risk")
  const before = numbers(stageOutput(panel.preview, "portfolio").weights)
  const after = numbers(panel.output.weights)
  const symbols = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .sort((left, right) => (after[right] ?? 0) - (after[left] ?? 0))
  return <StagePanel signalDate={panel.preview?.signal_date} running={panel.isRunning} error={panel.error} onRun={panel.run}>{!panel.preview ? <EmptyRunState /> : <><div className="stage-table-wrap"><table className="stage-table"><thead><tr><th>证券</th><th>约束前</th><th>约束后</th><th>变化</th></tr></thead><tbody>{symbols.map((symbol) => <tr key={symbol}><td><strong>{symbol}</strong></td><td>{formatPercent(before[symbol] ?? 0, 2)}</td><td>{formatPercent(after[symbol] ?? 0, 2)}</td><td className={(after[symbol] ?? 0) < (before[symbol] ?? 0) ? "negative" : ""}>{formatPercent((after[symbol] ?? 0) - (before[symbol] ?? 0), 2)}</td></tr>)}</tbody></table></div></>}</StagePanel>
}

export function RiskHistoryWidget() {
  const panel = useStagePanel("risk")
  const gross = numeric(panel.output.gross_exposure, Object.values(numbers(panel.output.weights)).reduce((sum, item) => sum + Math.abs(item), 0))
  const largest = Math.max(0, ...Object.values(numbers(panel.output.weights)))
  return <StagePanel signalDate={panel.preview?.signal_date} running={panel.isRunning} error={panel.error} onRun={panel.run}>{!panel.preview ? <EmptyRunState /> : <><div className="stage-kpi-grid"><div><span>风险后仓位</span><strong>{formatPercent(gross, 1)}</strong></div><div><span>最大单股</span><strong>{formatPercent(largest, 1)}</strong></div></div><RiskPieChart height={260} innerRadius={55} showLabels={false} data={[{ name: "股票仓位", value: gross, color: "#2962ff" }, { name: "现金", value: Math.max(0, 1 - gross), color: "#9aa4b2" }]} /></>}</StagePanel>
}

export function ExecutionSettingsWidget() {
  const panel = useStagePanel("execution")
  const settings = record(panel.output.execution)
  const labels: Record<string, string> = { rebalance_freq: "调仓频率", execution_price: "成交价格", cost_bps: "佣金 bps", slippage_bps: "滑点 bps", impact_bps: "冲击 bps", max_participation_rate: "最大成交占比", portfolio_value: "组合资金" }
  return <StagePanel signalDate={panel.preview?.signal_date} running={panel.isRunning} error={panel.error} onRun={panel.run}>{!panel.preview ? <EmptyRunState /> : <div className="stage-settings-grid">{Object.entries(settings).map(([key, value]) => <div key={key}><span>{labels[key] ?? key}</span><strong>{key.endsWith("_rate") ? formatPercent(numeric(value), 1) : String(value)}</strong></div>)}</div>}</StagePanel>
}

export function ExecutionHistoryWidget() {
  const panel = useStagePanel("execution")
  const totalCost = panel.points.reduce((sum, point) => sum + point.total_cost, 0)
  const averageTurnover = panel.points.length ? panel.points.reduce((sum, point) => sum + point.turnover, 0) / panel.points.length : 0
  return <StagePanel signalDate={panel.preview?.signal_date} running={panel.isRunning} error={panel.error} onRun={panel.run}>{!panel.preview ? <EmptyRunState /> : <><div className="stage-kpi-grid"><div><span>区间模拟成本</span><strong>{formatPercent(totalCost, 2)}</strong></div><div><span>平均换手</span><strong>{formatPercent(averageTurnover, 1)}</strong></div></div><div className="stage-table-wrap"><table className="stage-table"><thead><tr><th>信号日</th><th>成交日</th><th>换手</th><th>成本</th><th>现金</th></tr></thead><tbody>{[...panel.points].reverse().map((point) => <tr key={point.entry_date}><td>{point.signal_date}</td><td>{point.entry_date}</td><td>{formatPercent(point.turnover, 1)}</td><td>{formatPercent(point.total_cost, 3)}</td><td>{formatPercent(point.cash_weight, 1)}</td></tr>)}</tbody></table></div></>}</StagePanel>
}
