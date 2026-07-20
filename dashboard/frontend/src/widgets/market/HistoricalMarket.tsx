import { useEffect, useMemo, useState } from 'react'
import { BarChart3, CalendarDays } from 'lucide-react'
import { RollingLineChart } from '../../components/charts/RollingLineChart'
import { SymbolCombobox } from '../../components/shared/SymbolCombobox'
import { apiGet, type DataManifest, type MarketBar, type ProviderStatus } from '../../lib/api'
import { useDataProfile, type DataProfile } from '../../lib/data-profile'

export function HistoricalMarketWidget() {
  const [symbols, setSymbols] = useState<string[]>([])
  const [symbol, setSymbol] = useState('')
  const [manifest, setManifest] = useState<DataManifest | null>(null)
  const [rows, setRows] = useState<MarketBar[]>([])
  const [error, setError] = useState('')
  const [profile, chooseProfile] = useDataProfile()
  const [providerStatus, setProviderStatus] = useState<ProviderStatus | null>(null)

  useEffect(() => {
    setError('')
    setRows([])
    setSymbols([])
    setSymbol('')
    Promise.all([
      apiGet<{ symbols: string[] }>(`/data/market/symbols?profile=${profile}`),
      apiGet<DataManifest>('/data/manifest'),
      apiGet<ProviderStatus>('/data/providers'),
    ]).then(([symbolPayload, manifestPayload, statusPayload]) => {
      setSymbols(symbolPayload.symbols)
      setSymbol(symbolPayload.symbols[0] ?? '')
      setManifest(manifestPayload)
      setProviderStatus(statusPayload)
    }).catch((err: Error) => setError(err.message))
  }, [profile])

  useEffect(() => {
    if (!symbol || !manifest) return
    const profileEnd = providerStatus?.profiles[profile]?.latest_date || manifest.cutoff_date
    const start = new Date(profileEnd)
    start.setFullYear(start.getFullYear() - 1)
    apiGet<{ rows: MarketBar[] }>(`/data/market/bars?profile=${profile}&symbol=${encodeURIComponent(symbol)}&start=${start.toISOString().slice(0, 10)}&end=${profileEnd}`)
      .then((payload) => setRows(payload.rows))
      .catch((err: Error) => setError(err.message))
  }, [manifest, profile, providerStatus, symbol])

  const latest = rows.at(-1)
  const change = rows.length > 1 && latest ? latest.close / rows[0].close - 1 : 0
  const chartData = useMemo(() => rows.map((row) => ({ date: row.date, close: row.close })), [rows])

  return (
    <div className="panel">
      <div className="panel-heading">
        <div><h2>Historical Market</h2><p>Adjusted daily bars. This is not a realtime feed.</p></div>
        <div className="panel-selects">
          <select value={profile} onChange={(event) => chooseProfile(event.target.value as DataProfile)}>
            <option value="demo">Demo</option>
            <option value="runtime">Local RQ</option>
          </select>
          <SymbolCombobox symbols={symbols} value={symbol} onChange={setSymbol} ariaLabel="Market symbol" />
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="metric-grid compact-metrics">
        <div className="metric"><span><BarChart3 size={14} /> last close</span><strong>{latest?.close.toFixed(2) ?? '-'}</strong></div>
        <div className="metric"><span>12m change</span><strong>{(change * 100).toFixed(1)}%</strong></div>
        <div className="metric"><span><CalendarDays size={14} /> cutoff</span><strong>{providerStatus?.profiles[profile]?.latest_date ?? manifest?.cutoff_date ?? '-'}</strong></div>
      </div>
      <RollingLineChart data={chartData} series={[{ key: 'close', name: symbol || 'close', color: '#62d39e' }]} height={300} />
    </div>
  )
}
