import { useEffect, useMemo, useState } from 'react'
import { Activity, Blocks, Database, FlaskConical, ShieldCheck } from 'lucide-react'
import { apiGet, type BacktestRecord, type DataManifest, type ProviderStatus, type StrategyTemplate } from '../../lib/api'
import { useWorkspaceRefresh } from '../../hooks/useWorkspaceRefresh'

interface SystemStats {
  path: string
  strategies: number
  backtests: number
  signals: number
  orders: number
  journal: number
  checked_at: string
}

export function WorkstationHomeWidget() {
  const refreshRevision = useWorkspaceRefresh()
  const [providers, setProviders] = useState<ProviderStatus | null>(null)
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [backtests, setBacktests] = useState<BacktestRecord[]>([])
  const [stats, setStats] = useState<SystemStats | null>(null)
  const [manifest, setManifest] = useState<DataManifest | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      apiGet<ProviderStatus>('/data/providers'),
      apiGet<StrategyTemplate[]>('/strategies'),
      apiGet<BacktestRecord[]>('/backtests'),
      apiGet<SystemStats>('/system/stats'),
      apiGet<DataManifest>('/data/manifest'),
    ])
      .then(([providerRows, strategyRows, backtestRows, statRows, manifestRow]) => {
        setProviders(providerRows)
        setStrategies(strategyRows)
        setBacktests(backtestRows)
        setStats(statRows)
        setManifest(manifestRow)
      })
      .catch((err: Error) => setError(err.message))
  }, [refreshRevision])

  const providerCount = useMemo(
    () => Object.values(providers?.providers ?? {}).reduce((total, rows) => total + rows.length, 0),
    [providers],
  )

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>AlphaLab Workstation</h2>
          <p>Real historical sample, reproducible research loop, and paper-only execution.</p>
        </div>
        <span className="status-pill neutral">historical · realtime not configured</span>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="metric-grid">
        <div className="metric metric-accent">
          <span><Database size={14} /> providers</span>
          <strong>{providerCount}</strong>
          <small>{manifest?.symbol_count ?? 0} symbols · {providers?.latest_date ?? 'unavailable'}</small>
        </div>
        <div className="metric">
          <span><FlaskConical size={14} /> templates</span>
          <strong>{strategies.length}</strong>
          <small>generic YAML configs</small>
        </div>
        <div className="metric">
          <span><Activity size={14} /> backtests</span>
          <strong>{backtests.length}</strong>
          <small>stored in local SQLite</small>
        </div>
        <div className="metric">
          <span><ShieldCheck size={14} /> signals</span>
          <strong>{stats?.signals ?? 0}</strong>
          <small>no live broker attached</small>
        </div>
      </div>

      <div className="lane-grid">
        <section className="lane">
          <div className="lane-title"><Database size={15} /> Data Layer</div>
          <p>Bundled adjusted daily bars, point-in-time fundamentals, and monthly factor returns.</p>
          <code>data/market/bars.parquet</code>
          <code>data/fundamentals/fundamentals.parquet</code>
        </section>
        <section className="lane">
          <div className="lane-title"><FlaskConical size={15} /> Research Loop</div>
          <p>StrategyConfig feeds SignalEngine and run_backtest, then writes ResultStore records.</p>
          <code>DataEngine -&gt; StrategyConfig -&gt; run_backtest</code>
        </section>
        <section className="lane">
          <div className="lane-title"><Blocks size={15} /> Adapter Slots</div>
          <p>Sample cutoff is explicit; live feeds and real-order adapters remain unconfigured.</p>
          <code>{manifest?.sample_start ?? '-'} → {manifest?.cutoff_date ?? '-'}</code>
          <code>{manifest?.caveat ?? 'Development demonstration only.'}</code>
        </section>
      </div>
    </div>
  )
}
