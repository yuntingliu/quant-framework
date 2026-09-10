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

export function BacktestCompareWidget({ projectId, initialIds = [], embedded = false }: { projectId?: string; initialIds?: string[]; embedded?: boolean } = {}) {
  const { language } = useLanguage()
  const copy = embedded || language === "zh" ? {
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
    selected: "已选",
    loading: "正在读取对比结果…",
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
    selected: "Selected",
    loading: "Loading comparison…",
  }
  const [profile] = useDataProfile()
  const [records, setRecords] = useState<BacktestRecord[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [recordsError, setRecordsError] = useState("")
  const [loadingRecords, setLoadingRecords] = useState(true)
  const initialKey = initialIds.join(",")

  useEffect(() => {
    let current = true
    setLoadingRecords(true)
    setRecordsError("")
    setSelected([])
    api.get<BacktestRecord[]>("/backtests?limit=100")
      .then((items) => {
        if (!current) return
        const matching = items.filter((item) => projectId ? (item.project_id || item.strategy_id) === projectId : item.profile === profile)
        setRecords(matching)
        const requested = initialKey.split(",").filter((id) => matching.some((item) => item.id === id))
        setSelected(requested.length ? requested.slice(0, MAX_SELECTION) : matching.slice(0, 2).map((item) => item.id))
      })
      .catch((error: Error) => { if (current) setRecordsError(error.message) })
      .finally(() => { if (current) setLoadingRecords(false) })
    return () => { current = false }
  }, [profile, projectId, initialKey])

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

  const refresh = <button className="icon-command" type="button" title={copy.refresh} aria-label={copy.refresh} onClick={() => comparison.refetch()} disabled={selected.length < 2 || comparison.isFetching}><RefreshCw aria-hidden="true" className={comparison.isFetching ? "animate-spin" : ""} /></button>
  const content = <>
      <div className="backtest-comparison-heading"><strong>{copy.picker}</strong><span>{copy.selected} {selected.length} / {MAX_SELECTION}</span>{embedded ? refresh : null}</div>
      <div className="backtest-picker backtest-comparison-picker" role="group" aria-label={copy.picker}>
        {records.map((record) => (
          <label key={record.id} className={selected.includes(record.id) ? "selected" : ""} title={`${record.strategy_name || record.strategy_id} · ${record.run_at}`}>
            <input
              type="checkbox"
              checked={selected.includes(record.id)}
              disabled={!selected.includes(record.id) && selected.length >= MAX_SELECTION}
              onChange={() => toggle(record.id)}
            />
            <span>{record.strategy_name || record.strategy_id}</span>
            <small>{record.start_date} – {record.end_date}<br />{record.run_at.slice(0, 16).replace("T", " ")}</small>
          </label>
        ))}
      </div>
      {recordsError || comparison.error ? <div className="workbench-message error">{recordsError || analyticsError(comparison.error)}</div> : null}
      {loadingRecords ? <div className="analytics-empty">{copy.loading}</div> : records.length === 0 ? (
        <div className="analytics-empty">{profile} {copy.noBacktests}</div>
      ) : selected.length < 2 ? (
        <div className="analytics-empty">{copy.selectTwo}</div>
      ) : comparison.isPending ? <div className="analytics-empty">{copy.loading}</div> : comparison.data ? (
        <>
          {comparison.data?.warnings?.map((warning) => <p key={warning} className="workbench-message warning">{warning}</p>)}
          <CumulativeReturnsChart
            data={chartData}
            series={selected.map((id) => ({
              key: id,
              name: comparison.data?.labels[id] ?? records.find((record) => record.id === id)?.strategy_name ?? copy.strategy,
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
                    <td><strong>{String(row.strategy_name || row.strategy_id)}</strong></td>
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
      ) : null}
    </>
  return embedded
    ? <section className="backtest-comparison">{content}</section>
    : <Widget title={copy.title} actions={refresh} bodyPadding="compact">{content}</Widget>
}
