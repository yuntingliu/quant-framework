import { useEffect, useState } from 'react'
import { apiGet, type StrategyTemplate } from '../../lib/api'

export function StrategyListWidget() {
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<StrategyTemplate[]>('/strategies').then(setStrategies).catch((err: Error) => setError(err.message))
  }, [])

  return (
    <div className="panel">
      <h2>Strategy Templates</h2>
      {error && <p className="error">{error}</p>}
      <div className="table">
        <div className="table-row table-head"><span>Name</span><span>Factors</span><span>Status</span></div>
        {strategies.map((strategy) => (
          <div className="table-row" key={strategy.id}>
            <span><strong>{strategy.name}</strong><small>{strategy.description}</small></span>
            <span>{strategy.factors.join(', ')}</span>
            <span>{strategy.warnings.length ? strategy.warnings.join('; ') : 'ready'}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

