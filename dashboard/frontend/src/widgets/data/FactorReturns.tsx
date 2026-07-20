import { useEffect, useMemo, useState } from 'react'
import { RollingLineChart } from '../../components/charts/RollingLineChart'
import { apiGet, type FactorReturnsPayload } from '../../lib/api'
import { useWorkspaceRefresh } from '../../hooks/useWorkspaceRefresh'

export function FactorReturnsWidget() {
  const refreshRevision = useWorkspaceRefresh()
  const [payload, setPayload] = useState<FactorReturnsPayload | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<FactorReturnsPayload>('/data/factors/returns').then(setPayload).catch((err: Error) => setError(err.message))
  }, [refreshRevision])

  const factors = (payload?.names ?? []).filter((name) => name !== 'rf')
  const chartData = useMemo(() => {
    const cumulative: Record<string, number> = Object.fromEntries(factors.map((name) => [name, 1]))
    return (payload?.rows ?? []).map((row) => {
      const point: Record<string, string | number> = { date: String(row.date) }
      for (const name of factors) {
        cumulative[name] *= 1 + Number(row[name] ?? 0)
        point[name] = cumulative[name] - 1
      }
      return point
    })
  }, [factors, payload])

  return (
    <div className="panel">
      <div className="panel-heading"><div><h2>Factor Returns</h2><p>Monthly sample-universe factor spreads, shown cumulatively.</p></div><span className="status-pill neutral">historical</span></div>
      {error && <p className="error">{error}</p>}
      <RollingLineChart data={chartData} series={factors.map((name) => ({ key: name, name }))} height={360} yAxisFormat="percent" referenceLine={0} />
    </div>
  )
}
