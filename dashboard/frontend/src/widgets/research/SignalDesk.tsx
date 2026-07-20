import { useEffect, useState } from 'react'
import { Play, Radio } from 'lucide-react'
import { apiGet, apiPost, type SignalResult, type StrategyTemplate } from '../../lib/api'
import { useDataProfile, type DataProfile } from '../../lib/data-profile'

export function SignalDeskWidget() {
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [strategyId, setStrategyId] = useState('balanced')
  const [signal, setSignal] = useState<SignalResult | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [profile, chooseProfile] = useDataProfile()

  useEffect(() => {
    apiGet<StrategyTemplate[]>('/strategies').then(setStrategies).catch((err: Error) => setError(err.message))
  }, [])

  async function generate() {
    setRunning(true)
    setError('')
    try {
      setSignal(await apiPost<SignalResult>('/signals/generate', { strategy_id: strategyId, persist: true, profile }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-heading"><div><h2>Signal Preview</h2><p>Generate broker-neutral targets from the bundled historical cutoff.</p></div><Radio size={18} /></div>
      <div className="form-row signal-controls">
        <select value={profile} onChange={(event) => chooseProfile(event.target.value as DataProfile)}>
          <option value="demo">Demo</option>
          <option value="runtime">Local RQ</option>
        </select>
        <select value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>{strategies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        <button type="button" onClick={generate} disabled={running}><Play size={14} /> {running ? 'Generating' : 'Generate'}</button>
      </div>
      {error && <p className="error">{error}</p>}
      {signal && <>
        <div className="detail-strip"><span>{signal.strategy_id}</span><strong>{signal.signal_date}</strong></div>
        <div className="table"><div className="table-row table-head"><span>Symbol</span><span>Target</span></div>{Object.entries(signal.targets).map(([item, weight]) => <div className="table-row" key={item}><span>{item}</span><span>{(weight * 100).toFixed(2)}%</span></div>)}</div>
      </>}
    </div>
  )
}
