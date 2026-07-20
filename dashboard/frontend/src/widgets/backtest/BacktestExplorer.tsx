import { useQuery } from '@tanstack/react-query'
import { apiGet, type BacktestRecord } from '../../lib/api'
import { useWorkspace } from '../../contexts/WorkspaceContext'

export function BacktestExplorerWidget() {
  const { selectedBacktest, setSelectedBacktest, setSelectedStrategy } = useWorkspace()
  const { data: records = [], error } = useQuery({
    queryKey: ['backtests'],
    queryFn: () => apiGet<BacktestRecord[]>('/backtests'),
  })

  return (
    <div className="panel">
      <h2>Backtest Records</h2>
      {error && <p className="error">{error instanceof Error ? error.message : String(error)}</p>}
      <div className="table">
        <div className="table-row table-head backtest-table-row"><span>Strategy</span><span>Total</span><span>Sharpe</span><span>Run</span></div>
        {records.map((record) => (
          <button
            type="button"
            className={`table-row backtest-table-row w-full text-left ${selectedBacktest === record.id ? 'bg-primary/10' : ''}`}
            key={record.id}
            onClick={() => {
              setSelectedBacktest(record.id)
              setSelectedStrategy(record.strategy_id)
            }}
          >
            <span>{record.strategy_id}</span>
            <span>{typeof record.total_return === 'number' ? record.total_return.toFixed(4) : ''}</span>
            <span>{typeof record.sharpe === 'number' ? record.sharpe.toFixed(2) : ''}</span>
            <span>{record.run_at}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
