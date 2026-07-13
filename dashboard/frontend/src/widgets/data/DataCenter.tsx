import { useEffect, useState } from 'react'
import { apiGet, type DataManifest, type ProviderStatus } from '../../lib/api'

export function DataCenterWidget() {
  const [status, setStatus] = useState<ProviderStatus | null>(null)
  const [error, setError] = useState('')
  const [manifest, setManifest] = useState<DataManifest | null>(null)

  useEffect(() => {
    Promise.all([apiGet<ProviderStatus>('/data/providers'), apiGet<DataManifest>('/data/manifest')])
      .then(([providerStatus, dataManifest]) => { setStatus(providerStatus); setManifest(dataManifest) })
      .catch((err: Error) => setError(err.message))
  }, [])

  if (error) return <div className="panel"><h2>Data Providers</h2><p className="error">{error}</p></div>
  return (
    <div className="panel">
      <div className="panel-heading"><div><h2>Data Center</h2><p>Tracked example datasets and their integrity state.</p></div><span className="status-pill neutral">{status?.realtime.status ?? 'loading'}</span></div>
      <div className="metric-grid">
        {Object.entries(status?.datasets ?? {}).map(([kind, dataset]) => <div className="metric" key={kind}><span>{kind}</span><strong>{dataset.status}</strong><small>{(dataset.bytes / 1024 / 1024).toFixed(2)} MB</small></div>)}
        <div className="metric">
          <span>coverage</span>
          <strong>{manifest?.symbol_count ?? 0} symbols</strong>
          <small>{manifest?.sample_start ?? '-'} → {manifest?.cutoff_date ?? '-'}</small>
        </div>
      </div>
    </div>
  )
}
