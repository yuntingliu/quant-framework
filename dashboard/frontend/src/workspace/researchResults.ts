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

export interface AgentResearchResult {
  id: string
  version: 1
  requestId: string
  kind: "document"
  title: string
  description?: string
  markdown: string
  table?: ResearchResultTable
  sources: string[]
  generatedAt?: string
  runId?: string
  artifactId?: string
  updatedAt?: string
}

interface ResearchResultMetadata {
  markdown?: string
  runId?: string
  artifactId?: string
  updatedAt?: string
}

const FORMATS = new Set<ResearchResultFormat>(["text", "number", "percent", "date", "datetime"])
const MAX_COLUMNS = 30
const MAX_ROWS = 1_000
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

export function parseAgentResearchResult(
  value: unknown,
  metadata: ResearchResultMetadata = {},
): AgentResearchResult | null {
  if (!isRecord(value) || value.version !== 1 || value.kind !== "document") return null
  if (JSON.stringify(value).length > MAX_DESCRIPTOR_LENGTH) return null
  const requestId = text(value.requestId, 200)
  const title = text(value.title, 200)
  if (!requestId || !title) return null

  const table = parseTable(value)
  if (table === null) return null
  const description = value.description === undefined ? undefined : text(value.description, 2_000)
  if (value.description !== undefined && description === undefined) return null
  const markdown = text(metadata.markdown ?? value.markdown, MAX_MARKDOWN_LENGTH)
    ?? (table ? tableFallbackMarkdown(title, description, table) : undefined)
  if (!markdown) return null
  if (value.sources !== undefined && (!Array.isArray(value.sources) || value.sources.length > MAX_SOURCES)) return null
  const sources = (value.sources ?? []).map((source) => text(source, 500))
  if (sources.some((source) => source === undefined)) return null
  const generatedAt = value.generatedAt === undefined ? undefined : text(value.generatedAt, 100)
  if (value.generatedAt !== undefined && generatedAt === undefined) return null
  if (generatedAt && Number.isNaN(Date.parse(generatedAt))) return null

  return {
    id: requestId,
    version: 1,
    requestId,
    kind: "document",
    title,
    ...(description ? { description } : {}),
    markdown,
    ...(table ? { table } : {}),
    sources: sources as string[],
    ...(generatedAt ? { generatedAt } : {}),
    ...(metadata.runId ? { runId: metadata.runId } : {}),
    ...(metadata.artifactId ? { artifactId: metadata.artifactId } : {}),
    ...(metadata.updatedAt ? { updatedAt: metadata.updatedAt } : {}),
  }
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
