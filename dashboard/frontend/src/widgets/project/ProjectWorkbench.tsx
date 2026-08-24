import { useEffect, useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Copy, Plus, RefreshCw, Save, Trash2 } from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import {
  api,
  type PipelineComponentSummary,
  type PipelineProjectDetail,
  type PipelineProjectSummary,
  type PythonPipelineStage,
} from "@/lib/api"
import { useDataProfile, type DataProfile } from "@/lib/data-profile"
import { Widget } from "@/widgets/Widget"

const STAGES: Array<{ id: PythonPipelineStage; label: string }> = [
  { id: "selection", label: "选股" },
  { id: "portfolio", label: "组合" },
  { id: "execution", label: "执行" },
]

function internalId() {
  return `project-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function jsonText(value: unknown) {
  return JSON.stringify(value, null, 2)
}

export function ProjectWorkbenchWidget() {
  const queryClient = useQueryClient()
  const {
    selectedStrategy,
    selectedDate,
    setActiveMode,
    setSelectedDate,
    setSelectedStrategy,
    setSelectedStrategyRevision,
  } = useWorkspace()
  const [profile, setProfile] = useDataProfile()
  const [projects, setProjects] = useState<PipelineProjectSummary[]>([])
  const [components, setComponents] = useState<PipelineComponentSummary[]>([])
  const [project, setProject] = useState<PipelineProjectDetail | null>(null)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [settings, setSettings] = useState("{}")
  const [dialogMode, setDialogMode] = useState<"new" | "copy" | null>(null)
  const [newName, setNewName] = useState("我的研究项目")
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  const componentNames = useMemo(
    () => new Map(components.map((item) => [item.id, item.name])),
    [components],
  )

  async function openProject(projectId: string) {
    const detail = await api.get<PipelineProjectDetail>(`/pipeline/projects/${projectId}`)
    setProject(detail)
    setName(detail.name)
    setDescription(detail.description)
    setSettings(jsonText(detail.settings))
    setSelectedStrategy(detail.id)
    setSelectedStrategyRevision(detail.revision)
  }

  async function refresh(preferred?: string) {
    const [projectRows, componentRows] = await Promise.all([
      api.get<PipelineProjectSummary[]>("/pipeline/projects"),
      api.get<PipelineComponentSummary[]>("/pipeline/components"),
    ])
    setProjects(projectRows)
    setComponents(componentRows)
    queryClient.setQueryData(["pipeline", "projects"], projectRows)
    const next = projectRows.find((item) => item.id === (preferred ?? selectedStrategy))
      ?? projectRows.find((item) => item.id === "three-stage-default")
      ?? projectRows[0]
    if (next) await openProject(next.id)
  }

  useEffect(() => {
    void refresh().catch((reason: Error) => setError(reason.message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function createProject() {
    if (!newName.trim()) return
    const sourceId = dialogMode === "copy"
      ? project?.id
      : projects.find((item) => item.id === "three-stage-default")?.id
    if (!sourceId) return
    setBusy(true)
    setError("")
    try {
      const created = await api.post<PipelineProjectDetail>(
        `/pipeline/projects/${sourceId}/clone`,
        { target_id: internalId(), name: newName.trim() },
      )
      await refresh(created.id)
      setDialogMode(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function saveProject() {
    if (!project?.editable || !name.trim()) return
    setBusy(true)
    setError("")
    try {
      const nextSettings = JSON.parse(settings) as Record<string, unknown>
      const saved = await api.put<PipelineProjectDetail>(`/pipeline/projects/${project.id}`, {
        name: name.trim(),
        description: description.trim(),
        components: project.components,
        settings: nextSettings,
      })
      setProject(saved)
      setSettings(jsonText(saved.settings))
      setSelectedStrategyRevision(saved.revision)
      await refresh(saved.id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function deleteProject() {
    if (!project?.editable) return
    setBusy(true)
    setError("")
    try {
      await api.delete<void>(`/pipeline/projects/${project.id}`)
      setDeleteOpen(false)
      setProject(null)
      setSelectedStrategy(null)
      setSelectedStrategyRevision(null)
      await refresh("three-stage-default")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Widget headerless>
      <div className="flex h-full min-h-0 bg-card">
        <aside className="flex w-72 shrink-0 flex-col border-r border-border bg-muted/10">
          <div className="border-b border-border p-3">
            <div className="flex items-center justify-between gap-2">
              <div><strong className="text-sm text-foreground">研究项目</strong><p className="mt-0.5 text-[11px] text-muted-foreground">选择后供所有阶段共同使用</p></div>
              <button className="icon-command" type="button" title="刷新项目" onClick={() => void refresh()} disabled={busy}><RefreshCw size={14} /></button>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button className="secondary-command" type="button" onClick={() => { setNewName("我的研究项目"); setDialogMode("new") }} disabled={busy}><Plus size={14} />新建</button>
              <button className="secondary-command" type="button" onClick={() => { setNewName(`${project?.name ?? "研究项目"} 副本`); setDialogMode("copy") }} disabled={busy || !project}><Copy size={14} />复制</button>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {projects.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`mb-1 w-full rounded border px-3 py-2 text-left ${project?.id === item.id ? "border-primary/35 bg-primary/10" : "border-transparent hover:border-border hover:bg-muted/60"}`}
                onClick={() => void openProject(item.id)}
              >
                <strong className="block truncate text-xs text-foreground">{item.name}</strong>
                {item.description ? <span className="mt-1 block line-clamp-2 text-[10px] leading-4 text-muted-foreground">{item.description}</span> : null}
              </button>
            ))}
          </div>
        </aside>

        <main className="min-h-0 min-w-0 flex-1 overflow-auto p-5">
          {error ? <div className="workbench-message error">{error}</div> : null}
          {!project ? <div className="analytics-empty">请选择或新建研究项目</div> : (
            <div className="mx-auto flex max-w-5xl flex-col gap-5">
              <section className="rounded border border-border bg-background p-4">
                <div className="flex items-start justify-between gap-4">
                  <div><h2 className="text-base font-semibold text-foreground">项目与数据</h2><p className="mt-1 text-xs text-muted-foreground">这些设置决定阶段预览使用哪个项目和哪一份数据截面。</p></div>
                  <div className="flex gap-2">
                    {project.editable ? <button className="secondary-command" type="button" onClick={() => setDeleteOpen(true)} disabled={busy}><Trash2 size={14} />删除</button> : null}
                    <button className="primary-command" type="button" onClick={() => void saveProject()} disabled={busy || !project.editable || !name.trim()}><Save size={14} />保存项目</button>
                  </div>
                </div>
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <label className="grid gap-1.5 text-xs text-muted-foreground"><span>项目名称</span><input className="h-9 rounded border border-border bg-card px-3 text-sm text-foreground outline-none focus:border-primary" value={name} onChange={(event) => setName(event.target.value)} readOnly={!project.editable} /></label>
                  <label className="grid gap-1.5 text-xs text-muted-foreground"><span>数据环境</span><select className="h-9 rounded border border-border bg-card px-3 text-sm text-foreground outline-none focus:border-primary" value={profile} onChange={(event) => setProfile(event.target.value as DataProfile)}><option value="demo">示例数据</option><option value="runtime">本地数据</option></select></label>
                  <label className="grid gap-1.5 text-xs text-muted-foreground md:col-span-2"><span>项目说明</span><textarea className="min-h-20 rounded border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary" value={description} onChange={(event) => setDescription(event.target.value)} readOnly={!project.editable} /></label>
                  <label className="grid gap-1.5 text-xs text-muted-foreground"><span>数据截至日</span><div className="flex gap-2"><input className="h-9 min-w-0 flex-1 rounded border border-border bg-card px-3 text-sm text-foreground outline-none focus:border-primary" type="date" value={selectedDate ?? ""} onChange={(event) => setSelectedDate(event.target.value || null)} /><button className="secondary-command" type="button" onClick={() => setSelectedDate(null)}>最新数据</button></div></label>
                  <div className="rounded border border-border bg-muted/20 p-3 text-xs text-muted-foreground"><strong className="block text-foreground">数据截至日只用于阶段预览</strong><span className="mt-1 block leading-5">留空时使用最新数据；历史区间由回测工作台单独设置。</span></div>
                </div>
              </section>

              <section className="rounded border border-border bg-background p-4">
                <div><h2 className="text-base font-semibold text-foreground">三阶段组件</h2><p className="mt-1 text-xs text-muted-foreground">点击阶段进入对应工作台研究 Python 组件。</p></div>
                <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {STAGES.map((stage, index) => {
                    const ref = project.components[stage.id]
                    return (
                      <button key={stage.id} type="button" className="rounded border border-border bg-card p-3 text-left hover:border-primary/40 hover:bg-primary/5" onClick={() => setActiveMode(stage.id)}>
                        <span className="text-[10px] text-muted-foreground">{index + 1}</span>
                        <strong className="mt-1 block text-sm text-foreground">{stage.label}</strong>
                        <span className="mt-1 block truncate text-[11px] text-muted-foreground">{componentNames.get(ref.component_id) ?? "正在读取组件…"}</span>
                      </button>
                    )
                  })}
                </div>
              </section>

              <details className="rounded border border-border bg-background p-4">
                <summary className="cursor-pointer text-sm font-semibold text-foreground">股票池与高级项目设置</summary>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">基础股票池、点时资格约束、因子输入和回看窗口属于项目输入，不再是可编程策略阶段。</p>
                <textarea className="code-view code-editor mt-3 min-h-72 w-full" spellCheck={false} value={settings} onChange={(event) => setSettings(event.target.value)} readOnly={!project.editable} />
              </details>
              {!project.editable ? <div className="workbench-message">当前项目只读；请点击“新建”或“复制”创建可编辑项目。</div> : null}
            </div>
          )}
        </main>

        <Dialog open={dialogMode !== null} onOpenChange={(open) => { if (!open) setDialogMode(null) }}>
          <DialogContent className="max-w-sm">
            <DialogHeader><DialogTitle>{dialogMode === "copy" ? "复制研究项目" : "新建研究项目"}</DialogTitle><DialogDescription>{dialogMode === "copy" ? "复制当前项目的三阶段组件与研究设置。" : "从默认三阶段结构创建一个可编辑项目。"}</DialogDescription></DialogHeader>
            <label className="grid gap-1.5 text-xs text-muted-foreground"><span>项目名称</span><input autoFocus className="h-9 rounded border border-border bg-background px-3 text-sm text-foreground outline-none focus:border-primary" value={newName} onChange={(event) => setNewName(event.target.value)} /></label>
            <DialogFooter><button className="secondary-command" type="button" onClick={() => setDialogMode(null)}>取消</button><button className="primary-command" type="button" onClick={() => void createProject()} disabled={busy || !newName.trim()}><Plus size={14} />创建</button></DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogContent className="max-w-sm">
            <DialogHeader><DialogTitle>删除研究项目</DialogTitle><DialogDescription>将删除“{project?.name}”。已保存的历史回测不会被删除。</DialogDescription></DialogHeader>
            <DialogFooter><button className="secondary-command" type="button" onClick={() => setDeleteOpen(false)}>取消</button><button className="primary-command" type="button" onClick={() => void deleteProject()} disabled={busy}>确认删除</button></DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </Widget>
  )
}
