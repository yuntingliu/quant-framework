import { useEffect, useMemo, useState } from 'react'
import { BarChart3, Sigma } from 'lucide-react'
import { apiGet, type StrategyTemplate } from '../../lib/api'

export function FactorLibraryWidget() {
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<StrategyTemplate[]>('/strategies').then(setStrategies).catch((err: Error) => setError(err.message))
  }, [])

  const factorRows = useMemo(() => {
    const counts = new Map<string, number>()
    for (const strategy of strategies) {
      for (const factor of strategy.factors) counts.set(factor, (counts.get(factor) ?? 0) + 1)
    }
    return [...counts.entries()].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
  }, [strategies])

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Factor Library</h2>
          <p>Factors referenced by bundled generic strategy templates.</p>
        </div>
        <span className="status-pill ready"><Sigma size={13} /> {factorRows.length} factors</span>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="factor-cloud">
        {factorRows.map(([factor, count]) => (
          <div className="factor-chip" key={factor}>
            <BarChart3 size={14} />
            <span>{factor}</span>
            <strong>{count}</strong>
          </div>
        ))}
      </div>
    </div>
  )
}
