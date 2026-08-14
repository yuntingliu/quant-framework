import { useEffect, useState } from 'react'
import { BarChart3, Sigma } from 'lucide-react'
import { apiGet, type FactorResearchLibrary } from '../../lib/api'

export function FactorLibraryWidget() {
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
          <h2>Factor Library</h2>
          <p>Registered base factors available to strategies and safe custom expressions.</p>
        </div>
        <span className="status-pill ready"><Sigma size={13} /> {factorRows.length} factors</span>
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
