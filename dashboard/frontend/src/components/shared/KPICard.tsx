import { type LucideIcon, TrendingUp, TrendingDown } from "lucide-react"
import { AnimatePresence, motion } from "framer-motion"
import { Card, CardContent } from "@/components/ui/card"
import { SparkLine } from "@/components/charts/SparkLine"
import { cn } from "@/lib/utils"

interface KPICardProps {
  title: string
  value: string | number
  change?: number
  prefix?: string
  suffix?: string
  icon?: LucideIcon
  loading?: boolean
  className?: string
  variant?: "default" | "highlight" | "compact"
  /** Optional sparkline data (recent values for trend) */
  sparkline?: number[]
}

export function KPICard({
  title,
  value,
  change,
  prefix,
  suffix,
  icon: Icon,
  loading = false,
  className,
  variant = "default",
  sparkline,
}: KPICardProps) {
  if (loading) {
    return (
      <Card className={cn("kpi-card min-w-0 overflow-hidden card-glow", className)}>
        <CardContent className={cn("p-4", variant === "compact" && "p-3")}>
          <div className="space-y-2">
            <div className="h-3 w-20 animate-pulse rounded bg-muted" />
            <div className="h-7 w-28 animate-pulse rounded bg-muted" />
          </div>
        </CardContent>
      </Card>
    )
  }

  const formattedValue =
    typeof value === "number" ? value.toLocaleString() : value

  const isPositive = change !== undefined && change >= 0
  const isNegative = change !== undefined && change < 0

  return (
    <Card
      className={cn(
        "kpi-card min-w-0 overflow-hidden card-glow transition-colors",
        variant === "highlight" && "border-primary/20 bg-primary/[0.03]",
        className
      )}
    >
      <CardContent className={cn("p-4", variant === "compact" && "p-3")}>
        <div className="flex min-w-0 items-start justify-between gap-2">
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider truncate">
              {title}
            </p>

            <div className="flex min-w-0 items-baseline gap-1">
              {prefix && (
                <span className="shrink-0 text-sm font-medium text-muted-foreground">
                  {prefix}
                </span>
              )}
              <AnimatePresence mode="wait">
                <motion.span
                  key={String(value)}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                  className={cn(
                    "min-w-0 truncate font-bold font-mono font-tabular tracking-tight",
                    variant === "compact" ? "text-lg" : "text-xl"
                  )}
                  title={formattedValue}
                >
                  {formattedValue}
                </motion.span>
              </AnimatePresence>
              {suffix && (
                <span className="shrink-0 text-sm font-medium text-muted-foreground">
                  {suffix}
                </span>
              )}
            </div>

            {change !== undefined && (
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
                  {change.toFixed(2)}%
                </span>
              </div>
            )}
          </div>

          {sparkline && sparkline.length > 1 ? (
            <div className="kpi-card-sparkline shrink-0">
              <SparkLine data={sparkline} width={56} height={24} />
            </div>
          ) : Icon ? (
            <div className="kpi-card-icon shrink-0 rounded bg-secondary/80 p-2">
              <Icon className="h-4 w-4 text-muted-foreground" />
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
