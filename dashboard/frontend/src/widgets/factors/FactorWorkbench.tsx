import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Braces, Check, FlaskConical, Library, Play, Plus, Save, Search, Settings2 } from "lucide-react"

import { MarketResearchTerminal, type MarketRange, useMarketWatchlist } from "@/components/market"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk, type SdkEntrypoint, type SdkParameter } from "@/contexts/StrategySdkContext"
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

function factorSnippet(id = "new_factor") {
  return `\n\n@factor(id="${id}", label="自定义因子", inputs=["close"])
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

function parameterValue(parameter: SdkParameter, raw: string): unknown {
  if (typeof parameter.default === "boolean") return raw === "true"
  if (typeof parameter.default === "number") return Number(raw)
  if (parameter.default === null && raw === "None") return null
  return raw
}

function FactorParameterEditor({ factor, parameter }: { factor: SdkEntrypoint; parameter: SdkParameter }) {
  const sdk = useStrategySdk()
  const [value, setValue] = useState(String(parameter.default))
  const [busy, setBusy] = useState(false)
  useEffect(() => setValue(String(parameter.default)), [parameter.default])

  async function apply() {
    setBusy(true)
    try {
      await sdk.structuredEdit({
        operation: "parameter",
        entrypoint_id: factor.id,
        parameter: parameter.name,
        value: parameterValue(parameter, value),
      })
    } finally { setBusy(false) }
  }

  return (
    <label className="factor-form-field">
      <span>{parameter.label || parameter.name}{parameter.label ? <code>{parameter.name}</code> : null}</span>
      <div className="factor-parameter-control">
        {typeof parameter.default === "boolean" ? (
          <select value={value} onChange={(event) => setValue(event.target.value)}>
            <option value="true">启用</option><option value="false">关闭</option>
          </select>
        ) : (
          <input
            type={typeof parameter.default === "number" ? "number" : "text"}
            min={parameter.minimum ?? undefined}
            max={parameter.maximum ?? undefined}
            step={parameter.step ?? undefined}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        )}
        <button className="secondary-command" type="button" disabled={busy || !parameter.editable || !sdk.project?.editable} onClick={() => void apply()}>应用</button>
      </div>
      {parameter.description ? <small>{parameter.description}</small> : null}
      {parameter.minimum !== null || parameter.maximum !== null ? (
        <small>范围 {parameter.minimum ?? "−∞"} ～ {parameter.maximum ?? "+∞"}{parameter.step ? ` · 步长 ${parameter.step}` : ""}</small>
      ) : null}
    </label>
  )
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
              <div><strong>当前数据全部字段</strong><small>RQData Schema 自动生成；点击即插入 Python</small></div>
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
  const editor = useRef<HTMLTextAreaElement>(null)
  const [workspaceView, setWorkspaceView] = useState<"build" | "results" | "python">("build")
  const [source, setSource] = useState("")
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
    setSource(project?.draft_source ?? "")
    setSnapshot(null); setHistory(null)
    if (!project) return
    void api.get<FieldCatalog>(`/strategy/fields?profile=${project.profile}`).then((value) => {
      setFields(value); setStartDate(value.start_date); setEndDate(value.end_date)
    }).catch((reason: Error) => setError(reason.message))
  }, [project?.id, project?.draft_source_sha256, project?.profile])
  useEffect(() => { if (activeFactor && activeFactor.id !== selectedFactor) setSelectedFactor(activeFactor.id) }, [activeFactor, selectedFactor])

  function insert(text: string) {
    const target = editor.current
    const position = target?.selectionStart ?? source.length
    setSource(`${source.slice(0, position)}${text}${source.slice(position)}`)
    requestAnimationFrame(() => { target?.focus(); target?.setSelectionRange(position + text.length, position + text.length) })
  }
  function insertFactorStatement(text: string) {
    if (!activeFactor) return
    const target = editor.current
    if (target && document.activeElement === target) { insert(text); return }
    const functionStart = source.indexOf(`def ${activeFactor.function}(`)
    const nextDecorator = source.indexOf("\n@", functionStart)
    const functionEnd = nextDecorator >= 0 ? nextDecorator : source.length
    const returnPattern = /\n([ \t]+)return\b/g
    let match: RegExpExecArray | null = null
    let returnIndex = -1
    let indentation = "    "
    while ((match = returnPattern.exec(source)) !== null) {
      if (match.index > functionStart && match.index < functionEnd) { returnIndex = match.index + 1; indentation = match[1] || "    " }
    }
    if (returnIndex >= 0) setSource(`${source.slice(0, returnIndex)}${indentation}${text}\n${source.slice(returnIndex)}`)
  }
  function insertField(field: string, dataset: string) {
    const windowValue = activeFactor?.parameters.some((parameter) => parameter.name === "window") ? "window" : "20"
    insertFactorStatement(dataset === "fundamentals" ? `${field} = context.fundamental("${field}")` : `${field} = context.history("${field}", window=${windowValue})`)
    setWorkspaceView("python")
  }
  function insertFactorDependency(factor: SdkEntrypoint) {
    insertFactorStatement(`${factor.id} = context.factor("${factor.id}")`); setWorkspaceView("python")
  }
  function addFactor() {
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
      setSelectedFactor(template.id)
      await sdk.openProject(project.id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setInstallingTemplate("") }
  }
  async function saveDraft() {
    if (!project?.editable) return
    setBusy(true); setError("")
    try { await sdk.updateDraft(source) } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy(false) }
  }
  async function evaluate(mode: "snapshot" | "history") {
    if (!project || project.dirty || source !== project.draft_source || !activeFactor) return
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
  const localDirty = source !== project.draft_source
  const requiresFreeze = project.dirty || localDirty
  return (
    <Widget headerless>
      <div className="factor-workbench-shell">
        <FactorMarketBrowser fields={fields} onInsertField={insertField} />
        <Tabs className="factor-workbench-tabs" value={workspaceView} onValueChange={(value) => setWorkspaceView(value as "build" | "results" | "python")}>
          <div className="factor-workbench-toolbar">
            <TabsList><TabsTrigger value="build">因子库与参数</TabsTrigger><TabsTrigger value="results">最终验证{requiresFreeze ? <span className="factor-tab-warning">草稿未冻结</span> : null}</TabsTrigger><TabsTrigger value="python">高级 Python</TabsTrigger></TabsList>
            <div className="factor-workbench-actions">
              <Badge variant="outline">r{project.current_revision}</Badge>
              <span className={requiresFreeze ? "factor-build-hint" : "factor-validation-ready"}>{localDirty ? "源码尚未保存" : project.dirty ? "参数或源码已变化" : "当前源码可复现"}</span>
              <Button size="sm" disabled={!project.editable || busy || !localDirty} onClick={() => void saveDraft()}><Save />保存草稿</Button>
            </div>
          </div>
          {error ? <div className="workbench-message error factor-workbench-error">{error}</div> : null}

          <TabsContent className="factor-workbench-content" value="build"><div className="factor-build-grid">
            <section className="factor-lab-panel embedded">
              <header className="factor-panel-header"><span><strong><Library size={14} /> 因子库</strong><small>内置模板加入后即成为当前模块中的 @factor Python</small></span><Button size="sm" variant="outline" disabled={!project.editable} onClick={addFactor}><Plus />新因子</Button></header>
              <div className="factor-template-controls"><label><Search size={12} /><input value={templateQuery} onChange={(event) => setTemplateQuery(event.target.value)} placeholder="搜索 16 个内置因子" /></label><div>{(["all", "technical", "fundamental"] as const).map((category) => <button type="button" key={category} className={templateCategory === category ? "active" : ""} onClick={() => setTemplateCategory(category)}>{category === "all" ? "全部" : category === "technical" ? "技术" : "基本面"}</button>)}</div></div>
              <div className="factor-panel-scroll">
                <div className="factor-section-heading"><span>项目因子</span><strong>{factors.length}</strong></div>
                <div className="factor-project-list">{factors.map((factor) => <button type="button" key={factor.id} className={activeFactor?.id === factor.id ? "active" : ""} onClick={() => setSelectedFactor(factor.id)}><span><strong>{factor.label || factor.id}</strong><small>@factor · {factor.function}</small></span><Badge variant="outline">{factor.parameters.length} 参数</Badge></button>)}</div>
                <div className="factor-section-heading mt-4"><span>可用 Python 模板</span><strong>{templates.length}</strong></div>
                {templatesQuery.isLoading ? <div className="analytics-empty">正在读取因子模板…</div> : null}
                {templatesQuery.error instanceof Error ? <div className="workbench-message error">{templatesQuery.error.message}</div> : null}
                <div className="factor-template-list">{templates.map((template) => {
                  const installed = factors.some((factor) => factor.id === template.id)
                  return <article key={template.id}><div><span><strong>{template.label}</strong><small>{template.description}</small></span><em>{template.category === "technical" ? "技术" : "基本面"} · {template.recommended_direction === "higher" ? "高值优先" : "低值优先"}</em></div><footer><code>{template.id}</code><span>{template.inputs.join(" · ")}</span><button className={installed ? "installed" : ""} type="button" disabled={installed || !project.editable || localDirty || Boolean(installingTemplate)} onClick={() => void installTemplate(template)}>{installed ? <Check size={11} /> : <Plus size={11} />}{installed ? "已加入" : installingTemplate === template.id ? "加入中" : "加入项目"}</button></footer></article>
                })}</div>
                <div className="factor-section-heading mt-4">可复用因子依赖</div>
                <div className="factor-catalog-list">{factors.filter((factor) => factor.id !== activeFactor?.id).map((factor) => <button type="button" key={factor.id} onClick={() => insertFactorDependency(factor)}><span><strong>{factor.label || factor.id}</strong><small>插入 context.factor("{factor.id}")</small></span><Plus size={13} /></button>)}</div>
              </div>
            </section>
            <section className="factor-lab-panel embedded">
              <header className="factor-panel-header"><span><strong><Settings2 size={14} /> 无代码参数</strong><small>控件一对一修改当前因子的 Python 默认值</small></span>{activeFactor ? <Badge>@factor {activeFactor.id}</Badge> : null}</header>
              <div className="factor-panel-scroll">{activeFactor ? <>
                <div className="factor-definition-summary"><div><span>公开 ID</span><strong>{activeFactor.id}</strong></div><div><span>Python 函数</span><strong>{activeFactor.function}</strong></div><div><span>输入字段</span><strong>{(activeFactor.metadata.inputs as string[] | undefined)?.join(", ") || "源码自定义"}</strong></div></div>
                <div className="factor-form-grid mt-4">{activeFactor.parameters.length ? activeFactor.parameters.map((parameter) => <FactorParameterEditor key={parameter.name} factor={activeFactor} parameter={parameter} />) : <div className="analytics-empty">这个因子没有可投影参数；可在高级 Python 中自由编辑。</div>}</div>
                <div className="mt-4 rounded border border-border bg-muted/20 p-3 text-xs text-muted-foreground"><strong className="text-foreground">字段与依赖仍是 Python</strong><p className="mt-1">点击上方行情终端中的字段，或左侧其他因子，会把 <code>context.history()</code>、<code>context.fundamental()</code>、<code>context.factor()</code> 插入当前函数。</p></div>
              </> : <div className="analytics-empty">当前模块没有 @factor</div>}</div>
            </section>
          </div></TabsContent>

          <TabsContent className="factor-workbench-content" value="results"><div className="factor-final-validation">
            <div className="factor-final-validation-intro"><div><FlaskConical size={16} /><span><strong>同一函数，两种检验</strong><small>最近截面与历史截面都直接调用当前冻结 revision 中的 @factor。</small></span></div><div className="flex items-end gap-2"><label className="text-[11px] text-muted-foreground">开始<Input className="h-8" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label className="text-[11px] text-muted-foreground">结束<Input className="h-8" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label><Button size="sm" variant="outline" disabled={busy || requiresFreeze || !activeFactor} onClick={() => void evaluate("snapshot")}><Play />最近截面</Button><Button size="sm" disabled={busy || requiresFreeze || !activeFactor} onClick={() => void evaluate("history")}><Play />历史检验</Button></div></div>
            <FactorResultPanels snapshot={snapshot} history={history} />
          </div></TabsContent>

          <TabsContent className="factor-workbench-content" value="python"><section className="factor-lab-panel embedded h-full">
            <header className="factor-panel-header"><span><strong><Braces size={14} /> 完整 canonical module</strong><small>高级编辑不会生成第二份因子定义</small></span><Badge variant={requiresFreeze ? "destructive" : "outline"}>{localDirty ? "未保存" : project.draft_source_sha256.slice(0, 16)}</Badge></header>
            <div className="factor-editor-body"><Textarea ref={editor} className="min-h-[620px] resize-y rounded-none border-0 font-mono text-xs leading-5 focus-visible:ring-0" spellCheck={false} value={source} disabled={!project.editable} onChange={(event) => setSource(event.target.value)} /></div>
          </section></TabsContent>
        </Tabs>
      </div>
    </Widget>
  )
}
