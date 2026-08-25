import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  FlaskConical,
  Plus,
  Play,
  Save,
} from "lucide-react"

import { MarketResearchTerminal, type MarketRange, useMarketWatchlist } from "@/components/market"
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

interface FundamentalPayload {
  fields: string[]
  asof_date: string
  rows: Array<Record<string, string | number | null>>
}

function dateBefore(endDate: string, range: MarketRange): string | null {
  if (range === "all") return null
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
  const { activeMode, selectedSymbol, setSelectedSymbol } = useWorkspace()
  const [range, setRange] = useState<MarketRange>("6m")
  const [reloadRevision, setReloadRevision] = useState(0)
  const { watchlist, toggleWatchlist } = useMarketWatchlist()
  const endDate = lab.draft.endDate
  const sources = lab.library?.data_sources ?? []
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
    queryKey: ["factor-research", "data", "bars", profile, symbol, endDate, range, reloadRevision],
    queryFn: () => {
      const params = new URLSearchParams({
        profile,
        symbol,
        end: endDate,
      })
      const start = dateBefore(endDate, range)
      if (start) params.set("start", start)
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

  function insertField(name: string) {
    lab.requestExpressionInsert(name)
  }

  return (
    <section className="factor-source-browser" aria-label="因子研究数据">
      <MarketResearchTerminal
        instruments={instruments}
        rows={bars}
        symbol={symbol}
        onSymbolChange={setSelectedSymbol}
        range={range}
        onRangeChange={setRange}
        loading={symbolQuery.isLoading || barsQuery.isLoading}
        error={barsQuery.error instanceof Error ? barsQuery.error.message : barsQuery.error ? String(barsQuery.error) : ""}
        onReload={() => setReloadRevision((value) => value + 1)}
        watchlist={watchlist}
        onToggleWatchlist={toggleWatchlist}
        dataLabel={`日线 · 截至 ${endDate}`}
        emptyLabel="当前证券没有可用 K 线。"
        density="compact"
        contextPanelLabel="表达式数据字段"
        contextPanel={(
          <div className="factor-source-fields">
          <div className="factor-source-fields-heading">
            <div><strong>当前数据全部字段</strong><small>{profile === "runtime" ? "Local RQ" : "Demo"} Schema 自动生成；可计算字段点击后加入</small></div>
            <span>{fieldCount} 个字段</span>
          </div>
          {fundamentalsQuery.isLoading ? <div className="factor-source-fields-status">正在读取财务截面…</div> : null}
          {fundamentalsQuery.error ? <div className="factor-source-fields-status error">{fundamentalsQuery.error instanceof Error ? fundamentalsQuery.error.message : String(fundamentalsQuery.error)}</div> : null}
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
                      <button className="factor-source-field" type="button" key={`${source.id}-${field.name}`} title={`把 ${field.name} 插入表达式`} onClick={() => insertField(field.name)}>{content}</button>
                    ) : (
                      <div className="factor-source-field index" key={`${source.id}-${field.name}`} title={`${field.name}：当前字段只读`}>{content}</div>
                    )
                  })}
                </div>
              </section>
            ))}
          </div>
          </div>
        )}
      />
    </section>
  )
}

export function FactorWorkbenchWidget() {
  const lab = useFactorLab()
  const validated = Boolean(lab.result && !lab.resultStale)
  const savesToLibrary = lab.draft.source === "expression" && !lab.editingOriginalName
  const draftComplete = Boolean(lab.draft.name.trim() && (lab.draft.source !== "expression" || lab.draft.expression.trim()))
  const canSave = Boolean(draftComplete && (savesToLibrary || lab.project?.editable))
  const runDisabled = !lab.draft.name.trim() || (lab.draft.source === "expression" && !lab.draft.expression.trim())

  return (
    <Widget headerless>
      <div className="factor-workbench-shell">
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
                <Button size="sm" onClick={() => void (savesToLibrary ? lab.saveCustomFactor() : lab.saveToProject())} disabled={!canSave} isLoading={lab.saving} title={canSave ? savesToLibrary ? "保存到可用因子库" : "保存到当前项目" : "请填写完整因子定义；项目因子还需要选择可编辑项目"}><Save />{lab.editingOriginalName ? "保存项目修改" : savesToLibrary ? "保存到因子库" : "加入项目"}</Button>
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
