import { useEffect, useState } from 'react'
import { FileCode2 } from 'lucide-react'
import { apiGet, type StrategyTemplate, type StrategyTemplateDetail } from '../../lib/api'

export function StrategyEditorWidget() {
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [strategyId, setStrategyId] = useState('momentum')
  const [detail, setDetail] = useState<StrategyTemplateDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<StrategyTemplate[]>('/strategies')
      .then((items) => {
        setStrategies(items)
        if (items[0]) setStrategyId(items[0].id)
      })
      .catch((err: Error) => setError(err.message))
  }, [])

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
              onClick={() => setStrategyId(strategy.id)}
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
