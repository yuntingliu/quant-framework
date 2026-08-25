import { useEffect, useState } from "react"
import { Copy, RefreshCw, Save, Trash2 } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk } from "@/contexts/StrategySdkContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { useConfirm } from "@/hooks/useConfirm"
import { Widget } from "@/widgets/Widget"

function internalId() {
  return `strategy-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export function ProjectWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const { setActiveMode } = useWorkspace()
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [newName, setNewName] = useState("我的策略")
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState("")

  useEffect(() => {
    setName(sdk.project?.name ?? "")
    setDescription(sdk.project?.description ?? "")
  }, [sdk.project?.id, sdk.project?.name, sdk.project?.description])

  async function cloneProject() {
    if (!sdk.project || !newName.trim()) return
    if (!await confirm({
      title: "创建可编辑策略",
      description: "将当前完整 SDK 源码复制为新的 revision 1，并运行一次受限合同探针。",
      confirmText: "创建并验证",
    })) return
    setBusy(true)
    setLocalError("")
    try {
      await sdk.cloneProject(internalId(), newName.trim())
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
      description: "历史 BacktestRun 不会删除，但这个项目的草稿和源码修订将被移除。",
      requireText: sdk.project.id,
      confirmText: "删除",
      tone: "danger",
    })) return
    setBusy(true)
    try { await sdk.removeProject() }
    finally { setBusy(false) }
  }

  const project = sdk.project
  return (
    <Widget
      title="研究项目"
      loading={sdk.loading}
      error={localError || sdk.error}
      onRetry={() => void sdk.refresh()}
      actions={<Button variant="ghost" size="sm" onClick={() => void sdk.refresh(project?.id)}><RefreshCw />刷新</Button>}
      bodyClassName="overflow-auto"
    >
      <div className="grid min-h-full gap-4 xl:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="space-y-3 rounded-md border border-border p-3">
          <div>
            <div className="text-sm font-medium">策略项目</div>
            <div className="text-xs text-muted-foreground">每个项目只有一份 Python 草稿和一条不可变修订链。</div>
          </div>
          <div className="space-y-1">
            {sdk.projects.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`w-full rounded border px-2 py-2 text-left ${item.id === project?.id ? "border-primary bg-primary/10" : "border-border hover:bg-muted"}`}
                onClick={() => void sdk.openProject(item.id)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm">{item.name}</span>
                  <Badge variant={item.built_in ? "secondary" : "outline"}>r{item.current_revision}</Badge>
                </div>
                <div className="mt-1 truncate text-xs text-muted-foreground">{item.id}</div>
              </button>
            ))}
          </div>
          <div className="space-y-2 border-t border-border pt-3">
            <Input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="新策略名称" />
            <Button className="w-full" variant="outline" disabled={!project || busy} onClick={() => void cloneProject()}>
              <Copy />复制为可编辑项目
            </Button>
          </div>
        </aside>

        {project ? (
          <section className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge>SDK v1</Badge>
              <Badge variant="outline">revision {project.current_revision}</Badge>
              <Badge variant={project.dirty ? "destructive" : "secondary"}>{project.dirty ? "草稿未冻结" : "草稿已冻结"}</Badge>
              <span className="font-mono text-xs text-muted-foreground">{project.draft_source_sha256.slice(0, 16)}</span>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="space-y-1 text-sm">
                <span>项目名称</span>
                <Input value={name} disabled={!project.editable} onChange={(event) => setName(event.target.value)} />
              </label>
              <label className="space-y-1 text-sm">
                <span>数据环境</span>
                <select
                  className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                  value={project.profile}
                  disabled={!project.editable}
                  onChange={(event) => void sdk.updateMetadata({
                    name,
                    description,
                    profile: event.target.value as "demo" | "runtime",
                    settings: project.settings,
                  })}
                >
                  <option value="demo">Demo</option>
                  <option value="runtime">Runtime</option>
                </select>
              </label>
            </div>
            <label className="block space-y-1 text-sm">
              <span>说明</span>
              <Textarea value={description} disabled={!project.editable} onChange={(event) => setDescription(event.target.value)} />
            </label>
            <div className="rounded-md border border-border p-3 text-sm">
              <div className="font-medium">统一源码边界</div>
              <div className="mt-2 grid gap-2 sm:grid-cols-3">
                <button className="rounded bg-muted p-3 text-left hover:bg-muted/70" onClick={() => setActiveMode("factor")}>
                  <strong>因子</strong><br /><span className="text-xs text-muted-foreground">编辑 @factor 函数</span>
                </button>
                <button className="rounded bg-muted p-3 text-left hover:bg-muted/70" onClick={() => setActiveMode("strategy")}>
                  <strong>策略</strong><br /><span className="text-xs text-muted-foreground">编辑信号、组合、事件和执行</span>
                </button>
                <button className="rounded bg-muted p-3 text-left hover:bg-muted/70" onClick={() => setActiveMode("validation")}>
                  <strong>验证</strong><br /><span className="text-xs text-muted-foreground">运行冻结 revision</span>
                </button>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button disabled={!project.editable || busy} onClick={() => void saveMetadata()}><Save />保存项目资料</Button>
              <Button variant="destructive" disabled={!project.editable || busy} onClick={() => void removeProject()}><Trash2 />删除项目</Button>
            </div>
            {project.built_in ? <p className="text-xs text-muted-foreground">系统模板只读。复制后即可编辑完整 Python、参数和调度。</p> : null}
          </section>
        ) : null}
      </div>
    </Widget>
  )
}
