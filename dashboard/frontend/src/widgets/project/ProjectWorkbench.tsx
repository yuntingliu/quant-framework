import { useEffect, useState } from "react"
import { Plus, Save, Trash2, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk } from "@/contexts/StrategySdkContext"
import { useConfirm } from "@/hooks/useConfirm"
import { Widget } from "@/widgets/Widget"

function internalId() {
  return `strategy-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export function ProjectWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState("")

  useEffect(() => {
    setName(sdk.project?.name ?? "")
    setDescription(sdk.project?.description ?? "")
  }, [sdk.project?.id, sdk.project?.name, sdk.project?.description])

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
  return (
    <Widget
      loading={sdk.loading}
      error={localError || sdk.error}
      onRetry={() => void sdk.refresh()}
      bodyClassName="overflow-auto"
      headerless
    >
      <div className="grid min-h-full gap-4 xl:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="space-y-3 rounded-md border border-border p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium">项目</div>
            <Button size="sm" variant="outline" disabled={busy || creating} onClick={() => setCreating(true)}><Plus />新建项目</Button>
          </div>
          <div className="space-y-1">
            {sdk.projects.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`w-full rounded border px-2 py-2 text-left ${item.id === project?.id ? "border-primary bg-primary/10" : "border-border hover:bg-muted"}`}
                onClick={() => void sdk.openProject(item.id)}
              >
                <span className="block truncate text-sm">{item.name}</span>
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
          <section className="space-y-4">
            <label className="block space-y-1 text-sm">
              <span>项目名称</span>
              <Input value={name} disabled={!project.editable} onChange={(event) => setName(event.target.value)} />
            </label>
            <label className="block space-y-1 text-sm">
              <span>说明</span>
              <Textarea value={description} disabled={!project.editable} onChange={(event) => setDescription(event.target.value)} />
            </label>
            <div className="flex flex-wrap gap-2">
              <Button disabled={!project.editable || busy} onClick={() => void saveMetadata()}><Save />保存项目资料</Button>
              <Button variant="destructive" disabled={!project.editable || busy} onClick={() => void removeProject()}><Trash2 />删除项目</Button>
            </div>
            {project.built_in ? <p className="text-xs text-muted-foreground">系统模板只读。新建项目后即可编辑。</p> : null}
          </section>
        ) : null}
      </div>
    </Widget>
  )
}
