import { useEffect, useMemo, useState, type ComponentType } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowRight,
  BarChart3,
  CircleDashed,
  Clock3,
  Code2,
  Database,
  FileChartColumn,
  FlaskConical,
  Layers3,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk, type SdkEntrypoint } from "@/contexts/StrategySdkContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api, type BacktestRecord, type ProviderStatus } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { WorkspaceMode } from "@/layouts/presets"
import { Widget } from "@/widgets/Widget"

function internalId() {
  return `strategy-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function formatDate(value: string, includeTime = true) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value || "—"
  return date.toLocaleString("zh-CN", includeTime
    ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit" })
}

function formatPercent(value: number | "") {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—"
}

function formatNumber(value: number | "") {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—"
}

function switchMode(mode: WorkspaceMode) {
  window.dispatchEvent(new CustomEvent("alphalab:switchMode", { detail: mode }))
}

interface OverviewMetricProps {
  icon: ComponentType<{ className?: string }>
  label: string
  value: string
  tone?: "default" | "success" | "warning"
}

function OverviewMetric({ icon: Icon, label, value, tone = "default" }: OverviewMetricProps) {
  return (
    <div className="rounded-xl border border-border bg-card/70 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <span className={cn(
          "flex h-8 w-8 items-center justify-center rounded-lg border",
          tone === "success" && "border-emerald-500/25 bg-emerald-500/10 text-emerald-500",
          tone === "warning" && "border-amber-500/25 bg-amber-500/10 text-amber-500",
          tone === "default" && "border-primary/20 bg-primary/10 text-primary",
        )}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <div className="mt-2 text-xl font-semibold tracking-tight text-foreground">{value}</div>
    </div>
  )
}

interface WorkflowStepProps {
  icon: ComponentType<{ className?: string }>
  title: string
  status: string
  mode: WorkspaceMode
  ready?: boolean
}

function WorkflowStep({ icon: Icon, title, status, mode, ready = false }: WorkflowStepProps) {
  return (
    <button
      type="button"
      className="group flex min-w-0 items-start gap-3 rounded-lg border border-border bg-background/60 p-3 text-left transition-colors hover:border-primary/35 hover:bg-primary/[0.04]"
      onClick={() => switchMode(mode)}
    >
      <span className={cn(
        "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border",
        ready
          ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-500"
          : "border-border bg-muted text-muted-foreground",
      )}>
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold text-foreground">{title}</span>
          <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
        </span>
        <span className={cn("mt-0.5 block text-[11px] font-medium", ready ? "text-emerald-500" : "text-muted-foreground")}>{status}</span>
      </span>
    </button>
  )
}

function EntrypointRow({ label, items }: { label: string; items: SdkEntrypoint[] }) {
  return (
    <div className="grid gap-2 border-b border-border/70 py-3 last:border-b-0 sm:grid-cols-[92px_minmax(0,1fr)]">
      <span className="text-[11px] font-medium text-muted-foreground">{label}</span>
      <div className="flex min-w-0 flex-wrap gap-1.5">
        {items.length > 0 ? items.map((item) => (
          <span key={`${item.kind}-${item.id}`} className="max-w-full truncate rounded-md border border-border bg-muted/45 px-2 py-0.5 font-mono text-[10px] text-foreground" title={item.id}>
            {item.label || item.id}
          </span>
        )) : <span className="text-[11px] text-muted-foreground/70">未配置</span>}
      </div>
    </div>
  )
}

export function ProjectWorkbenchWidget() {
  const sdk = useStrategySdk()
  const workspace = useWorkspace()
  const confirm = useConfirm()
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState("")
  const [runs, setRuns] = useState<BacktestRecord[]>([])
  const [runsLoading, setRunsLoading] = useState(false)
  const [runsError, setRunsError] = useState("")
  const [runsRefreshKey, setRunsRefreshKey] = useState(0)
  const dataStatus = useQuery({
    queryKey: ["project-data-status"],
    queryFn: () => api.get<ProviderStatus>("/data/providers"),
    refetchInterval: 15000,
  })
  const hasBars = dataStatus.data?.datasets["rq.bars"]?.status === "ready"
  const dataLabel = dataStatus.isError ? "数据状态读取失败"
    : !dataStatus.data ? "正在检查数据"
      : hasBars ? "已有行情，需检查研究范围" : "尚未同步行情"

  useEffect(() => {
    setName(sdk.project?.name ?? "")
    setDescription(sdk.project?.description ?? "")
  }, [sdk.project?.id, sdk.project?.name, sdk.project?.description])

  const projectId = sdk.project?.id
  useEffect(() => {
    let current = true
    if (!projectId) {
      setRuns([])
      setRunsLoading(false)
      return () => { current = false }
    }
    setRuns([])
    setRunsLoading(true)
    setRunsError("")
    void api.get<BacktestRecord[]>("/backtests?limit=100")
      .then((rows) => {
        if (current) setRuns(rows.filter((item) => item.strategy_id === projectId))
      })
      .catch((reason: unknown) => {
        if (current) setRunsError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => {
        if (current) setRunsLoading(false)
      })
    return () => { current = false }
  }, [projectId, runsRefreshKey])

  async function createProject() {
    if (!newName.trim()) return
    setBusy(true)
    setLocalError("")
    try {
      await sdk.createProject(internalId(), newName.trim())
      setCreating(false)
      setNewName("")
    } catch (reason) {
      setLocalError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function saveMetadata() {
    if (!sdk.project?.editable || !name.trim()) return
    setBusy(true)
    setLocalError("")
    try {
      await sdk.updateMetadata({
        name: name.trim(),
        description: description.trim(),
        profile: sdk.project.profile,
        settings: sdk.project.settings,
      })
    } catch (reason) {
      setLocalError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function removeProject() {
    if (!sdk.project?.editable) return
    if (!await confirm({
      title: "删除策略项目",
      description: `历史回测不会删除，但“${sdk.project.name}”的策略代码将被移除。请输入项目名称确认。`,
      requireText: sdk.project.name,
      confirmText: "删除",
      tone: "danger",
    })) return
    setBusy(true)
    try { await sdk.removeProject() }
    finally { setBusy(false) }
  }

  const project = sdk.project
  const entrypoints = useMemo(
    () => project?.inspection.entrypoints ?? [],
    [project?.inspection.entrypoints],
  )
  const byKind = useMemo(() => Object.fromEntries(
    (["universe", "factor", "schedule", "signal", "portfolio", "event", "execution"] as const)
      .map((kind) => [kind, entrypoints.filter((item) => item.kind === kind)]),
  ) as Record<SdkEntrypoint["kind"], SdkEntrypoint[]>, [entrypoints])
  const latestRun = runs[0]
  const metadataDirty = Boolean(project && (
    name.trim() !== project.name || description.trim() !== project.description
  ))

  return (
    <Widget
      loading={sdk.loading}
      error={localError || sdk.error}
      onRetry={() => { setLocalError(""); void sdk.refresh() }}
      bodyClassName="overflow-auto"
      headerless
    >
      <div className="grid min-h-full gap-4 p-1 xl:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="h-fit space-y-3 rounded-xl border border-border bg-card/60 p-3 xl:sticky xl:top-1">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-foreground">研究项目</div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">{sdk.projects.length} 个项目</div>
            </div>
            <Button size="sm" variant="outline" disabled={busy || creating} onClick={() => setCreating(true)}><Plus />新建</Button>
          </div>
          <div className="space-y-1.5">
            {sdk.projects.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-current={item.id === project?.id ? "page" : undefined}
                className={cn(
                  "w-full rounded-lg border px-3 py-2.5 text-left transition-colors",
                  item.id === project?.id
                    ? "border-primary/40 bg-primary/10"
                    : "border-transparent bg-muted/25 hover:border-border hover:bg-muted/60",
                )}
                onClick={() => void sdk.openProject(item.id)}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="block min-w-0 flex-1 truncate text-xs font-medium text-foreground">{item.name}</span>
                  {item.built_in ? <span className="shrink-0 text-[9px] text-muted-foreground">模板</span> : null}
                </span>
                <span className="mt-1 flex items-center gap-1.5 text-[9px] text-muted-foreground">
                  <span className="truncate">{formatDate(item.updated_at, false)}</span>
                </span>
              </button>
            ))}
          </div>
          {creating ? (
            <div className="space-y-2 border-t border-border pt-3">
              <label className="space-y-1 text-xs"><span>项目名称</span><Input autoFocus value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="例如：ETF 月度轮动" onKeyDown={(event) => { if (event.key === "Enter") void createProject() }} /></label>
              <div className="flex gap-2">
                <Button className="flex-1" size="sm" disabled={!newName.trim() || busy} onClick={() => void createProject()}><Plus />创建</Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setCreating(false); setNewName("") }}><X />取消</Button>
              </div>
            </div>
          ) : null}
        </aside>

        {project ? (
          <main className="min-w-0 space-y-4 pb-2">
            <section className="relative overflow-hidden rounded-xl border border-border bg-gradient-to-br from-primary/[0.10] via-card to-card p-5 shadow-sm">
              <div className="pointer-events-none absolute -right-12 -top-16 h-44 w-44 rounded-full bg-primary/10 blur-3xl" />
              <div className="relative flex flex-wrap items-start gap-4">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-primary/25 bg-primary/10 text-primary">
                  <FlaskConical className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h1 className="min-w-0 truncate text-lg font-semibold tracking-tight text-foreground">{project.name}</h1>
                    <Badge variant={project.inspection.valid ? "success" : "warning"}>{project.inspection.valid ? "源码检查通过" : "源码需要检查"}</Badge>
                    {project.dirty ? <Badge variant="warning">有未保存修改</Badge> : <Badge variant="outline">已保存</Badge>}
                    {project.built_in ? <Badge variant="secondary">系统模板</Badge> : null}
                  </div>
                  {project.description ? <p className="mt-2 max-w-3xl text-xs leading-5 text-muted-foreground">{project.description}</p> : null}
                  <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
                    <span className="flex items-center gap-1"><Clock3 className="h-3 w-3" />更新于 {formatDate(project.updated_at)}</span>
                  </div>
                </div>
              </div>
            </section>

            <section className="grid gap-3 md:grid-cols-3" aria-label="项目摘要">
              <OverviewMetric
                icon={project.inspection.valid ? ShieldCheck : CircleDashed}
                label="源码契约"
                value={project.inspection.valid ? "源码检查通过" : "需要检查"}
                tone={project.inspection.valid ? "success" : "warning"}
              />
              <OverviewMetric
                icon={Layers3}
                label="研究因子"
                value={`${byKind.factor.length} 个`}
              />
              <OverviewMetric
                icon={BarChart3}
                label="最近回测"
                value={latestRun ? `Sharpe ${formatNumber(latestRun.sharpe)}` : "暂无结果"}
              />
            </section>

            <section className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card/60 p-4" aria-label="研究数据状态">
              <Database className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{dataLabel}</div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {!project.editable ? "系统模板只读，请先新建研究项目。" : ""}
                  源码检查只验证代码契约。因子评估和回测还需要覆盖所选标的、日期、预热区间和字段的研究数据，请先到数据工作台检查并同步。
                </p>
              </div>
              <Button size="sm" variant="outline" onClick={() => project.editable ? switchMode("data") : setCreating(true)}>
                {project.editable ? "检查研究数据" : "新建研究项目"} <ArrowRight />
              </Button>
            </section>

            <section className="rounded-xl border border-border bg-card/60 p-4 shadow-sm">
              <h2 className="mb-3 text-sm font-semibold text-foreground">研究路径</h2>
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                <WorkflowStep icon={Database} title="数据" status={dataLabel} mode="data" />
                <WorkflowStep icon={Layers3} title="因子" status={byKind.factor.length > 0 ? `${byKind.factor.length} 个因子` : "待添加因子"} mode="factor" ready={byKind.factor.length > 0} />
                <WorkflowStep icon={Code2} title="策略" status={project.inspection.valid && !project.dirty ? "已保存，源码检查通过" : project.dirty ? "存在未保存修改" : "需要检查"} mode="strategy" ready={project.inspection.valid && !project.dirty} />
                <WorkflowStep icon={BarChart3} title="验证" status={runs.length > 0 ? `${runs.length} 次历史 Run` : "尚未运行回测"} mode="validation" ready={runs.length > 0} />
                <WorkflowStep icon={FileChartColumn} title="报告" status="查看研究成果" mode="report" />
              </div>
            </section>

            <div className="grid gap-4 2xl:grid-cols-[minmax(0,1.1fr)_minmax(380px,0.9fr)]">
              <section className="rounded-xl border border-border bg-card/60 p-4 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <h2 className="text-sm font-semibold text-foreground">策略结构</h2>
                  <Button size="sm" variant="outline" onClick={() => switchMode("strategy")}>打开策略 <ArrowRight /></Button>
                </div>
                <div className="mt-3 rounded-lg border border-border bg-background/50 px-3">
                  <EntrypointRow label="股票池" items={byKind.universe} />
                  <EntrypointRow label="因子" items={byKind.factor} />
                  <EntrypointRow label="信号与调度" items={[...byKind.signal, ...byKind.schedule]} />
                  <EntrypointRow label="组合" items={byKind.portfolio} />
                  <EntrypointRow label="事件与执行" items={[...byKind.event, ...byKind.execution]} />
                </div>
              </section>

              <section className="rounded-xl border border-border bg-card/60 p-4 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <h2 className="text-sm font-semibold text-foreground">最近回测</h2>
                  <Button size="sm" variant="ghost" disabled={runsLoading} onClick={() => setRunsRefreshKey((value) => value + 1)} title="刷新回测记录">
                    <RefreshCw className={cn(runsLoading && "animate-spin")} />刷新
                  </Button>
                </div>
                {runsError ? (
                  <div className="mt-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">回测记录加载失败：{runsError}</div>
                ) : runsLoading && runs.length === 0 ? (
                  <div className="mt-3 space-y-2">
                    {[0, 1, 2].map((item) => <div key={item} className="skeleton h-14 rounded-lg" />)}
                  </div>
                ) : runs.length > 0 ? (
                  <div className="mt-3 space-y-2">
                    {runs.slice(0, 3).map((run) => (
                      <button
                        key={run.id}
                        type="button"
                        className="group grid w-full gap-2 rounded-lg border border-border bg-background/50 px-3 py-2.5 text-left transition-colors hover:border-primary/35 hover:bg-primary/[0.04] sm:grid-cols-[minmax(0,1fr)_auto]"
                        onClick={() => { workspace.setSelectedBacktest(run.id); switchMode("validation") }}
                      >
                        <span className="min-w-0">
                          <span className="flex items-center gap-2 text-xs font-medium text-foreground">
                            <span className="truncate">{run.start_date} → {run.end_date}</span>
                            <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground group-hover:text-primary" />
                          </span>
                          <span className="mt-1 block text-[10px] text-muted-foreground">{formatDate(run.run_at)}</span>
                        </span>
                        <span className="flex items-center gap-3 text-right text-[10px]">
                          <span><span className="block text-muted-foreground">年化</span><strong className="font-mono font-medium text-foreground">{formatPercent(run.annual_return)}</strong></span>
                          <span><span className="block text-muted-foreground">Sharpe</span><strong className="font-mono font-medium text-foreground">{formatNumber(run.sharpe)}</strong></span>
                          <span><span className="block text-muted-foreground">回撤</span><strong className="font-mono font-medium text-foreground">{formatPercent(run.max_drawdown)}</strong></span>
                        </span>
                      </button>
                    ))}
                    <Button className="w-full" size="sm" variant="outline" onClick={() => switchMode("validation")}>查看全部回测 <ArrowRight /></Button>
                  </div>
                ) : (
                  <div className="mt-3 flex min-h-44 flex-col items-center justify-center rounded-lg border border-dashed border-border bg-background/35 p-5 text-center">
                    <BarChart3 className="h-7 w-7 text-primary/65" />
                    <div className="mt-2 text-xs font-medium text-foreground">还没有回测结果</div>
                    <Button className="mt-3" size="sm" onClick={() => switchMode("validation")}>开始验证 <ArrowRight /></Button>
                  </div>
                )}
              </section>
            </div>

            <section className="rounded-xl border border-border bg-card/60 p-4 shadow-sm">
              <h2 className="mb-4 text-sm font-semibold text-foreground">项目资料</h2>
              <div className="grid gap-4 lg:grid-cols-[minmax(220px,0.4fr)_minmax(0,1fr)]">
                <label className="block space-y-1.5 text-xs font-medium text-foreground">
                  <span>项目名称</span>
                  <Input value={name} disabled={!project.editable} onChange={(event) => setName(event.target.value)} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium text-foreground">
                  <span>研究说明</span>
                  <Textarea className="min-h-24 resize-y" value={description} disabled={!project.editable} onChange={(event) => setDescription(event.target.value)} placeholder="说明研究目标、核心假设、适用市场与主要风险…" />
                </label>
              </div>
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
                <div className="text-[10px] text-muted-foreground">
                  {project.built_in ? "系统模板只读。新建项目后即可编辑。" : metadataDirty ? "项目资料有尚未保存的修改。" : "项目资料已保存。"}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="destructive" size="sm" disabled={!project.editable || busy} onClick={() => void removeProject()}><Trash2 />删除项目</Button>
                  <Button size="sm" disabled={!project.editable || busy || !metadataDirty || !name.trim()} onClick={() => void saveMetadata()}><Save />保存项目资料</Button>
                </div>
              </div>
            </section>
          </main>
        ) : (
          <div className="flex min-h-96 items-center justify-center rounded-xl border border-dashed border-border bg-card/50 p-6 text-center">
            <div className="max-w-xs">
              <FlaskConical className="mx-auto h-9 w-9 text-primary/65" />
              <div className="mt-3 text-sm font-semibold text-foreground">创建一个研究项目</div>
              <Button className="mt-4" size="sm" onClick={() => setCreating(true)}><Plus />新建项目</Button>
            </div>
          </div>
        )}
      </div>
    </Widget>
  )
}
