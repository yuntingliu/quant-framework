import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { DiffView, type Change } from "@/components/shared/SourceHistoryDialog"
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { api } from "@/lib/api"

type Revision = { id: number; package_revision: number; restored_from: number | null }
type Preview = { token: string; changes: Change[]; affected_strategies: { id: string; name: string }[] }
type Props = { project: string; strategy: string; factor: string; blocked: boolean; refresh: () => Promise<void> }

export function FactorRestoreDialog({ project, strategy, factor, blocked, refresh }: Props) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [items, setItems] = useState<Revision[]>([])
  const [selected, setSelected] = useState(0)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [requestId, setRequestId] = useState("")
  const [message, setMessage] = useState("")
  const [reload, setReload] = useState(0)
  const [before, setBefore] = useState<number>()
  const base = `/strategy/projects/${encodeURIComponent(project)}/factors/${encodeURIComponent(factor)}`
  const query = `strategy_id=${encodeURIComponent(strategy)}`
  useEffect(() => {
    if (!open) return
    let active = true
    setItems([]); setSelected(0); setPreview(null)
    void api.get<Revision[]>(`${base}/revisions?${query}${before ? "&before=" + before : ""}`).then(rows => {
      if (!active) return
      setItems(rows); setSelected((before ? rows[0] : rows[1] ?? rows[0])?.id ?? 0)
    }).catch((e: Error) => { if (active) setMessage(e.message) })
    return () => { active = false }
  }, [open, base, query, reload, before])
  useEffect(() => {
    if (!open || !selected) return
    let active = true
    setPreview(null)
    void api.get<Preview>(`${base}/restore-preview?${query}&revision=${selected}`).then(value => {
      if (active) { setRequestId(crypto.randomUUID()); setPreview(value) }
    }).catch((e: Error) => { if (active) setMessage(e.message) })
    return () => { active = false }
  }, [open, base, query, selected])

  async function apply() {
    if (!preview?.changes.length || blocked || busy) return
    if (Array.from(document.querySelectorAll<HTMLElement>("[data-unsaved-project]"))
      .some(el => el.dataset.unsavedProject === project)) {
      setMessage("其他工作台有未保存修改。请先保存代码或应用参数。")
      return
    }
    setBusy(true)
    try {
      const result = await api.post<{ revision: number }>(`${base}/restore?${query}`, {
        revision: selected, token: preview.token, request_id: requestId, confirm_write: true, confirm_python_execution: true,
      })
      setPreview(null); setBefore(undefined); setReload(n => n + 1)
      setMessage(`已恢复为新修订 r${result.revision}。`)
      await refresh()
    } catch (e) { setMessage(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <Dialog open={open} onOpenChange={next => {
    if (busy) return
    setOpen(next); setBefore(undefined); setMessage(""); setPreview(null)
  }}>
    <DialogTrigger asChild><Button variant="outline" size="sm">恢复因子版本</Button></DialogTrigger>
    <DialogContent className="source-history-panel max-h-[90dvh] max-w-2xl overflow-y-auto">
      <DialogTitle>恢复共享因子 · {factor}</DialogTitle>
      <DialogDescription>选择修订查看差异。确认恢复时会校验受影响策略的本地 Python，并保存一个新修订。</DialogDescription>
      <label>历史修订 <select value={selected} disabled={busy} onChange={e => {
        setPreview(null); setMessage(""); setSelected(Number(e.target.value))
      }}>{items.map(r => <option key={r.id} value={r.id}>r{r.id} · 代码包 v{r.package_revision}{r.restored_from ? ` · 恢复自 r${r.restored_from}` : ""}</option>)}</select></label>
      {items.length === 50 && <Button disabled={busy} onClick={() => { setPreview(null); setBefore(items[49].id) }}>更早修订</Button>}
      {preview && <><p>将更新的策略代码包：{preview.affected_strategies.map(s => s.name + " (" + s.id + ")").join("、")}</p>
        {preview.changes.length ? preview.changes.map(change => <DiffView key={change.path} change={change} zh />)
          : <p>当前源码与所选修订一致。</p>}</>}
      {blocked && <p>请先保存当前修改，并等待工作台就绪。</p>}
      {message && <p role="status">{message}</p>}
      <div className="flex gap-2"><Button variant="outline" disabled={busy} onClick={() => {
        setMessage(""); setPreview(null); setBefore(undefined); setReload(n => n + 1)
      }}>刷新预览</Button>
      <Button disabled={busy || blocked || !preview?.changes.length} onClick={() => void apply()}>{busy ? "正在恢复…" : "确认校验并恢复"}</Button></div>
    </DialogContent>
  </Dialog>
}
