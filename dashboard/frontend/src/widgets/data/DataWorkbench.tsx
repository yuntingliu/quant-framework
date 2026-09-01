import { useEffect, useMemo, useState } from "react"
import {
  Braces,
  CheckCircle2,
  ExternalLink,
  Loader2,
  PlugZap,
  Save,
  Trash2,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PythonEditor } from "@/components/python"
import { useStrategySdk } from "@/contexts/StrategySdkContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

interface RecipeParameter {
  name: string
  default: unknown
  editable: boolean
  custom_source: string | null
}

interface RecipeInspection {
  valid: boolean
  recipe_id: string
  function: string
  label: string | null
  source_sha256: string
  parameters: RecipeParameter[]
  template_id: string | null
  matched_template_id: string | null
  warnings: Array<{ line: number; code: string; message: string }>
}

interface RecipeDraft {
  project_id: string
  source: string
  source_sha256: string
  selected_template_id?: string | null
  updated_at: string
  inspection: RecipeInspection
}

interface RecipeTemplate {
  id: string
  label: string
  description: string
  kind: "built_in" | "custom"
  instrument_types?: string[]
  datasets?: string[]
  source_sha256?: string
}

interface RecipeWorkspace {
  draft: RecipeDraft
  templates: RecipeTemplate[]
  bounds: { start: string; end: string }
  docs_url: string
  execution: "trusted_local_python"
}

interface DataSyncHealth {
  rq: { status: string; installed: boolean; configured: boolean; ready: boolean; last_error?: string | null }
}

interface SyncJob {
  id: string
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted"
  progress: number
  total: number
  message?: string | null
  error?: string | null
  request: { project_id?: string; template_id?: string; kind?: string }
}

export function DataWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const project = sdk.project
  const [workspace, setWorkspace] = useState<RecipeWorkspace | null>(null)
  const [source, setSource] = useState("")
  const [syncStart, setSyncStart] = useState("")
  const [syncEnd, setSyncEnd] = useState("")
  const [syncSymbols, setSyncSymbols] = useState("")
  const [health, setHealth] = useState<DataSyncHealth | null>(null)
  const [jobs, setJobs] = useState<SyncJob[]>([])
  const [connection, setConnection] = useState("")
  const [customName, setCustomName] = useState("")
  const [customDescription, setCustomDescription] = useState("")
  const [showCustomSave, setShowCustomSave] = useState(false)
  const [switchingTemplateId, setSwitchingTemplateId] = useState<string | null>(null)
  const [parameterSaving, setParameterSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const projectId = project?.id

  useEffect(() => {
    if (!projectId) return
    let current = true
    setWorkspace(null)
    setError("")
    void Promise.all([
      api.get<RecipeWorkspace>(`/data-sync/recipes/${projectId}`),
      api.get<DataSyncHealth>("/data-sync/health"),
      api.get<SyncJob[]>("/data-sync/jobs?limit=20"),
    ]).then(([recipeResult, healthResult, jobResult]) => {
      if (!current) return
      setWorkspace(recipeResult)
      setSource(recipeResult.draft.source)
      const parameters = new Map(
        recipeResult.draft.inspection.parameters.map((item) => [item.name, item.default]),
      )
      setSyncStart(String(parameters.get("start") ?? recipeResult.bounds.start ?? ""))
      setSyncEnd(String(parameters.get("end") ?? recipeResult.bounds.end ?? ""))
      const symbols = parameters.get("symbols")
      setSyncSymbols(Array.isArray(symbols) ? symbols.join(", ") : "")
      setHealth(healthResult)
      setJobs(jobResult)
    }).catch((reason: Error) => { if (current) setError(reason.message) })
    return () => { current = false }
  }, [projectId])

  useEffect(() => {
    if (!jobs.some((item) => item.status === "queued" || item.status === "running")) return
    const timer = window.setInterval(() => {
      void api.get<SyncJob[]>("/data-sync/jobs?limit=20").then(setJobs).catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [jobs])

  const selectedTemplate = useMemo(() => {
    if (!workspace) return null
    const explicit = workspace.draft.selected_template_id
    if (explicit && workspace.templates.some((item) => item.id === explicit)) return explicit
    const matched = workspace.draft.inspection.matched_template_id
    if (matched) return matched
    const custom = workspace.templates.find((item) => (
      item.kind === "custom" && item.source_sha256 === workspace.draft.source_sha256
    ))?.id
    if (custom) return custom
    const declared = workspace.draft.inspection.template_id
    return workspace.templates.some((item) => item.id === declared) ? declared : null
  }, [workspace])
  const dirty = Boolean(workspace && source !== workspace.draft.source)
  const selectedTemplateItem = workspace?.templates.find((item) => item.id === selectedTemplate)
  const draftMatchesSelectedTemplate = selectedTemplateItem?.kind === "built_in"
    ? workspace?.draft.inspection.matched_template_id === selectedTemplateItem.id
    : selectedTemplateItem?.source_sha256 === workspace?.draft.source_sha256
  const saveAsRequired = selectedTemplateItem?.kind === "built_in"
    && (dirty || !draftMatchesSelectedTemplate)
  const editableParameters = new Set(
    workspace?.draft.inspection.parameters.filter((item) => item.editable).map((item) => item.name),
  )
  const activeJob = jobs.some((item) => item.status === "queued" || item.status === "running")
  const latestJob = jobs.find((item) => item.request.project_id === project?.id)

  function adoptRecipeDraft(draft: RecipeDraft, parent = workspace) {
    if (parent) setWorkspace({ ...parent, draft })
    setSource(draft.source)
    const parameters = new Map(draft.inspection.parameters.map((item) => [item.name, item.default]))
    setSyncStart(String(parameters.get("start") ?? parent?.bounds.start ?? ""))
    setSyncEnd(String(parameters.get("end") ?? parent?.bounds.end ?? ""))
    const symbols = parameters.get("symbols")
    setSyncSymbols(Array.isArray(symbols) ? symbols.join(", ") : "")
  }

  async function persistSource(nextSource = source): Promise<RecipeDraft> {
    if (!project || !workspace) throw new Error("数据配方尚未加载")
    if (nextSource === workspace.draft.source) return workspace.draft
    const draft = await api.put<RecipeDraft>(`/data-sync/recipes/${project.id}`, {
      source: nextSource,
      expected_source_sha256: workspace.draft.source_sha256,
      confirm_write: true,
    })
    adoptRecipeDraft(draft)
    return draft
  }

  async function saveSource(nextSource = source) {
    setBusy(true); setError("")
    try { await persistSource(nextSource) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function selectTemplate(item: RecipeTemplate) {
    if (!project || !workspace) return
    const reloadSelectedTemplate = item.id === selectedTemplate && !draftMatchesSelectedTemplate
    if (item.id === selectedTemplate && !dirty && !reloadSelectedTemplate) return
    if ((dirty || reloadSelectedTemplate) && !await confirm({
      title: `切换到${item.label}`,
      description: "当前 Python 修改会被模板中保存的源码替换。",
      confirmText: "替换源码",
      tone: "danger",
    })) return
    setSwitchingTemplateId(item.id); setBusy(true); setError(""); setConnection("")
    try {
      const draft = await api.post<RecipeDraft>(
        `/data-sync/recipes/${project.id}/templates/${encodeURIComponent(item.id)}`,
        { expected_source_sha256: workspace.draft.source_sha256, confirm_write: true },
      )
      adoptRecipeDraft(draft)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setSwitchingTemplateId(null); setBusy(false) }
  }

  function changeSyncStart(value: string) {
    if (!workspace) return
    const next = clampDate(value, workspace.bounds.start, syncEnd || workspace.bounds.end)
    const nextEnd = syncEnd && next > syncEnd ? next : syncEnd
    setSyncStart(next)
    if (nextEnd !== syncEnd) setSyncEnd(nextEnd)
    void writeParameters(next, nextEnd, syncSymbols)
  }

  function changeSyncEnd(value: string) {
    if (!workspace) return
    const next = clampDate(value, syncStart || workspace.bounds.start, workspace.bounds.end)
    const nextStart = syncStart && next < syncStart ? next : syncStart
    setSyncEnd(next)
    if (nextStart !== syncStart) setSyncStart(nextStart)
    void writeParameters(nextStart, next, syncSymbols)
  }

  async function writeParameters(start = syncStart, end = syncEnd, symbolText = syncSymbols) {
    if (!project || !workspace || busy || dirty) return
    const symbols = splitSymbols(symbolText)
    if (recipeParametersEqual(workspace.draft.inspection, start, end, symbols)) return
    setParameterSaving(true); setBusy(true); setError("")
    try {
      const draft = await api.patch<RecipeDraft>(`/data-sync/recipes/${project.id}/parameters`, {
        start,
        end,
        symbols: symbols.length ? symbols : null,
        expected_source_sha256: workspace.draft.source_sha256,
        confirm_write: true,
      })
      adoptRecipeDraft(draft)
    } catch (reason) {
      adoptRecipeDraft(workspace.draft)
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setParameterSaving(false)
      setBusy(false)
    }
  }

  async function saveCustomTemplate() {
    if (!project || !customName.trim()) return
    setBusy(true); setError("")
    try {
      const draft = await persistSource()
      await api.post(`/data-sync/recipes/${project.id}/templates`, {
        name: customName.trim(),
        description: customDescription.trim(),
        expected_source_sha256: draft.source_sha256,
        confirm_save: true,
      })
      const refreshed = await api.get<RecipeWorkspace>(`/data-sync/recipes/${project.id}`)
      setWorkspace(refreshed)
      adoptRecipeDraft(refreshed.draft, refreshed)
      setCustomName(""); setCustomDescription(""); setShowCustomSave(false)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  function openCustomSave() {
    if (!customName.trim()) {
      setCustomName(`${selectedTemplateItem?.label ?? "数据配方"} 自定义`)
    }
    setShowCustomSave(true)
  }

  async function deleteCustomTemplate(item: RecipeTemplate) {
    if (!await confirm({
      title: `删除模板「${item.label}」`,
      description: "只删除模板副本，当前 Python 配方不会改变。",
      confirmText: "删除模板",
      tone: "danger",
    })) return
    setBusy(true); setError("")
    try {
      await api.delete(`/data-sync/recipe-templates/${encodeURIComponent(item.id)}?confirm_delete=true`)
      if (workspace) setWorkspace({
        ...workspace,
        templates: workspace.templates.filter((candidate) => candidate.id !== item.id),
      })
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function testRqConnection() {
    if (!workspace) return
    setBusy(true); setError(""); setConnection("")
    try {
      const templateId = workspace.draft.inspection.template_id ?? "rq.a_share_research"
      const result = await api.post<{ latest_trading_date: string; rqdatac_version: string }>(
        "/data-sync/connection-test",
        { template_id: templateId },
      )
      setConnection(`已连接 · rqdatac ${result.rqdatac_version} · 最新交易日 ${result.latest_trading_date}`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function runRecipe() {
    if (!project || !workspace) return
    if (!await confirm({
      title: "运行当前 Python 数据配方",
      description: "将在本机 Python 中执行编辑器里的源码，并把结果写入统一研究数据仓库。",
      confirmText: "运行并同步",
    })) return
    setBusy(true); setError("")
    try {
      await persistSource()
      const job = await api.post<SyncJob>(`/data-sync/recipes/${project.id}/jobs`, {
        confirm_python_execution: true,
      })
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project || !workspace) {
    return <Widget headerless loading={sdk.loading || Boolean(project && !error)} error={error || sdk.error}><span /></Widget>
  }

  return (
    <Widget headerless error={error} bodyClassName="overflow-auto">
      <div className="space-y-4">
        <section className="rounded border border-border p-3">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div>
              <strong className="text-sm">数据模板</strong>
            </div>
            <a className="inline-flex items-center gap-1 text-xs text-primary hover:underline" href={workspace.docs_url} target="_blank" rel="noreferrer">
              RQData Python API 文档 <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
            {workspace.templates.map((item) => (
              <div key={item.id} className={`relative rounded border transition-colors ${selectedTemplate === item.id ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50"}`}>
                <button type="button" className="h-full w-full p-3 text-left" disabled={busy} onClick={() => void selectTemplate(item)}>
                  <span className="flex items-center justify-between gap-2 pr-5 text-sm font-medium">
                    {item.label}
                    {switchingTemplateId === item.id
                      ? <Loader2 className="h-4 w-4 animate-spin text-primary" />
                      : selectedTemplate === item.id && <CheckCircle2 className="h-4 w-4 text-primary" />}
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">{item.description || "保存的自定义 Python 数据配方"}</span>
                  <span className="mt-2 flex flex-wrap gap-1">
                    {item.kind === "custom" ? <Badge variant="outline">自定义</Badge> : item.instrument_types?.map((value) => <Badge key={value} variant="outline">{value}</Badge>)}
                  </span>
                </button>
                {item.kind === "custom" && (
                  <Button className="absolute right-1 top-1 h-7 w-7" size="icon" variant="ghost" disabled={busy} onClick={() => void deleteCustomTemplate(item)} aria-label={`删除 ${item.label}`}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="rounded border border-border p-3">
          <div className="mb-3 flex items-center gap-2">
            <strong className="text-sm">常用参数</strong>
            {parameterSaving && <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在更新 Python</span>}
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-[170px_170px_minmax(240px,1fr)]">
            <label className="text-xs">开始日期<Input type="date" min={workspace.bounds.start} max={syncEnd || workspace.bounds.end} value={syncStart} disabled={busy || dirty || !editableParameters.has("start")} onChange={(event) => changeSyncStart(event.target.value)} /></label>
            <label className="text-xs">结束日期<Input type="date" min={syncStart || workspace.bounds.start} max={workspace.bounds.end} value={syncEnd} disabled={busy || dirty || !editableParameters.has("end")} onChange={(event) => changeSyncEnd(event.target.value)} /></label>
            <label className="text-xs">标的（可空，逗号或换行分隔）<Input placeholder="空 = 模板全部标的" value={syncSymbols} disabled={busy || dirty || !editableParameters.has("symbols")} onChange={(event) => setSyncSymbols(event.target.value)} onBlur={() => void writeParameters()} onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur() }} /></label>
          </div>
        </section>

        <section className="space-y-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2"><Braces className="h-4 w-4" /><strong className="text-sm">Python 数据配方</strong>{dirty ? <Badge variant="outline">未保存</Badge> : <Badge variant="outline">已保存</Badge>}</div>
            </div>
            <Button size="sm" variant="outline" disabled={busy || (!dirty && !saveAsRequired)} onClick={() => { if (saveAsRequired) openCustomSave(); else void saveSource() }}><Save />{saveAsRequired ? "另存为模板" : "保存代码"}</Button>
          </div>
          {showCustomSave && (
            <div className="grid gap-2 rounded border border-border p-3 md:grid-cols-[220px_minmax(260px,1fr)_auto]">
              <Input placeholder="模板名称" value={customName} onChange={(event) => setCustomName(event.target.value)} />
              <Input placeholder="模板说明（可选）" value={customDescription} onChange={(event) => setCustomDescription(event.target.value)} />
              <Button size="sm" disabled={busy || !customName.trim()} onClick={() => void saveCustomTemplate()}>保存模板</Button>
            </div>
          )}
          <PythonEditor
            kind="data"
            documentId={project.id}
            value={source}
            version={workspace.draft.source_sha256}
            disabled={busy}
            height={560}
            onChange={setSource}
            onSave={(nextSource) => saveSource(nextSource)}
          />
        </section>

        <section className="rounded border border-border p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" disabled={busy || !health?.rq.ready} onClick={() => void testRqConnection()}><PlugZap />连接测试</Button>
            <Button size="sm" disabled={busy || activeJob} onClick={() => void runRecipe()}>{busy && <Loader2 className="animate-spin" />}运行并同步</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => { if (showCustomSave) setShowCustomSave(false); else openCustomSave() }}>另存为模板</Button>
          </div>
          {!health?.rq.ready && <p className="mt-2 text-xs text-amber-600">RQData 尚未就绪；仍可编辑和预览不访问 RQData 的自定义代码。</p>}
          {connection && <p className="mt-2 text-xs text-emerald-600">{connection}</p>}
          {latestJob && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              <Badge variant="outline">{latestJob.status}</Badge>
              <span>{latestJob.error ?? latestJob.message ?? latestJob.id}</span>
              {latestJob.total > 0 && <span className="text-muted-foreground">{latestJob.progress}/{latestJob.total}</span>}
            </div>
          )}
        </section>
      </div>
    </Widget>
  )
}

function clampDate(value: string, minimum: string, maximum: string): string {
  if (!value) return minimum
  if (value < minimum) return minimum
  if (value > maximum) return maximum
  return value
}

function splitSymbols(value: string): string[] {
  return value.split(/[\s,]+/).map((symbol) => symbol.trim()).filter(Boolean)
}

function recipeParametersEqual(
  inspection: RecipeInspection,
  start: string,
  end: string,
  symbols: string[],
): boolean {
  const parameters = new Map(inspection.parameters.map((item) => [item.name, item]))
  const sameStart = !parameters.get("start")?.editable || parameters.get("start")?.default === start
  const sameEnd = !parameters.get("end")?.editable || parameters.get("end")?.default === end
  const currentSymbols = parameters.get("symbols")?.default
  const normalizedCurrent = Array.isArray(currentSymbols) ? currentSymbols.map(String) : []
  const sameSymbols = !parameters.get("symbols")?.editable
    || (normalizedCurrent.length === symbols.length
      && normalizedCurrent.every((symbol, index) => symbol === symbols[index]))
  return sameStart && sameEnd && sameSymbols
}
