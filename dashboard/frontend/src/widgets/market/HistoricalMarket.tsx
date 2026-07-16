import { useEffect, useMemo, useState } from 'react'
import { BarChart3, CalendarDays } from 'lucide-react'
import { RollingLineChart } from '../../components/charts/RollingLineChart'
import { apiGet, type DataManifest, type MarketBar } from '../../lib/api'

export function HistoricalMarketWidget() {
  const [symbols, setSymbols] = useState<string[]>([])
  const [symbol, setSymbol] = useState('')
  const [manifest, setManifest] = useState<DataManifest | null>(null)
  const [rows, setRows] = useState<MarketBar[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      apiGet<{ symbols: string[] }>('/data/market/symbols'),
      apiGet<DataManifest>('/data/manifest'),
    ]).then(([symbolPayload, manifestPayload]) => {
      setSymbols(symbolPayload.symbols)
      setSymbol(symbolPayload.symbols[0] ?? '')
      setManifest(manifestPayload)
    }).catch((err: Error) => setError(err.message))
  }, [])

  useEffect(() => {
    if (!symbol || !manifest) return
    const start = new Date(manifest.cutoff_date)
    start.setFullYear(start.getFullYear() - 1)
    apiGet<{ rows: MarketBar[] }>(`/data/market/bars?symbol=${encodeURIComponent(symbol)}&start=${start.toISOString().slice(0, 10)}&end=${manifest.cutoff_date}`)
      .then((payload) => setRows(payload.rows))
      .catch((err: Error) => setError(err.message))
  }, [manifest, symbol])

  const latest = rows.at(-1)
  const change = rows.length > 1 && latest ? latest.close / rows[0].close - 1 : 0
  const chartData = useMemo(() => rows.map((row) => ({ date: row.date, close: row.close })), [rows])

  return (
    <div className="panel">
      <div className="panel-heading">
        <div><h2>Historical Market</h2><p>Bundled adjusted daily bars. This is not a realtime feed.</p></div>
        <select value={symbol} onChange={(event) => setSymbol(event.target.value)}>
          {symbols.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="metric-grid compact-metrics">
        <div className="metric"><span><BarChart3 size={14} /> last close</span><strong>{latest?.close.toFixed(2) ?? '-'}</strong></div>
        <div className="metric"><span>12m change</span><strong>{(change * 100).toFixed(1)}%</strong></div>
        <div className="metric"><span><CalendarDays size={14} /> cutoff</span><strong>{manifest?.cutoff_date ?? '-'}</strong></div>
      </div>
      <RollingLineChart data={chartData} series={[{ key: 'close', name: symbol || 'close', color: '#62d39e' }]} height={300} />
    </div>
  )
}
