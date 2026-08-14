import { useEffect, useState } from 'react'
import { BarChart3, Sigma } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { apiGet, type FactorResearchLibrary } from '../../lib/api'

export function FactorLibraryWidget() {
  const { language } = useLanguage()
  const copy = language === 'zh'
    ? { title: '因子库', description: '可供策略和安全自定义表达式使用的已注册基础因子。', factors: '个因子' }
    : { title: 'Factor Library', description: 'Registered base factors available to strategies and safe custom expressions.', factors: 'factors' }
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
      <div className="factor-cloud">
        {factorRows.map((factor) => (
          <div className="factor-chip" key={factor.name} title={factor.description}>
            <BarChart3 size={14} />
            <span>{factor.name}</span>
            <strong>{factor.source}</strong>
          </div>
        ))}
      </div>
    </div>
  )
}
