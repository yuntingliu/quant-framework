import { useState, type ComponentProps, type ReactNode } from "react"
import { Code2, GitCompareArrows, History } from "lucide-react"
import { useLanguage } from "@/contexts/LanguageContext"
import { SourceHistoryPanel } from "./SourceHistoryPanel"
import { SourceRestorePanel, type RestoreOptions } from "./SourceRestorePanel"

type Props = ComponentProps<typeof SourceHistoryPanel> & {
  children: ReactNode
  restore?: RestoreOptions
  initialView?: "editor" | "restore"
  dirty?: boolean
}

/** Keep the editor mounted while an inline review occupies its workbench. */
export function SourceReviewWorkspace({ children, restore, dirty, initialView = "editor", ...history }: Props) {
  const zh = useLanguage().language === "zh"
  const [selected, select] = useState<"editor" | "history" | "restore">(initialView)
  const [wrap, setWrap] = useState(true)
  const [saving, setSaving] = useState(false)
  const view = selected === "restore" && !restore ? "editor" : selected
  const strategy = history.strategyId ?? "main"
  const restoreLabel = restore?.factor ? "恢复因子版本" : "恢复策略版本"
  return <div className="source-review-workspace" data-unsaved-project={dirty ? history.projectId : undefined}>
    <nav className="source-review-nav" aria-label={zh ? "工作台视图" : "Workbench views"}>
      <button type="button" disabled={saving} aria-pressed={view === "editor"} onClick={() => select("editor")}><Code2 size={14} />{zh ? "工作台" : "Workbench"}</button>
      <button type="button" disabled={saving} aria-pressed={view === "history"} onClick={() => select("history")}><History size={14} />{zh ? "源码历史与比较" : "Source history & comparison"}</button>
      {restore && <button type="button" disabled={saving} aria-pressed={view === "restore"} onClick={() => select("restore")}><GitCompareArrows size={14} />{restoreLabel}</button>}
      {view !== "editor" && <label><input type="checkbox" checked={wrap} onChange={e => setWrap(e.target.checked)} />
        {zh ? "自动换行" : "Wrap lines"}</label>}
    </nav>
    <div hidden={view !== "editor"} className="source-review-editor">{children}</div>
    {view !== "editor" && <section aria-label={zh ? "源码差异" : "Source diff"} className="source-review-content" data-wrap={wrap}>
      {view === "history" ? <SourceHistoryPanel {...history} wrap={wrap} /> : restore &&
        <SourceRestorePanel key={JSON.stringify([history.projectId, strategy, restore.factor])}
          project={history.projectId} strategy={strategy} {...restore} wrap={wrap} onSaving={setSaving} />}
    </section>}
  </div>
}
