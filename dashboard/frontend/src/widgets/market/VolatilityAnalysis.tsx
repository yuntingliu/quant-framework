import { useMemo } from "react"

import { VolatilityChart } from "@/components/charts"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useVolatilityAnalysis } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"

import { AnalyticsFilters } from "./AnalyticsControls"
import { analyticsError } from "./analytics-utils"

export function VolatilityAnalysisWidget() {
  const [profile, setProfile] = useDataProfile()
  const { startDate, endDate } = useGlobalFilter()
  const query = useVolatilityAnalysis(profile, startDate, endDate)
  const chartData = useMemo(
    () =>
      (query.data?.dates ?? []).map((date, index) => ({
        date,
        volatility: query.data?.vol_values[index] ?? 0,
        regime: (query.data?.regimes[index] ?? "normal") as "low" | "normal" | "high",
      })),
    [query.data],
  )
  return (
    <Widget
      title="Volatility Regimes"
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
      actions={<AnalyticsFilters profile={profile} onProfileChange={setProfile} />}
      bodyPadding="compact"
    >
      <VolatilityChart data={chartData} height={280} />
      <div className="analytics-regime-grid">
        {(query.data?.regime_stats ?? []).map((item) => (
          <div key={item.regime}>
            <span>{item.regime}</span>
            <strong>{item.n_months} months</strong>
            <small>{formatPercent(item.mean, 1)} mean · {formatPercent(item.proportion, 0)} share</small>
          </div>
        ))}
      </div>
    </Widget>
  )
}
