import { TrendingUp, TrendingDown } from "lucide-react"
import { cn } from "@/lib/utils"

interface StatItem {
  label: string
  value: string | number
  format?: "percent" | "number" | "ratio"
  change?: number
}

interface StatsGridProps {
  stats: StatItem[]
  columns?: 2 | 3 | 4 | 5
  className?: string
}

function formatStatValue(
  value: string | number,
  format?: "percent" | "number" | "ratio"
): string {
  if (typeof value === "string") return value

  switch (format) {
    case "percent":
      return `${(value * 100).toFixed(2)}%`
    case "number":
      return value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    case "ratio":
      return value.toFixed(3)
    default:
      return typeof value === "number" ? value.toLocaleString() : String(value)
  }
}

const gridColsClass = {
  2: "grid-cols-2",
  3: "grid-cols-2 sm:grid-cols-3",
  4: "grid-cols-2 sm:grid-cols-4",
  5: "grid-cols-2 sm:grid-cols-3 lg:grid-cols-5",
} as const

export function StatsGrid({
  stats,
  columns = 4,
  className,
}: StatsGridProps) {
  return (
    <div className={cn("grid gap-4", gridColsClass[columns], className)}>
      {stats.map((stat) => {
        const isPositive = stat.change !== undefined && stat.change >= 0
        const isNegative = stat.change !== undefined && stat.change < 0

        return (
          <div key={stat.label} className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground truncate">
              {stat.label}
            </p>
            <p className="text-lg font-bold font-mono font-tabular tracking-tight">
              {formatStatValue(stat.value, stat.format)}
            </p>
            {stat.change !== undefined && (
              <div
                className={cn(
                  "flex items-center gap-1 text-xs font-medium",
                  isPositive && "text-profit",
                  isNegative && "text-loss"
                )}
              >
                {isPositive ? (
                  <TrendingUp className="h-3 w-3" />
                ) : (
                  <TrendingDown className="h-3 w-3" />
                )}
                <span className="font-mono font-tabular">
                  {isPositive ? "+" : ""}
                  {stat.change.toFixed(2)}%
                </span>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
