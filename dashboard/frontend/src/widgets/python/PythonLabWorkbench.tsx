import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, CheckCircle2, FlaskConical, Play, RefreshCw, ShieldCheck } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import {
  api,
  type PipelineProjectDetail,
  type PythonLabCapabilities,
  type PythonLabRunDetail,
  type PythonLabRunSummary,
} from "@/lib/api"
import { useDataProfile } from "@/lib/data-profile"
import { Widget } from "@/widgets/Widget"


const DEFAULT_SOURCE = `def run(context):
    """Inspect the bounded snapshot and return JSON only.

    A candidate is not part of the strategy until it is explicitly promoted.
    """
    bars = context["market_bars"]
    closes = [row["close"] for row in bars if row.get("close") is not None]
    return {
        "summary": {
            "rows": len(bars),
            "symbols": context["dataset"]["symbols_returned"],
            "last_close": closes[-1] if closes else None,
        },
        "factor_candidate": {
            "name": "lab_momentum_blend",
            "description": "Python Lab proposed factor; review before promotion",
            "expression": "0.7 * zscore(momentum_20d) + 0.3 * zscore(momentum_60d)",
            "direction": "long",
            "winsorize": 0.01,
            "neutralize": [],
        },
    }
`

function jsonText(value: unknown) {
  return JSON.stringify(value, null, 2)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

export function PythonLabWorkbenchWidget() {
  const { selectedStrategy, selectedStrategyRevision, setSelectedStrategyRevision } = useWorkspace()
  const [profile] = useDataProfile()
  const [capabilities, setCapabilities] = useState<PythonLabCapabilities | null>(null)
  const [project, setProject] = useState<PipelineProjectDetail | null>(null)
  const [runs, setRuns] = useState<PythonLabRunSummary[]>([])
  const [selectedRun, setSelectedRun] = useState<PythonLabRunDetail | null>(null)
  const [source, setSource] = useState(DEFAULT_SOURCE)
  const [lookbackDays, setLookbackDays] = useState(120)
  const [symbols, setSymbols] = useState("")
  const [asOfDate, setAsOfDate] = useState("")
  const [targetId, setTargetId] = useState("")
  const [applyToProject, setApplyToProject] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  const refreshRuns = () => api.get<PythonLabRunSummary[]>("/python-lab/runs?limit=50")
    .then(setRuns)
    .catch((reason: Error) => setError(reason.message))

  useEffect(() => {
    api.get<PythonLabCapabilities>("/python-lab/capabilities")
      .then(setCapabilities)
      .catch((reason: Error) => setError(reason.message))
    refreshRuns()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selectedStrategy) {
      setProject(null)
      return
    }
    api.get<PipelineProjectDetail>(`/pipeline/projects/${selectedStrategy}`)
      .then((value) => {
        setProject(value)
        setSelectedStrategyRevision(value.revision)
      })
      .catch((reason: Error) => setError(reason.message))
  }, [selectedStrategy, selectedStrategyRevision, setSelectedStrategyRevision])

  const output = selectedRun?.output
  const candidates = useMemo(() => {
    if (!isRecord(output)) return { component: false, factor: false }
    return {
      component: isRecord(output.component_candidate),
      factor: isRecord(output.factor_candidate),
    }
  }, [output])
  const projectRuns = useMemo(
    () => runs.filter((item) => item.project_id === selectedStrategy),
    [runs, selectedStrategy],
  )

  const loadRun = async (runId: string) => {
    setError("")
    try {
      setSelectedRun(await api.get<PythonLabRunDetail>(`/python-lab/runs/${runId}`))
    } catch (reason) {
      setError((reason as Error).message)
    }
  }

  const execute = async () => {
    if (!project || !capabilities?.available) return
    const isolation = capabilities.isolated
      ? "该代码将在断网、只读、无宿主挂载的 Docker 沙箱运行。"
      : "当前是 trusted_local：代码可访问本机文件、网络和进程，不属于安全沙箱。"
    if (!window.confirm(`${isolation}\n\n确认执行这段任意 Python 代码？`)) return
    let confirmTrustedLocal = false
    if (capabilities.trusted_local) {
      confirmTrustedLocal = window.confirm("再次确认：你信任这段源码，并接受它以本机用户权限运行？")
      if (!confirmTrustedLocal) return
    }
    setBusy(true)
    setError("")
    try {
      const result = await api.post<PythonLabRunDetail>("/python-lab/runs", {
        project_id: project.id,
        profile,
        source,
        as_of_date: asOfDate || null,
        lookback_days: lookbackDays,
        symbols: symbols.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean) || null,
        max_rows: 50_000,
        confirm_python_execution: true,
        confirm_trusted_local: confirmTrustedLocal,
      })
      setSelectedRun(result)
      await refreshRuns()
    } catch (reason) {
      setError((reason as Error).message)
      await refreshRuns()
    } finally {
      setBusy(false)
    }
  }

  const promote = async (kind: "component" | "factor") => {
    if (!selectedRun || !targetId.trim()) return
    const action = applyToProject ? "创建对象并写入当前项目新版本" : "只创建对象"
    if (!window.confirm(`${action}。确认提升这个 ${kind} 候选？`)) return
    setBusy(true)
    setError("")
    try {
      const result = await api.post<Record<string, unknown>>(
        `/python-lab/runs/${selectedRun.id}/promote`,
        {
          kind,
          target_id: targetId.trim(),
          apply_to_project: applyToProject,
          confirm_write: true,
        },
      )
      const updated = result.project
      if (isRecord(updated) && typeof updated.revision === "number") {
        setSelectedStrategyRevision(updated.revision)
      }
      await loadRun(selectedRun.id)
      window.alert("候选已提升；后续回测仍使用现有唯一策略流水线。")
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Widget
      title="Python Lab"
      actions={<Badge variant="outline">非权威实验</Badge>}
      bodyPadding="none"
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-xs">
          {capabilities?.isolated ? <ShieldCheck className="h-4 w-4 text-emerald-500" /> : <AlertTriangle className="h-4 w-4 text-amber-500" />}
          <span className="font-medium">运行时：{capabilities?.runtime_kind ?? "读取中"}</span>
          <Badge variant="outline">{capabilities?.available ? "可执行" : "未启用"}</Badge>
          <span className="text-muted-foreground">
            {capabilities?.isolated
              ? "断网 · 只读 · 无宿主挂载 · 有资源上限"
              : capabilities?.reason ?? "本机可信模式不是安全沙箱"}
          </span>
        </div>
        {error && (
          <div className="flex items-start justify-between gap-3 border-b border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-300">
            <span>{error}</span>
            <button type="button" className="shrink-0 underline" onClick={() => setError("")}>关闭</button>
          </div>
        )}

        <Tabs defaultValue="lab" className="flex min-h-0 flex-1 flex-col">
          <TabsList className="mx-3 mt-2 w-fit">
            <TabsTrigger value="lab">实验代码</TabsTrigger>
            <TabsTrigger value="effective">最终有效策略</TabsTrigger>
            <TabsTrigger value="result">结果与提升</TabsTrigger>
            <TabsTrigger value="history">历史</TabsTrigger>
          </TabsList>

          <TabsContent value="lab" className="min-h-0 flex-1 overflow-auto px-3 pb-3">
            <div className="grid min-h-full gap-3 lg:grid-cols-[minmax(0,1fr)_260px]">
              <textarea
                className="min-h-[440px] w-full resize-y rounded border border-border bg-background p-3 font-mono text-xs leading-5 outline-none focus:border-primary"
                value={source}
                onChange={(event) => setSource(event.target.value)}
                spellCheck={false}
                aria-label="Python Lab source"
              />
              <div className="space-y-3 rounded border border-border p-3 text-xs">
                <div>
                  <div className="font-medium">有界研究上下文</div>
                  <p className="mt-1 text-muted-foreground">只注入项目快照和 OHLCV JSON；不注入数据目录、凭证或回测存储。</p>
                </div>
                <label className="block space-y-1">
                  <span>数据日期（留空取最新）</span>
                  <input className="w-full rounded border border-border bg-background px-2 py-1.5" type="date" value={asOfDate} onChange={(event) => setAsOfDate(event.target.value)} />
                </label>
                <label className="block space-y-1">
                  <span>每个标的回看交易日</span>
                  <input className="w-full rounded border border-border bg-background px-2 py-1.5" type="number" min={5} max={500} value={lookbackDays} onChange={(event) => setLookbackDays(Number(event.target.value))} />
                </label>
                <label className="block space-y-1">
                  <span>标的子集（可选）</span>
                  <textarea className="min-h-20 w-full rounded border border-border bg-background p-2 font-mono" value={symbols} onChange={(event) => setSymbols(event.target.value)} placeholder="510300, 513100" />
                </label>
                <Button className="w-full" disabled={busy || !project || !capabilities?.available} onClick={execute}>
                  <Play className="mr-1 h-4 w-4" />{busy ? "运行中…" : "确认并运行"}
                </Button>
                {!project && <p className="text-amber-500">请先选择研究项目。</p>}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="effective" className="min-h-0 flex-1 overflow-auto px-3 pb-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              <span>这里才是回测使用的权威源码</span>
              {project && <Badge variant="outline">revision {project.revision}</Badge>}
              {project && <code className="text-muted-foreground">{project.source_sha256.slice(0, 12)}</code>}
            </div>
            <pre className="min-h-[440px] overflow-auto whitespace-pre rounded border border-border bg-background p-3 font-mono text-xs leading-5">{project?.composed_source ?? "请选择项目"}</pre>
          </TabsContent>

          <TabsContent value="result" className="min-h-0 flex-1 overflow-auto px-3 pb-3">
            {!selectedRun ? (
              <div className="flex min-h-64 flex-col items-center justify-center text-sm text-muted-foreground"><FlaskConical className="mb-2 h-7 w-7" />运行实验或从历史中选择一条记录。</div>
            ) : (
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
                <div>
                  <div className="mb-2 flex items-center gap-2 text-xs"><Badge variant="outline">{selectedRun.status}</Badge><code>{selectedRun.id}</code></div>
                  {selectedRun.promotions.length > 0 && (
                    <div className="mb-2 flex flex-wrap gap-2 text-xs">
                      {selectedRun.promotions.map((item) => (
                        <Badge key={item.id} variant="outline">
                          已提升 {item.kind}: {item.target_id}{item.project_revision ? ` · revision ${item.project_revision}` : ""}
                        </Badge>
                      ))}
                    </div>
                  )}
                  <pre className="max-h-[560px] overflow-auto whitespace-pre-wrap rounded border border-border bg-background p-3 font-mono text-xs">{jsonText(selectedRun.output)}</pre>
                  {(selectedRun.stdout || selectedRun.stderr) && <details className="mt-2 text-xs"><summary>标准输出 / 错误</summary><pre className="mt-1 whitespace-pre-wrap rounded bg-muted p-2">{selectedRun.stdout}{selectedRun.stderr}</pre></details>}
                </div>
                <div className="space-y-3 rounded border border-border p-3 text-xs">
                  <div className="font-medium">提升到正式流水线</div>
                  <p className="text-muted-foreground">提升会重新验证候选。Lab 代码本身不会进入回测路径。</p>
                  <input className="w-full rounded border border-border bg-background px-2 py-1.5" value={targetId} onChange={(event) => setTargetId(event.target.value)} placeholder="新对象 ID / 因子名" />
                  <label className="flex items-center gap-2"><input type="checkbox" checked={applyToProject} onChange={(event) => setApplyToProject(event.target.checked)} />同时写入当前项目新版本</label>
                  <Button variant="outline" className="w-full" disabled={busy || !candidates.factor || !targetId.trim()} onClick={() => promote("factor")}>提升因子候选</Button>
                  <Button variant="outline" className="w-full" disabled={busy || !candidates.component || !targetId.trim()} onClick={() => promote("component")}>提升组件候选</Button>
                  {!candidates.factor && !candidates.component && <p className="text-muted-foreground">输出中没有可提升的候选对象。</p>}
                </div>
              </div>
            )}
          </TabsContent>

          <TabsContent value="history" className="min-h-0 flex-1 overflow-auto px-3 pb-3">
            <div className="mb-2 flex justify-end"><Button size="sm" variant="ghost" onClick={refreshRuns}><RefreshCw className="mr-1 h-3.5 w-3.5" />刷新</Button></div>
            <div className="space-y-1">
              {projectRuns.map((item) => (
                <button key={item.id} type="button" className="flex w-full items-center gap-3 rounded border border-border px-3 py-2 text-left text-xs hover:bg-muted/40" onClick={() => loadRun(item.id)}>
                  <Badge variant="outline">{item.status}</Badge><code className="min-w-0 flex-1 truncate">{item.id}</code><span>{item.runtime_kind}</span><span className="text-muted-foreground">{item.created_at}</span>
                </button>
              ))}
              {!projectRuns.length && <div className="py-10 text-center text-xs text-muted-foreground">当前项目暂无 Python Lab 记录</div>}
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </Widget>
  )
}
