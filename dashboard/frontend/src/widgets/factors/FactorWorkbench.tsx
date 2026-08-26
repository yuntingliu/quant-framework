import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Braces, Check, FlaskConical, Library, Play, Plus, Save, Search, Trash2 } from "lucide-react"

import { MarketResearchTerminal, type MarketRange, useMarketWatchlist } from "@/components/market"
import { PythonEditor, type PythonEditorHandle } from "@/components/python"
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
    close = context.history("close", window=window)
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

function FactorMarketBrowser({ fields, onInsertField }: {
  fields: FieldCatalog | null
  onInsertField: (field: string, dataset: string) => void
}) {
  const sdk = useStrategySdk()
  const { activeMode, selectedSymbol, setSelectedSymbol } = useWorkspace()
  const project = sdk.project
  const [range, setRange] = useState<MarketRange>("6m")
  const [reloadRevision, setReloadRevision] = useState(0)
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
    queryKey: ["sdk-factor", "fundamentals", project?.profile, symbol, endDate],
    queryFn: () => {
      const params = new URLSearchParams({ profile: project?.profile ?? "runtime", asof_date: endDate, limit: "40" })
      params.append("symbols", symbol)
      return api.get<FundamentalPayload>(`/data/fundamentals?${params.toString()}`)
    },
    enabled: activeMode === "factor" && Boolean(project && symbol),
    staleTime: 30_000,
  })
  const bars = barsQuery.data?.rows ?? []
  const latestBar = bars.at(-1)
  const latestFundamental = useMemo(
    () => [...(fundamentalsQuery.data?.rows ?? [])]
      .sort((left, right) => String(left.quarter ?? "").localeCompare(String(right.quarter ?? ""))).at(-1),
    [fundamentalsQuery.data?.rows],
  )
  const fieldCount = Object.values(fields?.datasets ?? {}).reduce((count, rows) => count + rows.length, 0)

  return (
    <section className="factor-source-browser" aria-label="因子研究数据">
      <MarketResearchTerminal
        instruments={instruments} rows={bars} symbol={symbol} onSymbolChange={setSelectedSymbol}
        range={range} onRangeChange={setRange} loading={symbolQuery.isLoading || barsQuery.isLoading}
        error={barsQuery.error instanceof Error ? barsQuery.error.message : ""}
        onReload={() => setReloadRevision((value) => value + 1)} watchlist={watchlist}
        onToggleWatchlist={toggleWatchlist} dataLabel={`日线 · 截至 ${endDate}`}
        emptyLabel="当前证券没有可用 K 线。" density="compact" contextPanelLabel="SDK 数据字段"
        contextPanel={(
          <div className="factor-source-fields">
            <div className="factor-source-fields-heading">
              <div><strong>当前数据全部字段</strong><small>点击即插入 Python</small></div>
              <span>{fieldCount} 个字段</span>
            </div>
            <div className="factor-source-field-groups">
              {Object.entries(fields?.datasets ?? {}).map(([dataset, rows]) => (
                <section className="factor-source-field-group" key={dataset}>
                  <header><strong>{dataset}</strong><code>context</code><span>{rows.length}</span></header>
                  <div className="factor-source-field-grid">
                    {rows.map((field) => {
                      const value = dataset === "fundamentals" ? latestFundamental?.[field.name] : latestBar?.[field.name as keyof MarketBar]
                      return (
                        <button className="factor-source-field" type="button" key={`${dataset}-${field.name}`} title={`把 ${field.name} 插入当前 @factor`} onClick={() => onInsertField(field.name, dataset)}>
                          <span><code>{field.name}</code><small>{field.data_type}</small></span>
                          <strong>{displayValue(value)}</strong><Plus size={11} />
                        </button>
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
        <header className="factor-panel-header"><span><strong>历史截面</strong><small>同一冻结 @factor 函数逐期调用</small></span><Badge variant="outline">{history?.observations ?? 0} 期</Badge></header>
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
  const [workspaceView, setWorkspaceView] = useState<"build" | "results" | "python">("build")
  const [source, setSource] = useState("")
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
  const projectSource = project?.draft_source ?? ""
  const activeFactorId = activeFactor?.id ?? ""
  const moduleDirty = Boolean(project && source !== project.draft_source)
  const factorDirty = Boolean(activeFactor && factorSource !== savedFactorSource)
  const localDirty = moduleDirty || factorDirty
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
    setSource(projectSource)
    setSnapshot(null); setHistory(null)
    if (!projectProfile) return
    void api.get<FieldCatalog>(`/strategy/fields?profile=${projectProfile}`).then((value) => {
      setFields(value); setStartDate(value.start_date); setEndDate(value.end_date)
    }).catch((reason: Error) => setError(reason.message))
  }, [projectHash, projectId, projectProfile, projectSource])
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
    if (workspaceView === "python" && moduleDirty) {
      setError("完整源码还有未保存修改，请先保存后再编辑单个因子。")
      return
    }
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
  function insertField(field: string, dataset: string) {
    insertAtFactorCursor(dataset === "fundamentals"
      ? `context.fundamental("${field}")`
      : `context.current("${field}")`)
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
  }
  function changeWorkspaceView(nextView: "build" | "results" | "python") {
    if (nextView === "python" && factorDirty) {
      setError("当前因子还有未保存修改，请先保存再打开完整源码。")
      return
    }
    if (workspaceView === "python" && nextView !== "python" && moduleDirty) {
      setError("完整源码还有未保存修改，请先保存再返回因子视图。")
      return
    }
    setError("")
    setWorkspaceView(nextView)
  }
  function addFactor() {
    if (factorDirty) {
      setError("当前因子还有未保存修改，请先保存再新建因子。")
      return
    }
    const id = `factor_${factors.length + 1}`
    const marker = source.indexOf("\n@signal")
    const position = marker >= 0 ? marker : source.length
    setSource(`${source.slice(0, position)}${factorSnippet(id)}${source.slice(position)}`); setWorkspaceView("python")
  }
  async function installTemplate(template: FactorTemplate) {
    if (!project?.editable || localDirty || factors.some((factor) => factor.id === template.id)) return
    if (!await confirm({
      title: `加入因子 ${template.label}`,
      description: `将把模板转换为 @factor(${template.id}) Python 函数并写入当前项目草稿，同时补齐数据字段声明。`,
      confirmText: "加入项目",
    })) return
    setInstallingTemplate(template.id); setError("")
    try {
      await api.post(`/strategy/projects/${project.id}/factor-templates/${encodeURIComponent(template.id)}`, {
        expected_source_sha256: project.draft_source_sha256,
        confirm_write: true,
      })
      setFactorSource("")
      setSavedFactorSource("")
      setSelectedFactor(template.id)
      await sdk.openProject(project.id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setInstallingTemplate("") }
  }
  async function saveDraft(nextSource = source) {
    if (!project?.editable) return
    setBusy(true); setError("")
    try { await sdk.updateDraft(nextSource) } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy(false) }
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
      setSource(updated.draft_source)
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
      setSource(updated.draft_source)
      setSnapshot(null); setHistory(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }
  async function evaluate(mode: "snapshot" | "history") {
    if (!project || project.dirty || moduleDirty || factorDirty || !activeFactor) return
    if (!await confirm({
      title: mode === "history" ? "运行因子历史检验" : "运行因子截面检验",
      description: `将运行 ${project.id}@${project.current_revision} 中的 @factor ${activeFactor.id}。本机 Python 不是安全沙箱。`,
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
  const requiresFreeze = project.dirty || localDirty
  return (
    <Widget headerless>
      <div className="factor-workbench-shell">
        <FactorMarketBrowser fields={fields} onInsertField={insertField} />
        <Tabs className="factor-workbench-tabs" value={workspaceView} onValueChange={(value) => changeWorkspaceView(value as "build" | "results" | "python")}>
          <div className="factor-workbench-toolbar">
            <TabsList><TabsTrigger value="build">因子库与参数</TabsTrigger><TabsTrigger value="results">最终验证{requiresFreeze ? <span className="factor-tab-warning">草稿未冻结</span> : null}</TabsTrigger><TabsTrigger value="python">高级 Python</TabsTrigger></TabsList>
            <div className="factor-workbench-actions">
              <span className={requiresFreeze ? "factor-build-hint" : "factor-validation-ready"}>{factorDirty ? "当前因子尚未保存" : moduleDirty ? "完整源码尚未保存" : project.dirty ? "参数或源码已变化" : "当前源码可复现"}</span>
            </div>
          </div>
          {error ? <div className="workbench-message error factor-workbench-error">{error}</div> : null}

          <TabsContent className="factor-workbench-content" value="build"><div className="factor-build-grid">
            <section className="factor-lab-panel embedded">
              <header className="factor-panel-header"><span><strong><Library size={14} /> 因子库</strong></span><Button size="sm" variant="outline" disabled={!project.editable} onClick={addFactor}><Plus />新因子</Button></header>
              <div className="factor-template-controls"><label><Search size={12} /><input value={templateQuery} onChange={(event) => setTemplateQuery(event.target.value)} placeholder="搜索 16 个内置因子" /></label><div>{(["all", "technical", "fundamental"] as const).map((category) => <button type="button" key={category} className={templateCategory === category ? "active" : ""} onClick={() => setTemplateCategory(category)}>{category === "all" ? "全部" : category === "technical" ? "技术" : "基本面"}</button>)}</div></div>
              <div className="factor-panel-scroll">
                <div className="factor-section-heading"><span>项目因子</span><strong>{factors.length}</strong></div>
                <div className="factor-project-list">{factors.map((factor) => <button type="button" key={factor.id} className={activeFactor?.id === factor.id ? "active" : ""} onClick={() => selectProjectFactor(factor.id)}><span><strong>{factor.id}</strong><small>{factor.label ? `${factor.label} · ` : ""}{factor.function}</small></span></button>)}</div>
                <div className="factor-section-heading mt-4"><span>可用 Python 模板</span><strong>{templates.length}</strong></div>
                {templatesQuery.isLoading ? <div className="analytics-empty">正在读取因子模板…</div> : null}
                {templatesQuery.error instanceof Error ? <div className="workbench-message error">{templatesQuery.error.message}</div> : null}
                <div className="factor-template-list">{templates.map((template) => {
                  const installed = factors.some((factor) => factor.id === template.id)
                  return <article key={template.id}><div><span><strong>{template.label}</strong><small>{template.description}</small></span><em>{template.category === "technical" ? "技术" : "基本面"} · {template.recommended_direction === "higher" ? "高值优先" : "低值优先"}</em></div><footer><code>{template.id}</code><button className={installed ? "installed" : ""} type="button" disabled={installed || !project.editable || localDirty || Boolean(installingTemplate)} onClick={() => void installTemplate(template)}>{installed ? <Check size={11} /> : <Plus size={11} />}{installed ? "已加入" : installingTemplate === template.id ? "加入中" : "加入项目"}</button></footer></article>
                })}</div>
                <div className="factor-section-heading mt-4">可复用因子依赖</div>
                <div className="factor-catalog-list">{factors.filter((factor) => factor.id !== activeFactor?.id).map((factor) => <button type="button" key={factor.id} onClick={() => insertFactorDependency(factor)}><span><strong>{factor.label || factor.id}</strong><small>插入 context.factor("{factor.id}")</small></span><Plus size={13} /></button>)}</div>
              </div>
            </section>
            <section className="factor-lab-panel embedded">
              <header className="factor-panel-header"><span><strong><Braces size={14} /> 当前因子 Python</strong></span><div className="flex items-center gap-2">{activeFactor ? <Badge>@factor {activeFactor.id}</Badge> : null}<Button size="sm" variant="outline" disabled={!project.editable || busy || !activeFactor} onClick={() => void deleteActiveFactor()}><Trash2 />删除</Button><Button size="sm" disabled={!project.editable || busy || !factorDirty} onClick={() => void saveActiveFactor()}><Save />保存当前因子</Button></div></header>
              <div className="factor-editor-body">{loadingFactorSource ? <div className="python-editor-loading">正在加载当前因子…</div> : activeFactor ? <PythonEditor
                ref={factorEditor}
                kind="factor"
                documentId={`${project.id}.factor.${factors.indexOf(activeFactor)}`}
                value={factorSource}
                version={`${project.draft_source_sha256}:${activeFactor.id}`}
                baselineValue={savedFactorSource}
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
            <div className="factor-final-validation-intro"><div><FlaskConical size={16} /><span><strong>同一函数，两种检验</strong><small>最近截面与历史截面都直接调用当前冻结 revision 中的 @factor。</small></span></div><div className="flex items-end gap-2"><label className="text-[11px] text-muted-foreground">开始<Input className="h-8" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label className="text-[11px] text-muted-foreground">结束<Input className="h-8" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label><Button size="sm" variant="outline" disabled={busy || requiresFreeze || !activeFactor} onClick={() => void evaluate("snapshot")}><Play />最近截面</Button><Button size="sm" disabled={busy || requiresFreeze || !activeFactor} onClick={() => void evaluate("history")}><Play />历史检验</Button></div></div>
            <FactorResultPanels snapshot={snapshot} history={history} />
          </div></TabsContent>

          <TabsContent className="factor-workbench-content" value="python"><section className="factor-lab-panel embedded h-full">
            <header className="factor-panel-header"><span><strong><Braces size={14} /> 完整 canonical module</strong><small>高级编辑不会生成第二份因子定义</small></span><div className="flex items-center gap-2"><Badge variant={requiresFreeze ? "destructive" : "outline"}>{moduleDirty ? "未保存" : project.draft_source_sha256.slice(0, 16)}</Badge><Button size="sm" disabled={!project.editable || busy || !moduleDirty} onClick={() => void saveDraft()}><Save />保存完整源码</Button></div></header>
            <div className="factor-editor-body"><PythonEditor
              kind="strategy"
              documentId={project.id}
              value={source}
              version={project.draft_source_sha256}
              baselineValue={project.draft_source}
              disabled={!project.editable}
              height={620}
              revealLine={activeFactor?.line}
              fields={Object.entries(fields?.datasets ?? {}).flatMap(([dataset, items]) => items.map((item) => ({ name: item.name, dataset, detail: `${item.data_type}${item.nullable ? " · 可空" : ""}` })))}
              factors={factors.map((factor) => ({ id: factor.id, label: factor.label }))}
              parameters={activeFactor?.parameters ?? []}
              onChange={setSource}
              onSave={(nextSource) => saveDraft(nextSource)}
            /></div>
          </section></TabsContent>
        </Tabs>
      </div>
    </Widget>
  )
}
