import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChartSpline,
  Download,
  FileSpreadsheet,
  FileText,
  RefreshCw,
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
import { ResearchResultCharts } from "./ResearchResultCharts"

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

export function ReportWorkbenchWidget() {
  const { language } = useLanguage()
  const panel = usePanel()
  const { researchResults, refreshResearchResults } = useAgentPrompt()
  const [refreshing, setRefreshing] = useState(false)
  const refreshReports = useCallback(async () => {
    setRefreshing(true)
    try {
      await refreshResearchResults()
    } finally {
      setRefreshing(false)
    }
  }, [refreshResearchResults])
  useEffect(() => {
    void refreshReports().catch(() => undefined)
  }, [refreshReports])
  const requestedReportId = typeof panel?.params.resultId === "string" ? panel.params.resultId : undefined
  const [selectedReportId, setSelectedReportId] = useState<string | undefined>(requestedReportId)
  useEffect(() => {
    if (requestedReportId && researchResults.some((item) => item.id === requestedReportId)) {
      setSelectedReportId(requestedReportId)
      return
    }
    if (!selectedReportId || !researchResults.some((item) => item.id === selectedReportId)) {
      setSelectedReportId(researchResults[0]?.id)
    }
  }, [researchResults, requestedReportId, selectedReportId])
  const result = researchResults.find((item) => item.id === selectedReportId)
  const [activeView, setActiveView] = useState<"document" | "table" | "charts">("document")
  const [query, setQuery] = useState("")
  const [sort, setSort] = useState<SortState | null>(null)

  const copy = language === "zh" ? {
    emptyTitle: "暂无研究结果",
    emptyDetail: "让 Agent 生成研究报告并打开到工作区后，结果会显示在这里。",
    document: "研究报告",
    table: "数据表",
    charts: "图表",
    search: "筛选当前表格…",
    export: "导出 CSV",
    rows: "行",
    shown: "显示",
    updated: "更新时间",
    history: "报告历史",
    count: "份工作区报告",
    refresh: "刷新报告历史",
  } : {
    emptyTitle: "No research result",
    emptyDetail: "Ask the Agent to create a research report and open it in the workspace.",
    document: "Research report",
    table: "Data table",
    charts: "Charts",
    search: "Filter this table…",
    export: "Export CSV",
    rows: "rows",
    shown: "shown",
    updated: "Updated",
    history: "Report history",
    count: "workspace reports",
    refresh: "Refresh report history",
  }

  const table = result?.table
  const charts = result?.charts
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

  const historySidebar = (
    <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-muted/10">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-foreground">{copy.history}</div>
          <div className="mt-0.5 text-[10px] text-muted-foreground">
            {researchResults.length} {copy.count}
          </div>
        </div>
        <button
          type="button"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-border bg-background text-muted-foreground hover:border-primary/40 hover:text-foreground disabled:opacity-50"
          onClick={() => { void refreshReports().catch(() => undefined) }}
          disabled={refreshing}
          title={copy.refresh}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        {researchResults.map((item) => {
          const timestamp = item.persistedAt ?? item.generatedAt ?? item.updatedAt
          return (
            <button
              key={item.id}
              type="button"
              className={cn(
                "mb-1 w-full rounded border px-2.5 py-2 text-left",
                item.id === result?.id
                  ? "border-primary/35 bg-primary/10"
                  : "border-transparent hover:border-border hover:bg-muted/60",
              )}
              onClick={() => setSelectedReportId(item.id)}
            >
              <span className="block truncate text-xs font-medium text-foreground">{item.title}</span>
              {timestamp ? (
                <span className="mt-1 block truncate text-[10px] text-muted-foreground">
                  {new Date(timestamp).toLocaleString(language === "zh" ? "zh-CN" : "en-US")}
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
    </aside>
  )

  if (!result) {
    return (
      <div className="flex h-full min-h-0 bg-card">
        {historySidebar}
        <div className="flex min-w-0 flex-1 items-center justify-center p-6 text-center">
          <div className="max-w-sm">
            <FileText className="mx-auto h-9 w-9 text-primary/70" />
            <div className="mt-3 text-sm font-semibold text-foreground">{copy.emptyTitle}</div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">{copy.emptyDetail}</p>
          </div>
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
    <div className="flex h-full min-h-0 bg-card">
      {historySidebar}
      <div className="flex min-w-0 flex-1 flex-col bg-card">
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
          {charts ? <span className="rounded bg-muted px-2 py-1 font-mono">{charts.length} {copy.charts}</span> : null}
          {result.persistedAt || result.generatedAt || result.updatedAt ? (
            <span>{copy.updated}: {new Date(result.persistedAt ?? result.generatedAt ?? result.updatedAt ?? "").toLocaleString(language === "zh" ? "zh-CN" : "en-US")}</span>
          ) : null}
        </div>
        </div>

        {table || charts ? (
          <div className="flex shrink-0 items-center gap-1 border-b border-border px-3 py-2">
          <button
            type="button"
            className={cn("flex h-7 items-center gap-1.5 rounded px-2 text-xs", activeView === "document" ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted")}
            onClick={() => setActiveView("document")}
          >
            <FileText className="h-3.5 w-3.5" />
            {copy.document}
          </button>
          {table ? (
            <button
              type="button"
              className={cn("flex h-7 items-center gap-1.5 rounded px-2 text-xs", activeView === "table" ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted")}
              onClick={() => setActiveView("table")}
            >
              <FileSpreadsheet className="h-3.5 w-3.5" />
              {copy.table}
            </button>
          ) : null}
          {charts ? (
            <button
              type="button"
              className={cn("flex h-7 items-center gap-1.5 rounded px-2 text-xs", activeView === "charts" ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted")}
              onClick={() => setActiveView("charts")}
            >
              <ChartSpline className="h-3.5 w-3.5" />
              {copy.charts}
            </button>
          ) : null}
          </div>
        ) : null}

        {activeView === "document" || (activeView === "table" && !table) || (activeView === "charts" && !charts) ? (
          <div className="min-h-0 flex-1 overflow-auto p-5">
            <SafeMarkdown className="mx-auto max-w-5xl">{result.markdown}</SafeMarkdown>
          </div>
        ) : activeView === "charts" && charts ? (
          <div className="min-h-0 flex-1 overflow-auto p-4">
            <ResearchResultCharts charts={charts} />
          </div>
        ) : table ? (
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
        ) : null}
      </div>
    </div>
  )
}
