import { useCallback } from "react"
import { Download } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useLanguage } from "@/contexts/LanguageContext"
import { cn } from "@/lib/utils"

interface CSVExportButtonProps {
  data: Record<string, unknown>[] | number[][]
  headers?: string[]
  filename: string
  label?: string
  className?: string
}

function escapeCSVField(value: unknown): string {
  if (value === null || value === undefined) return ""
  const str = String(value)
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`
  }
  return str
}

function generateCSV(
  data: Record<string, unknown>[] | number[][],
  headers?: string[]
): string {
  const rows: string[] = []

  if (Array.isArray(data) && data.length === 0) {
    return ""
  }

  if (Array.isArray(data[0])) {
    // number[][] format
    if (headers) {
      rows.push(headers.map(escapeCSVField).join(","))
    }
    for (const row of data as number[][]) {
      rows.push(row.map(escapeCSVField).join(","))
    }
  } else {
    // Record<string, unknown>[] format
    const records = data as Record<string, unknown>[]
    const keys = headers ?? Object.keys(records[0] ?? {})
    rows.push(keys.map(escapeCSVField).join(","))
    for (const record of records) {
      rows.push(keys.map((key) => escapeCSVField(record[key])).join(","))
    }
  }

  return rows.join("\n")
}

export function CSVExportButton({
  data,
  headers,
  filename,
  label,
  className,
}: CSVExportButtonProps) {
  const { language } = useLanguage()
  const resolvedLabel = label ?? (language === "zh" ? "导出 CSV" : "Export CSV")
  const handleExport = useCallback(() => {
    if (!data || data.length === 0) return

    const csv = generateCSV(data, headers)

    // UTF-8 BOM for Excel Chinese character support
    const BOM = "\uFEFF"
    const blob = new Blob([BOM + csv], { type: "text/csv;charset=utf-8;" })

    const url = URL.createObjectURL(blob)
    try {
      const link = document.createElement("a")
      link.href = url
      const safeFilename = filename.replace(/[\\/:*?"<>|]+/g, "-").replace(/\.csv$/i, "") || "export"
      link.download = `${safeFilename}.csv`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    } finally {
      URL.revokeObjectURL(url)
    }
  }, [data, headers, filename])

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={handleExport}
      disabled={!data || data.length === 0}
      className={cn("gap-2", className)}
    >
      <Download className="h-4 w-4" />
      {resolvedLabel}
    </Button>
  )
}
