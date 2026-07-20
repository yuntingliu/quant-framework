import { useEffect, useState } from 'react'
import { apiGet, apiPost, type BacktestRecord, type BacktestRunResult, type DataManifest, type ProviderStatus, type RuntimeCatalog, type StrategyTemplate } from '../../lib/api'
import { useDataProfile, type DataProfile } from '../../lib/data-profile'

export function BacktestRunnerWidget() {
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [strategyId, setStrategyId] = useState('momentum')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BacktestRunResult | null>(null)
  const [error, setError] = useState('')
  const [profile, chooseProfile] = useDataProfile()

  useEffect(() => {
    setError('')
    setResult(null)
    Promise.all([
      apiGet<StrategyTemplate[]>('/strategies'),
      apiGet<DataManifest>('/data/manifest'),
      apiGet<ProviderStatus>('/data/providers'),
      apiGet<RuntimeCatalog>('/data-sync/catalog'),
      profile === 'demo' ? apiGet<BacktestRecord[]>('/backtests?limit=1') : Promise.resolve([]),
    ]).then(async ([items, manifest, providerStatus, catalog, records]) => {
      setStrategies(items)
      const runtimeBars = catalog.datasets.find((dataset) => dataset.id === 'rq.bars')
      if (profile === 'demo') {
        setStartDate(manifest.sample_start)
        setEndDate(manifest.cutoff_date)
      } else if (providerStatus.profiles.runtime.status === 'ready' && runtimeBars) {
        setStartDate(runtimeBars.date_start ?? '')
        setEndDate(runtimeBars.date_end ?? '')
      } else {
        setStartDate('')
        setEndDate('')
        setError('Runtime data is not ready. Open Data Center and complete an RQ sync.')
      }
      if (items[0]) setStrategyId(items[0].id)
      if (records[0]) setResult(await apiGet<BacktestRunResult>(`/backtests/${records[0].id}`))
    }).catch((err: Error) => setError(err.message))
  }, [profile])

  async function run() {
    setRunning(true)
    setError('')
    setResult(null)
    try {
      const response = await apiPost<BacktestRunResult>('/backtests/run', { strategy_id: strategyId, start_date: startDate, end_date: endDate, profile })
      setResult(response)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="panel">
      <h2>Backtest Runner</h2>
      <div className="form-row backtest-controls">
        <select value={profile} onChange={(event) => chooseProfile(event.target.value as DataProfile)}>
          <option value="demo">Demo</option>
          <option value="runtime">Local RQ</option>
        </select>
        <select value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>
          {strategies.map((strategy) => <option key={strategy.id} value={strategy.id}>{strategy.name}</option>)}
        </select>
        <input value={startDate} onChange={(event) => setStartDate(event.target.value)} />
        <input value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        <button type="button" onClick={run} disabled={running || !startDate || !endDate}>{running ? 'Running' : 'Run'}</button>
      </div>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="metric-grid">
          {Object.entries(result.metrics ?? {}).map(([key, value]) => (
            <div className="metric" key={key}>
              <span>{key}</span>
              <strong>{Number(value).toFixed(4)}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
