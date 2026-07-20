import { useEffect, useState } from 'react'
import { apiGet, type StrategyTemplate } from '../../lib/api'
import { useWorkspace } from '../../contexts/WorkspaceContext'
import { useWorkspaceRefresh } from '../../hooks/useWorkspaceRefresh'

export function StrategyListWidget() {
  const refreshRevision = useWorkspaceRefresh()
  const { selectedStrategy, setSelectedStrategy } = useWorkspace()
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<StrategyTemplate[]>('/strategies').then(setStrategies).catch((err: Error) => setError(err.message))
  }, [refreshRevision])

  return (
    <div className="panel">
      <h2>Strategy Templates</h2>
      {error && <p className="error">{error}</p>}
      <div className="table">
        <div className="table-row table-head"><span>Name</span><span>Factors</span><span>Status</span></div>
        {strategies.map((strategy) => (
          <button
            type="button"
            className={`table-row w-full text-left ${selectedStrategy === strategy.id ? 'bg-primary/10' : ''}`}
            key={strategy.id}
            onClick={() => setSelectedStrategy(strategy.id)}
          >
            <span><strong>{strategy.name}</strong><small>{strategy.description}</small></span>
            <span>{strategy.factors.join(', ')}</span>
            <span>{strategy.warnings.length ? strategy.warnings.join('; ') : 'ready'}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

