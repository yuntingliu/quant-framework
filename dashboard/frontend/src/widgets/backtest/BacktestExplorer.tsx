import { useEffect, useState } from 'react'
import { apiGet, type BacktestRecord } from '../../lib/api'

export function BacktestExplorerWidget() {
  const [records, setRecords] = useState<BacktestRecord[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<BacktestRecord[]>('/backtests').then(setRecords).catch((err: Error) => setError(err.message))
  }, [])

  return (
    <div className="panel">
      <h2>Backtest Records</h2>
      {error && <p className="error">{error}</p>}
      <div className="table">
        <div className="table-row table-head backtest-table-row"><span>Strategy</span><span>Total</span><span>Sharpe</span><span>Run</span></div>
        {records.map((record) => (
          <div className="table-row backtest-table-row" key={record.id}>
            <span>{record.strategy_id}</span>
            <span>{typeof record.total_return === 'number' ? record.total_return.toFixed(4) : ''}</span>
            <span>{typeof record.sharpe === 'number' ? record.sharpe.toFixed(2) : ''}</span>
            <span>{record.run_at}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
