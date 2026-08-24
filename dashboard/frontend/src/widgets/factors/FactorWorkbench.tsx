import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  ChartCandlestick,
  Database,
  FlaskConical,
  FolderKanban,
  Loader2,
  Plus,
  Play,
  Save,
  TableProperties,
} from "lucide-react"

import { CandlestickChart, RollingLineChart } from "@/components/charts"
import { SymbolCombobox } from "@/components/shared/SymbolCombobox"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useFactorLab } from "@/contexts/FactorLabContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { api, type MarketBar, type MarketInstrument } from "@/lib/api"
import { useDataProfile } from "@/lib/data-profile"
import { formatNumber } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"

import {
  FactorEditorWidget,
  FactorEvidenceWidget,
  FactorLibraryWidget,
  FactorSnapshotWidget,
  FactorValidationSettings,
} from "./FactorLabPanels"

type ResearchDataset = "market_bars" | "fundamentals"
type MarketRange = "3m" | "6m" | "1y"
const NO_INDICATORS: [] = []

interface FundamentalPayload {
  fields: string[]
  asof_date: string
  rows: Array<Record<string, string | number | null>>
}

function dateBefore(endDate: string, range: MarketRange): string {
  const start = new Date(`${endDate}T00:00:00`)
  if (range === "3m") start.setMonth(start.getMonth() - 3)
  if (range === "6m") start.setMonth(start.getMonth() - 6)
  if (range === "1y") start.setFullYear(start.getFullYear() - 1)
  return start.toISOString().slice(0, 10)
}

function displayValue(value: unknown): string {
  if (typeof value === "string") return value || "—"
  if (typeof value !== "number" || !Number.isFinite(value)) return "—"
  if (Math.abs(value) >= 1_000_000) {
    return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 2 }).format(value)
  }
  return formatNumber(value, Math.abs(value) < 1 ? 4 : 2)
}

function FactorResearchDataBrowser() {
  const lab = useFactorLab()
  const [profile] = useDataProfile()
  const { activeMode, selectedDate, selectedSymbol, setSelectedSymbol } = useWorkspace()
  const [dataset, setDataset] = useState<ResearchDataset>("market_bars")
  const [range, setRange] = useState<MarketRange>("6m")
  const [fundamentalField, setFundamentalField] = useState("roe")
  const endDate = selectedDate ?? new Date().toISOString().slice(0, 10)
  const sources = lab.library?.data_sources ?? []
  const activeSource = sources.find((source) => source.id === dataset)
  const fieldCount = sources.reduce((count, source) => count + source.fields.length, 0)

  const symbolQuery = useQuery({
    queryKey: ["factor-research", "data", "symbols", profile],
    queryFn: () => api.get<{ instruments: MarketInstrument[] }>(`/data/market/symbols?profile=${profile}`),
    enabled: activeMode === "factor",
    staleTime: 60_000,
  })
  const instruments = symbolQuery.data?.instruments ?? []
  const symbol = selectedSymbol && instruments.some((item) => item.symbol === selectedSymbol)
    ? selectedSymbol
    : instruments[0]?.symbol ?? ""

  useEffect(() => {
    if (symbol && symbol !== selectedSymbol) setSelectedSymbol(symbol)
  }, [selectedSymbol, setSelectedSymbol, symbol])

  const barsQuery = useQuery({
    queryKey: ["factor-research", "data", "bars", profile, symbol, endDate, range],
    queryFn: () => {
      const params = new URLSearchParams({
        profile,
        symbol,
        start: dateBefore(endDate, range),
        end: endDate,
      })
      return api.get<{ rows: MarketBar[] }>(`/data/market/bars?${params.toString()}`)
    },
    enabled: activeMode === "factor" && Boolean(symbol),
    staleTime: 30_000,
  })

  const fundamentalsQuery = useQuery({
    queryKey: ["factor-research", "data", "fundamentals", profile, symbol, endDate],
    queryFn: () => {
      const params = new URLSearchParams({ profile, asof_date: endDate, limit: "40" })
      params.append("symbols", symbol)
      return api.get<FundamentalPayload>(`/data/fundamentals?${params.toString()}`)
    },
    enabled: activeMode === "factor" && Boolean(symbol),
    staleTime: 30_000,
  })

  const bars = barsQuery.data?.rows ?? []
  const latestBar = bars.at(-1)
  const fundamentalRows = useMemo(
    () => [...(fundamentalsQuery.data?.rows ?? [])].sort((left, right) => String(left.quarter ?? "").localeCompare(String(right.quarter ?? ""))),
    [fundamentalsQuery.data?.rows],
  )
  const latestFundamental = fundamentalRows.at(-1)
  const fundamentalSeries = fundamentalRows.flatMap((row) => {
    const value = row[fundamentalField]
    return typeof value === "number" && Number.isFinite(value)
      ? [{ date: String(row.quarter ?? row.available_date ?? ""), value }]
      : []
  })
  const loading = dataset === "market_bars" ? barsQuery.isLoading : fundamentalsQuery.isLoading
  const error = dataset === "market_bars" ? barsQuery.error : fundamentalsQuery.error

  function insertField(sourceId: ResearchDataset, name: string) {
    setDataset(sourceId)
    if (sourceId === "fundamentals") setFundamentalField(name)
    lab.requestExpressionInsert(name)
  }

  return (
    <section className="factor-source-browser" aria-label="因子研究数据">
      <div className="factor-source-browser-toolbar">
        <div className="factor-source-heading">
          <span><Database size={15} /></span>
          <div><strong>研究数据</strong><small>来自公共数据 API，点击字段直接加入表达式</small></div>
        </div>
        <div className="factor-source-tabs" role="tablist" aria-label="研究数据源">
          {sources.map((source) => (
            <button type="button" role="tab" aria-selected={dataset === source.id} className={dataset === source.id ? "active" : ""} key={source.id} onClick={() => setDataset(source.id)}>
              {source.id === "market_bars" ? <ChartCandlestick size={12} /> : <TableProperties size={12} />}{source.name}
            </button>
          ))}
        </div>
        <SymbolCombobox symbols={instruments} value={symbol} onChange={setSelectedSymbol} ariaLabel="研究证券" />
        {dataset === "market_bars" ? (
          <select aria-label="K 线区间" value={range} onChange={(event) => setRange(event.target.value as MarketRange)}>
            <option value="3m">3 个月</option><option value="6m">6 个月</option><option value="1y">1 年</option>
          </select>
        ) : <Badge variant="outline">截至 {fundamentalsQuery.data?.asof_date ?? endDate}</Badge>}
      </div>

      <div className="factor-source-browser-grid">
        <div className="factor-source-visual">
          {loading ? (
            <div className="factor-source-empty"><Loader2 className="spin" size={15} />正在读取 {activeSource?.endpoint ?? "数据 API"}…</div>
          ) : error ? (
            <div className="factor-source-empty error">{error instanceof Error ? error.message : String(error)}</div>
          ) : dataset === "market_bars" ? (
              bars.length ? <CandlestickChart rows={bars} selectedIndicators={NO_INDICATORS} height={216} /> : <div className="factor-source-empty">当前证券没有可用 K 线。</div>
          ) : fundamentalSeries.length ? (
            <RollingLineChart data={fundamentalSeries} series={[{ key: "value", name: fundamentalField, color: "#2962ff" }]} height={216} />
          ) : <div className="factor-source-empty">当前字段没有可视化数据。</div>}
        </div>

        <aside className="factor-source-fields">
          <div className="factor-source-fields-heading">
            <div><strong>当前数据全部字段</strong><small>{profile === "runtime" ? "Local RQ" : "Demo"} Schema 自动生成；可计算字段点击后加入</small></div>
            <span>{fieldCount} 个字段</span>
          </div>
          <div className="factor-source-field-groups">
            {sources.map((source) => (
              <section className="factor-source-field-group" key={source.id}>
                <header><strong>{source.name}</strong><code>{source.endpoint}</code><span>{source.fields.length}</span></header>
                <div className="factor-source-field-grid">
                  {source.fields.map((field) => {
                    const value = source.id === "market_bars"
                      ? latestBar?.[field.name as keyof MarketBar]
                      : latestFundamental?.[field.name]
                    const content = (
                      <>
                        <span><code>{field.name}</code><small>{field.label}</small></span>
                        <strong>{displayValue(value)}</strong>
                        {field.expression_compatible ? <Plus size={11} /> : <em>{field.data_type === "number" ? "只读" : "索引"}</em>}
                      </>
                    )
                    return field.expression_compatible ? (
                      <button className="factor-source-field" type="button" key={`${source.id}-${field.name}`} title={`把 ${field.name} 插入表达式`} onClick={() => insertField(source.id, field.name)}>{content}</button>
                    ) : (
                      <div className="factor-source-field index" key={`${source.id}-${field.name}`} title={`${field.name}：当前字段只读`}>{content}</div>
                    )
                  })}
                </div>
              </section>
            ))}
          </div>
        </aside>
      </div>
    </section>
  )
}

export function FactorWorkbenchWidget() {
  const lab = useFactorLab()
  const { setActiveMode } = useWorkspace()
  const [profile] = useDataProfile()
  const validated = Boolean(lab.result && !lab.resultStale)
  const canSave = Boolean(lab.project?.editable && validated)
  const runDisabled = !lab.draft.name.trim() || (lab.draft.source === "expression" && !lab.draft.expression.trim())

  return (
    <Widget headerless>
      <div className="factor-workbench-shell">
        <header className="factor-workbench-header">
          <div className="factor-workbench-title">
            <span className="factor-workbench-mark"><FlaskConical size={18} /></span>
            <div>
              <div><h1>因子研究</h1><Badge variant="outline">{profile === "demo" ? "示例数据" : "本地数据"}</Badge></div>
              <p>先观察可获得的数据并编写表达式，完成定义后再进入最终验证</p>
            </div>
          </div>
          <div className="factor-workbench-context">
            <div><span>当前研究项目</span><strong>{lab.project?.name ?? "尚未选择"}</strong></div>
            <Button variant="outline" size="sm" onClick={() => setActiveMode("project")}><FolderKanban />{lab.project ? "项目设置" : "选择项目"}</Button>
          </div>
        </header>

        <FactorResearchDataBrowser />

        <Tabs className="factor-workbench-tabs" value={lab.workspaceView} onValueChange={(value) => lab.setWorkspaceView(value as "build" | "results")}>
          <div className="factor-workbench-toolbar">
            <TabsList>
              <TabsTrigger value="build">因子编辑</TabsTrigger>
              <TabsTrigger value="results">最终验证{lab.resultStale ? <span className="factor-tab-warning">定义已变化</span> : null}</TabsTrigger>
            </TabsList>
            {lab.workspaceView === "results" ? (
              <div className="factor-workbench-actions">
                <span className={validated ? "factor-validation-ready" : "factor-build-hint"}>{validated ? `已验证 · ${lab.result?.periods ?? 0} 个截面` : "尚未运行最终验证"}</span>
                <Button size="sm" onClick={() => void lab.saveToProject()} disabled={!canSave} isLoading={lab.saving} title={canSave ? "把当前已验证定义保存到项目" : "需要当前定义验证通过且项目可编辑"}><Save />{lab.editingOriginalName ? "更新项目因子" : "加入项目"}</Button>
              </div>
            ) : <span className="factor-build-hint">构建阶段不运行：先把数据字段和表达式定义清楚</span>}
          </div>

          {lab.error ? <div className="workbench-message error factor-workbench-error">{lab.error}</div> : null}

          <TabsContent className="factor-workbench-content" value="build">
            <div className="factor-build-grid">
              <FactorLibraryWidget embedded />
              <FactorEditorWidget embedded />
            </div>
          </TabsContent>
          <TabsContent className="factor-workbench-content" value="results">
            <div className="factor-final-validation">
              <div className="factor-final-validation-intro">
                <div><FlaskConical size={16} /><span><strong>定义完成后再验证</strong><small>最后检查 IC、分组收益、衰减、覆盖率和最近截面；验证不会修改项目。</small></span></div>
                <FactorValidationSettings />
                <Button size="sm" onClick={() => void lab.evaluate()} disabled={runDisabled} isLoading={lab.running}><Play />{lab.result ? "重新验证" : "运行最终验证"}</Button>
              </div>
              <div className="factor-results-grid">
                <FactorEvidenceWidget embedded />
                <FactorSnapshotWidget embedded />
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </Widget>
  )
}
