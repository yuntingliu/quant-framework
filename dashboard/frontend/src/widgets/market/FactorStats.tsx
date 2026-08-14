import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
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
  const { language } = useLanguage()
  const copy = language === "zh"
    ? { title: "因子统计", factor: "因子", annual: "年化收益", volatility: "波动率", maxDrawdown: "最大回撤", positive: "正收益占比", skew: "偏度", kurtosis: "峰度" }
    : { title: "Factor Statistics", factor: "Factor", annual: "Annual", volatility: "Volatility", maxDrawdown: "Max DD", positive: "Positive", skew: "Skew", kurtosis: "Kurtosis" }
  const [profile, setProfile] = useDataProfile()
  const { startDate, endDate } = useGlobalFilter()
  const query = useFactorStats(profile, startDate, endDate)
  return (
    <Widget
      title={copy.title}
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
              <th>{copy.factor}</th>
              <th>{copy.annual}</th>
              <th>{copy.volatility}</th>
              <th>Sharpe</th>
              <th>{copy.maxDrawdown}</th>
              <th>{copy.positive}</th>
              <th>{copy.skew}</th>
              <th>{copy.kurtosis}</th>
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
