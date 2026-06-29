import { useState, useMemo, useCallback } from "react"
import { ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react"
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

export interface ColumnDef {
  key: string
  label: string
  format?: "percent" | "number" | "string"
  decimals?: number
  align?: "left" | "center" | "right"
  priority?: "always" | "medium" | "wide"
}

interface DataTableProps<T extends Record<string, unknown> = Record<string, unknown>> {
  columns: ColumnDef[]
  data: T[]
  sortable?: boolean
  defaultSort?: { key: string; direction: "asc" | "desc" }
  onRowClick?: (row: T) => void
  selectedRowId?: string | null
  rowIdKey?: string
  loading?: boolean
  emptyMessage?: string
  className?: string
  highlightColumn?: string
  striped?: boolean
}

function formatCellValue(
  value: unknown,
  format?: "percent" | "number" | "string",
  decimals = 2
): string {
  if (value === null || value === undefined || value === "") {
    return "--"
  }

  const numValue = Number(value)

  switch (format) {
    case "percent":
      if (isNaN(numValue)) return String(value)
      return `${(numValue * 100).toFixed(decimals)}%`
    case "number":
      if (isNaN(numValue)) return String(value)
      return numValue.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    default:
      return String(value)
  }
}

function getAlignClass(align?: "left" | "center" | "right"): string {
  switch (align) {
    case "center":
      return "text-center"
    case "right":
      return "text-right"
    default:
      return "text-left"
  }
}

function getPriorityClass(priority: ColumnDef["priority"]): string {
  switch (priority) {
    case "medium":
      return "data-table-priority-medium"
    case "wide":
      return "data-table-priority-wide"
    default:
      return "data-table-priority-always"
  }
}

export function DataTable<T extends Record<string, unknown> = Record<string, unknown>>({
  columns,
  data,
  sortable = true,
  defaultSort,
  onRowClick,
  selectedRowId,
  rowIdKey = "id",
  loading = false,
  emptyMessage = "暂无数据",
  className,
  highlightColumn,
  striped = false,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(
    defaultSort?.key ?? null
  )
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">(
    defaultSort?.direction ?? "asc"
  )

  const handleSort = useCallback(
    (key: string) => {
      if (!sortable) return
      if (sortKey === key) {
        setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"))
      } else {
        setSortKey(key)
        setSortDirection("asc")
      }
    },
    [sortable, sortKey]
  )

  const sortedData = useMemo(() => {
    if (!sortKey) return data

    return [...data].sort((a, b) => {
      const aVal = a[sortKey]
      const bVal = b[sortKey]

      if (aVal === null || aVal === undefined) return 1
      if (bVal === null || bVal === undefined) return -1

      const aNum = Number(aVal)
      const bNum = Number(bVal)

      let comparison: number
      if (!isNaN(aNum) && !isNaN(bNum)) {
        comparison = aNum - bNum
      } else {
        comparison = String(aVal).localeCompare(String(bVal))
      }

      return sortDirection === "asc" ? comparison : -comparison
    })
  }, [data, sortKey, sortDirection])

  if (loading) {
    return (
      <div className={cn("data-table-container relative w-full min-w-0 overflow-auto", className)}>
        <Table className="w-full table-fixed">
          <TableHeader>
            <TableRow>
              {columns.map((col) => (
                <TableHead key={col.key} className={cn(getAlignClass(col.align), getPriorityClass(col.priority))}>
                  <span className="block truncate">{col.label}</span>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {Array.from({ length: 5 }).map((_, rowIdx) => (
              <TableRow key={rowIdx}>
                {columns.map((col) => (
                  <TableCell key={col.key} className={cn(getAlignClass(col.align), getPriorityClass(col.priority))}>
                    <div className="h-4 w-20 animate-pulse rounded bg-muted" />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    )
  }

  if (data.length === 0) {
    return (
      <div className={cn("data-table-container relative w-full min-w-0 overflow-auto", className)}>
        <Table className="w-full table-fixed">
          <TableHeader>
            <TableRow>
              {columns.map((col) => (
                <TableHead key={col.key} className={cn(getAlignClass(col.align), getPriorityClass(col.priority))}>
                  <span className="block truncate">{col.label}</span>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell
                colSpan={columns.length}
                className="h-24 text-center text-muted-foreground"
              >
                {emptyMessage}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    )
  }

  return (
    <div className={cn("data-table-container relative w-full min-w-0 overflow-auto", className)}>
      <Table className="w-full table-fixed">
        <TableHeader>
          <TableRow>
            {columns.map((col) => (
              <TableHead
                key={col.key}
                className={cn(
                  getAlignClass(col.align),
                  getPriorityClass(col.priority),
                  sortable && "cursor-pointer select-none hover:text-foreground"
                )}
                onClick={() => handleSort(col.key)}
              >
                <div
                  className={cn(
                    "flex min-w-0 items-center gap-1",
                    col.align === "center" && "justify-center",
                    col.align === "right" && "justify-end"
                  )}
                >
                  <span className="truncate">{col.label}</span>
                  {sortable && (
                    <span className="inline-flex shrink-0 flex-col">
                      {sortKey === col.key ? (
                        sortDirection === "asc" ? (
                          <ChevronUp className="h-3.5 w-3.5" />
                        ) : (
                          <ChevronDown className="h-3.5 w-3.5" />
                        )
                      ) : (
                        <ChevronsUpDown className="h-3.5 w-3.5 opacity-30" />
                      )}
                    </span>
                  )}
                </div>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortedData.map((row, rowIdx) => (
            <TableRow
              key={rowIdx}
              className={cn(
                onRowClick && "cursor-pointer",
                striped && rowIdx % 2 === 1 && "bg-muted/30",
                selectedRowId != null && String(row[rowIdKey]) === selectedRowId && "bg-primary/10 border-l-2 border-l-primary"
              )}
              onClick={() => onRowClick?.(row)}
            >
              {columns.map((col) => {
                const rawValue = row[col.key]
                const formatted = formatCellValue(
                  rawValue,
                  col.format,
                  col.decimals
                )
                const numValue = Number(rawValue)
                const isHighlighted = col.key === highlightColumn
                const isNumericFormat =
                  col.format === "percent" || col.format === "number"

                return (
                  <TableCell
                    key={col.key}
                    className={cn(
                      getAlignClass(col.align),
                      getPriorityClass(col.priority),
                      isNumericFormat && "font-mono font-tabular",
                      isHighlighted &&
                        !isNaN(numValue) &&
                        numValue > 0 &&
                        "text-profit",
                      isHighlighted &&
                        !isNaN(numValue) &&
                        numValue < 0 &&
                        "text-loss"
                    )}
                    title={formatted}
                  >
                    <span className="block truncate">{formatted}</span>
                  </TableCell>
                )
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
