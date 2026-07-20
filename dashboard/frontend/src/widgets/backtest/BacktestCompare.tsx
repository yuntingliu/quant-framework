import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"

import { CumulativeReturnsChart } from "@/components/charts"
import {
  api,
  type BacktestComparison,
  type BacktestRecord,
} from "@/lib/api"
import { useDataProfile } from "@/lib/data-profile"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"
import { ProfileSelect } from "@/widgets/market/AnalyticsControls"
import { analyticsError } from "@/widgets/market/analytics-utils"

const MAX_SELECTION = 6

function metric(value: string | number | null | undefined, kind: "pct" | "number"): string {
  if (typeof value !== "number") return "—"
  return kind === "pct" ? formatPercent(value, 1) : formatNumber(value, 2)
}

export function BacktestCompareWidget() {
  const [profile, setProfile] = useDataProfile()
  const [records, setRecords] = useState<BacktestRecord[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [recordsError, setRecordsError] = useState("")

  useEffect(() => {
    setRecordsError("")
    setSelected([])
    api.get<BacktestRecord[]>("/backtests?limit=100")
      .then((items) => {
        const matching = items.filter((item) => item.profile === profile)
        setRecords(matching)
        setSelected(matching.slice(0, 2).map((item) => item.id))
      })
      .catch((error: Error) => setRecordsError(error.message))
  }, [profile])

  const comparison = useQuery({
    queryKey: ["backtests", "compare", profile, selected],
    queryFn: () => api.post<BacktestComparison>("/backtests/compare", { ids: selected }),
    enabled: selected.length >= 2,
  })

  const chartData = useMemo(
    () =>
      (comparison.data?.dates ?? []).map((date, index) => {
        const point: Record<string, string | number> = { date }
        for (const id of selected) {
          const value = comparison.data?.series[id]?.[index]
          if (value != null) point[id] = value
        }
        return point
      }),
    [comparison.data, selected],
  )

  function toggle(id: string) {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((value) => value !== id)
      if (current.length >= MAX_SELECTION) return current
      return [...current, id]
    })
  }

  return (
    <Widget
      title="Strategy Compare"
      loading={records.length === 0 && !recordsError && comparison.isLoading}
      error={recordsError || analyticsError(comparison.error)}
      onRetry={() => comparison.refetch()}
      actions={
        <>
          <ProfileSelect profile={profile} onChange={setProfile} />
          <button
            className="icon-command"
            type="button"
            title="Refresh comparison"
            onClick={() => comparison.refetch()}
            disabled={selected.length < 2}
          >
            <RefreshCw aria-hidden="true" />
          </button>
        </>
      }
      bodyPadding="compact"
    >
      <div className="backtest-picker" aria-label="Backtests to compare">
        {records.map((record) => (
          <label key={record.id} title={`${record.strategy_id} · ${record.run_at}`}>
            <input
              type="checkbox"
              checked={selected.includes(record.id)}
              onChange={() => toggle(record.id)}
            />
            <span>{record.strategy_id}</span>
            <small>{record.id.slice(0, 6)}</small>
          </label>
        ))}
      </div>
      {records.length === 0 ? (
        <div className="analytics-empty">No persisted {profile} backtests.</div>
      ) : selected.length < 2 ? (
        <div className="analytics-empty">Select at least two saved runs.</div>
      ) : (
        <>
          <CumulativeReturnsChart
            data={chartData}
            series={selected.map((id) => ({
              key: id,
              name: comparison.data?.labels[id] ?? id,
            }))}
            height={290}
          />
          <div className="analytics-table-wrap">
            <table className="analytics-table compact">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Total</th>
                  <th>Annual</th>
                  <th>Volatility</th>
                  <th>Sharpe</th>
                  <th>Max DD</th>
                </tr>
              </thead>
              <tbody>
                {(comparison.data?.metrics ?? []).map((row) => (
                  <tr key={String(row.id)}>
                    <td><strong>{String(row.strategy_id)}</strong></td>
                    <td>{metric(row.total_return, "pct")}</td>
                    <td>{metric(row.annual_return, "pct")}</td>
                    <td>{metric(row.annual_vol, "pct")}</td>
                    <td>{metric(row.sharpe, "number")}</td>
                    <td>{metric(row.max_drawdown, "pct")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Widget>
  )
}
