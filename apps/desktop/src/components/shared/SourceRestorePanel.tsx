import { useEffect, useRef, useState } from "react"
import { ArrowRight, FileCode2, Info, Loader2, RotateCcw, ShieldCheck } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useLanguage } from "@/contexts/LanguageContext"
import { useConfirm } from "@/hooks/useConfirm"
import { api } from "@/lib/api"
import { DiffView, type Change } from "./SourceHistoryPanel"

export type RestoreKind = "strategy" | "factor" | "recipe" | "validation"
type Revision = { id: number; package_revision?: number; restored_from: number | null }
type HistoryPage = { items: Array<{ revision: number; restored_from: number | null }> }
type MetadataChange = { field: string; before: string | null; after: string | null }
type Preview = {
  token: string
  changes: Change[]
  metadata_changes?: MetadataChange[]
  affected_strategies: Array<{ id: string; name: string }>
}
type Receipt = { revision: number; restored_from?: number }
export type RestoreOptions = {
  kind: RestoreKind
  factor?: string
  blocked: boolean
  refresh: () => Promise<void>
  onRefreshError?: (message: string) => void
  onRefreshSuccess?: () => void
}
type Props = RestoreOptions & {
  project: string
  strategy: string
  wrap: boolean
  onSaving: (value: boolean) => void
}

const objectNames: Record<RestoreKind, [string, string]> = {
  strategy: ["策略", "strategy"],
  factor: ["共享因子", "shared factor"],
  recipe: ["数据配方", "data recipe"],
  validation: ["验证源码", "validation source"],
}

export function SourceRestorePanel({ project, strategy, kind, factor, blocked, refresh, onRefreshError, onRefreshSuccess, wrap, onSaving }: Props) {
  const zh = useLanguage().language === "zh"
  const confirm = useConfirm()
  const mounted = useRef(true)
  const [busy, setBusy] = useState(false)
  const [loadingRevisions, setLoadingRevisions] = useState(true)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [items, setItems] = useState<Revision[]>([])
  const [selected, setSelected] = useState(0)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [requestId, setRequestId] = useState("")
  const [error, setError] = useState("")
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [refreshError, setRefreshError] = useState("")
  const [reload, setReload] = useState(0)
  const [before, setBefore] = useState<number>()
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      onSaving(false)
    }
  }, [onSaving])

  const object = objectNames[kind][zh ? 0 : 1]
  const resource = kind === "factor" ? `factors/${encodeURIComponent(factor ?? "")}` : "strategy-module"
  const base = kind === "recipe"
    ? `/data-sync/recipes/${encodeURIComponent(project)}`
    : kind === "validation"
      ? `/validation/projects/${encodeURIComponent(project)}`
      : `/strategy/projects/${encodeURIComponent(project)}/${resource}`
  const ownerQuery = kind === "strategy" || kind === "factor" ? `strategy_id=${encodeURIComponent(strategy)}` : ""
  const withQuery = (path: string, parameters = "") => {
    const query = [ownerQuery, parameters].filter(Boolean).join("&")
    return `${path}${query ? `?${query}` : ""}`
  }
  const revisionUrl = kind === "recipe" || kind === "validation"
    ? `/strategy/projects/${encodeURIComponent(project)}/source-history?kind=${kind}&limit=50${before ? `&before=${before}` : ""}`
    : withQuery(`${base}/revisions`, before ? `before=${before}` : "")

  useEffect(() => {
    let active = true
    setLoadingRevisions(true)
    setError("")
    setItems([])
    setSelected(0)
    setPreview(null)
    void api.get<Revision[] | HistoryPage>(revisionUrl).then(result => {
      if (!active) return
      const rows = Array.isArray(result)
        ? result
        : result.items.map(item => ({ id: item.revision, package_revision: item.revision, restored_from: item.restored_from }))
      setItems(rows)
      setSelected((before ? rows[0] : rows[1] ?? rows[0])?.id ?? 0)
    }).catch((reason: Error) => {
      if (active) setError(reason.message)
    }).finally(() => {
      if (active) setLoadingRevisions(false)
    })
    return () => { active = false }
  }, [revisionUrl, reload, before])

  const previewUrl = withQuery(`${base}/restore-preview`, `revision=${selected}`)
  useEffect(() => {
    if (!selected) return
    let active = true
    setLoadingPreview(true)
    setError("")
    setPreview(null)
    void api.get<Preview>(previewUrl).then(value => {
      if (active) {
        setRequestId(crypto.randomUUID())
        setPreview(value)
      }
    }).catch((reason: Error) => {
      if (active) setError(reason.message)
    }).finally(() => {
      if (active) setLoadingPreview(false)
    })
    return () => { active = false }
  }, [previewUrl, selected])

  const hasChanges = Boolean(preview && (preview.changes.length || preview.metadata_changes?.length))
  const blockedReason = zh ? "请先返回工作台保存源码或应用参数。" : "Return to the workbench and save source or apply parameter edits first."

  async function refreshWorkbench() {
    try {
      await refresh()
      if (!mounted.current) return
      setRefreshError("")
      onRefreshSuccess?.()
    } catch (reason) {
      if (!mounted.current) return
      const message = reason instanceof Error ? reason.message : String(reason)
      setRefreshError(message)
      onRefreshError?.(message)
    }
  }

  async function apply() {
    if (!preview || !hasChanges || blocked || busy) return
    if (Array.from(document.querySelectorAll<HTMLElement>("[data-unsaved-project]"))
      .some(element => element.dataset.unsavedProject === project)) {
      setError(zh ? "其他工作台有未保存修改。请先保存代码或应用参数。" : "Another workbench has unsaved changes. Save source or apply parameter edits first.")
      return
    }
    const approved = await confirm({
      title: zh ? `恢复${object}到 r${selected}` : `Restore ${object} to r${selected}`,
      description: zh
        ? "将保存为一个带来源的新修订；旧代码包、旧回测和历史证据保持不变。"
        : "This creates a new linked revision. Existing packages, backtests, and historical evidence remain unchanged.",
      body: <div className="space-y-1">
        {kind === "strategy" ? <p>{zh ? "仅恢复此策略模块，并与当前共享因子重新组装。" : "Only this strategy module is restored and assembled with the current shared factors."}</p> : null}
        {kind === "factor" ? <p>{zh ? `将重新组装并校验 ${preview.affected_strategies.length} 个当前策略。` : `${preview.affected_strategies.length} current strategies will be assembled and checked.`}</p> : null}
        {kind === "recipe" ? <p>{zh ? "恢复源码和模板选择，不会运行配方或同步数据。" : "Restores source and template selection without running the recipe or syncing data."}</p> : null}
        {kind === "validation" ? <p>{zh ? "只做静态校验，不会执行验证或启动回测。" : "Only static checks run; validation and backtests are not executed."}</p> : null}
        {kind === "strategy" || kind === "factor" ? <p>{zh ? "确认后会校验受信任的本机 Python，但不会自动运行回测。" : "Trusted local Python is checked, but no backtest starts automatically."}</p> : null}
      </div>,
      confirmText: zh ? "创建恢复修订" : "Restore version",
    })
    if (!approved || !mounted.current) return
    setBusy(true)
    onSaving(true)
    setError("")
    setRefreshError("")
    let result: Receipt
    try {
      result = await api.post<Receipt>(withQuery(`${base}/restore`), {
        revision: selected,
        token: preview.token,
        request_id: requestId,
        confirm_write: true,
        ...(kind === "strategy" || kind === "factor" ? { confirm_python_execution: true } : {}),
      })
      if (!mounted.current) return
    } catch (reason) {
      if (!mounted.current) return
      setError(reason instanceof Error ? reason.message : String(reason))
      setBusy(false)
      onSaving(false)
      return
    }
    if (!mounted.current) return
    setReceipt({ revision: result.revision, restored_from: result.restored_from ?? selected })
    setPreview(null)
    setBefore(undefined)
    setReload(value => value + 1)
    await refreshWorkbench()
    if (!mounted.current) return
    setBusy(false)
    onSaving(false)
  }

  function refreshPreview() {
    setError("")
    setRefreshError("")
    setReceipt(null)
    setPreview(null)
    setBefore(undefined)
    setReload(value => value + 1)
  }

  return <section className="source-history-panel source-restore-panel" aria-labelledby="source-restore-title" aria-busy={loadingRevisions || loadingPreview || busy}>
    <header className="sh-restore-heading">
      <div>
        <span className="sh-eyebrow"><RotateCcw size={13} />{zh ? "安全恢复" : "Safe restore"}</span>
        <h2 id="source-restore-title">{zh ? `恢复${object}版本` : `Restore ${object} version`}</h2>
      </div>
      <span className="sh-readonly-badge"><ShieldCheck size={12} />{zh ? "保留历史" : "History preserved"}</span>
    </header>
    <div className="source-restore-controls">
      <label>{zh ? "历史修订" : "Historical revision"}
        <select aria-label={zh ? "历史修订" : "Historical revision"} value={selected} disabled={busy || loadingRevisions} onChange={event => {
          const next = Number(event.target.value)
          if (next === selected) return
          setPreview(null)
          setError("")
          setReceipt(null)
          setSelected(next)
        }}>
          {items.map(item => <option key={item.id} value={item.id}>
            r{item.id}{item.package_revision !== undefined ? ` · ${zh ? "代码包" : "package"} v${item.package_revision}` : ""}{item.restored_from ? ` · ${zh ? `恢复自 r${item.restored_from}` : `restored from r${item.restored_from}`}` : ""}
          </option>)}
        </select>
      </label>
      {items.length === 50 ? <Button variant="outline" disabled={busy || loadingRevisions} onClick={() => {
        setPreview(null)
        setReceipt(null)
        setBefore(items[49].id)
      }}>{zh ? "更早修订" : "Earlier revisions"}</Button> : null}
      <Button variant="outline" disabled={busy || loadingRevisions || loadingPreview} onClick={refreshPreview}>
        {zh ? "刷新预览" : "Refresh preview"}
      </Button>
      <Button aria-describedby={blocked ? "restore-blocked-reason" : undefined} disabled={busy || blocked || !hasChanges || loadingPreview} onClick={() => void apply()}>
        {busy ? (zh ? "正在恢复…" : "Restoring…") : (zh ? "确认校验并恢复" : "Review and restore")}
      </Button>
    </div>
    {blocked ? <p id="restore-blocked-reason" className="sh-state sh-state-warning"><Info size={14} />{blockedReason}</p> : null}
    {error ? <div className="sh-state sh-state-error" role="alert"><Info size={15} /><span>{error}</span><Button variant="outline" size="sm" onClick={refreshPreview}>{zh ? "刷新预览" : "Refresh preview"}</Button></div> : null}
    {receipt ? <div className="sh-state sh-state-success" role="status"><ShieldCheck size={15} /><span>
      {zh ? `已恢复为新修订 r${receipt.revision}（来源 r${receipt.restored_from}）。` : `Restored as new revision r${receipt.revision} from r${receipt.restored_from}.`}
    </span></div> : null}
    {refreshError ? <div className="sh-state sh-state-warning" role="alert"><Info size={15} /><span>
      {zh ? `恢复已提交，但工作台刷新失败：${refreshError}` : `Restore committed, but the workbench refresh failed: ${refreshError}`}
    </span><Button variant="outline" size="sm" onClick={() => void refreshWorkbench()}>{zh ? "重试刷新" : "Retry refresh"}</Button></div> : null}
    {loadingRevisions ? <p className="sh-state" role="status"><Loader2 size={14} className="animate-spin" />{zh ? "正在读取历史修订…" : "Loading historical revisions…"}</p> : null}
    {!loadingRevisions && !items.length && !error ? <div className="sh-empty"><h3>{zh ? "尚无可恢复修订" : "No restorable revisions"}</h3><p>{zh ? "先在工作台保存源码，再回来选择历史修订。" : "Save source in the workbench, then choose a historical revision."}</p></div> : null}
    {loadingPreview ? <p className="sh-state" role="status"><Loader2 size={14} className="animate-spin" />{zh ? "正在生成恢复预览…" : "Building restore preview…"}</p> : null}
    {preview ? <>
      <div className="sh-restore-impact">
        <FileCode2 size={15} />
        <div>
          <strong>{zh ? "恢复影响" : "Restore impact"}</strong>
          <p>{kind === "strategy"
            ? (zh ? "仅恢复此策略模块；共享因子保持当前版本，旧回测不变。" : "Only this strategy module changes; shared factors stay current and old backtests are unchanged.")
            : kind === "factor"
              ? (zh ? `将重新组装并校验 ${preview.affected_strategies.length} 个当前策略；旧回测不变。` : `${preview.affected_strategies.length} current strategies will be assembled and checked; old backtests are unchanged.`)
              : kind === "recipe"
                ? (zh ? "只保存配方源码与模板选择，不同步数据。" : "Only recipe source and template selection are saved; no data sync runs.")
                : (zh ? "只保存新的验证源码修订，不运行验证或回测。" : "Only a new validation source revision is saved; validation and backtests do not run.")}</p>
        </div>
      </div>
      {preview.changes.map(change => <DiffView key={change.path} change={change} zh={zh} wrap={wrap}
        labels={[zh ? "当前源码" : "Current source", `${zh ? "恢复预览" : "Restore preview"} · r${selected}`]} />)}
      {preview.metadata_changes?.length ? <div className="sh-metadata"><h4>{zh ? "配置变化" : "Configuration changes"}</h4>{preview.metadata_changes.map(item => <p key={item.field}>
        <span>{item.field === "selected_template_id" ? (zh ? "采集模板" : "Recipe template") : item.field}</span>
        <code>{item.before ?? "—"}</code><ArrowRight size={14} /><code>{item.after ?? "—"}</code>
      </p>)}</div> : null}
      {!hasChanges ? <div className="sh-identical"><ShieldCheck size={18} /><div><strong>{zh ? "当前状态与所选修订一致" : "Current state matches this revision"}</strong><p>{zh ? "无需创建重复恢复修订。" : "No duplicate restore revision is needed."}</p></div></div> : null}
      <footer className="sh-restore-note">{zh ? "恢复始终创建新修订，不会自动重跑历史回测。" : "Restoring always creates a new revision and never reruns historical backtests."}</footer>
    </> : null}
  </section>
}
