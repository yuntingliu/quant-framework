import { useEffect, useState } from 'react'
import { apiGet } from '../../lib/api'

interface ProviderStatus {
  providers: Record<string, string[]>
  latest_date: string | null
}

export function DataCenterWidget() {
  const [status, setStatus] = useState<ProviderStatus | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet<ProviderStatus>('/data/providers').then(setStatus).catch((err: Error) => setError(err.message))
  }, [])

  if (error) return <div className="panel"><h2>Data Providers</h2><p className="error">{error}</p></div>
  return (
    <div className="panel">
      <h2>Data Providers</h2>
      <div className="metric-grid">
        {Object.entries(status?.providers ?? {}).map(([kind, providers]) => (
          <div className="metric" key={kind}>
            <span>{kind}</span>
            <strong>{providers.join(', ') || 'none'}</strong>
          </div>
        ))}
        <div className="metric">
          <span>latest date</span>
          <strong>{status?.latest_date ?? 'unavailable'}</strong>
        </div>
      </div>
    </div>
  )
}

