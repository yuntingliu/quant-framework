import { useEffect, useState } from 'react'
import { ArrowRight, BarChart3, Sigma } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { apiGet, type FactorResearchLibrary } from '../../lib/api'

interface FactorLibraryWidgetProps {
  onEvaluate?: (factor: { name: string; source: 'technical' | 'fundamental' }) => void
}

export function FactorLibraryWidget({ onEvaluate }: FactorLibraryWidgetProps) {
  const { language } = useLanguage()
  const copy = language === 'zh'
    ? { title: '股票横截面因子库', description: '用于在同一时点给股票排序，可供横截面检验、选股策略和安全自定义表达式使用。', factors: '个因子', evaluate: '立即检验', technical: '技术面', fundamental: '基本面' }
    : { title: 'Stock Cross-sectional Factor Library', description: 'Factors for ranking stocks at the same point in time, available to cross-sectional tests, stock-selection strategies, and safe custom expressions.', factors: 'factors', evaluate: 'Test factor', technical: 'Technical', fundamental: 'Fundamental' }
  const [library, setLibrary] = useState<FactorResearchLibrary | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<FactorResearchLibrary>('/factor-research/library').then(setLibrary).catch((err: Error) => setError(err.message))
  }, [])
  const factorRows = library?.factors ?? []

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
        <span className="status-pill ready"><Sigma size={13} /> {factorRows.length} {copy.factors}</span>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="factor-library-grid">
        {factorRows.map((factor) => (
          <div className="factor-library-card" key={factor.name}>
            <div className="factor-library-card-heading">
              <BarChart3 size={15} />
              <strong>{factor.name}</strong>
              <span>{factor.source === 'technical' ? copy.technical : copy.fundamental}</span>
            </div>
            <p>{factor.description}</p>
            {onEvaluate && (
              <button type="button" onClick={() => onEvaluate({ name: factor.name, source: factor.source })}>
                {copy.evaluate} <ArrowRight size={13} />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
