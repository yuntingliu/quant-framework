import { CorrelationHeatmap } from "@/components/charts"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useMarketCorrelation } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { Widget } from "@/widgets/Widget"

import { AnalyticsFilters } from "./AnalyticsControls"
import { analyticsError } from "./analytics-utils"

export function CorrelationMatrixWidget() {
  const [profile, setProfile] = useDataProfile()
  const { startDate, endDate, selectedFactors } = useGlobalFilter()
  const query = useMarketCorrelation(profile, startDate, endDate, selectedFactors)
  return (
    <Widget
      title="Factor Correlation"
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
      actions={<AnalyticsFilters profile={profile} onProfileChange={setProfile} />}
      bodyPadding="compact"
    >
      <div className="analytics-correlation">
        <CorrelationHeatmap
          labels={query.data?.labels ?? []}
          matrix={(query.data?.matrix ?? []).map((row) => row.map((value) => value ?? 0))}
        />
      </div>
    </Widget>
  )
}
