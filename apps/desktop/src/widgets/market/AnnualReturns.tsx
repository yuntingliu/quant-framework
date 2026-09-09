import { useMemo } from "react"

import { AnnualReturnsChart } from "@/components/charts"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { useAnnualReturns } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { Widget } from "@/widgets/Widget"

import { analyticsError } from "./analytics-utils"

export function AnnualReturnsWidget() {
  const { language } = useLanguage()
  const [profile] = useDataProfile()
  const { startDate, endDate, selectedFactors } = useGlobalFilter()
  const query = useAnnualReturns(profile, startDate, endDate, selectedFactors)
  const chartData = useMemo(
    () =>
      (query.data?.years ?? []).map((year, index) => {
        const point: Record<string, string | number> = { year }
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
      title={language === "zh" ? "年度收益" : "Annual Returns"}
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
      bodyPadding="compact"
    >
      <AnnualReturnsChart
        data={chartData}
        series={selectedFactors.map((factor) => ({ key: factor, name: factor }))}
        height={340}
      />
    </Widget>
  )
}
