import { useMemo, useState } from "react"
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Database,
  Download,
  FileSpreadsheet,
  FileText,
  Search,
} from "lucide-react"

import { SafeMarkdown } from "@/components/shared/SafeMarkdown"
import { useAgentPrompt } from "@/contexts/AgentPromptContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { usePanel } from "@/contexts/PanelContext"
import { cn } from "@/lib/utils"
import {
  researchResultToCsv,
  type ResearchResultCell,
  type ResearchResultColumn,
} from "@/workspace/researchResults"

interface SortState {
  key: string
  direction: "asc" | "desc"
}

function displayCell(value: ResearchResultCell, column: ResearchResultColumn, language: "zh" | "en"): string {
  if (value === null) return "--"
  if (typeof value === "boolean") return language === "zh" ? (value ? "是" : "否") : (value ? "Yes" : "No")
  if (column.format === "number" && typeof value === "number") {
    return value.toLocaleString(undefined, { maximumFractionDigits: 6 })
  }
  if (column.format === "percent" && typeof value === "number") {
    return `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}%`
  }
  if ((column.format === "date" || column.format === "datetime") && typeof value === "string") {
    const date = new Date(value)
    if (!Number.isNaN(date.getTime())) {
      return column.format === "date" ? date.toLocaleDateString() : date.toLocaleString()
    }
  }
  return String(value)
}

function compareCells(left: ResearchResultCell, right: ResearchResultCell): number {
  if (left === right) return 0
  if (left === null) return 1
  if (right === null) return -1
  if (typeof left === "number" && typeof right === "number") return left - right
  if (typeof left === "boolean" && typeof right === "boolean") return Number(left) - Number(right)
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" })
}

export function ResearchResultViewerWidget() {
  const panel = usePanel()
  const { language } = useLanguage()
  const { researchResults } = useAgentPrompt()
  const requestedId = typeof panel?.params.resultId === "string" ? panel.params.resultId : undefined
  const result = researchResults.find((item) => item.id === requestedId) ?? (!requestedId ? researchResults[0] : undefined)
  const [activeView, setActiveView] = useState<"document" | "table">("document")
  const [query, setQuery] = useState("")
  const [sort, setSort] = useState<SortState | null>(null)

  const copy = language === "zh" ? {
    emptyTitle: "暂无研究结果",
    emptyDetail: "让 Agent 生成研究报告并打开到工作区后，结果会显示在这里。",
    document: "研究报告",
    table: "数据表",
    search: "筛选当前表格…",
    export: "导出 CSV",
    rows: "行",
    shown: "显示",
    sources: "数据来源",
    generated: "生成时间",
  } : {
    emptyTitle: "No research result",
    emptyDetail: "Ask the Agent to create a research report and open it in the workspace.",
    document: "Research report",
    table: "Data table",
    search: "Filter this table…",
    export: "Export CSV",
    rows: "rows",
    shown: "shown",
    sources: "Sources",
    generated: "Generated",
  }

  const table = result?.table
  const visibleRows = useMemo(() => {
    if (!table) return []
    const normalized = query.trim().toLocaleLowerCase()
    const filtered = normalized
      ? table.rows.filter((row) => table.columns.some((column) => displayCell(row[column.key], column, language).toLocaleLowerCase().includes(normalized)))
      : table.rows
    if (!sort) return filtered
    return [...filtered].sort((left, right) => {
      const comparison = compareCells(left[sort.key], right[sort.key])
      return sort.direction === "asc" ? comparison : -comparison
    })
  }, [language, query, sort, table])

  if (!result) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-card p-6 text-center">
        <div className="max-w-sm">
          <FileText className="mx-auto h-9 w-9 text-primary/70" />
          <div className="mt-3 text-sm font-semibold text-foreground">{copy.emptyTitle}</div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{copy.emptyDetail}</p>
        </div>
      </div>
    )
  }

  const changeSort = (key: string) => {
    setSort((current) => current?.key === key
      ? current.direction === "asc" ? { key, direction: "desc" } : null
      : { key, direction: "asc" })
  }

  const downloadCsv = () => {
    if (!table) return
    const blob = new Blob([researchResultToCsv(table)], { type: "text/csv;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = `${result.title.replace(/[\\/:*?"<>|]+/g, "-") || "research-result"}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <div className="shrink-0 border-b border-border px-4 py-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-primary/25 bg-primary/10 text-primary">
            <FileText className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-foreground">{result.title}</div>
            {result.description ? <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-muted-foreground">{result.description}</div> : null}
          </div>
          {table ? (
            <button
              type="button"
              className="flex h-8 shrink-0 items-center gap-1.5 rounded border border-border bg-background px-2.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
              onClick={downloadCsv}
              title={copy.export}
            >
              <Download className="h-3.5 w-3.5" />
              {copy.export}
            </button>
          ) : null}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
          {table ? <span className="rounded bg-muted px-2 py-1 font-mono">{table.rows.length} {copy.rows}</span> : null}
          {result.generatedAt || result.updatedAt ? (
            <span>{copy.generated}: {new Date(result.generatedAt ?? result.updatedAt ?? "").toLocaleString()}</span>
          ) : null}
          {result.sources.length ? (
            <span className="flex min-w-0 items-center gap-1">
              <Database className="h-3 w-3 shrink-0" />
              <span className="truncate">{copy.sources}: {result.sources.join(" · ")}</span>
            </span>
          ) : null}
        </div>
      </div>

      {table ? (
        <div className="flex shrink-0 items-center gap-1 border-b border-border px-3 py-2">
          <button
            type="button"
            className={cn("flex h-7 items-center gap-1.5 rounded px-2 text-xs", activeView === "document" ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted")}
            onClick={() => setActiveView("document")}
          >
            <FileText className="h-3.5 w-3.5" />
            {copy.document}
          </button>
          <button
            type="button"
            className={cn("flex h-7 items-center gap-1.5 rounded px-2 text-xs", activeView === "table" ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted")}
            onClick={() => setActiveView("table")}
          >
            <FileSpreadsheet className="h-3.5 w-3.5" />
            {copy.table}
          </button>
        </div>
      ) : null}

      {activeView === "document" || !table ? (
        <div className="min-h-0 flex-1 overflow-auto p-5">
          <SafeMarkdown className="mx-auto max-w-5xl">{result.markdown}</SafeMarkdown>
        </div>
      ) : (
        <>
          <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={copy.search}
                className="h-8 w-full rounded border border-border bg-background pl-8 pr-3 text-xs text-foreground outline-none focus:border-primary"
              />
            </div>
            <span className="shrink-0 text-[10px] text-muted-foreground">{copy.shown} {visibleRows.length}/{table.rows.length}</span>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <table className="min-w-full border-separate border-spacing-0 text-xs">
              <thead className="sticky top-0 z-10 bg-card">
                <tr>
                  {table.columns.map((column) => {
                    const active = sort?.key === column.key
                    const SortIcon = !active ? ArrowUpDown : sort.direction === "asc" ? ArrowUp : ArrowDown
                    return (
                      <th key={column.key} className="whitespace-nowrap border-b border-r border-border bg-muted/40 px-3 py-2 text-left font-semibold text-foreground last:border-r-0">
                        <button type="button" className="flex w-full items-center gap-1.5" onClick={() => changeSort(column.key)}>
                          <span>{column.label}</span>
                          <SortIcon className={cn("h-3 w-3", active ? "text-primary" : "text-muted-foreground/60")} />
                        </button>
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, rowIndex) => (
                  <tr key={rowIndex} className="odd:bg-background even:bg-muted/15 hover:bg-primary/5">
                    {table.columns.map((column) => (
                      <td
                        key={column.key}
                        className={cn(
                          "max-w-[420px] whitespace-nowrap border-b border-r border-border/70 px-3 py-2 text-foreground last:border-r-0",
                          (column.format === "number" || column.format === "percent") && "text-right font-mono",
                        )}
                        title={displayCell(row[column.key], column, language)}
                      >
                        {displayCell(row[column.key], column, language)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
