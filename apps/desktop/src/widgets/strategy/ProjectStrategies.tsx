import { useEffect, useRef, useState } from "react"
import { Copy, FileCode2, MoreHorizontal, Plus, Pencil } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { useStrategySdk } from "@/contexts/StrategySdkContext"

export function ProjectStrategies({ locked, onError, onBusy }: {
  locked: boolean
  onError: (message: string) => void
  onBusy: (busy: boolean) => void
}) {
  const sdk = useStrategySdk()
  const project = sdk.project
  const [action, setAction] = useState<"new" | "copy" | "rename" | null>(null)
  const [name, setName] = useState("")
  const [id, setId] = useState("")
  const [formError, setFormError] = useState("")
  const [saving, setSaving] = useState(false)
  const selectedTab = useRef<HTMLButtonElement>(null)
  useEffect(() => { selectedTab.current?.scrollIntoView({ block: "nearest", inline: "nearest" }) }, [project?.strategy_id])
  if (!project) return null
  const selected = project.strategies.find((item) => item.id === project.strategy_id)
  const disabled = locked || sdk.loading

  function begin(next: "new" | "copy" | "rename") {
    let index = 2
    while (project!.strategies.some((item) => item.id === `strategy_${index}`)) index++
    setAction(next)
    setFormError("")
    setId(`strategy_${index}`)
    setName(next === "rename" ? selected?.name ?? "" : next === "copy" ? `${selected?.name ?? "策略"} 副本` : `策略 ${index}`)
  }

  async function apply() {
    if (!action || !name.trim() || disabled || saving) return
    setSaving(true); onBusy(true); setFormError("")
    try {
      if (action === "rename") await sdk.renameStrategy(name.trim())
      else await sdk.createStrategy(id.trim(), name.trim(), action === "copy" ? project!.strategy_id : "main")
      setAction(null)
    } catch (reason) { setFormError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setSaving(false); onBusy(false) }
  }

  async function select(value: string) {
    onBusy(true); onError(""); setAction(null)
    try { await sdk.openProject(project!.id, value) }
    catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)) }
    finally { onBusy(false) }
  }

  return <section className="project-strategies" aria-label="项目策略">
    <div className="project-strategies-label">策略 <span>{project.strategies.length}</span></div>
    <div className="project-strategy-tabs" role="group" aria-label="切换策略">
      {project.strategies.map((item) => <button key={item.id} type="button"
        ref={item.id === project.strategy_id ? selectedTab : undefined}
        className="project-strategy-tab" aria-pressed={item.id === project.strategy_id}
        title={`${item.name} · ${item.path}`} disabled={disabled}
        onClick={() => void select(item.id)}><FileCode2 size={14} /><span>{item.name}</span></button>)}
    </div>
    <div className="project-strategy-actions">
      <button type="button" className="project-strategy-add" disabled={disabled || !project.editable || project.strategies.length >= 20} onClick={() => begin("new")}><Plus size={14} /><span>新建策略</span></button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild><button type="button" className="project-strategy-menu" aria-label="当前策略操作" title="当前策略操作" disabled={disabled || !project.editable}><MoreHorizontal size={17} /></button></DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem disabled={project.strategies.length >= 20} onSelect={() => begin("copy")}><Copy size={14} />复制策略</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => begin("rename")}><Pencil size={14} />重命名</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
    <Dialog open={action !== null} onOpenChange={(open) => { if (!open && !saving) setAction(null) }}>
      <DialogContent showClose={!saving}>
        <DialogHeader>
          <DialogTitle>{action === "rename" ? "重命名策略" : action === "copy" ? "复制策略" : "新建策略"}</DialogTitle>
          <DialogDescription>{action === "rename" ? "修改显示名称，已有回测记录会保留原名称。" : `以「${action === "copy" ? selected?.name : project.strategies.find((item) => item.id === "main")?.name}」为起点，创建可独立编辑的策略。`}</DialogDescription>
        </DialogHeader>
        <form className="project-strategy-form" onSubmit={(event) => { event.preventDefault(); void apply() }}>
          <label><span>策略名称</span><Input aria-label="策略名称" autoFocus required maxLength={100} value={name} disabled={saving} onChange={(event) => setName(event.target.value)} /></label>
          {action !== "rename" ? <label><span>Python 文件名</span><div className="project-strategy-filename"><Input aria-label="策略文件名" required maxLength={64} pattern="[a-z][a-z0-9_]{0,63}" value={id} disabled={saving} onChange={(event) => setId(event.target.value)} /><span>.py</span></div><small>使用小写字母、数字和下划线，以字母开头。</small></label> : null}
          {formError ? <p className="text-xs text-destructive" role="alert">{formError}</p> : null}
          <DialogFooter>
            <Button type="button" size="sm" variant="outline" disabled={saving} onClick={() => setAction(null)}>取消</Button>
            <Button type="submit" size="sm" isLoading={saving} disabled={disabled || !name.trim()}>{action === "rename" ? "保存名称" : "创建策略"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  </section>
}
