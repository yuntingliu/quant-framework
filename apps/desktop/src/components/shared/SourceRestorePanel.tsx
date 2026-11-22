import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { DiffView, type Change } from "./SourceHistoryPanel"
import { api } from "@/lib/api"

type Revision = { id: number; package_revision: number; restored_from: number | null }
type Preview = { token: string; changes: Change[]; affected_strategies: { id: string; name: string }[] }
export type RestoreOptions = { factor?: string; blocked: boolean; refresh: () => Promise<void> }
type Props = RestoreOptions & { project: string; strategy: string; wrap: boolean; onSaving: (value: boolean) => void }

export function SourceRestorePanel({ project, strategy, factor, blocked, refresh, wrap, onSaving }: Props) {
  const [busy, setBusy] = useState(false)
  const [items, setItems] = useState<Revision[]>([])
  const [selected, setSelected] = useState(0)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [requestId, setRequestId] = useState("")
  const [message, setMessage] = useState("")
  const [reload, setReload] = useState(0)
  const [before, setBefore] = useState<number>()
  const isFactor = factor !== undefined
  const resource = isFactor ? `factors/${encodeURIComponent(factor)}` : "strategy-module"
  const base = `/strategy/projects/${encodeURIComponent(project)}/${resource}`
  const query = `strategy_id=${encodeURIComponent(strategy)}`
  useEffect(() => {
    let active = true
    setItems([]); setSelected(0); setPreview(null)
    void api.get<Revision[]>(`${base}/revisions?${query}${before ? "&before=" + before : ""}`).then(rows => {
      if (!active) return
      setItems(rows); setSelected((before ? rows[0] : rows[1] ?? rows[0])?.id ?? 0)
    }).catch((e: Error) => { if (active) setMessage(e.message) })
    return () => { active = false }
  }, [base, query, reload, before])
  useEffect(() => {
    if (!selected) return
    let active = true
    setPreview(null)
    void api.get<Preview>(`${base}/restore-preview?${query}&revision=${selected}`).then(value => {
      if (active) { setRequestId(crypto.randomUUID()); setPreview(value) }
    }).catch((e: Error) => { if (active) setMessage(e.message) })
    return () => { active = false }
  }, [base, query, selected])

  async function apply() {
    if (!preview?.changes.length || blocked || busy) return
    if (Array.from(document.querySelectorAll<HTMLElement>("[data-unsaved-project]"))
      .some(el => el.dataset.unsavedProject === project)) {
      setMessage("其他工作台有未保存修改。请先保存代码或应用参数。")
      return
    }
    setBusy(true)
    onSaving(true)
    try {
      const result = await api.post<{ revision: number }>(`${base}/restore?${query}`, {
        revision: selected, token: preview.token, request_id: requestId, confirm_write: true, confirm_python_execution: true,
      })
      setPreview(null); setBefore(undefined); setReload(n => n + 1)
      setMessage(`已恢复为新修订 r${result.revision}。`)
      await refresh()
    } catch (e) { setMessage(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false); onSaving(false) }
  }
  return <section className="source-history-panel source-restore-panel">
      <div className="source-restore-controls">
      <label>历史修订 <select value={selected} disabled={busy} onChange={e => {
        setPreview(null); setMessage(""); setSelected(Number(e.target.value))
      }}>{items.map(r => <option key={r.id} value={r.id}>r{r.id} · 代码包 v{r.package_revision}{r.restored_from ? ` · 恢复自 r${r.restored_from}` : ""}</option>)}</select></label>
      {items.length === 50 && <Button disabled={busy} onClick={() => { setPreview(null); setBefore(items[49].id) }}>更早修订</Button>}
      <Button variant="outline" disabled={busy} onClick={() => {
        setMessage(""); setPreview(null); setBefore(undefined); setReload(n => n + 1)
      }}>刷新预览</Button>
      <Button disabled={busy || blocked || !preview?.changes.length} onClick={() => void apply()}>{busy ? "正在恢复…" : "确认校验并恢复"}</Button></div>
      {blocked && <p>请先返回工作台保存源码或应用参数。</p>}
      {message && <p role="status">{message}</p>}
      {preview && <>
        {preview.changes.length ? preview.changes.map(change => <DiffView key={change.path} change={change} zh wrap={wrap}
          labels={["当前源码", `恢复预览 · r${selected}`]} />) : <p className="sh-notice">当前源码与所选修订一致。</p>}
        <footer className="sh-restore-note">确认后将校验 Python 并保存新修订。涉及策略：{preview.affected_strategies.map(s => s.name).join("、")}</footer>
      </>}
  </section>
}
