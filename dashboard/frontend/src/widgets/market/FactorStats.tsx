import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useFactorStats } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"

import { AnalyticsFilters } from "./AnalyticsControls"
import { analyticsError } from "./analytics-utils"

function pct(value: number | null): string {
  return value == null ? "—" : formatPercent(value, 1)
}

function num(value: number | null): string {
  return value == null ? "—" : formatNumber(value, 2)
}

export function FactorStatsWidget() {
  const [profile, setProfile] = useDataProfile()
  const { startDate, endDate } = useGlobalFilter()
  const query = useFactorStats(profile, startDate, endDate)
  return (
    <Widget
      title="Factor Statistics"
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
      actions={<AnalyticsFilters profile={profile} onProfileChange={setProfile} />}
      bodyPadding="none"
    >
      <div className="analytics-table-wrap">
        <table className="analytics-table">
          <thead>
            <tr>
              <th>Factor</th>
              <th>Annual</th>
              <th>Volatility</th>
              <th>Sharpe</th>
              <th>Max DD</th>
              <th>Positive</th>
              <th>Skew</th>
              <th>Kurtosis</th>
            </tr>
          </thead>
          <tbody>
            {(query.data?.stats ?? []).map((row) => (
              <tr key={row.factor}>
                <td><strong>{row.factor}</strong></td>
                <td>{pct(row.ann_return)}</td>
                <td>{pct(row.ann_vol)}</td>
                <td>{num(row.sharpe)}</td>
                <td>{pct(row.max_dd)}</td>
                <td>{pct(row.pos_ratio)}</td>
                <td>{num(row.skew)}</td>
                <td>{num(row.kurt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Widget>
  )
}
