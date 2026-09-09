import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"

import { CumulativeReturnsChart } from "@/components/charts"
import { useLanguage } from "@/contexts/LanguageContext"
import {
  api,
  type BacktestComparison,
  type BacktestRecord,
} from "@/lib/api"
import { useDataProfile } from "@/lib/data-profile"
import { formatNumber, formatPercent } from "@/lib/utils"
import { Widget } from "@/widgets/Widget"
import { analyticsError } from "@/widgets/market/analytics-utils"

const MAX_SELECTION = 6

function metric(value: string | number | null | undefined, kind: "pct" | "number"): string {
  if (typeof value !== "number") return "—"
  return kind === "pct" ? formatPercent(value, 1) : formatNumber(value, 2)
}

export function BacktestCompareWidget() {
  const { language } = useLanguage()
  const copy = language === "zh" ? {
    title: "策略对比",
    refresh: "刷新对比结果",
    picker: "选择要对比的回测",
    noBacktests: "没有已保存的回测。",
    selectTwo: "请至少选择两条回测记录。",
    strategy: "策略",
    range: "区间",
    total: "总收益",
    annual: "年化收益",
    volatility: "波动率",
    maxDrawdown: "最大回撤",
  } : {
    title: "Strategy Compare",
    refresh: "Refresh comparison",
    picker: "Backtests to compare",
    noBacktests: "has no persisted backtests.",
    selectTwo: "Select at least two saved runs.",
    strategy: "Strategy",
    range: "Range",
    total: "Total",
    annual: "Annual",
    volatility: "Volatility",
    maxDrawdown: "Max DD",
  }
  const [profile] = useDataProfile()
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
      title={copy.title}
      loading={records.length === 0 && !recordsError && comparison.isLoading}
      error={recordsError || analyticsError(comparison.error)}
      onRetry={() => comparison.refetch()}
      actions={
        <button
            className="icon-command"
            type="button"
            title={copy.refresh}
            onClick={() => comparison.refetch()}
            disabled={selected.length < 2}
          >
            <RefreshCw aria-hidden="true" />
          </button>
      }
      bodyPadding="compact"
    >
      <div className="backtest-picker" aria-label={copy.picker}>
        {records.map((record) => (
          <label key={record.id} title={`${record.strategy_id} · ${record.run_at}`}>
            <input
              type="checkbox"
              checked={selected.includes(record.id)}
              onChange={() => toggle(record.id)}
            />
            <span>{record.strategy_id}</span>
            <small>{record.start_date.slice(0, 7)}–{record.end_date.slice(0, 7)} · {record.run_at.slice(0, 10)}</small>
          </label>
        ))}
      </div>
      {records.length === 0 ? (
        <div className="analytics-empty">{profile} {copy.noBacktests}</div>
      ) : selected.length < 2 ? (
        <div className="analytics-empty">{copy.selectTwo}</div>
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
                  <th>{copy.strategy}</th>
                  <th>{copy.range}</th>
                  <th>{copy.total}</th>
                  <th>{copy.annual}</th>
                  <th>{copy.volatility}</th>
                  <th>Sharpe</th>
                  <th>{copy.maxDrawdown}</th>
                </tr>
              </thead>
              <tbody>
                {(comparison.data?.metrics ?? []).map((row) => (
                  <tr key={String(row.id)}>
                    <td><strong>{String(row.strategy_id)}</strong></td>
                    <td>{String(row.start_date).slice(0, 7)}–{String(row.end_date).slice(0, 7)}</td>
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
