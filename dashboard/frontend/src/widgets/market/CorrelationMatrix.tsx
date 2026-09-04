import { CorrelationHeatmap } from "@/components/charts"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { useMarketCorrelation } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { Widget } from "@/widgets/Widget"

import { analyticsError } from "./analytics-utils"

export function CorrelationMatrixWidget() {
  const { language } = useLanguage()
  const [profile] = useDataProfile()
  const { startDate, endDate, selectedFactors } = useGlobalFilter()
  const query = useMarketCorrelation(profile, startDate, endDate, selectedFactors)
  return (
    <Widget
      title={language === "zh" ? "因子相关性" : "Factor Correlation"}
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
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
