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
        <div className="table-row table-head strategy-table-row">
          <span>Name</span><span>Factors</span><span>Research gate</span><span>Latest</span>
        </div>
        {strategies.map((strategy) => (
          <div className="table-row strategy-table-row" key={strategy.id}>
            <span>
              <strong>{strategy.name}</strong>
              <small>{strategy.built_in ? "built-in template" : "local strategy"}</small>
            </span>
            <span>{strategy.factors.join(', ')}</span>
            <span className={`research-status ${strategy.research_status ?? "watch"}`}>
              {strategy.research_status ?? "not run"}
            </span>
            <span>
              {strategy.latest_backtest
                ? `${(Number(strategy.latest_backtest.total_return ?? 0) * 100).toFixed(1)}%`
                : "—"}
              <small>{strategy.latest_signal_date ?? "no signal"}</small>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
