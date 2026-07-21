import { useMemo } from "react"

import { DrawdownChart } from "@/components/charts"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useDrawdowns } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"

import { AnalyticsFilters } from "./AnalyticsControls"
import { analyticsError } from "./analytics-utils"

export function DrawdownAnalysisWidget() {
  const [profile, setProfile] = useDataProfile()
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
      title="Drawdown Analysis"
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
      actions={<AnalyticsFilters profile={profile} onProfileChange={setProfile} />}
      bodyPadding="compact"
    >
      <DrawdownChart data={chartData} height={250} />
      <div className="analytics-table-wrap">
        <table className="analytics-table compact">
          <thead>
            <tr><th>Start</th><th>Trough</th><th>End</th><th>Depth</th><th>Recovery</th></tr>
          </thead>
          <tbody>
            {(query.data?.top_drawdowns ?? []).map((period) => (
              <tr key={`${period.start}-${period.trough}`}>
                <td>{period.start}</td>
                <td>{period.trough}</td>
                <td>{period.end}</td>
                <td>{formatPercent(period.depth, 1)}</td>
                <td>{period.recovery_months == null ? "Open" : `${period.recovery_months} mo`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Widget>
  )
}
