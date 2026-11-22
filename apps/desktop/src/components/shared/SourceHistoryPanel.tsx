import { useEffect, useRef, useState } from "react"
import { ArrowRight, Check, Clock3, FileCode2, GitCompare, History, Info, Layers, Loader2, ShieldCheck } from "lucide-react"

import { SourceDiffEditor } from "./SourceDiffEditor"
import { Button } from "@/components/ui/button"
import { useLanguage } from "@/contexts/LanguageContext"
import { api } from "@/lib/api"
import "./SourceReviewWorkspace.css"

type Kind = "strategy" | "validation" | "recipe"
interface Revision { revision: number; created_at: string; summary: string; origin?: string }
interface HistoryPage { items: Revision[]; next_before: number | null; history_kind: string }
export interface Change { path: string; kind: string; change: string; diff: string; diff_truncated: boolean; before_source?: string; after_source?: string }
interface Comparison {
  summary: string
  changes: Change[]
  metadata_changes: Array<{ field: string; before: string | null; after: string | null }>
  affected_strategies: Array<{ id: string; name: string; current_revision: number }>
  impact_basis: string
}
interface Props { projectId: string; projectName?: string; strategyId?: string; kind?: Kind; version?: string | number; wrap?: boolean }


export function SourceHistoryPanel({ projectId, projectName, strategyId = "main", kind = "strategy", version, wrap = true }: Props) {
  const { language } = useLanguage()
  const zh = language === "zh"
  const label = kind === "strategy" ? (zh ? "策略与因子" : "Strategy & factors") : kind === "validation" ? (zh ? "回测验证" : "Validation") : (zh ? "数据采集" : "Data recipe")
  return <section className="source-history-panel">
    <header className="sh-heading">
      <History size={15} /><div className="sh-breadcrumb">{projectName ?? projectId}<span>/</span>{label}<span>/</span>{zh ? "版本历史" : "Version history"}</div>
    </header>
    <HistoryReader key={`${projectId}:${strategyId}:${kind}:${version}`} projectId={projectId} strategyId={strategyId} kind={kind} zh={zh} wrap={wrap} />
  </section>
}

function formatDate(value: string, zh: boolean) {
  // SQLite timestamps are UTC even when they omit the timezone suffix.
  const normalized = value.replace(" ", "T")
  const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : normalized + "Z")
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(zh ? "zh-CN" : "en-GB", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date)
}

export function DiffView({ change, zh, labels, wrap }: { change: Change; zh: boolean; labels: [string, string]; wrap: boolean }) {
  let oldLine = 0, newLine = 0
  let inHunk = false
  const rows = change.diff.split("\n").filter((line, index, lines) => !(index === lines.length - 1 && line === "")).map((line) => {
    if (!inHunk && (line.startsWith("--- ") || line.startsWith("+++ "))) return null
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line)
    if (hunk) {
      inHunk = true
      oldLine = Number(hunk[1]); newLine = Number(hunk[2])
      return { type: "hunk", old: "", next: "", sign: "", text: line }
    }
    if (line.startsWith("\\")) return { type: "note", old: "", next: "", sign: "", text: line }
    if (line.startsWith("+")) return { type: "added", old: "", next: String(newLine++), sign: "+", text: line.slice(1) }
    if (line.startsWith("-")) return { type: "removed", old: String(oldLine++), next: "", sign: "−", text: line.slice(1) }
    return { type: "context", old: String(oldLine++), next: String(newLine++), sign: "", text: line.startsWith(" ") ? line.slice(1) : line }
  }).filter((row) => row !== null)
  const added = rows.filter((r) => r.type === "added").length
  const removed = rows.filter((r) => r.type === "removed").length
  const operation = zh ? ({ added: "新增", removed: "删除", modified: "修改" }[change.change] ?? change.change) : change.change
  const patch = rows.length > 0 ? <div className="sh-diff-scroll" tabIndex={0} role="region" aria-label={zh ? `${change.path} 代码差异` : `${change.path} code diff`}>
      <div className="sh-diff-code">{rows.map((row, index) => <div key={index} className={`sh-line sh-line-${row.type}`}>
        <span className="sh-line-number" aria-hidden="true">{row.old}</span><span className="sh-line-number" aria-hidden="true">{row.next}</span><span className="sh-line-sign" aria-hidden="true">{row.sign}</span><code>{row.text || " "}</code>
      </div>)}</div>
    </div> : null
  return <article className="sh-file">
    <header className="sh-file-header">
      <h4 aria-label={`${operation} · ${change.path}`}><FileCode2 size={16} /><span>{change.path}</span></h4>
      <div className="sh-file-stats"><span className="sh-operation">{operation}</span><span className="sh-plus">+{added}</span><span className="sh-minus">−{removed}</span></div>
    </header>
    {change.diff_truncated ? <p role="status" className="sh-truncated">{zh ? "差异过大，部分内容已截断或省略；行数仅统计已显示内容。" : "Large diff: some content is truncated or omitted. Counts cover displayed lines only."}</p> : null}
    {change.before_source !== undefined && change.after_source !== undefined ?
      <SourceDiffEditor before={change.before_source} after={change.after_source} labels={labels} zh={zh} wrap={wrap} fallback={patch} /> : patch}
  </article>
}

function HistoryReader({ projectId, strategyId, kind, zh, wrap = true }: Props & { kind: Kind; zh: boolean }) {
  const [page, setPage] = useState<HistoryPage | null>(null)
  const [items, setItems] = useState<Revision[]>([])
  const [from, setFrom] = useState("")
  const [to, setTo] = useState("")
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [loading, setLoading] = useState(false)
  const [comparing, setComparing] = useState(false)
  const [error, setError] = useState("")
  const [reload, setReload] = useState(0)
  const mounted = useRef(true)
  const comparisonSequence = useRef(0)
  const query = new URLSearchParams({ kind, strategy_id: strategyId ?? "main" }).toString()
  const base = `/strategy/projects/${encodeURIComponent(projectId)}`

  useEffect(() => {
    mounted.current = true
    let current = true
    setLoading(true)
    setError("")
    void api.get<HistoryPage>(`${base}/source-history?${query}`).then(async (result) => {
      if (!current) return
      setPage(result)
      setItems(result.items)
      const initialFrom = String(result.items[1]?.revision ?? result.items[0]?.revision ?? "")
      const initialTo = String(result.items[0]?.revision ?? "")
      setFrom(initialFrom)
      setTo(initialTo)
      if (initialFrom && initialTo) {
        const sequence = ++comparisonSequence.current
        setComparing(true)
        try {
          const diff = await api.get<Comparison>(`${base}/source-comparison?${query}&from_revision=${initialFrom}&to_revision=${initialTo}`)
          if (current && sequence === comparisonSequence.current) setComparison(diff)
        } finally { if (current && sequence === comparisonSequence.current) setComparing(false) }
      }
    }).catch((reason: unknown) => {
      if (current) setError(reason instanceof Error ? reason.message : String(reason))
    }).finally(() => { if (current) setLoading(false) })
    return () => { current = false; mounted.current = false }
  }, [base, query, reload])

  const loadMore = async () => {
    if (!page?.next_before || loading) return
    setLoading(true)
    setError("")
    try {
      const result = await api.get<HistoryPage>(`${base}/source-history?${query}&before=${page.next_before}`)
      if (!mounted.current) return
      setPage(result)
      setItems((previous) => [...previous, ...result.items.filter((item) => !previous.some((old) => old.revision === item.revision))])
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { if (mounted.current) setLoading(false) }
  }
  const clearComparison = () => {
    comparisonSequence.current += 1
    setComparison(null)
    setComparing(false)
    setError("")
  }
  const compare = async () => {
    const sequence = ++comparisonSequence.current
    setComparing(true)
    setError("")
    setComparison(null)
    try {
      const result = await api.get<Comparison>(`${base}/source-comparison?${query}&from_revision=${from}&to_revision=${to}`)
      if (mounted.current && sequence === comparisonSequence.current) setComparison(result)
    } catch (reason) {
      if (mounted.current && sequence === comparisonSequence.current) setError(reason instanceof Error ? reason.message : String(reason))
    } finally { if (mounted.current && sequence === comparisonSequence.current) setComparing(false) }
  }
  const summary = (item: Revision) => item.origin === "migration_baseline"
    ? (zh ? "迁移时保留的草稿" : "Preserved migration baseline")
    : (zh ? (kind === "strategy" ? "策略与共享因子快照" : kind === "validation" ? "验证源码已保存" : "采集配方已保存") : item.summary)

  return <>
    <div className="sh-layout">
      <aside className="sh-sidebar" aria-label={zh ? "已保存版本" : "Saved versions"}>
        <div className="sh-sidebar-heading"><h3>{zh ? "已保存版本" : "Saved versions"}</h3><span>{items.length}{page?.next_before ? "+" : ""}</span></div>
        <p className="sh-sidebar-hint">{zh ? "点击版本，设为比较起点" : "Select a version to compare from"}</p>
        <ol className="sh-timeline">{items.map((item, index) => <li key={item.revision}>
          <button type="button" className={`sh-version ${from === String(item.revision) ? "is-selected" : ""}`} disabled={loading} aria-pressed={from === String(item.revision)} onClick={() => { setFrom(String(item.revision)); clearComparison() }}>
            <span className="sh-version-title"><strong>v{item.revision}</strong>{from === String(item.revision) ? <span className="sh-version-tag">{zh ? "原版本" : "From"}</span> : index === 0 ? <span className="sh-version-tag sh-version-tag-neutral">{zh ? "最近记录" : "Most recent"}</span> : null}</span>
            <span className="sh-version-summary">{summary(item)}</span>
            <span className="sh-version-date"><Clock3 size={12} /><time dateTime={item.created_at}>{formatDate(item.created_at, zh)}</time></span>
          </button>
        </li>)}</ol>
        {loading ? <p className="sh-loading" role="status"><Loader2 size={15} className="animate-spin" />{zh ? "正在读取…" : "Loading…"}</p> : null}
        {page?.next_before ? <Button className="sh-more" variant="outline" size="sm" disabled={loading} onClick={() => void loadMore()}>{zh ? "加载更早版本" : "Load earlier versions"}</Button> : null}
        <details className="sh-history-note"><summary><Info size={13} />{zh ? "历史记录说明" : "About this history"}</summary><p>{kind === "strategy"
          ? (zh ? "这里只列出不同的已保存代码包，不是每次编辑的流水记录。因子改名显示为旧路径删除与新路径新增。" : "Distinct saved packages, not every edit event. Factor renames appear as removed and added paths.")
          : (zh ? "版本号属于当前项目和源码类型。采集配方迁移前的编辑无法追溯。" : "Revisions belong to this project and source type. Recipe edits before migration are unavailable.")}</p></details>
      </aside>
      <div className="sh-main">
        <div className="sh-compare-toolbar">
          <div className="sh-version-selectors">
            <label><span>{zh ? "原版本" : "From revision"}</span><select aria-label={zh ? "原版本" : "From revision"} value={from} disabled={loading} onChange={(event) => { setFrom(event.target.value); clearComparison() }}>{items.map((item) => <option key={item.revision} value={item.revision}>v{item.revision}</option>)}</select></label>
            <ArrowRight size={18} className="sh-compare-arrow" />
            <label><span>{zh ? "目标版本" : "To revision"}</span><select aria-label={zh ? "目标版本" : "To revision"} value={to} disabled={loading} onChange={(event) => { setTo(event.target.value); clearComparison() }}>{items.map((item) => <option key={item.revision} value={item.revision}>v{item.revision}</option>)}</select></label>
          </div>
          <Button className="sh-compare-button" disabled={!from || !to || loading || comparing} onClick={() => void compare()}>{comparing ? <Loader2 size={15} className="animate-spin" /> : <GitCompare size={15} />}{comparing ? (zh ? "比较中…" : "Comparing…") : (zh ? "比较版本" : "Compare versions")}</Button>
        </div>
        <div className="sh-results-scroll">
          {error ? <div role="alert" className="sh-error"><Info size={18} /><div><strong>{zh ? "暂时无法读取历史" : "Could not read history"}</strong><p>{error}</p></div><Button variant="outline" size="sm" onClick={() => { clearComparison(); setReload((n) => n + 1) }} disabled={loading || comparing}>{zh ? "重试" : "Retry"}</Button></div> : null}
          {comparison ? <section className="sh-results" aria-label={zh ? "比较结果" : "Comparison results"}>
            <div className="sh-results-heading"><div><h3>{zh ? "变更概览" : "Changes"}</h3><p>{zh ? `从 v${from} 到 v${to}` : `From v${from} to v${to}`}</p></div><span className="sh-change-count">{zh ? `${comparison.changes.length} 个文件变化` : `${comparison.changes.length} changed files`}</span></div>
            <span className="sr-only">{comparison.summary}</span>
            {comparison.changes.length === 0 && comparison.metadata_changes.length === 0 ? <div className="sh-identical"><Check size={20} /><div><strong>{zh ? "两个版本内容一致" : "These versions are identical"}</strong><p>{zh ? "没有源码或模板选择变化。" : "No source or template-selection changes."}</p></div></div> : null}
            {comparison.changes.map((item) => <DiffView key={item.path} change={item} zh={zh} wrap={wrap} labels={[`v${from}`, `v${to}`]} />)}
            {comparison.metadata_changes.length ? <div className="sh-metadata"><h4>{zh ? "配置变化" : "Configuration changes"}</h4>{comparison.metadata_changes.map((item) => <p key={item.field}><span>{item.field === "selected_template_id" ? (zh ? "采集模板" : "Recipe template") : item.field}</span><code>{item.before ?? "—"}</code><ArrowRight size={14} /><code>{item.after ?? "—"}</code></p>)}</div> : null}
            {comparison.affected_strategies.length ? <div className="sh-impact"><div className="sh-impact-heading"><Layers size={17} /><h4>{zh ? "关联策略" : "Related strategies"}</h4><span>{comparison.affected_strategies.length}</span></div><p>{zh ? "以下策略的代码包包含本次变化。不代表信号实际使用了该因子。" : "These current strategy packages include the changed source. This does not establish signal usage."}</p><ul>{comparison.affected_strategies.map((item) => <li key={item.id}><FileCode2 size={14} /><span>{item.name}</span><code>{item.id}</code><span className="sh-impact-revision">v{item.current_revision}</span></li>)}</ul></div> : null}
          </section> : !error ? <div className="sh-empty"><div className="sh-empty-icon">{loading || comparing ? <Loader2 size={28} className="animate-spin" /> : <GitCompare size={28} />}</div><h3>{loading || comparing ? (zh ? "正在读取版本差异" : "Loading version changes") : items.length ? (zh ? "准备比较这两个版本" : "Ready to compare") : (zh ? "还没有保存的版本" : "No saved versions yet")}</h3><p>{loading || comparing ? (zh ? "正在读取已保存的代码，请稍候。" : "Reading saved code. Please wait.") : items.length ? (zh ? "点击“比较版本”，查看具体变化。" : "Choose Compare versions to inspect changes.") : (zh ? "保存源码后，可在这里查看历史。" : "Save source to begin tracking its history.")}</p></div> : null}
        </div>
      </div>
    </div>
    <footer className="sh-footer"><ShieldCheck size={14} /><span>{zh ? "查看历史不会覆盖未保存的修改，也不会运行代码。" : "Viewing history never overwrites unsaved edits or runs code."}</span></footer>
  </>
}
