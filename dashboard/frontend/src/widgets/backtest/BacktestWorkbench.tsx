import { useEffect, useState } from "react"
import { History, Play, RefreshCw, Save } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk, type SourcePackage } from "@/contexts/StrategySdkContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

interface BacktestJob {
  id: string
  status: "queued" | "running" | "succeeded" | "failed" | "interrupted"
  result_id: string | null
  result: Record<string, unknown> | null
  message: string
  error: string | null
}

interface BacktestSummary {
  id: string
  strategy_id: string
  start_date: string
  end_date: string
  total_return: number
  annual_return: number
  sharpe: number
  max_drawdown: number
  run_at: string
}

interface ProfileFields { start_date: string; end_date: string }

function percent(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—"
}

export function ValidationWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const { selectedBacktest, setSelectedBacktest } = useWorkspace()
  const project = sdk.project
  const [source, setSource] = useState("")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [revisions, setRevisions] = useState<SourcePackage[]>([])
  const [revision, setRevision] = useState<number | null>(null)
  const [jobs, setJobs] = useState<BacktestJob[]>([])
  const [runs, setRuns] = useState<BacktestSummary[]>([])
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    setSource(project?.draft_source ?? "")
    setRevision(project?.current_revision ?? null)
    if (!project) return
    void Promise.all([
      api.get<ProfileFields>(`/strategy/fields?profile=${project.profile}`),
      api.get<SourcePackage[]>(`/strategy/projects/${project.id}/revisions`),
      api.get<BacktestSummary[]>("/backtests?limit=50"),
      api.get<BacktestJob[]>("/backtests/jobs?limit=20"),
    ]).then(([profile, revisionRows, runRows, jobRows]) => {
      setStartDate(profile.start_date); setEndDate(profile.end_date)
      setRevisions(revisionRows); setRuns(runRows.filter((item) => item.strategy_id === project.id)); setJobs(jobRows)
    }).catch((reason: Error) => setError(reason.message))
  }, [project?.id, project?.draft_source_sha256, project?.profile])

  useEffect(() => {
    const active = jobs.some((item) => item.status === "queued" || item.status === "running")
    if (!active) return
    const timer = window.setInterval(() => {
      void api.get<BacktestJob[]>("/backtests/jobs?limit=20").then((rows) => {
        setJobs(rows)
        const completed = rows.find((item) => item.status === "succeeded" && item.result_id)
        if (completed?.result_id) {
          setSelectedBacktest(completed.result_id)
          void api.get<BacktestSummary[]>("/backtests?limit=50").then(setRuns)
        }
      })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [jobs, setSelectedBacktest])

  useEffect(() => {
    if (!selectedBacktest) return
    void api.get<Record<string, unknown>>(`/backtests/${selectedBacktest}`).then(setDetail).catch((reason: Error) => setError(reason.message))
  }, [selectedBacktest])

  async function runBacktest() {
    if (!project || !revision) return
    const sourcePackage = revisions.find((item) => item.revision === revision)
    if (!sourcePackage) return
    if (!await confirm({
      title: "运行完整事件回测",
      description: `运行 ${project.id}@${revision}（${sourcePackage.source_sha256.slice(0, 16)}），逐交易日执行状态、成交和账户逻辑。本机 Python 不是安全沙箱。`,
      confirmText: "运行回测",
    })) return
    setBusy(true); setError("")
    try {
      const job = await api.post<BacktestJob>("/backtests/jobs", {
        project_id: project.id,
        revision,
        profile: project.profile,
        start_date: startDate,
        end_date: endDate,
        confirm_python_execution: true,
      })
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function saveAsDraft() {
    if (!project?.editable) return
    setBusy(true)
    try { await sdk.updateDraft(source) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project) return <Widget title="验证与回测" loading={sdk.loading} error={sdk.error}><span /></Widget>
  const metrics = detail?.metrics as Record<string, unknown> | undefined
  return (
    <Widget title="验证与回测" error={error} bodyClassName="overflow-auto">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Badge>StrategySourcePackage</Badge><Badge variant="outline">{project.id}@{revision ?? project.current_revision}</Badge>
        {project.dirty ? <Badge variant="destructive">草稿与当前 revision 不同</Badge> : <Badge variant="secondary">源码已冻结</Badge>}
      </div>
      <Tabs defaultValue="run">
        <TabsList><TabsTrigger value="run">运行</TabsTrigger><TabsTrigger value="source">源码</TabsTrigger><TabsTrigger value="history">历史 Run</TabsTrigger></TabsList>
        <TabsContent value="run" className="space-y-4">
          <div className="grid gap-3 rounded border border-border p-3 md:grid-cols-4">
            <label className="text-xs">冻结修订
              <select className="mt-1 h-9 w-full rounded border border-input bg-background px-2" value={revision ?? ""} onChange={(event) => setRevision(Number(event.target.value))}>
                {revisions.map((item) => <option key={item.revision} value={item.revision}>r{item.revision} · {item.source_sha256.slice(0, 10)}</option>)}
              </select>
            </label>
            <label className="text-xs">开始日期<Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
            <label className="text-xs">结束日期<Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
            <div className="flex items-end"><Button className="w-full" disabled={busy || !revision} onClick={() => void runBacktest()}><Play />运行冻结源码</Button></div>
          </div>
          <div className="space-y-1">
            {jobs.filter((item) => item.status === "queued" || item.status === "running" || item.status === "failed").map((job) => (
              <div key={job.id} className="flex items-center justify-between rounded bg-muted px-3 py-2 text-xs"><span>{job.message || job.error}</span><Badge variant={job.status === "failed" ? "destructive" : "outline"}>{job.status}</Badge></div>
            ))}
          </div>
          {metrics ? (
            <div className="grid gap-2 sm:grid-cols-4">
              <div className="rounded border p-3"><small>总收益</small><div className="text-lg font-semibold">{percent(metrics.total_return)}</div></div>
              <div className="rounded border p-3"><small>年化收益</small><div className="text-lg font-semibold">{percent(metrics.annual_return)}</div></div>
              <div className="rounded border p-3"><small>夏普</small><div className="text-lg font-semibold">{String(metrics.sharpe ?? "—")}</div></div>
              <div className="rounded border p-3"><small>最大回撤</small><div className="text-lg font-semibold">{percent(metrics.max_drawdown)}</div></div>
            </div>
          ) : null}
          {detail ? <pre className="max-h-[480px] overflow-auto rounded bg-muted p-3 text-xs">{JSON.stringify(detail, null, 2)}</pre> : null}
        </TabsContent>
        <TabsContent value="source" className="space-y-2">
          <p className="text-xs text-muted-foreground">修改会形成项目草稿，不会改变已选 revision 或已有 Run。</p>
          <Textarea className="min-h-[620px] resize-y font-mono text-xs leading-5" value={source} disabled={!project.editable} onChange={(event) => setSource(event.target.value)} />
          <Button disabled={!project.editable || busy} onClick={() => void saveAsDraft()}><Save />保存为新草稿</Button>
        </TabsContent>
        <TabsContent value="history" className="space-y-2">
          {runs.filter((item) => item.strategy_id === project.id).map((run) => (
            <button key={run.id} className={`flex w-full items-center justify-between rounded border p-3 text-left ${selectedBacktest === run.id ? "border-primary" : "border-border"}`} onClick={() => setSelectedBacktest(run.id)}>
              <span className="flex items-center gap-2"><History />{run.start_date} → {run.end_date}</span>
              <span className="text-xs">{percent(run.total_return)} · Sharpe {run.sharpe?.toFixed?.(2) ?? "—"}</span>
            </button>
          ))}
          <Button variant="outline" onClick={() => void api.get<BacktestSummary[]>("/backtests?limit=50").then(setRuns)}><RefreshCw />刷新</Button>
        </TabsContent>
      </Tabs>
    </Widget>
  )
}
