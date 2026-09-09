import { useMemo } from "react"

import { VolatilityChart } from "@/components/charts"
import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import { useVolatilityAnalysis } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"

import { analyticsError } from "./analytics-utils"

export function VolatilityAnalysisWidget() {
  const { language } = useLanguage()
  const copy = language === "zh"
    ? { title: "波动率区间", low: "低波动", normal: "正常", high: "高波动", months: "个月", mean: "均值", share: "占比" }
    : { title: "Volatility Regimes", low: "low", normal: "normal", high: "high", months: "months", mean: "mean", share: "share" }
  const regimeLabels: Record<string, string> = {
    low: copy.low,
    normal: copy.normal,
    high: copy.high,
  }
  const [profile] = useDataProfile()
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
      title={copy.title}
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
      bodyPadding="compact"
    >
      <VolatilityChart data={chartData} height={280} />
      <div className="analytics-regime-grid">
        {(query.data?.regime_stats ?? []).map((item) => (
          <div key={item.regime}>
            <span>{regimeLabels[item.regime] ?? item.regime}</span>
            <strong>{item.n_months} {copy.months}</strong>
            <small>{formatPercent(item.mean, 1)} {copy.mean} · {formatPercent(item.proportion, 0)} {copy.share}</small>
          </div>
        ))}
      </div>
    </Widget>
  )
}
