import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Braces, FlaskConical, Library, LineChart, Play, Plus, Save, Search, Trash2 } from "lucide-react"

import { MarketResearchTerminal, type MarketFieldSeries, type MarketRange, useMarketWatchlist } from "@/components/market"
import { PythonEditor, type PythonEditorHandle } from "@/components/python"
import { SdkDocumentation } from "@/components/shared/SdkDocumentation"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useStrategySdk, type SdkEntrypoint } from "@/contexts/StrategySdkContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api, type MarketBar, type MarketInstrument } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

interface FieldCatalog {
  profile: "runtime"
  start_date: string
  end_date: string
  datasets: Record<string, Array<{ name: string; data_type: string; nullable: boolean }>>
}

interface FundamentalPayload {
  fields: string[]
  asof_date: string
  rows: Array<Record<string, string | number | null>>
}

interface SelectedResearchField {
  dataset: string
  name: string
}

interface FactorValue { symbol: string; value: number | null }
interface FactorSnapshotResult {
  factor_id?: string
  as_of?: string
  values?: FactorValue[]
  invoked?: string[]
  revision?: number
  source_sha256?: string
}
interface FactorHistoryResult {
  factor_id?: string
  observations?: number
  frequency?: string
  snapshots?: Array<{ date: string; values: FactorValue[] }>
  invoked?: string[]
  revision?: number
  source_sha256?: string
}

interface FactorTemplate {
  id: string
  label: string
  category: "technical" | "fundamental"
  description: string
  inputs: string[]
  requirements: Record<string, string[]>
  recommended_direction: "higher" | "lower"
  source: string
}

interface FactorTemplateCatalog { templates: FactorTemplate[] }

interface EntrypointSource {
  project_id: string
  entrypoint_id: string
  source_sha256: string
  source: string
}

function factorSnippet(id = "new_factor") {
  return `\n\n@factor(id="${id}", label="自定义因子")
def ${id}(context, *, window: int = 20):
    """返回每个标的最近 window 个交易日的累计收益率。"""
    # 多读取一个收盘价，才能形成完整的 window 段收益。
    close = context.history("close", window=window + 1)
    if len(close.index) < window + 1:
        # 历史不足的标的保留为 NaN，不用不完整窗口制造信号。
        return close.mean(axis=0) * float("nan")
    return close.iloc[-1] / close.iloc[0] - 1.0
`
}

function dateBefore(endDate: string, range: MarketRange): string | null {
  if (range === "all") return null
  const start = new Date(`${endDate}T00:00:00`)
  if (range === "3m") start.setMonth(start.getMonth() - 3)
  if (range === "6m") start.setMonth(start.getMonth() - 6)
  if (range === "1y") start.setFullYear(start.getFullYear() - 1)
  return start.toISOString().slice(0, 10)
}

function quarterForDate(date: string): string {
  const [year, month] = date.split("-").map(Number)
  return `${year}q${Math.ceil(month / 3)}`
}

function displayValue(value: unknown): string {
  if (typeof value === "string") return value || "—"
  if (typeof value !== "number" || !Number.isFinite(value)) return "—"
  if (Math.abs(value) >= 1_000_000) {
    return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 2 }).format(value)
  }
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: Math.abs(value) < 1 ? 4 : 2,
  })
}

function numericFieldValue(row: Record<string, unknown> | undefined, field: string): number | null {
  const value = row?.[field]
  if (typeof value === "boolean") return value ? 1 : 0
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function fundamentalAtDate(rows: Array<Record<string, string | number | null>>, date: string) {
  return rows
    .filter((row) => typeof row.available_date === "string" && row.available_date <= date)
    .sort((left, right) => String(left.available_date).localeCompare(String(right.available_date)))
    .at(-1)
}

function FactorMarketBrowser({ fields }: { fields: FieldCatalog | null }) {
  const sdk = useStrategySdk()
  const { activeMode, selectedSymbol, setSelectedSymbol } = useWorkspace()
  const project = sdk.project
  const [range, setRange] = useState<MarketRange>("6m")
  const [reloadRevision, setReloadRevision] = useState(0)
  const [selectedFields, setSelectedFields] = useState<SelectedResearchField[]>([])
  const [crosshairDate, setCrosshairDate] = useState("")
  const { watchlist, toggleWatchlist } = useMarketWatchlist()
  const endDate = fields?.end_date || new Date().toISOString().slice(0, 10)
  const symbolQuery = useQuery({
    queryKey: ["sdk-factor", "symbols", project?.profile],
    queryFn: () => api.get<{ instruments: MarketInstrument[] }>(`/data/market/symbols?profile=${project?.profile}`),
    enabled: activeMode === "factor" && Boolean(project),
    staleTime: 60_000,
  })
  const instruments = symbolQuery.data?.instruments ?? []
  const symbol = selectedSymbol && instruments.some((item) => item.symbol === selectedSymbol)
    ? selectedSymbol : instruments[0]?.symbol ?? ""

  useEffect(() => {
    if (symbol && symbol !== selectedSymbol) setSelectedSymbol(symbol)
  }, [selectedSymbol, setSelectedSymbol, symbol])

  const barsQuery = useQuery({
    queryKey: ["sdk-factor", "bars", project?.profile, symbol, endDate, range, reloadRevision],
    queryFn: () => {
      const params = new URLSearchParams({ profile: project?.profile ?? "runtime", symbol, end: endDate })
      const start = dateBefore(endDate, range)
      if (start) params.set("start", start)
      return api.get<{ rows: MarketBar[] }>(`/data/market/bars?${params.toString()}`)
    },
    enabled: activeMode === "factor" && Boolean(project && symbol),
    staleTime: 30_000,
  })
  const fundamentalsQuery = useQuery({
    queryKey: ["sdk-factor", "fundamentals", project?.profile, symbol, fields?.start_date, endDate, range],
    queryFn: () => {
      const startDate = dateBefore(endDate, range) ?? fields?.start_date ?? endDate
      const params = new URLSearchParams({
        profile: project?.profile ?? "runtime",
        asof_date: endDate,
        start_quarter: quarterForDate(startDate),
        end_quarter: quarterForDate(endDate),
        limit: "1000",
      })
      params.append("symbols", symbol)
      return api.get<FundamentalPayload>(`/data/fundamentals?${params.toString()}`)
    },
    enabled: activeMode === "factor" && Boolean(project && symbol),
    staleTime: 30_000,
  })
  const bars = useMemo(() => barsQuery.data?.rows ?? [], [barsQuery.data?.rows])
  const latestBar = bars.at(-1)
  const displayedDate = crosshairDate || latestBar?.date || endDate
  const displayedBar = bars.find((row) => row.date === displayedDate) ?? latestBar
  const fundamentalRows = useMemo(
    () => fundamentalsQuery.data?.rows ?? [],
    [fundamentalsQuery.data?.rows],
  )
  const displayedFundamental = useMemo(
    () => fundamentalAtDate(fundamentalRows, displayedDate),
    [displayedDate, fundamentalRows],
  )
  const fieldSeries = useMemo<MarketFieldSeries[]>(() => selectedFields.map((selected) => {
    const values = selected.dataset === "fundamentals"
      ? bars.map((bar) => ({
        date: bar.date,
        value: numericFieldValue(fundamentalAtDate(fundamentalRows, bar.date), selected.name),
      }))
      : bars.map((bar) => ({
        date: bar.date,
        value: numericFieldValue(bar as unknown as Record<string, unknown>, selected.name),
      }))
    return {
      id: `${selected.dataset}-${selected.name}`,
      label: `${selected.dataset === "fundamentals" ? "基本面" : "行情"} · ${selected.name}`,
      placement: selected.dataset === "market_bars" && ["open", "high", "low", "close"].includes(selected.name) ? "main" : "sub",
      values,
    }
  }), [bars, fundamentalRows, selectedFields])
  const fieldCount = Object.values(fields?.datasets ?? {}).reduce((count, rows) => count + rows.length, 0)

  function toggleField(dataset: string, name: string) {
    setSelectedFields((current) => {
      const active = current.some((item) => item.dataset === dataset && item.name === name)
      if (active) return current.filter((item) => item.dataset !== dataset || item.name !== name)
      const next = { dataset, name }
      return current.length >= 2 ? [current[1], next] : [...current, next]
    })
  }

  return (
    <section className="factor-source-browser" aria-label="因子研究数据">
      <MarketResearchTerminal
        instruments={instruments} rows={bars} symbol={symbol} onSymbolChange={setSelectedSymbol}
        range={range} onRangeChange={setRange} loading={symbolQuery.isLoading || barsQuery.isLoading}
        error={barsQuery.error instanceof Error ? barsQuery.error.message : ""}
        onReload={() => setReloadRevision((value) => value + 1)} watchlist={watchlist}
        onToggleWatchlist={toggleWatchlist} dataLabel={`日线 · 截至 ${endDate}`}
        emptyLabel="当前证券没有可用 K 线。" density="compact" fieldSeries={fieldSeries}
        onCrosshairBarChange={(bar) => setCrosshairDate(bar?.date ?? "")}
      />
      <div className="factor-source-fields">
        <div className="factor-source-fields-heading">
          <div><strong>数据字段</strong><small>点击字段，在上方图表展示；最多同时比较 2 项</small></div>
          <span>{selectedFields.length ? `已展示 ${selectedFields.length}/2` : `${fieldCount} 个字段`}</span>
        </div>
        <div className="factor-source-field-groups">
          {Object.entries(fields?.datasets ?? {}).map(([dataset, rows]) => (
            <section className="factor-source-field-group" key={dataset}>
              <header><strong>{dataset}</strong><code>{dataset === "fundamentals" ? "点时基本面" : "行情序列"}</code><span>{rows.length}</span></header>
              <div className="factor-source-field-grid">
                {rows.map((field) => {
                  const active = selectedFields.some((item) => item.dataset === dataset && item.name === field.name)
                  const value = dataset === "fundamentals"
                    ? displayedFundamental?.[field.name]
                    : (displayedBar as unknown as Record<string, unknown> | undefined)?.[field.name]
                  return (
                    <button className={`factor-source-field ${active ? "active" : ""}`} type="button" key={`${dataset}-${field.name}`} aria-pressed={active} title={`${active ? "隐藏" : "展示"} ${field.name} 数据序列`} onClick={() => toggleField(dataset, field.name)}>
                      <span><code>{field.name}</code><small>{field.data_type}</small></span>
                      <strong>{displayValue(value)}</strong><LineChart size={11} />
                    </button>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
      </div>
    </section>
  )
}

function FactorResultPanels({ snapshot, history }: {
  snapshot: FactorSnapshotResult | null
  history: FactorHistoryResult | null
}) {
  const latest = snapshot?.values ?? history?.snapshots?.at(-1)?.values ?? []
  const finite = latest
    .filter((item): item is { symbol: string; value: number } => typeof item.value === "number" && Number.isFinite(item.value))
    .sort((left, right) => right.value - left.value)
  const values = finite.map((item) => item.value)
  const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
  const minimum = values.length ? Math.min(...values) : null
  const maximum = values.length ? Math.max(...values) : null
  const historyRows = history?.snapshots ?? []
  if (!snapshot && !history) return <div className="analytics-empty">运行最近截面或历史检验后，这里显示覆盖率、分布和高低分证券。</div>

  return (
    <div className="factor-results-grid">
      <section className="factor-lab-panel embedded">
        <header className="factor-panel-header"><span><strong>最近验证截面</strong><small>{snapshot?.as_of || historyRows.at(-1)?.date || "—"}</small></span><Badge variant="outline">{finite.length} 个有效值</Badge></header>
        <div className="factor-panel-scroll">
          <div className="stage-kpi-grid three">
            <div><span>均值</span><strong>{displayValue(mean)}</strong></div>
            <div><span>最小</span><strong>{displayValue(minimum)}</strong></div>
            <div><span>最大</span><strong>{displayValue(maximum)}</strong></div>
          </div>
          <div className="analytics-table-wrap mt-3"><table className="analytics-table compact">
            <thead><tr><th>排名</th><th>证券</th><th>因子值</th></tr></thead>
            <tbody>{finite.slice(0, 20).map((item, index) => <tr key={item.symbol}><td>{index + 1}</td><td><code>{item.symbol}</code></td><td>{displayValue(item.value)}</td></tr>)}</tbody>
          </table></div>
        </div>
      </section>
      <section className="factor-lab-panel embedded">
        <header className="factor-panel-header"><span><strong>历史截面</strong><small>同一 @factor 函数逐期调用</small></span><Badge variant="outline">{history?.observations ?? 0} 期</Badge></header>
        <div className="factor-panel-scroll">
          {historyRows.length ? <div className="editor-list">{[...historyRows].reverse().slice(0, 30).map((row) => {
            const count = row.values.filter((item) => typeof item.value === "number").length
            return <div key={row.date}><strong>{row.date}</strong><small>{count} 个有效证券</small></div>
          })}</div> : <div className="analytics-empty">尚未运行历史检验</div>}
          {(snapshot?.invoked || history?.invoked)?.length ? <div className="mt-3 text-xs text-muted-foreground">依赖链：{(snapshot?.invoked || history?.invoked)?.join(" → ")}</div> : null}
        </div>
      </section>
    </div>
  )
}

export function FactorWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const project = sdk.project
  const factorEditor = useRef<PythonEditorHandle>(null)
  const [workspaceView, setWorkspaceView] = useState<"build" | "results">("build")
  const [factorSource, setFactorSource] = useState("")
  const [savedFactorSource, setSavedFactorSource] = useState("")
  const [loadingFactorSource, setLoadingFactorSource] = useState(false)
  const [selectedFactor, setSelectedFactor] = useState("")
  const [fields, setFields] = useState<FieldCatalog | null>(null)
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [snapshot, setSnapshot] = useState<FactorSnapshotResult | null>(null)
  const [history, setHistory] = useState<FactorHistoryResult | null>(null)
  const [templateQuery, setTemplateQuery] = useState("")
  const [templateCategory, setTemplateCategory] = useState<"all" | "technical" | "fundamental">("all")
  const [installingTemplate, setInstallingTemplate] = useState("")
  const factors = useMemo(() => project?.inspection.entrypoints.filter((item) => item.kind === "factor") ?? [], [project?.inspection.entrypoints])
  const activeFactor = factors.find((item) => item.id === selectedFactor) ?? factors[0]
  const projectId = project?.id ?? ""
  const projectHash = project?.draft_source_sha256 ?? ""
  const projectProfile = project?.profile
  const activeFactorId = activeFactor?.id ?? ""
  const factorDirty = Boolean(activeFactor && factorSource !== savedFactorSource)
  const localDirty = factorDirty
  const templatesQuery = useQuery({
    queryKey: ["sdk-factor-templates"],
    queryFn: () => api.get<FactorTemplateCatalog>("/strategy/factor-templates"),
    staleTime: Number.POSITIVE_INFINITY,
  })
  const templates = useMemo(() => {
    const needle = templateQuery.trim().toLowerCase()
    return (templatesQuery.data?.templates ?? []).filter((template) => (
      (templateCategory === "all" || template.category === templateCategory)
      && (!needle || template.id.toLowerCase().includes(needle) || template.label.toLowerCase().includes(needle) || template.description.toLowerCase().includes(needle))
    ))
  }, [templateCategory, templateQuery, templatesQuery.data?.templates])

  useEffect(() => {
    setSnapshot(null); setHistory(null)
    if (!projectProfile) return
    void api.get<FieldCatalog>(`/strategy/fields?profile=${projectProfile}`).then((value) => {
      setFields(value); setStartDate(value.start_date); setEndDate(value.end_date)
    }).catch((reason: Error) => setError(reason.message))
  }, [projectHash, projectId, projectProfile])
  useEffect(() => {
    if (!projectId || !activeFactorId) {
      setFactorSource("")
      setSavedFactorSource("")
      return
    }
    let current = true
    setLoadingFactorSource(true)
    void api.get<EntrypointSource>(`/strategy/projects/${projectId}/entrypoints/${encodeURIComponent(activeFactorId)}/source`).then((payload) => {
      if (!current || payload.source_sha256 !== projectHash) return
      setFactorSource(payload.source)
      setSavedFactorSource(payload.source)
    }).catch((reason: Error) => {
      if (current) setError(reason.message)
    }).finally(() => {
      if (current) setLoadingFactorSource(false)
    })
    return () => { current = false }
  }, [activeFactorId, projectHash, projectId])
  useEffect(() => { if (activeFactor && activeFactor.id !== selectedFactor) setSelectedFactor(activeFactor.id) }, [activeFactor, selectedFactor])

  function insertAtFactorCursor(text: string) {
    if (!activeFactor) return
    setWorkspaceView("build")
    const insert = () => {
      if (!factorEditor.current) {
        setError("因子编辑器尚未就绪，请稍后再试。")
        return
      }
      factorEditor.current.insertText(text)
    }
    if (factorEditor.current) insert()
    else window.setTimeout(insert, 0)
  }
  function insertFactorDependency(factor: SdkEntrypoint) {
    insertAtFactorCursor(`context.factor("${factor.id}")`)
  }
  function selectProjectFactor(factorId: string) {
    if (factorDirty && factorId !== activeFactor?.id) {
      setError("当前因子还有未保存修改，请先保存再切换因子。")
      return
    }
    setError("")
    setFactorSource("")
    setSavedFactorSource("")
    setSelectedFactor(factorId)
    setSnapshot(null)
    setHistory(null)
  }
  async function addFactor() {
    if (!project?.editable || busy) return
    if (factorDirty) {
      setError("当前因子还有未保存修改，请先保存再新建因子。")
      return
    }
    const knownIds = new Set(factors.map((factor) => factor.id))
    let suffix = factors.length + 1
    while (knownIds.has(`factor_${suffix}`)) suffix += 1
    const id = `factor_${suffix}`
    setBusy(true); setError("")
    try {
      const added = await sdk.addFactorSource(factorSnippet(id))
      setFactorSource("")
      setSavedFactorSource("")
      setSelectedFactor(added.factor.id)
      setWorkspaceView("build")
      setSnapshot(null); setHistory(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }
  async function installTemplate(template: FactorTemplate) {
    if (!project?.editable || localDirty) return
    if (!await confirm({
      title: `加入因子 ${template.label}`,
      description: "将复制一份模板源码到当前项目；重复加入会创建新的独立因子。",
      confirmText: "加入项目",
    })) return
    setInstallingTemplate(template.id); setError("")
    try {
      const installed = await sdk.installFactorTemplate(template.id)
      setFactorSource("")
      setSavedFactorSource("")
      setSelectedFactor(installed.factor.id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setInstallingTemplate("") }
  }
  async function saveActiveFactor(nextSource = factorSource) {
    if (!project?.editable || !activeFactor) return
    const factorIndex = factors.findIndex((factor) => factor.id === activeFactor.id)
    setBusy(true); setError("")
    try {
      const updated = await sdk.structuredEdit({
        operation: "replace_function",
        entrypoint_id: activeFactor.id,
        function_source: nextSource,
      })
      const updatedFactors = updated.inspection.entrypoints.filter((item) => item.kind === "factor")
      const nextFactor = updatedFactors.find((item) => item.function === activeFactor.function)
        ?? updatedFactors[factorIndex]
      if (nextFactor) setSelectedFactor(nextFactor.id)
      setFactorSource(nextSource)
      setSavedFactorSource(nextSource)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }
  async function deleteActiveFactor() {
    if (!project?.editable || !activeFactor) return
    const factorIndex = factors.findIndex((factor) => factor.id === activeFactor.id)
    if (!await confirm({
      title: `删除因子 ${activeFactor.id}`,
      description: factorDirty
        ? "当前因子的未保存修改也会丢失。若策略或其他因子仍引用它，系统将拒绝删除。"
        : "若策略或其他因子仍引用它，系统将拒绝删除。",
      confirmText: "删除因子",
      tone: "danger",
    })) return
    setBusy(true); setError("")
    try {
      const updated = await sdk.structuredEdit({
        operation: "delete_function",
        entrypoint_id: activeFactor.id,
      })
      const remaining = updated.inspection.entrypoints.filter((item) => item.kind === "factor")
      const nextFactor = remaining[Math.min(factorIndex, Math.max(0, remaining.length - 1))]
      setFactorSource("")
      setSavedFactorSource("")
      setSelectedFactor(nextFactor?.id ?? "")
      setSnapshot(null); setHistory(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }
  async function evaluate(mode: "snapshot" | "history") {
    if (!project || project.dirty || factorDirty || !activeFactor) return
    if (!await confirm({
      title: mode === "history" ? "运行因子历史检验" : "运行因子截面检验",
      description: `将运行当前已保存的 @factor ${activeFactor.id}。本机 Python 不是安全沙箱。`,
      confirmText: "确认运行",
    })) return
    setBusy(true); setError("")
    try {
      const endpoint = `/strategy/projects/${project.id}/factors/${activeFactor.id}/${mode}`
      if (mode === "history") setHistory(await api.post<FactorHistoryResult>(endpoint, {
        profile: project.profile, start_date: startDate, end_date: endDate, revision: project.current_revision,
        frequency: "monthly", parameters: {}, confirm_python_execution: true,
      }))
      else setSnapshot(await api.post<FactorSnapshotResult>(endpoint, {
        profile: project.profile, as_of_date: endDate, revision: project.current_revision,
        parameters: {}, confirm_python_execution: true,
      }))
      setWorkspaceView("results")
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy(false) }
  }

  if (!project) return <Widget title="因子研究" loading={sdk.loading} error={sdk.error}><span /></Widget>
  const hasUnsavedChanges = project.dirty || localDirty
  return (
    <Widget headerless>
      <div className="factor-workbench-shell">
        <FactorMarketBrowser fields={fields} />
        <Tabs className="factor-workbench-tabs" value={workspaceView} onValueChange={(value) => setWorkspaceView(value as "build" | "results")}>
          <div className="factor-workbench-toolbar">
            <TabsList><TabsTrigger value="build">因子库与编辑</TabsTrigger><TabsTrigger value="results">因子检验{hasUnsavedChanges ? <span className="factor-tab-warning">尚未保存</span> : null}</TabsTrigger></TabsList>
            <div className="factor-workbench-actions">
              <span className={hasUnsavedChanges ? "factor-build-hint" : "factor-validation-ready"}>{hasUnsavedChanges ? "当前因子尚未保存" : "已保存"}</span>
              <SdkDocumentation topic="factor" />
            </div>
          </div>
          {error ? <div className="workbench-message error factor-workbench-error">{error}</div> : null}

          <TabsContent className="factor-workbench-content" value="build"><div className="factor-build-grid">
            <section className="factor-lab-panel embedded">
              <header className="factor-panel-header"><span><strong><Library size={14} /> 因子库</strong></span><Button size="sm" variant="outline" disabled={!project.editable || busy || factorDirty} onClick={() => void addFactor()}><Plus />新因子</Button></header>
              <div className="factor-template-controls"><label><Search size={12} /><input value={templateQuery} onChange={(event) => setTemplateQuery(event.target.value)} placeholder={`搜索 ${templatesQuery.data?.templates.length ?? 0} 个默认因子`} /></label><div>{(["all", "technical", "fundamental"] as const).map((category) => <button type="button" key={category} className={templateCategory === category ? "active" : ""} onClick={() => setTemplateCategory(category)}>{category === "all" ? "全部" : category === "technical" ? "技术" : "基本面"}</button>)}</div></div>
              <div className="factor-panel-scroll">
                <div className="factor-section-heading"><span>项目因子</span><strong>{factors.length}</strong></div>
                <div className="factor-project-list">{factors.map((factor) => <button type="button" key={factor.id} className={activeFactor?.id === factor.id ? "active" : ""} onClick={() => selectProjectFactor(factor.id)}><span><strong>{factor.id}</strong><small>{factor.label ? `${factor.label} · ` : ""}{factor.function}</small></span></button>)}</div>
                <div className="factor-section-heading mt-4"><span>默认因子库</span><strong>{templates.length}</strong></div>
                {templatesQuery.isLoading ? <div className="analytics-empty">正在读取因子模板…</div> : null}
                {templatesQuery.error instanceof Error ? <div className="workbench-message error">{templatesQuery.error.message}</div> : null}
                <div className="factor-template-list">{templates.map((template) => <article key={template.id}><div><span><strong>{template.label}</strong><small>{template.description}</small><small>数据：{template.inputs.join("、")}</small></span><em>{template.category === "technical" ? "技术" : "基本面"} · {template.recommended_direction === "higher" ? "高值优先" : "低值优先"}</em></div><footer><code>{template.id}</code><button type="button" disabled={!project.editable || localDirty || Boolean(installingTemplate)} onClick={() => void installTemplate(template)}><Plus size={11} />{installingTemplate === template.id ? "加入中" : "加入项目"}</button></footer></article>)}</div>
                <div className="factor-section-heading mt-4">可复用因子依赖</div>
                <div className="factor-catalog-list">{factors.filter((factor) => factor.id !== activeFactor?.id).map((factor) => <button type="button" key={factor.id} onClick={() => insertFactorDependency(factor)}><span><strong>{factor.label || factor.id}</strong><small>插入 context.factor("{factor.id}")</small></span><Plus size={13} /></button>)}</div>
              </div>
            </section>
            <section className="factor-lab-panel embedded">
              <header className="factor-panel-header"><span><strong><Braces size={14} /> 当前因子 Python</strong></span><div className="flex items-center gap-2">{activeFactor ? <Badge>@factor {activeFactor.id}</Badge> : null}<Button size="sm" variant="outline" disabled={!project.editable || busy || !activeFactor} onClick={() => void deleteActiveFactor()}><Trash2 />删除</Button><Button size="sm" disabled={!project.editable || busy || (!factorDirty && !project.dirty)} onClick={() => void saveActiveFactor()}><Save />保存当前因子</Button></div></header>
              <div className="factor-editor-body">{loadingFactorSource ? <div className="python-editor-loading">正在加载当前因子…</div> : activeFactor ? <PythonEditor
                ref={factorEditor}
                kind="factor"
                documentId={`${project.id}.factor.${factors.indexOf(activeFactor)}`}
                value={factorSource}
                version={`${project.draft_source_sha256}:${activeFactor.id}`}
                disabled={!project.editable}
                height={640}
                fields={Object.entries(fields?.datasets ?? {}).flatMap(([dataset, items]) => items.map((item) => ({ name: item.name, dataset, detail: `${item.data_type}${item.nullable ? " · 可空" : ""}` })))}
                factors={factors.map((factor) => ({ id: factor.id, label: factor.label }))}
                parameters={activeFactor.parameters}
                onChange={setFactorSource}
                onSave={(nextSource) => saveActiveFactor(nextSource)}
              /> : <div className="analytics-empty">当前模块没有 @factor</div>}</div>
            </section>
          </div></TabsContent>

          <TabsContent className="factor-workbench-content" value="results"><div className="factor-final-validation">
            <div className="factor-final-validation-intro"><div><FlaskConical size={16} /><span><strong>单因子检验</strong><small>选择一个已保存因子，运行截面或历史检验。</small></span></div><div className="factor-validation-controls"><label className="factor-validation-control"><span>检验因子</span><select value={activeFactor?.id ?? ""} disabled={busy || factorDirty || factors.length === 0} onChange={(event) => selectProjectFactor(event.target.value)}>{factors.map((factor) => <option key={factor.id} value={factor.id}>{factor.label ? `${factor.label} (${factor.id})` : factor.id}</option>)}</select></label><label className="factor-validation-control"><span>开始</span><Input className="h-8" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label className="factor-validation-control"><span>结束</span><Input className="h-8" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label><Button size="sm" variant="outline" disabled={busy || hasUnsavedChanges || !activeFactor} onClick={() => void evaluate("snapshot")}><Play />截面检验</Button><Button size="sm" disabled={busy || hasUnsavedChanges || !activeFactor} onClick={() => void evaluate("history")}><Play />历史检验</Button></div></div>
            <FactorResultPanels snapshot={snapshot} history={history} />
          </div></TabsContent>
        </Tabs>
      </div>
    </Widget>
  )
}
