import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { CalendarDays, Check, CircleAlert, Clock3, RefreshCw } from "lucide-react"

import {
  CandlestickChart,
  CorrelationHeatmap,
  DistributionChart,
  HorizontalBarChart,
} from "@/components/charts"
import type { CandlestickMarker } from "@/components/charts/CandlestickChart"
import { SymbolCombobox, type SymbolOption } from "@/components/shared/SymbolCombobox"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { usePipelineStageRun } from "@/hooks/use-pipeline-stage-run"
import {
  api,
  type MarketBar,
  type MarketInstrument,
  type PipelineProjectDetail,
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

function records(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.map(record).filter((item) => Object.keys(item).length) : []
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

function useStageParameters(stage: PythonPipelineStage) {
  const { selectedStrategy, selectedStrategyRevision } = useWorkspace()
  const project = useQuery({
    queryKey: ["pipeline", "project-detail", selectedStrategy, selectedStrategyRevision],
    queryFn: () => api.get<PipelineProjectDetail>(`/pipeline/projects/${selectedStrategy}`),
    enabled: Boolean(selectedStrategy),
    staleTime: 30_000,
  })
  const parameters = record(
    project.data?.component_manifest.find((item) => item.stage === stage)?.parameters,
  )
  return stage === "selection"
    ? { ...record(parameters.selection), ...parameters }
    : parameters
}

function formatMoney(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    notation: Math.abs(value) >= 1_000_000 ? "compact" : "standard",
    maximumFractionDigits: 1,
  }).format(value)
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
  embedded = false,
}: {
  signalDate?: string | null
  profile: DataProfile
  running: boolean
  error?: Error | null
  children: React.ReactNode
  embedded?: boolean
}) {
  if (embedded) {
    return (
      <div className="pipeline-stage-panel embedded">
        {error ? <div className="workbench-message error">{error.message}</div> : null}
        <div className="stage-panel-body">{children}</div>
      </div>
    )
  }
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
      {stale ? "项目、数据环境或当前截面日期已变化，请在模型/组件配置中重新运行当前阶段" : "当前上下文没有有效结果，请先在模型/组件配置中运行当前阶段"}
    </div>
  )
}

type AuditStatus = "pass" | "warning" | "pending"

function AuditRow({
  label,
  value,
  detail,
  status,
}: {
  label: string
  value: string
  detail: string
  status: AuditStatus
}) {
  const Icon = status === "pass" ? Check : status === "warning" ? CircleAlert : Clock3
  return (
    <div className={`stage-audit-row ${status}`}>
      <Icon size={15} />
      <div><strong>{label}</strong><span>{detail}</span></div>
      <em>{value}</em>
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

export function SelectionFunnelWidget({ embedded = false }: { embedded?: boolean } = {}) {
  const panel = useStagePanel("selection")
  const snapshot = record(panel.preview?.selection)
  const diagnostics = record(panel.preview?.diagnostics)
  const universe = numeric(snapshot.universe_size ?? diagnostics.universe_size)
  const eligible = numeric(snapshot.eligible_count ?? diagnostics.eligible_count)
  const scored = numeric(snapshot.scored_count ?? diagnostics.score_count)
  const selected = numeric(snapshot.selected_count ?? diagnostics.selected_count)
  const exclusions = Object.entries(numbers(snapshot.exclusions ?? diagnostics.exclusions))
    .sort((left, right) => right[1] - left[1])
  const instrumentFilterApplied = diagnostics.instrument_filter_applied === true
  const snapshotFuture = diagnostics.instrument_snapshot_future === true
  const instrumentSnapshot = String(diagnostics.instrument_snapshot || "未提供")
  const steps = [
    { label: "研究范围", value: universe },
    { label: "数据可用", value: eligible },
    { label: "完成评分", value: scored },
    { label: "最终入选", value: selected },
  ]

  return (
    <StagePanel embedded={embedded} signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-funnel" aria-label="信号覆盖漏斗">
            {steps.map((step, index) => (
              <div className="stage-funnel-step" key={step.label}>
                <span>{step.label}</span>
                <strong>{step.value}</strong>
                {index ? <small>{steps[index - 1]?.value ? formatPercent(step.value / steps[index - 1].value, 0) : "—"}</small> : <small>基准</small>}
              </div>
            ))}
          </div>
          <div className="stage-section-label">可研究性检查</div>
          <div className="stage-audit-list">
            <AuditRow
              label="证券主数据时间点"
              value={snapshotFuture ? "未来快照" : instrumentFilterApplied ? "已过滤" : "未接入"}
              detail={instrumentFilterApplied ? `快照日期 ${instrumentSnapshot}` : "当前数据源未提供证券状态快照"}
              status={snapshotFuture ? "warning" : instrumentFilterApplied ? "pass" : "pending"}
            />
          </div>
          <div className="stage-section-label">排除原因</div>
          {exclusions.length ? (
            <div className="stage-reason-list">
              {exclusions.map(([reason, count]) => (
                <div key={reason}><span>{reason}</span><strong>{count}</strong></div>
              ))}
            </div>
          ) : <div className="analytics-empty compact">没有因数据完整性被排除的证券。</div>}
        </>
      )}
    </StagePanel>
  )
}

export function SelectionDistributionWidget({ embedded = false }: { embedded?: boolean } = {}) {
  const panel = useStagePanel("selection")
  const scores = numbers(panel.output.scores)
  const selectedSymbols = strings(panel.output.selected)
  const values = Object.values(scores).sort((left, right) => left - right)
  const selectedValues = selectedSymbols.map((symbol) => scores[symbol]).filter((value) => Number.isFinite(value))
  const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0
  const middle = Math.floor(values.length / 2)
  const median = values.length
    ? values.length % 2 ? values[middle] ?? 0 : ((values[middle - 1] ?? 0) + (values[middle] ?? 0)) / 2
    : 0
  const cutoff = selectedValues.length ? Math.min(...selectedValues) : 0

  return (
    <StagePanel embedded={embedded} signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : !values.length ? (
        <div className="analytics-empty">当前信号模型没有输出可绘制的 scores。</div>
      ) : (
        <>
          <div className="stage-kpi-grid three">
            <div><span>平均分</span><strong>{formatNumber(mean, 3)}</strong></div>
            <div><span>中位数</span><strong>{formatNumber(median, 3)}</strong></div>
            <div><span>入选门槛</span><strong>{formatNumber(cutoff, 3)}</strong></div>
          </div>
          <div className="stage-chart-card">
            <DistributionChart data={values} bins={Math.min(20, Math.max(5, Math.ceil(Math.sqrt(values.length))))} meanLine={mean} height={250} />
          </div>
          <div className="stage-note">分布基于当前截面的实际综合得分；入选门槛是本次入选证券中的最低分。</div>
        </>
      )}
    </StagePanel>
  )
}

export function SelectionFactorEvidenceWidget({ embedded = false }: { embedded?: boolean } = {}) {
  const panel = useStagePanel("selection")
  const parameters = useStageParameters("selection")
  const snapshot = record(panel.preview?.selection)
  const correlation = record(record(panel.preview?.diagnostics).factor_score_correlation)
  const labels = strings(correlation.labels)
  const rawMatrix = Array.isArray(correlation.matrix) ? correlation.matrix : []
  const matrix = rawMatrix.map((row) => Array.isArray(row)
    ? row.map((value) => value == null ? Number.NaN : Number(value))
    : [])
  const completeMatrix = labels.length > 1
    && matrix.length === labels.length
    && matrix.every((row) => row.length === labels.length && row.every(Number.isFinite))
  const factors = labels.length ? labels : strings(snapshot.factor_names)
  const observations = numeric(correlation.observations)
  const factorWeights = numbers(parameters.factor_weights)
  const normalizationLabels: Record<string, string> = { percentile_rank: "百分位排名", zscore: "Z-score" }
  const frequencyLabels: Record<string, string> = { daily: "每日", weekly: "每周", monthly: "每月" }

  return (
    <StagePanel embedded={embedded} signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid">
            <div><span>参与因子</span><strong>{factors.length}</strong></div>
            <div><span>有效截面</span><strong>{observations}</strong></div>
            <div><span>信号频率</span><strong>{frequencyLabels[String(parameters.signal_frequency)] ?? String(parameters.signal_frequency || "—")}</strong></div>
            <div><span>标准化</span><strong>{normalizationLabels[String(parameters.normalization)] ?? String(parameters.normalization || "—")}</strong></div>
          </div>
          <div className="stage-factor-list">
            {factors.map((factor) => <span key={factor}>{factor}{factor in factorWeights ? ` · ${formatNumber(factorWeights[factor] ?? 0, 2)}` : ""}</span>)}
          </div>
          {completeMatrix ? (
            <div className="stage-chart-card correlation">
              <CorrelationHeatmap labels={labels} matrix={matrix} />
            </div>
          ) : (
            <div className="analytics-empty compact">
              {factors.length < 2 ? "至少需要两个因子才能检查截面相关性。" : "当前截面的因子相关矩阵不完整。"}
            </div>
          )}
          <div className="stage-note">这里检查因子冗余；因子收益和稳定性仍以完整回测证据为准。</div>
        </>
      )}
    </StagePanel>
  )
}

export function SelectionRankingWidget({ embedded = false }: { embedded?: boolean } = {}) {
  const panel = useStagePanel("selection")
  const { selectedSymbol, setSelectedSymbol } = useWorkspace()
  const instruments = useInstrumentOptions(panel.profile)
  const selected = new Set(strings(panel.output.selected))
  const scores = numbers(panel.output.scores)
  const targetWeights = numbers(stageOutput(panel.preview, "portfolio").weights)
  const gross = Object.values(targetWeights).reduce((sum, weight) => sum + Math.abs(weight), 0)
  const names = useMemo(
    () => new Map((instruments.data ?? []).map((item) => [item.symbol, item.name ?? null])),
    [instruments.data],
  )
  const rows = Object.entries(scores).sort((left, right) => right[1] - left[1])

  return (
    <StagePanel embedded={embedded} signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid">
            <div><span>信号集合</span><strong>{selected.size}</strong></div>
            <div><span>参与排名</span><strong>{rows.length}</strong></div>
            <div><span>目标总仓位</span><strong>{targetWeights && Object.keys(targetWeights).length ? formatPercent(gross, 1) : "—"}</strong></div>
            <div><span>目标现金</span><strong>{targetWeights && Object.keys(targetWeights).length ? formatPercent(Math.max(0, 1 - gross), 1) : "—"}</strong></div>
          </div>
          <div className="stage-table-wrap">
            <table className="stage-table">
              <thead><tr><th>#</th><th>证券</th><th>得分</th><th>目标权重</th><th>结果</th></tr></thead>
              <tbody>{rows.map(([symbol, score], index) => (
                <tr key={symbol} className={selectedSymbol === symbol ? "active" : ""} onClick={() => setSelectedSymbol(symbol)}>
                  <td>{index + 1}</td><td><strong>{symbol}</strong><small>{names.get(symbol) || ""}</small></td>
                  <td>{formatNumber(score, 3)}</td>
                  <td>{symbol in targetWeights ? formatPercent(targetWeights[symbol] ?? 0, 2) : "—"}</td>
                  <td>{symbol in targetWeights ? <em>目标持仓</em> : selected.has(symbol) ? "候选" : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
    </StagePanel>
  )
}

function SecurityChartWidget({ embedded = false }: { embedded?: boolean }) {
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
    <StagePanel embedded={embedded} signalDate={end} profile={panel.profile} running={panel.isRunning} error={panel.error ?? bars.error}>
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

export const SelectionChartWidget = ({ embedded = false }: { embedded?: boolean } = {}) => <SecurityChartWidget embedded={embedded} />

export function PortfolioInputWidget() {
  const panel = useStagePanel("portfolio")
  const snapshot = record(panel.preview?.selection)
  const selectedSymbols = new Set(strings(stageOutput(panel.preview, "selection").selected))
  const snapshotRows = records(snapshot.rows)
    .filter((row) => row.selected === true || selectedSymbols.has(String(row.symbol)))
    .sort((left, right) => numeric(left.rank, Number.MAX_SAFE_INTEGER) - numeric(right.rank, Number.MAX_SAFE_INTEGER))
  const fallbackScores = numbers(stageOutput(panel.preview, "selection").scores)
  const rows = snapshotRows.length ? snapshotRows : [...selectedSymbols].map((symbol, index) => ({
    rank: index + 1,
    symbol,
    composite_score: fallbackScores[symbol],
    factor_coverage: undefined,
  }))
  const averageCoverage = snapshotRows.length
    ? snapshotRows.reduce((sum, row) => sum + numeric(row.factor_coverage), 0) / snapshotRows.length
    : null

  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid">
            <div><span>上游入选</span><strong>{rows.length} 只</strong></div>
            <div><span>平均因子覆盖</span><strong>{averageCoverage == null ? "—" : formatPercent(averageCoverage, 0)}</strong></div>
          </div>
          <div className="stage-table-wrap">
            <table className="stage-table">
              <thead><tr><th>#</th><th>证券</th><th>综合得分</th><th>因子覆盖</th></tr></thead>
              <tbody>{rows.map((row) => (
                <tr key={String(row.symbol)}>
                  <td>{numeric(row.rank)}</td>
                  <td><strong>{String(row.symbol)}</strong></td>
                  <td>{Number.isFinite(Number(row.composite_score)) ? formatNumber(numeric(row.composite_score), 3) : "—"}</td>
                  <td>{Number.isFinite(Number(row.factor_coverage)) ? formatPercent(numeric(row.factor_coverage), 0) : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <div className="stage-note">这是组合构建实际接收到的信号集合，不是另一次独立筛选。</div>
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
  const gross = Object.values(weights).reduce((sum, item) => sum + Math.abs(item), 0)
  const largest = Math.max(0, ...Object.values(weights))
  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <div className="stage-settings-grid">
          <div><span>输入信号集合</span><strong>{selection.length} 只</strong></div>
          <div><span>组合现金</span><strong>{formatPercent(Math.max(0, 1 - gross), 1)}</strong></div>
          <div><span>组合总仓位</span><strong>{formatPercent(gross, 1)}</strong></div>
          <div><span>最大单股</span><strong>{formatPercent(largest, 1)}</strong></div>
        </div>
      )}
    </StagePanel>
  )
}

export function PortfolioConstraintsWidget() {
  const panel = useStagePanel("portfolio")
  const parameters = useStageParameters("portfolio")
  const rawWeights = record(panel.output.weights)
  const weights = numbers(rawWeights)
  const selected = new Set(strings(stageOutput(panel.preview, "selection").selected))
  const values = Object.values(weights)
  const gross = values.reduce((sum, value) => sum + Math.abs(value), 0)
  const largest = Math.max(0, ...values)
  const maxWeight = numeric(parameters.max_weight, 1)
  const grossLimit = numeric(parameters.max_gross_exposure, 1)
  const invalidWeights = Object.values(rawWeights).filter((value) => !Number.isFinite(Number(value)) || Number(value) < 0)
  const outsideSelection = Object.keys(weights).filter((symbol) => !selected.has(symbol))
  const normalized = gross > 0 ? values.map((value) => value / gross) : []
  const concentration = normalized.reduce((sum, value) => sum + value * value, 0)
  const effectiveHoldings = concentration > 0 ? 1 / concentration : 0
  const maxWeightPass = largest <= maxWeight + 1e-9
  const grossPass = gross <= grossLimit + 1e-9

  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid three">
            <div><span>现金余量</span><strong>{formatPercent(Math.max(0, 1 - gross), 1)}</strong></div>
            <div><span>HHI 集中度</span><strong>{formatNumber(concentration, 3)}</strong></div>
            <div><span>有效持仓数</span><strong>{formatNumber(effectiveHoldings, 1)}</strong></div>
          </div>
          <div className="stage-limit-meter">
            <div><span>总敞口使用</span><strong>{formatPercent(gross, 1)} / {formatPercent(grossLimit, 1)}</strong></div>
            <i><b style={{ width: `${Math.min(100, grossLimit > 0 ? gross / grossLimit * 100 : 0)}%` }} /></i>
          </div>
          <div className="stage-audit-list">
            <AuditRow label="权重合同" value={invalidWeights.length ? `${invalidWeights.length} 项异常` : "通过"} detail="所有权重必须有限且非负" status={invalidWeights.length ? "warning" : "pass"} />
            <AuditRow label="信号集合边界" value={outsideSelection.length ? `${outsideSelection.length} 项越界` : "通过"} detail="组合不得引入信号模型未输出的证券" status={outsideSelection.length ? "warning" : "pass"} />
            <AuditRow label="单股上限" value={`${formatPercent(largest, 1)} / ${formatPercent(maxWeight, 1)}`} detail="实际最大权重 / 组件参数上限" status={maxWeightPass ? "pass" : "warning"} />
            <AuditRow label="总敞口上限" value={`${formatPercent(gross, 1)} / ${formatPercent(grossLimit, 1)}`} detail="实际总仓位 / 组件参数上限" status={grossPass ? "pass" : "warning"} />
          </div>
        </>
      )}
    </StagePanel>
  )
}

export function ExecutionSettingsWidget() {
  const panel = useStagePanel("execution")
  const signalParameters = useStageParameters("selection")
  const settings = record(panel.output.execution)
  const frequency = String(signalParameters.signal_frequency || "未设置")
  const price = String(settings.execution_price || "未设置")
  const frequencyLabels: Record<string, string> = { daily: "每日", weekly: "每周", monthly: "每月" }
  const priceLabels: Record<string, string> = { next_open: "下一交易日开盘", next_close: "下一交易日收盘", monthly_factor_close: "月度因子收盘" }
  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-execution-flow">
            <div><span>信号形成</span><strong>{panel.preview?.signal_date}</strong></div>
            <i>→</i>
            <div><span>成交时点</span><strong>{priceLabels[price] ?? price}</strong></div>
            <i>→</i>
            <div><span>形成持仓</span><strong>成交后生效</strong></div>
          </div>
          <div className="stage-settings-grid">
            <div><span>上游信号频率</span><strong>{frequencyLabels[frequency] ?? frequency}</strong></div>
            <div><span>最大成交占比</span><strong>{formatPercent(numeric(settings.max_participation_rate), 1)}</strong></div>
            <div><span>组合资金</span><strong>¥ {formatMoney(numeric(settings.portfolio_value))}</strong></div>
            <div><span>预览边界</span><strong>目标与假设</strong></div>
          </div>
          <div className="stage-note">频率只读继承自信号模型；执行阶段负责成交时点、流动性和成本。确切日期由完整回测按交易日历逐期生成。</div>
        </>
      )}
    </StagePanel>
  )
}

export function ExecutionCostsWidget() {
  const panel = useStagePanel("execution")
  const settings = record(panel.output.execution)
  const commission = numeric(settings.cost_bps)
  const slippage = numeric(settings.slippage_bps)
  const impact = numeric(settings.impact_bps)
  const total = commission + slippage + impact
  const portfolioValue = numeric(settings.portfolio_value)
  const fullTurnoverEstimate = portfolioValue * total / 10_000
  const parts = [
    { label: "固定费率", value: commission, className: "commission" },
    { label: "滑点", value: slippage, className: "slippage" },
    { label: "冲击", value: impact, className: "impact" },
  ]

  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <>
          <div className="stage-kpi-grid three">
            <div><span>固定成本</span><strong>{formatNumber(commission + slippage, 1)} bps</strong></div>
            <div><span>冲击参数</span><strong>{formatNumber(impact, 1)} bps</strong></div>
            <div><span>参数合计</span><strong>{formatNumber(total, 1)} bps</strong></div>
          </div>
          <div className="stage-cost-stack" aria-label="交易成本参数构成">
            {total > 0 ? parts.map((part) => (
              <div
                className={part.className}
                key={part.label}
                style={{ width: `${total > 0 ? part.value / total * 100 : 33.333}%` }}
                title={`${part.label} ${formatNumber(part.value, 1)} bps`}
              />
            )) : <div className="empty" />}
          </div>
          <div className="stage-cost-legend">
            {parts.map((part) => <span className={part.className} key={part.label}>{part.label} <strong>{formatNumber(part.value, 1)}</strong></span>)}
          </div>
          <div className="stage-estimate-card">
            <span>100% 单边换手、冲击达到参数值时</span>
            <strong>约 ¥ {formatMoney(fullTurnoverEstimate)}</strong>
          </div>
          <div className="stage-note">这是参数情景，不是本次真实成本；真实值取决于上一期持仓、成交量和参与率。</div>
        </>
      )}
    </StagePanel>
  )
}

export function ExecutionGuardrailsWidget() {
  const panel = useStagePanel("execution")
  const settings = record(panel.output.execution)
  const diagnostics = record(panel.preview?.diagnostics)
  const snapshotApplied = diagnostics.instrument_filter_applied === true
  const snapshotFuture = diagnostics.instrument_snapshot_future === true
  const participation = numeric(settings.max_participation_rate)
  return (
    <StagePanel signalDate={panel.preview?.signal_date} profile={panel.profile} running={panel.isRunning} error={panel.error}>
      {!panel.preview ? <EmptyRunState stale={panel.isStale} /> : (
        <div className="stage-audit-list">
          <AuditRow
            label="目标权重合同"
            value="通过"
            detail="组合权重已通过非负、单股与总敞口硬门禁"
            status="pass"
          />
          <AuditRow
            label="证券状态时间点"
            value={snapshotFuture ? "需检查" : snapshotApplied ? "通过" : "待数据"}
            detail={snapshotApplied ? `主数据快照 ${String(diagnostics.instrument_snapshot || "已应用")}` : "当前数据源没有证券状态快照"}
            status={snapshotFuture ? "warning" : snapshotApplied ? "pass" : "pending"}
          />
          <AuditRow
            label="成交量参与率"
            value={participation ? `≤ ${formatPercent(participation, 1)}` : "未设置"}
            detail="逐证券可成交数量需在回测中结合当日成交额计算"
            status={participation ? "pending" : "warning"}
          />
          <AuditRow
            label="换手与实际成本"
            value="回测评估"
            detail="依赖上一期持仓，阶段预览不会虚构换手率"
            status="pending"
          />
        </div>
      )}
    </StagePanel>
  )
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
