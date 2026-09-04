import { useMemo } from "react"

import { DrawdownChart } from "@/components/charts"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { useDrawdowns } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"

import { analyticsError } from "./analytics-utils"

export function DrawdownAnalysisWidget() {
  const { language } = useLanguage()
  const copy = language === "zh"
    ? { title: "回撤分析", start: "开始", trough: "谷底", end: "结束", depth: "深度", recovery: "修复", open: "尚未修复", months: "个月" }
    : { title: "Drawdown Analysis", start: "Start", trough: "Trough", end: "End", depth: "Depth", recovery: "Recovery", open: "Open", months: "mo" }
  const [profile] = useDataProfile()
  const { startDate, endDate } = useGlobalFilter()
  const query = useDrawdowns(profile, startDate, endDate, 5)
  const chartData = useMemo(
    () =>
      (query.data?.dates ?? []).map((date, index) => ({
        date,
        strategy: query.data?.drawdown_values[index] ?? 0,
      })),
    [query.data],
  )
  return (
    <Widget
      title={copy.title}
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
      bodyPadding="compact"
    >
      <DrawdownChart data={chartData} height={250} />
      <div className="analytics-table-wrap">
        <table className="analytics-table compact">
          <thead>
            <tr><th>{copy.start}</th><th>{copy.trough}</th><th>{copy.end}</th><th>{copy.depth}</th><th>{copy.recovery}</th></tr>
          </thead>
          <tbody>
            {(query.data?.top_drawdowns ?? []).map((period) => (
              <tr key={`${period.start}-${period.trough}`}>
                <td>{period.start}</td>
                <td>{period.trough}</td>
                <td>{period.end}</td>
                <td>{formatPercent(period.depth, 1)}</td>
                <td>{period.recovery_months == null ? copy.open : `${period.recovery_months} ${copy.months}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Widget>
  )
}
