import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useMarketKPI } from "@/hooks"
import { useDataProfile } from "@/lib/data-profile"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"

import { AnalyticsFilters } from "./AnalyticsControls"
import { analyticsError } from "./analytics-utils"

function pct(value: number | null): string {
  return value == null ? "—" : formatPercent(value, 1)
}

function ratio(value: number | null): string {
  return value == null ? "—" : formatNumber(value, 2)
}

export function MarketKPIWidget() {
  const [profile, setProfile] = useDataProfile()
  const { startDate, endDate } = useGlobalFilter()
  const query = useMarketKPI(profile, startDate, endDate)
  const data = query.data
  const metrics = [
    ["Range", data?.data_start && data.data_end ? `${data.data_start.slice(0, 7)} – ${data.data_end.slice(0, 7)}` : "—"],
    ["Months", data ? String(data.n_months) : "—"],
    ["MKT annual", pct(data?.mkt_ann_return ?? null)],
    ["MKT Sharpe", ratio(data?.mkt_sharpe_full ?? null)],
    ["SMB annual", pct(data?.smb_ann_return ?? null)],
    ["HML annual", pct(data?.hml_ann_return ?? null)],
    ["Latest vol", pct(data?.latest_vol ?? null)],
    ["Vol regime", data?.current_regime ?? "—"],
  ]
  return (
    <Widget
      title="Market KPI"
      loading={query.isLoading}
      error={analyticsError(query.error)}
      onRetry={() => query.refetch()}
      actions={<AnalyticsFilters profile={profile} onProfileChange={setProfile} showDates />}
      bodyPadding="compact"
    >
      <div className="analytics-kpi-grid">
        {metrics.map(([label, value]) => (
          <div className="analytics-kpi" key={label}>
            <span>{label}</span>
            <strong title={value}>{value}</strong>
          </div>
        ))}
      </div>
    </Widget>
  )
}
