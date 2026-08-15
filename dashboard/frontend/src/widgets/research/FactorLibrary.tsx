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
    ? { title: '横截面选股信号库', description: '技术面与基本面因子是信号定义的输入；先检验其预测能力，再由策略工作台组合成持仓。', factors: '个信号输入', evaluate: '检验预测能力', technical: '技术面', fundamental: '基本面' }
    : { title: 'Cross-sectional Stock Signal Library', description: 'Technical and fundamental factors are signal inputs. Test predictive power here, then combine them into holdings in Strategy & Portfolio Workbench.', factors: 'signal inputs', evaluate: 'Test predictive power', technical: 'Technical', fundamental: 'Fundamental' }
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
