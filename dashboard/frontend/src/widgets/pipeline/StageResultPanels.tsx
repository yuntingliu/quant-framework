import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { CalendarDays, RefreshCw } from "lucide-react"

import { CandlestickChart, HorizontalBarChart } from "@/components/charts"
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

function SecurityChartWidget() {
  const panel = useStagePanel("selection")
  const { selectedSymbol, setSelectedSymbol } = useWorkspace()
  const instruments = useInstrumentOptions(panel.profile)
  const selection = strings(stageOutput(panel.preview, "selection").selected)
  const candidates = selection
  const symbol = selectedSymbol && candidates.includes(selectedSymbol) ? selectedSymbol : candidates[0] ?? ""
  const end = panel.preview?.signal_date ?? null
  const bars = useStageBars(panel.profile, symbol, end)
  const options = useMemo(() => {
    const names = new Map((instruments.data ?? []).map((item) => [item.symbol, item.name]))
    return candidates.map((item) => ({ symbol: item, name: names.get(item) ?? null }))
  }, [candidates, instruments.data])
  const markers = useMemo<CandlestickMarker[]>(
    () => end ? [{ time: end, position: "aboveBar", shape: "circle", color: "#2962ff", text: "入选" }] : [],
    [end],
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

export const SelectionChartWidget = () => <SecurityChartWidget />

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
  const gross = Object.values(weights).reduce((sum, item) => sum + Math.abs(item), 0)
  const largest = Math.max(0, ...Object.values(weights))
  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <div className="stage-settings-grid">
          <div><span>输入选股池</span><strong>{selection.length} 只</strong></div>
          <div><span>组合现金</span><strong>{formatPercent(Math.max(0, 1 - gross), 1)}</strong></div>
          <div><span>组合总仓位</span><strong>{formatPercent(gross, 1)}</strong></div>
          <div><span>最大单股</span><strong>{formatPercent(largest, 1)}</strong></div>
        </div>
      )}
    </StagePanel>
  )
}

export function ExecutionSettingsWidget() {
  const panel = useStagePanel("execution")
  const settings = record(panel.output.execution)
  const labels: Record<string, string> = { rebalance_freq: "调仓频率", execution_price: "成交价格", cost_bps: "佣金 bps", slippage_bps: "滑点 bps", impact_bps: "冲击 bps", max_participation_rate: "最大成交占比", portfolio_value: "组合资金" }
  return <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>{!panel.preview ? <EmptyRunState /> : <div className="stage-settings-grid">{Object.entries(settings).map(([key, value]) => <div key={key}><span>{labels[key] ?? key}</span><strong>{key.endsWith("_rate") ? formatPercent(numeric(value), 1) : String(value)}</strong></div>)}</div>}</StagePanel>
}

export function ExecutionTargetsWidget() {
  const panel = useStagePanel("execution")
  const weights = numbers(stageOutput(panel.preview, "portfolio").weights)
  const rows = Object.entries(weights).sort((left, right) => right[1] - left[1])
  const gross = Object.values(weights).reduce((sum, item) => sum + Math.abs(item), 0)
  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid"><div><span>目标标的</span><strong>{rows.length}</strong></div><div><span>目标总仓位</span><strong>{formatPercent(gross, 1)}</strong></div></div>
          <div className="stage-table-wrap"><table className="stage-table"><thead><tr><th>证券</th><th>最终目标权重</th></tr></thead><tbody>{rows.map(([symbol, weight]) => <tr key={symbol}><td><strong>{symbol}</strong></td><td>{formatPercent(weight, 2)}</td></tr>)}</tbody></table></div>
          <div className="analytics-empty compact">实际换手、成交限制和成本需要完整回测结合上一期持仓计算。</div>
        </>
      )}
    </StagePanel>
  )
}
