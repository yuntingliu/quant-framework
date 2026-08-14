import { useCallback, useEffect, useMemo, useState } from 'react'
import { BarChart3, CalendarDays, Database, Play, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import { CandlestickChart } from '../../components/charts'
import { SymbolCombobox } from '../../components/shared/SymbolCombobox'
import {
  apiGet,
  apiPost,
  type DataManifest,
  type DataSyncHealth,
  type MarketBar,
  type ProviderStatus,
  type RuntimeCatalog,
  type SyncJob,
  type SyncPlan,
} from '../../lib/api'
import { useWorkspaceRefresh } from '../../hooks/useWorkspaceRefresh'
import { useDataProfile, type DataProfile } from '../../lib/data-profile'
import { useWorkspace } from '../../contexts/WorkspaceContext'

type DataWorkbenchTab = 'market' | 'catalog' | 'preview' | 'jobs'
type MarketRange = '3m' | '6m' | '1y' | 'all'

interface DataPreview {
  dataset: string
  matched_rows: number
  returned_rows: number
  truncated: boolean
  rows: Array<Record<string, unknown>>
}

interface DataToolResult<T> {
  result: T
}

function previewCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function rangeStart(end: string, range: MarketRange): string | null {
  if (range === 'all') return null
  const start = new Date(`${end}T00:00:00`)
  if (range === '3m') start.setMonth(start.getMonth() - 3)
  if (range === '6m') start.setMonth(start.getMonth() - 6)
  if (range === '1y') start.setFullYear(start.getFullYear() - 1)
  return start.toISOString().slice(0, 10)
}

function compactNumber(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(value)
}

export function DataWorkbenchWidget() {
  const refreshRevision = useWorkspaceRefresh()
  const { selectedDataset, setSelectedDataset, selectedSymbol, setSelectedSymbol } = useWorkspace()
  const [status, setStatus] = useState<ProviderStatus | null>(null)
  const [error, setError] = useState('')
  const [manifest, setManifest] = useState<DataManifest | null>(null)
  const [health, setHealth] = useState<DataSyncHealth | null>(null)
  const [catalog, setCatalog] = useState<RuntimeCatalog | null>(null)
  const [jobs, setJobs] = useState<SyncJob[]>([])
  const [plan, setPlan] = useState<SyncPlan | null>(null)
  const [profile, chooseProfile] = useDataProfile()
  const [busy, setBusy] = useState(false)
  const [quality, setQuality] = useState('')
  const [tab, setTab] = useState<DataWorkbenchTab>('market')
  const [datasetId, setDatasetId] = useState('')
  const [symbolFilter, setSymbolFilter] = useState('')
  const [startFilter, setStartFilter] = useState('')
  const [endFilter, setEndFilter] = useState('')
  const [columnFilter, setColumnFilter] = useState('')
  const [rowLimit, setRowLimit] = useState(50)
  const [dataPreview, setDataPreview] = useState<DataPreview | null>(null)
  const [marketSymbols, setMarketSymbols] = useState<string[]>([])
  const [marketRows, setMarketRows] = useState<MarketBar[]>([])
  const [marketRange, setMarketRange] = useState<MarketRange>('1y')
  const [marketBusy, setMarketBusy] = useState(false)
  const [marketError, setMarketError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [providerStatus, dataManifest, syncHealth, runtimeCatalog, syncJobs] = await Promise.all([
        apiGet<ProviderStatus>('/data/providers'),
        apiGet<DataManifest>('/data/manifest'),
        apiGet<DataSyncHealth>('/data-sync/health'),
        apiGet<RuntimeCatalog>('/data-sync/catalog'),
        apiGet<SyncJob[]>('/data-sync/jobs?limit=8'),
      ])
      setStatus(providerStatus)
      setManifest(dataManifest)
      setHealth(syncHealth)
      setCatalog(runtimeCatalog)
      setJobs(syncJobs)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    const datasets = catalog?.datasets ?? []
    const next = datasetId && datasets.some((item) => item.id === datasetId)
      ? datasetId
      : selectedDataset && datasets.some((item) => item.id === selectedDataset)
        ? selectedDataset
        : datasets.find((item) => item.status === 'ready')?.id ?? datasets[0]?.id ?? ''
    if (!next) return
    if (next !== datasetId) setDatasetId(next)
    if (next !== selectedDataset) setSelectedDataset(next)
  }, [catalog, datasetId, selectedDataset, setSelectedDataset])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => { void refresh() }, 5000)
    return () => window.clearInterval(timer)
  }, [refresh, refreshRevision])

  useEffect(() => {
    let active = true
    setMarketSymbols([])
    setMarketRows([])
    setMarketError('')
    apiGet<{ symbols: string[] }>(`/data/market/symbols?profile=${profile}`)
      .then((payload) => {
        if (active) setMarketSymbols(payload.symbols)
      })
      .catch((err: Error) => {
        if (active) setMarketError(err.message)
      })
    return () => { active = false }
  }, [profile, refreshRevision])

  const marketSymbol = selectedSymbol && marketSymbols.includes(selectedSymbol)
    ? selectedSymbol
    : marketSymbols[0] ?? ''
  const marketEnd = status?.profiles[profile]?.latest_date
    ?? (profile === 'demo' ? manifest?.cutoff_date : null)

  useEffect(() => {
    if (marketSymbol && marketSymbol !== selectedSymbol) setSelectedSymbol(marketSymbol)
  }, [marketSymbol, selectedSymbol, setSelectedSymbol])

  useEffect(() => {
    if (!marketSymbol || !marketEnd) return
    let active = true
    const params = new URLSearchParams({
      profile,
      symbol: marketSymbol,
      end: marketEnd,
    })
    const start = rangeStart(marketEnd, marketRange)
    if (start) params.set('start', start)
    setMarketBusy(true)
    setMarketError('')
    apiGet<{ rows: MarketBar[] }>(`/data/market/bars?${params.toString()}`)
      .then((payload) => {
        if (active) setMarketRows(payload.rows)
      })
      .catch((err: Error) => {
        if (active) {
          setMarketRows([])
          setMarketError(err.message)
        }
      })
      .finally(() => {
        if (active) setMarketBusy(false)
      })
    return () => { active = false }
  }, [marketEnd, marketRange, marketSymbol, profile, refreshRevision])

  async function preview() {
    setBusy(true)
    setError('')
    try {
      setPlan(await apiPost<SyncPlan>('/data-sync/plan', {
        source: 'rq',
        datasets: ['instruments', 'bars', 'fundamentals', 'factors'],
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function startSync() {
    if (!plan) return
    setBusy(true)
    setError('')
    try {
      await apiPost<SyncJob>('/data-sync/jobs', {
        source: 'rq',
        datasets: ['instruments', 'bars', 'fundamentals', 'factors'],
        start: plan.requested_start,
        end: plan.requested_end,
      })
      setPlan(null)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function validateRuntime() {
    setBusy(true)
    setError('')
    try {
      const reports = await apiPost<Array<{ status: string }>>('/data-sync/validate', {})
      const passed = reports.filter((item) => item.status === 'passed').length
      setQuality(`${passed}/${reports.length} passed`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function queryDataset() {
    if (!datasetId) return
    setBusy(true)
    setError('')
    try {
      const columns = columnFilter.split(',').map((value) => value.trim()).filter(Boolean)
      const symbols = symbolFilter.split(',').map((value) => value.trim().toUpperCase()).filter(Boolean)
      const response = await apiPost<DataToolResult<DataPreview>>('/agent/data-tools/data.query/invoke', {
        input: {
          dataset: datasetId,
          limit: rowLimit,
          ...(symbols.length ? { symbols } : {}),
          ...(startFilter ? { start: startFilter } : {}),
          ...(endFilter ? { end: endFilter } : {}),
          ...(columns.length ? { columns } : {}),
        },
      })
      setDataPreview(response.result)
      setTab('preview')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const previewColumns = useMemo(
    () => dataPreview?.rows.length ? Object.keys(dataPreview.rows[0]) : [],
    [dataPreview],
  )
  const latestBar = marketRows.at(-1)
  const previousBar = marketRows.at(-2)
  const dailyChange = latestBar && previousBar && previousBar.close
    ? latestBar.close / previousBar.close - 1
    : null
  const rangeChange = latestBar && marketRows[0]?.close
    ? latestBar.close / marketRows[0].close - 1
    : null

  function chooseDataset(id: string) {
    setDatasetId(id)
    setSelectedDataset(id)
    setDataPreview(null)
  }

  if (error) return <div className="panel"><h2>Data Workbench</h2><p className="error">{error}</p></div>
  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Data Workbench</h2>
          <p>Acquire, inspect, filter, and validate bounded local datasets.</p>
        </div>
        <select value={profile} onChange={(event) => chooseProfile(event.target.value as DataProfile)}>
          <option value="demo">Demo profile</option>
          <option value="runtime">Local RQ profile</option>
        </select>
      </div>
      <div className="metric-grid">
        {Object.entries(status?.datasets ?? {}).map(([kind, dataset]) => <div className="metric" key={kind}><span>{kind}</span><strong>{dataset.status}</strong><small>{(dataset.bytes / 1024 / 1024).toFixed(2)} MB</small></div>)}
        <div className="metric">
          <span>coverage</span>
          <strong>{manifest?.symbol_count ?? 0} symbols</strong>
          <small>{manifest?.sample_start ?? '-'} → {manifest?.cutoff_date ?? '-'}</small>
        </div>
      </div>
      <div className="sync-toolbar">
        <span className={`status-pill ${health?.rq.configured ? 'ready' : 'neutral'}`}>
          <Database size={13} /> RQ {health?.rq.status ?? 'loading'}
        </span>
        <button type="button" onClick={preview} disabled={busy} title="Preview local RQ synchronization">
          <RefreshCw size={14} /> Preview
        </button>
        <button type="button" onClick={startSync} disabled={busy || !plan || !health?.rq.configured} title="Start the planned local synchronization">
          <Play size={14} /> Sync
        </button>
        <button type="button" onClick={validateRuntime} disabled={busy || catalog?.ready === 0} title="Validate runtime datasets">
          <ShieldCheck size={14} /> Validate
        </button>
        {quality && <span className="status-pill ready">{quality}</span>}
      </div>
      {!health?.rq.configured && (
        <p className="data-guidance">
          Configure {health?.rq.missing.join(', ') || 'the local RQ environment'} before syncing.
          Demo remains available, while runtime requests fail explicitly until sync is complete.
        </p>
      )}
      {plan && (
        <div className="detail-strip">
          <span>{plan.symbol_count} symbols · {plan.steps.length} datasets · {plan.estimated_batches} batches</span>
          <strong>{plan.requested_start} → {plan.requested_end}</strong>
        </div>
      )}
      <div className="workbench-tabs" role="tablist" aria-label="Data workbench view">
        <button type="button" role="tab" aria-selected={tab === 'market'} onClick={() => setTab('market')}>Market / K-line</button>
        <button type="button" role="tab" aria-selected={tab === 'catalog'} onClick={() => setTab('catalog')}>Catalog</button>
        <button type="button" role="tab" aria-selected={tab === 'preview'} onClick={() => setTab('preview')}>Query &amp; Preview</button>
        <button type="button" role="tab" aria-selected={tab === 'jobs'} onClick={() => setTab('jobs')}>Sync Jobs</button>
      </div>

      {tab === 'market' && (
        <div className="workbench-body market-kline-workbench">
          <div className="market-kline-toolbar">
            <SymbolCombobox
              symbols={marketSymbols}
              value={marketSymbol}
              onChange={setSelectedSymbol}
              ariaLabel="Market symbol"
            />
            <select aria-label="K-line range" value={marketRange} onChange={(event) => setMarketRange(event.target.value as MarketRange)}>
              <option value="3m">3 months</option>
              <option value="6m">6 months</option>
              <option value="1y">1 year</option>
              <option value="all">All history</option>
            </select>
            <span className="status-pill neutral"><CalendarDays size={13} /> Daily · adjusted OHLCV</span>
          </div>
          {marketError ? <div className="workbench-message error">{marketError}</div> : null}
          {!marketError && marketBusy && marketRows.length === 0 ? <div className="analytics-empty">Loading daily OHLCV…</div> : null}
          {!marketError && !marketBusy && marketRows.length === 0 ? <div className="analytics-empty">No OHLCV bars are available for this profile and symbol.</div> : null}
          {marketRows.length > 0 ? (
            <>
              <div className="analytics-kpi-grid market-kline-kpis">
                <div className="analytics-kpi"><span><BarChart3 size={12} /> Last close</span><strong>{latestBar?.close.toFixed(2) ?? '—'}</strong></div>
                <div className="analytics-kpi"><span>Daily change</span><strong className={dailyChange !== null && dailyChange < 0 ? 'gate-fail-text' : 'gate-pass-text'}>{dailyChange === null ? '—' : `${(dailyChange * 100).toFixed(2)}%`}</strong></div>
                <div className="analytics-kpi"><span>Range change</span><strong className={rangeChange !== null && rangeChange < 0 ? 'gate-fail-text' : 'gate-pass-text'}>{rangeChange === null ? '—' : `${(rangeChange * 100).toFixed(2)}%`}</strong></div>
                <div className="analytics-kpi"><span>Latest volume</span><strong>{compactNumber(latestBar?.volume)}</strong></div>
                <div className="analytics-kpi"><span>Latest date</span><strong>{latestBar?.date ?? '—'}</strong></div>
              </div>
              <div className="market-kline-frame">
                <CandlestickChart rows={marketRows} />
              </div>
            </>
          ) : null}
        </div>
      )}

      {tab === 'catalog' && (
        <div className="table runtime-table">
          <div className="table-row table-head"><span>Runtime dataset</span><span>Status</span><span>Rows</span><span>Coverage</span></div>
          {(catalog?.datasets ?? []).map((dataset) => (
            <button className={`table-row ${dataset.id === datasetId ? 'active' : ''}`} type="button" key={dataset.id} onClick={() => chooseDataset(dataset.id)}>
              <span>{dataset.label}<small>{dataset.id}</small></span>
              <span>{dataset.status}</span>
              <span>{dataset.rows.toLocaleString()}</span>
              <span>{dataset.date_start ?? '-'} → {dataset.date_end ?? '-'}</span>
            </button>
          ))}
        </div>
      )}

      {tab === 'preview' && (
        <div className="workbench-body">
          <div className="workbench-controls">
            <select aria-label="Runtime dataset" value={datasetId} onChange={(event) => chooseDataset(event.target.value)}>
              {(catalog?.datasets ?? []).map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.label}</option>)}
            </select>
            <input aria-label="Symbols" placeholder="Symbols, comma separated" value={symbolFilter} onChange={(event) => setSymbolFilter(event.target.value)} />
            <input aria-label="Start date" type="date" value={startFilter} onChange={(event) => setStartFilter(event.target.value)} />
            <input aria-label="End date" type="date" value={endFilter} onChange={(event) => setEndFilter(event.target.value)} />
            <input aria-label="Columns" placeholder="Columns, comma separated" value={columnFilter} onChange={(event) => setColumnFilter(event.target.value)} />
            <input aria-label="Row limit" type="number" min={1} max={1000} value={rowLimit} onChange={(event) => setRowLimit(Math.max(1, Math.min(1000, Number(event.target.value) || 1)))} />
            <button className="primary-command" type="button" onClick={queryDataset} disabled={busy || !datasetId}>
              <Search size={14} /> Query
            </button>
          </div>
          {dataPreview ? (
            <>
              <div className="detail-strip">
                <span>{dataPreview.returned_rows.toLocaleString()} of {dataPreview.matched_rows.toLocaleString()} matched rows</span>
                <strong>{dataPreview.dataset}{dataPreview.truncated ? ' · bounded preview' : ''}</strong>
              </div>
              <div className="analytics-table-wrap">
                <table className="analytics-table compact">
                  <thead><tr>{previewColumns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
                  <tbody>{dataPreview.rows.map((row, index) => (
                    <tr key={index}>{previewColumns.map((column) => <td key={column}>{previewCell(row[column])}</td>)}</tr>
                  ))}</tbody>
                </table>
              </div>
            </>
          ) : <div className="analytics-empty">Choose a runtime dataset and run a bounded query.</div>}
        </div>
      )}

      {tab === 'jobs' && (
        <div className="table runtime-jobs">
          <div className="table-row table-head"><span>Job</span><span>Status</span><span>Progress</span><span>Message</span></div>
          {jobs.length === 0 && <div className="table-row"><span>No local sync jobs yet.</span></div>}
          {jobs.map((job) => (
            <div className="table-row" key={job.id}>
              <span>{job.id.slice(0, 8)}<small>{job.created_at}</small></span>
              <span>{job.status}</span>
              <span>{job.progress}/{job.total}</span>
              <span>{job.error || job.message || '-'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
