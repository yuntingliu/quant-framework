import { useEffect, useState } from 'react'
import { apiGet, apiPost, type BacktestRunResult, type StrategyTemplate } from '../../lib/api'

export function BacktestRunnerWidget() {
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [strategyId, setStrategyId] = useState('momentum')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BacktestRunResult | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<StrategyTemplate[]>('/strategies').then((items) => {
      setStrategies(items)
      if (items[0]) setStrategyId(items[0].id)
    }).catch((err: Error) => setError(err.message))
  }, [])

  async function run() {
    setRunning(true)
    setError('')
    setResult(null)
    try {
      const response = await apiPost<BacktestRunResult>('/backtests/run', { strategy_id: strategyId, start_date: startDate, end_date: endDate })
      setResult(response)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="panel">
      <h2>Backtest Runner</h2>
      <div className="form-row">
        <select value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>
          {strategies.map((strategy) => <option key={strategy.id} value={strategy.id}>{strategy.name}</option>)}
        </select>
        <input value={startDate} onChange={(event) => setStartDate(event.target.value)} />
        <input value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        <button type="button" onClick={run} disabled={running}>{running ? 'Running' : 'Run'}</button>
      </div>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="metric-grid">
          {Object.entries(result.metrics).map(([key, value]) => (
            <div className="metric" key={key}>
              <span>{key}</span>
              <strong>{Number(value).toFixed(4)}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

