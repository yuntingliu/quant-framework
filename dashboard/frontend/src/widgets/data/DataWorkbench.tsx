import { useEffect, useMemo, useState } from "react"
import { Braces, CheckCircle2, Database, Loader2, PlugZap, Save, ShieldCheck } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk } from "@/contexts/StrategySdkContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

interface FieldCatalog {
  profile: "demo" | "runtime"
  start_date: string
  end_date: string
  datasets: Record<string, Array<{ name: string; data_type: string; nullable: boolean }>>
}

interface RqTemplate {
  id: string
  label: string
  description: string
  market: string
  instrument_types: string[]
  datasets: string[]
  scope: string
}

interface TemplateCatalog {
  templates: RqTemplate[]
  python_sdk: { version: number; import: string; execution: string; uses_data_engine_contracts: boolean }
}

interface DataSyncHealth {
  runtime: { status: string; ready: number; total: number }
  rq: { status: string; installed: boolean; configured: boolean; ready: boolean; last_error?: string | null }
}

interface SyncPlan {
  template_id: string
  scope: string
  symbol_count: number | null
  requested_start: string
  requested_end: string
  estimated_batches: number | null
  steps: Array<{ dataset: string; mode: string }>
}

interface SyncJob {
  id: string
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted"
  progress: number
  total: number
  message?: string | null
  error?: string | null
  request: { template_id?: string }
}

export function DataWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const project = sdk.project
  const [source, setSource] = useState("")
  const [fields, setFields] = useState<FieldCatalog | null>(null)
  const [lookback, setLookback] = useState("260")
  const [maxWeight, setMaxWeight] = useState("1")
  const [maxGross, setMaxGross] = useState("1")
  const [portfolioValue, setPortfolioValue] = useState("1000000")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [templates, setTemplates] = useState<TemplateCatalog | null>(null)
  const [health, setHealth] = useState<DataSyncHealth | null>(null)
  const [selectedTemplate, setSelectedTemplate] = useState("rq.a_share_daily")
  const [syncStart, setSyncStart] = useState("")
  const [syncEnd, setSyncEnd] = useState("")
  const [syncSymbols, setSyncSymbols] = useState("")
  const [syncBusy, setSyncBusy] = useState(false)
  const [connection, setConnection] = useState("")
  const [plan, setPlan] = useState<SyncPlan | null>(null)
  const [jobs, setJobs] = useState<SyncJob[]>([])
  const universe = useMemo(
    () => project?.inspection.entrypoints.find((item) => item.kind === "universe"),
    [project?.inspection.entrypoints],
  )

  useEffect(() => {
    if (!project) return
    setSource(project.draft_source)
    setLookback(String(project.settings.lookback_days ?? 260))
    setMaxWeight(String(project.settings.max_weight ?? 1))
    setMaxGross(String(project.settings.max_gross_exposure ?? 1))
    setPortfolioValue(String(project.settings.portfolio_value ?? 1_000_000))
    void api.get<FieldCatalog>(`/strategy/fields?profile=${project.profile}`).then(setFields).catch((reason: Error) => setError(reason.message))
  }, [project?.id, project?.draft_source_sha256, project?.profile])

  useEffect(() => {
    let current = true
    void Promise.all([
      api.get<TemplateCatalog>("/data-sync/templates"),
      api.get<DataSyncHealth>("/data-sync/health"),
      api.get<SyncJob[]>("/data-sync/jobs?limit=5"),
    ]).then(([templateResult, healthResult, jobResult]) => {
      if (!current) return
      setTemplates(templateResult)
      setHealth(healthResult)
      setJobs(jobResult)
    }).catch((reason: Error) => { if (current) setError(reason.message) })
    return () => { current = false }
  }, [])

  useEffect(() => {
    if (!jobs.some((item) => item.status === "queued" || item.status === "running")) return
    const timer = window.setInterval(() => {
      void api.get<SyncJob[]>("/data-sync/jobs?limit=5").then(setJobs).catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [jobs])

  const activeTemplate = templates?.templates.find((item) => item.id === selectedTemplate)
  const latestJob = jobs[0]

  function syncRequest() {
    const symbols = syncSymbols.split(/[\s,]+/).map((value) => value.trim()).filter(Boolean)
    return {
      source: "rq",
      template_id: selectedTemplate,
      start: syncStart || undefined,
      end: syncEnd || undefined,
      symbols: symbols.length ? symbols : undefined,
      force: false,
    }
  }

  async function testRqConnection() {
    setSyncBusy(true); setError(""); setConnection("")
    try {
      const result = await api.post<{ latest_trading_date: string; rqdatac_version: string }>("/data-sync/connection-test", { template_id: selectedTemplate })
      setConnection(`已连接 · rqdatac ${result.rqdatac_version} · 最新交易日 ${result.latest_trading_date}`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setSyncBusy(false) }
  }

  async function previewSync() {
    setSyncBusy(true); setError("")
    try { setPlan(await api.post<SyncPlan>("/data-sync/plan", syncRequest())) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setSyncBusy(false) }
  }

  async function runSync() {
    if (!activeTemplate) return
    if (!await confirm({
      title: `同步 ${activeTemplate.label}`,
      description: `将通过 RQData 下载 ${activeTemplate.datasets.join("、")} 并写入本机 runtime 数据目录。`,
      confirmText: "开始同步",
    })) return
    setSyncBusy(true); setError("")
    try {
      const job = await api.post<SyncJob>("/data-sync/jobs", syncRequest())
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setSyncBusy(false) }
  }

  async function saveDataContract() {
    if (!project?.editable) return
    setBusy(true); setError("")
    try {
      if (source !== project.draft_source) await sdk.updateDraft(source)
      await sdk.updateMetadata({
        name: project.name,
        description: project.description,
        profile: project.profile,
        settings: {
          ...project.settings,
          lookback_days: Number(lookback),
          max_weight: Number(maxWeight),
          max_gross_exposure: Number(maxGross),
          portfolio_value: Number(portfolioValue),
        },
      })
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project) return <Widget title="数据工作台" loading={sdk.loading} error={sdk.error}><span /></Widget>
  return (
    <Widget title="数据工作台" error={error} bodyClassName="overflow-auto" actions={<Button size="sm" disabled={!project.editable || busy} onClick={() => void saveDataContract()}><Save />保存数据契约</Button>}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge><Database />{project.profile}</Badge>
          <Badge variant="outline">{fields?.start_date ?? "—"} → {fields?.end_date ?? "—"}</Badge>
          <Badge variant="secondary"><ShieldCheck />Point-in-time</Badge>
          <span className="text-muted-foreground">运行时不会自动切换数据环境，也不会向 Context 暴露 provider 或文件路径。</span>
        </div>
        <section className="rounded border border-border p-3">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
            <div><strong className="text-sm">RQData 数据模板</strong><p className="text-xs text-muted-foreground">模板只定义采集范围；数据仍进入统一 DataEngine 契约。</p></div>
            <div className="flex items-center gap-2 text-xs">
              <Badge variant={health?.rq.ready ? "secondary" : "outline"}>RQ {health?.rq.status ?? "检测中"}</Badge>
              <Badge variant="outline">runtime {health?.runtime.ready ?? 0}/{health?.runtime.total ?? 0}</Badge>
            </div>
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
            {templates?.templates.map((item) => (
              <button key={item.id} type="button" className={`rounded border p-3 text-left transition-colors ${selectedTemplate === item.id ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50"}`} onClick={() => { setSelectedTemplate(item.id); setPlan(null); setConnection("") }}>
                <span className="flex items-center justify-between gap-2 text-sm font-medium">{item.label}{selectedTemplate === item.id && <CheckCircle2 className="h-4 w-4 text-primary" />}</span>
                <span className="mt-1 block text-xs text-muted-foreground">{item.description}</span>
                <span className="mt-2 flex flex-wrap gap-1">{item.instrument_types.map((value) => <Badge key={value} variant="outline">{value}</Badge>)}</span>
              </button>
            ))}
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-[160px_160px_minmax(220px,1fr)_auto]">
            <label className="text-xs">开始日期（可空）<Input type="date" value={syncStart} onChange={(event) => setSyncStart(event.target.value)} /></label>
            <label className="text-xs">结束日期（可空）<Input type="date" value={syncEnd} onChange={(event) => setSyncEnd(event.target.value)} /></label>
            <label className="text-xs">标的（可空，逗号或换行分隔）<Input placeholder="空 = 模板全部标的" value={syncSymbols} onChange={(event) => setSyncSymbols(event.target.value)} /></label>
            <div className="flex items-end gap-2">
              <Button size="sm" variant="outline" disabled={syncBusy || !health?.rq.ready} onClick={() => void testRqConnection()}><PlugZap />连接测试</Button>
              <Button size="sm" variant="outline" disabled={syncBusy} onClick={() => void previewSync()}>预览</Button>
              <Button size="sm" disabled={syncBusy || !health?.rq.ready} onClick={() => void runSync()}>{syncBusy && <Loader2 className="animate-spin" />}同步</Button>
            </div>
          </div>
          {connection && <p className="mt-2 text-xs text-emerald-600">{connection}</p>}
          {plan && <div className="mt-2 rounded bg-muted p-2 text-xs"><strong>{plan.requested_start} → {plan.requested_end}</strong> · {plan.symbol_count ?? "运行时解析"} 个标的 · {plan.estimated_batches ?? "待解析"} 批 · {plan.steps.map((item) => `${item.dataset}(${item.mode})`).join(" → ")}</div>}
          {latestJob && <div className="mt-2 flex flex-wrap items-center gap-2 text-xs"><Badge variant="outline">{latestJob.status}</Badge><span>{latestJob.message ?? latestJob.error ?? latestJob.id}</span>{latestJob.total > 0 && <span className="text-muted-foreground">{latestJob.progress}/{latestJob.total}</span>}</div>}
        </section>
        <section className="flex flex-wrap items-center justify-between gap-3 rounded border border-border p-3">
          <div className="flex items-start gap-2"><Braces className="mt-0.5 h-4 w-4" /><div><strong className="text-sm">自定义 Python 数据源</strong><p className="text-xs text-muted-foreground">保留任意 Python 接入：实现 bars 与 instruments provider，再通过版本化 Data SDK 注册；与 RQ 模板使用同一 DataEngine。</p></div></div>
          <code className="rounded bg-muted px-2 py-1 text-xs">from {templates?.python_sdk.import ?? "alphalab.data_sdk.v1"} import data_source</code>
        </section>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section className="space-y-2">
            <div className="flex items-center justify-between"><strong className="text-sm">@universe Python</strong><code className="text-xs text-muted-foreground">{universe?.function ?? "missing"}</code></div>
            <Textarea className="min-h-[560px] resize-y font-mono text-xs leading-5" value={source} disabled={!project.editable} onChange={(event) => setSource(event.target.value)} />
            <p className="text-xs text-muted-foreground">这里编辑的是同一完整模块。Project/Data 关注 @universe 与 DATA_REQUIREMENTS；其他自定义代码会原样保留。</p>
          </section>
          <aside className="space-y-3">
            <div className="space-y-2 rounded border border-border p-3">
              <strong className="text-sm">核心不可绕过限制</strong>
              <label className="block text-xs">历史窗口<Input type="number" min={20} max={2000} value={lookback} onChange={(event) => setLookback(event.target.value)} /></label>
              <label className="block text-xs">单标的最大权重<Input type="number" min={0.01} max={1} step={0.01} value={maxWeight} onChange={(event) => setMaxWeight(event.target.value)} /></label>
              <label className="block text-xs">最大总敞口<Input type="number" min={0.01} max={1} step={0.01} value={maxGross} onChange={(event) => setMaxGross(event.target.value)} /></label>
              <label className="block text-xs">组合资金<Input type="number" min={1} value={portfolioValue} onChange={(event) => setPortfolioValue(event.target.value)} /></label>
            </div>
            {Object.entries(fields?.datasets ?? {}).map(([dataset, rows]) => (
              <div key={dataset} className="rounded border border-border p-3">
                <div className="mb-2 flex items-center justify-between text-sm"><strong>{dataset}</strong><Badge variant="outline">{rows.length} fields</Badge></div>
                <div className="flex flex-wrap gap-1">{rows.map((field) => <code key={field.name} className="rounded bg-muted px-1.5 py-1 text-[11px]">{field.name}: {field.data_type}</code>)}</div>
              </div>
            ))}
          </aside>
        </div>
      </div>
    </Widget>
  )
}
