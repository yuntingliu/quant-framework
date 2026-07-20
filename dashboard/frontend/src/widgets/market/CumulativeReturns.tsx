import { useMemo } from "react"

import { CumulativeReturnsChart } from "@/components/charts"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useCumulativeReturns } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { Widget } from "@/widgets/Widget"

import { AnalyticsFilters } from "./AnalyticsControls"
import { analyticsError } from "./analytics-utils"

export function CumulativeReturnsWidget() {
  const [profile, setProfile] = useDataProfile()
  const { startDate, endDate, selectedFactors } = useGlobalFilter()
  const query = useCumulativeReturns(profile, startDate, endDate, selectedFactors)
  const chartData = useMemo(
    () =>
      (query.data?.dates ?? []).map((date, index) => {
        const point: Record<string, string | number> = { date }
        for (const factor of selectedFactors) {
          const value = query.data?.series[factor]?.[index]
          if (value != null) point[factor] = value
        }
        return point
      }),
    [query.data, selectedFactors],
  )
  return (
    <Widget
      title="Cumulative Returns"
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
      actions={<AnalyticsFilters profile={profile} onProfileChange={setProfile} />}
      bodyPadding="compact"
    >
      <CumulativeReturnsChart
        data={chartData}
        series={selectedFactors.map((factor) => ({ key: factor, name: factor }))}
        height={340}
        yAxisFormat="percent"
      />
    </Widget>
  )
}
