export type ResearchResultCell = string | number | boolean | null
export type ResearchResultFormat = "text" | "number" | "percent" | "date" | "datetime"

export interface ResearchResultColumn {
  key: string
  label: string
  format: ResearchResultFormat
}

export interface ResearchResultTable {
  columns: ResearchResultColumn[]
  rows: Array<Record<string, ResearchResultCell>>
}

export type ResearchResultChartType = "line" | "bar" | "area" | "scatter" | "pie"
export type ResearchResultChartFormat = "number" | "percent"

export interface ResearchResultChartSeries {
  key: string
  label: string
  format: ResearchResultChartFormat
  color?: string
}

export interface ResearchResultChart {
  id: string
  type: ResearchResultChartType
  title: string
  description?: string
  xKey: string
  xLabel?: string
  yLabel?: string
  series: ResearchResultChartSeries[]
  rows: Array<Record<string, ResearchResultCell>>
}

export interface AgentResearchResult {
  id: string
  version: 1
  reportId: string
  requestId?: string
  kind: "document"
  title: string
  description?: string
  markdown: string
  table?: ResearchResultTable
  charts?: ResearchResultChart[]
  sources: string[]
  generatedAt?: string
  runId?: string
  artifactId?: string
  updatedAt?: string
  profile?: "demo" | "runtime"
  provenance?: Record<string, unknown>
  persistedAt?: string
  backtestId?: string
}

interface ResearchResultMetadata {
  markdown?: string
  runId?: string
  artifactId?: string
  updatedAt?: string
}

const FORMATS = new Set<ResearchResultFormat>(["text", "number", "percent", "date", "datetime"])
const CHART_TYPES = new Set<ResearchResultChartType>(["line", "bar", "area", "scatter", "pie"])
const CHART_FORMATS = new Set<ResearchResultChartFormat>(["number", "percent"])
const MAX_COLUMNS = 30
const MAX_ROWS = 1_000
const MAX_CHARTS = 6
const MAX_CHART_SERIES = 12
const MAX_CHART_ROWS = 500
const MAX_SOURCES = 20
const MAX_DESCRIPTOR_LENGTH = 600_000
const MAX_MARKDOWN_LENGTH = 500_000

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function text(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") return undefined
  const result = value.trim()
  return result && result.length <= maxLength ? result : undefined
}

function cell(value: unknown): ResearchResultCell | undefined {
  if (value === null || typeof value === "boolean") return value
  if (typeof value === "string" && value.length <= 2_000) return value
  if (typeof value === "number" && Number.isFinite(value)) return value
  return undefined
}

function parseTable(value: Record<string, unknown>): ResearchResultTable | null | undefined {
  const hasColumns = value.columns !== undefined
  const hasRows = value.rows !== undefined
  if (!hasColumns && !hasRows) return undefined
  if (!Array.isArray(value.columns) || !Array.isArray(value.rows)) return null
  if (value.columns.length === 0 || value.columns.length > MAX_COLUMNS || value.rows.length > MAX_ROWS) return null

  const columns: ResearchResultColumn[] = []
  const keys = new Set<string>()
  for (const rawColumn of value.columns) {
    if (!isRecord(rawColumn)) return null
    const key = text(rawColumn.key, 80)
    const label = text(rawColumn.label, 120)
    const format = rawColumn.format === undefined ? "text" : rawColumn.format
    if (!key || !label || keys.has(key) || key === "__proto__" || key === "constructor" || key === "prototype") return null
    if (typeof format !== "string" || !FORMATS.has(format as ResearchResultFormat)) return null
    keys.add(key)
    columns.push({ key, label, format: format as ResearchResultFormat })
  }

  const rows: Array<Record<string, ResearchResultCell>> = []
  for (const rawRow of value.rows) {
    if (!isRecord(rawRow)) return null
    const row: Record<string, ResearchResultCell> = {}
    for (const column of columns) {
      const parsed = cell(rawRow[column.key])
      if (parsed === undefined) return null
      row[column.key] = parsed
    }
    rows.push(row)
  }
  return { columns, rows }
}

function safeKey(value: unknown): string | undefined {
  const key = text(value, 80)
  return key && key !== "__proto__" && key !== "constructor" && key !== "prototype"
    ? key
    : undefined
}

function parseCharts(value: Record<string, unknown>): ResearchResultChart[] | null | undefined {
  if (value.charts === undefined) return undefined
  if (!Array.isArray(value.charts) || value.charts.length === 0 || value.charts.length > MAX_CHARTS) return null

  const charts: ResearchResultChart[] = []
  const chartIds = new Set<string>()
  for (const rawChart of value.charts) {
    if (!isRecord(rawChart)) return null
    const id = safeKey(rawChart.id)
    const title = text(rawChart.title, 200)
    const type = typeof rawChart.type === "string" && CHART_TYPES.has(rawChart.type as ResearchResultChartType)
      ? rawChart.type as ResearchResultChartType
      : undefined
    const xKey = safeKey(rawChart.xKey)
    if (!id || chartIds.has(id) || !title || !type || !xKey) return null
    chartIds.add(id)

    const description = rawChart.description === undefined ? undefined : text(rawChart.description, 1_000)
    const xLabel = rawChart.xLabel === undefined ? undefined : text(rawChart.xLabel, 120)
    const yLabel = rawChart.yLabel === undefined ? undefined : text(rawChart.yLabel, 120)
    if (
      (rawChart.description !== undefined && !description)
      || (rawChart.xLabel !== undefined && !xLabel)
      || (rawChart.yLabel !== undefined && !yLabel)
      || !Array.isArray(rawChart.series)
      || rawChart.series.length === 0
      || rawChart.series.length > MAX_CHART_SERIES
      || !Array.isArray(rawChart.rows)
      || rawChart.rows.length === 0
      || rawChart.rows.length > MAX_CHART_ROWS
    ) return null

    const series: ResearchResultChartSeries[] = []
    const seriesKeys = new Set<string>()
    for (const rawSeries of rawChart.series) {
      if (!isRecord(rawSeries)) return null
      const key = safeKey(rawSeries.key)
      const label = text(rawSeries.label, 120)
      const format = rawSeries.format === undefined ? "number" : rawSeries.format
      const color = rawSeries.color === undefined ? undefined : text(rawSeries.color, 7)
      if (
        !key
        || key === xKey
        || seriesKeys.has(key)
        || !label
        || typeof format !== "string"
        || !CHART_FORMATS.has(format as ResearchResultChartFormat)
        || (color !== undefined && !/^#[0-9a-f]{6}$/i.test(color))
      ) return null
      seriesKeys.add(key)
      series.push({
        key,
        label,
        format: format as ResearchResultChartFormat,
        ...(color ? { color } : {}),
      })
    }
    if (type === "pie" && series.length !== 1) return null

    const rows: Array<Record<string, ResearchResultCell>> = []
    for (const rawRow of rawChart.rows) {
      if (!isRecord(rawRow)) return null
      const xValue = cell(rawRow[xKey])
      if (
        xValue === undefined
        || xValue === null
        || typeof xValue === "boolean"
        || (type === "scatter" && typeof xValue !== "number")
      ) return null
      const row: Record<string, ResearchResultCell> = { [xKey]: xValue }
      for (const item of series) {
        const parsed = cell(rawRow[item.key])
        if (parsed === undefined || (parsed !== null && typeof parsed !== "number")) return null
        row[item.key] = parsed
      }
      rows.push(row)
    }
    charts.push({
      id,
      type,
      title,
      ...(description ? { description } : {}),
      xKey,
      ...(xLabel ? { xLabel } : {}),
      ...(yLabel ? { yLabel } : {}),
      series,
      rows,
    })
  }
  return charts
}

function markdownTableCell(value: ResearchResultCell, column: ResearchResultColumn): string {
  if (value === null) return "--"
  const displayed = column.format === "percent" && typeof value === "number"
    ? `${value * 100}%`
    : String(value)
  return displayed.replace(/\|/g, "\\|").replace(/[\r\n]+/g, " ").slice(0, 240)
}

function tableFallbackMarkdown(
  title: string,
  description: string | undefined,
  table: ResearchResultTable,
): string {
  const visibleRows = table.rows.slice(0, 100)
  const lines = [
    `# ${title}`,
    ...(description ? ["", description] : []),
    "",
    `| ${table.columns.map((column) => column.label.replace(/\|/g, "\\|")).join(" | ")} |`,
    `| ${table.columns.map(() => "---").join(" | ")} |`,
    ...visibleRows.map((row) => `| ${table.columns.map((column) => markdownTableCell(row[column.key], column)).join(" | ")} |`),
  ]
  if (visibleRows.length < table.rows.length) {
    lines.push("", `仅在文档预览中显示前 ${visibleRows.length} 行；完整 ${table.rows.length} 行请切换到“数据表”。`)
  }
  return lines.join("\n")
}

function chartFallbackMarkdown(
  title: string,
  description: string | undefined,
  charts: ResearchResultChart[],
): string {
  return [
    `# ${title}`,
    ...(description ? ["", description] : []),
    "",
    `该结果包含 ${charts.length} 个交互图表，请切换到“图表”视图查看。`,
    "",
    ...charts.flatMap((chart) => [`- ${chart.title}`, ...(chart.description ? [`  ${chart.description}`] : [])]),
  ].join("\n")
}

export function parseAgentResearchResult(
  value: unknown,
  metadata: ResearchResultMetadata = {},
): AgentResearchResult | null {
  if (!isRecord(value) || value.version !== 1 || value.kind !== "document") return null
  if (JSON.stringify(value).length > MAX_DESCRIPTOR_LENGTH) return null
  const reportId = text(value.reportId ?? value.artifactId ?? value.projectId, 200)
  const requestId = value.requestId === undefined ? undefined : text(value.requestId, 200)
  const title = text(value.title, 200)
  if (!reportId || !/^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$/.test(reportId) || !title) return null
  if (value.requestId !== undefined && !requestId) return null

  if (value.table !== undefined && !isRecord(value.table)) return null
  const table = parseTable(isRecord(value.table) ? value.table : value)
  if (table === null) return null
  const charts = parseCharts(value)
  if (charts === null) return null
  const description = value.description === undefined ? undefined : text(value.description, 2_000)
  if (value.description !== undefined && description === undefined) return null
  const markdown = text(metadata.markdown ?? value.markdown, MAX_MARKDOWN_LENGTH)
    ?? (table
      ? tableFallbackMarkdown(title, description, table)
      : charts
        ? chartFallbackMarkdown(title, description, charts)
        : undefined)
  if (!markdown) return null
  if (value.sources !== undefined && (!Array.isArray(value.sources) || value.sources.length > MAX_SOURCES)) return null
  const sources = (value.sources ?? []).map((source) => text(source, 500))
  if (sources.some((source) => source === undefined)) return null
  const generatedAt = value.generatedAt === undefined ? undefined : text(value.generatedAt, 100)
  if (value.generatedAt !== undefined && generatedAt === undefined) return null
  if (generatedAt && Number.isNaN(Date.parse(generatedAt))) return null
  const profile = value.profile === "demo" || value.profile === "runtime" ? value.profile : undefined
  const provenance = isRecord(value.provenance) ? value.provenance : undefined
  const persistedAt = value.persistedAt === undefined ? undefined : text(value.persistedAt, 100)
  if (value.persistedAt !== undefined && !persistedAt) return null
  const backtestId = value.backtestId === undefined ? undefined : text(value.backtestId, 200)
  if (value.backtestId !== undefined && !backtestId) return null
  const runId = metadata.runId ?? (value.runId === undefined ? undefined : text(value.runId, 200))
  const artifactId = metadata.artifactId ?? (value.artifactId === undefined ? undefined : text(value.artifactId, 200))
  const updatedAt = metadata.updatedAt ?? (value.updatedAt === undefined ? undefined : text(value.updatedAt, 100))
  if (
    (value.runId !== undefined && !runId)
    || (value.artifactId !== undefined && !artifactId)
    || (value.updatedAt !== undefined && !updatedAt)
  ) return null

  return {
    id: reportId,
    version: 1,
    reportId,
    ...(requestId ? { requestId } : {}),
    kind: "document",
    title,
    ...(description ? { description } : {}),
    markdown,
    ...(table ? { table } : {}),
    ...(charts ? { charts } : {}),
    sources: sources as string[],
    ...(generatedAt ? { generatedAt } : {}),
    ...(runId ? { runId } : {}),
    ...(artifactId ? { artifactId } : {}),
    ...(updatedAt ? { updatedAt } : {}),
    ...(profile ? { profile } : {}),
    ...(provenance ? { provenance } : {}),
    ...(persistedAt ? { persistedAt } : {}),
    ...(backtestId ? { backtestId } : {}),
  }
}

export const ALPHALAB_REPORT_DESCRIPTION = "AlphaLab quantitative report"

export function parseWorkspaceReportNode(value: unknown): AgentResearchResult | null {
  if (!isRecord(value) || (value.type !== "note" && value.type !== "document")) return null
  if (value.description !== ALPHALAB_REPORT_DESCRIPTION) return null
  const values = isRecord(value.values) ? value.values : null
  const markdown = values ? text(values.content, MAX_MARKDOWN_LENGTH) : undefined
  const reportId = text(value.id, 200)
  const title = text(value.label, 200)
  const updatedAt = text(value.updatedAt, 100)
  if (!markdown || !reportId || !title) return null
  return parseAgentResearchResult({
    version: 1,
    reportId,
    kind: "document",
    title,
    markdown,
    sources: [],
    ...(updatedAt ? { updatedAt } : {}),
  })
}

function csvCell(value: ResearchResultCell): string {
  const raw = value === null ? "" : String(value)
  return /[",\r\n]/.test(raw) ? `"${raw.replace(/"/g, '""')}"` : raw
}

export function researchResultToCsv(table: ResearchResultTable): string {
  const header = table.columns.map((column) => csvCell(column.label)).join(",")
  const rows = table.rows.map((row) => table.columns.map((column) => csvCell(row[column.key])).join(","))
  return `\uFEFF${[header, ...rows].join("\r\n")}`
}
