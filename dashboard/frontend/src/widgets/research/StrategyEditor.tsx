import { useEffect, useState } from 'react'
import { FileCode2 } from 'lucide-react'
import { apiGet, type StrategyTemplate, type StrategyTemplateDetail } from '../../lib/api'
import { useWorkspace } from '../../contexts/WorkspaceContext'
import { useWorkspaceRefresh } from '../../hooks/useWorkspaceRefresh'

export function StrategyEditorWidget() {
  const refreshRevision = useWorkspaceRefresh()
  const { selectedStrategy, setSelectedStrategy } = useWorkspace()
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [detail, setDetail] = useState<StrategyTemplateDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<StrategyTemplate[]>('/strategies')
      .then((items) => {
        setStrategies(items)
      })
      .catch((err: Error) => setError(err.message))
  }, [refreshRevision])

  useEffect(() => {
    if (!selectedStrategy && strategies[0]) setSelectedStrategy(strategies[0].id)
  }, [selectedStrategy, setSelectedStrategy, strategies])

  const strategyId = selectedStrategy ?? strategies[0]?.id ?? ''

  useEffect(() => {
    if (!strategyId) return
    apiGet<StrategyTemplateDetail>(`/strategies/${strategyId}`).then(setDetail).catch((err: Error) => setError(err.message))
  }, [strategyId])

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Strategy Editor</h2>
          <p>Inspect generic YAML templates before moving strategy logic into a separate plugin.</p>
        </div>
        <FileCode2 size={18} />
      </div>
      {error && <p className="error">{error}</p>}
      <div className="editor-layout">
        <aside className="editor-list">
          {strategies.map((strategy) => (
            <button
              key={strategy.id}
              type="button"
              className={strategy.id === strategyId ? 'active' : ''}
              onClick={() => setSelectedStrategy(strategy.id)}
            >
              <strong>{strategy.name}</strong>
              <span>{strategy.factors.length} factors</span>
            </button>
          ))}
        </aside>
        <section className="editor-main">
          <div className="detail-strip">
            <span>{detail?.description ?? 'Select a template'}</span>
            <strong>{detail?.warnings.length ? detail.warnings.join('; ') : 'ready'}</strong>
          </div>
          <pre className="code-view">{detail?.yaml ?? ''}</pre>
        </section>
      </div>
    </div>
  )
}
