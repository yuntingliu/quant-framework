import { useEffect, useMemo, useState } from 'react'
import { Activity, Blocks, Database, FlaskConical, ShieldCheck } from 'lucide-react'
import { apiGet, type BacktestRecord, type StrategyTemplate } from '../../lib/api'

interface ProviderStatus {
  providers: Record<string, string[]>
  latest_date: string | null
}

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
  const [providers, setProviders] = useState<ProviderStatus | null>(null)
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [backtests, setBacktests] = useState<BacktestRecord[]>([])
  const [stats, setStats] = useState<SystemStats | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      apiGet<ProviderStatus>('/data/providers'),
      apiGet<StrategyTemplate[]>('/strategies'),
      apiGet<BacktestRecord[]>('/backtests'),
      apiGet<SystemStats>('/system/stats'),
    ])
      .then(([providerRows, strategyRows, backtestRows, statRows]) => {
        setProviders(providerRows)
        setStrategies(strategyRows)
        setBacktests(backtestRows)
        setStats(statRows)
      })
      .catch((err: Error) => setError(err.message))
  }, [])

  const providerCount = useMemo(
    () => Object.values(providers?.providers ?? {}).reduce((total, rows) => total + rows.length, 0),
    [providers],
  )

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>AlphaLab Workstation</h2>
          <p>Framework shell with provider slots, strategy templates, paper execution, and local result state.</p>
        </div>
        <span className="status-pill neutral">barebone</span>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="metric-grid">
        <div className="metric metric-accent">
          <span><Database size={14} /> providers</span>
          <strong>{providerCount}</strong>
          <small>latest: {providers?.latest_date ?? 'unavailable'}</small>
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
          <p>Protocol-first market, fundamental, factor, and realtime provider contracts.</p>
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
          <p>External data and broker adapters are visible extension points, but no concrete connection is bundled.</p>
          <code>MarketDataProvider</code>
          <code>Broker-neutral execution contracts</code>
        </section>
      </div>
    </div>
  )
}
