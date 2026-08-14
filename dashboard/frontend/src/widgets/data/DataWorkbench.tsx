import { useCallback, useEffect, useMemo, useState } from 'react'
import { BarChart3, CalendarDays, Database, Play, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import { CandlestickChart } from '../../components/charts'
import { CSVExportButton } from '../../components/shared/CSVExportButton'
import { IndicatorMenu } from '../../components/shared/IndicatorMenu'
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
import { useIndicatorSelection } from '../../hooks/useIndicatorSelection'
import { useLanguage } from '../../contexts/LanguageContext'
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

function compactNumber(value: number | undefined, locale: string): string {
  if (value === undefined || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 2 }).format(value)
}

export function DataWorkbenchWidget() {
  const { language } = useLanguage()
  const copy = language === 'zh' ? {
    title: '数据工作台',
    description: '获取、检查、筛选和校验有界本地数据集。',
    demoProfile: '演示数据',
    runtimeProfile: '本地 RQ 数据',
    coverage: '覆盖范围',
    symbols: '只证券',
    loading: '加载中',
    preview: '预览',
    previewTitle: '预览本地 RQ 同步计划',
    sync: '同步',
    syncTitle: '开始执行已规划的本地同步',
    validate: '校验',
    validateTitle: '校验运行时数据集',
    configuredRequired: '请先配置',
    rqEnvironment: '本地 RQ 环境',
    beforeSync: '后再执行同步。',
    demoFallback: '演示数据仍可用；运行时数据在同步完成前会明确报错。',
    datasets: '个数据集',
    batches: '个批次',
    tabsLabel: '数据工作台视图',
    marketTab: '行情 / K 线',
    catalogTab: '数据目录',
    previewTab: '查询与预览',
    jobsTab: '同步任务',
    marketSymbol: '行情证券代码',
    klineRange: 'K 线区间',
    threeMonths: '3 个月',
    sixMonths: '6 个月',
    oneYear: '1 年',
    allHistory: '全部历史',
    dailyAdjusted: '日频 · 复权 OHLCV',
    loadingBars: '正在加载日频 OHLCV…',
    noBars: '当前数据画像和证券没有可用的 OHLCV 数据。',
    lastClose: '最新收盘',
    dailyChange: '日涨跌',
    rangeChange: '区间涨跌',
    latestVolume: '最新成交量',
    latestDate: '最新日期',
    runtimeDataset: '运行时数据集',
    status: '状态',
    rows: '行数',
    symbolsInput: '证券代码',
    symbolsPlaceholder: '证券代码，以逗号分隔',
    startDate: '开始日期',
    endDate: '结束日期',
    columns: '字段',
    columnsPlaceholder: '字段名，以逗号分隔',
    rowLimit: '行数上限',
    query: '查询',
    ofMatchedRows: '行，共匹配',
    matchedRowsSuffix: '行',
    boundedPreview: '有界预览',
    exportCsv: '导出 CSV',
    chooseDataset: '请选择运行时数据集并执行有界查询。',
    job: '任务',
    progress: '进度',
    message: '消息',
    noJobs: '暂无本地同步任务。',
    passed: '项通过',
  } : {
    title: 'Data Workbench',
    description: 'Acquire, inspect, filter, and validate bounded local datasets.',
    demoProfile: 'Demo profile',
    runtimeProfile: 'Local RQ profile',
    coverage: 'Coverage',
    symbols: 'symbols',
    loading: 'loading',
    preview: 'Preview',
    previewTitle: 'Preview local RQ synchronization',
    sync: 'Sync',
    syncTitle: 'Start the planned local synchronization',
    validate: 'Validate',
    validateTitle: 'Validate runtime datasets',
    configuredRequired: 'Configure',
    rqEnvironment: 'the local RQ environment',
    beforeSync: 'before syncing.',
    demoFallback: 'Demo remains available, while runtime requests fail explicitly until sync is complete.',
    datasets: 'datasets',
    batches: 'batches',
    tabsLabel: 'Data workbench view',
    marketTab: 'Market / K-line',
    catalogTab: 'Catalog',
    previewTab: 'Query & Preview',
    jobsTab: 'Sync Jobs',
    marketSymbol: 'Market symbol',
    klineRange: 'K-line range',
    threeMonths: '3 months',
    sixMonths: '6 months',
    oneYear: '1 year',
    allHistory: 'All history',
    dailyAdjusted: 'Daily · adjusted OHLCV',
    loadingBars: 'Loading daily OHLCV…',
    noBars: 'No OHLCV bars are available for this profile and symbol.',
    lastClose: 'Last close',
    dailyChange: 'Daily change',
    rangeChange: 'Range change',
    latestVolume: 'Latest volume',
    latestDate: 'Latest date',
    runtimeDataset: 'Runtime dataset',
    status: 'Status',
    rows: 'Rows',
    symbolsInput: 'Symbols',
    symbolsPlaceholder: 'Symbols, comma separated',
    startDate: 'Start date',
    endDate: 'End date',
    columns: 'Columns',
    columnsPlaceholder: 'Columns, comma separated',
    rowLimit: 'Row limit',
    query: 'Query',
    ofMatchedRows: 'of',
    matchedRowsSuffix: 'matched rows',
    boundedPreview: 'bounded preview',
    exportCsv: 'Export CSV',
    chooseDataset: 'Choose a runtime dataset and run a bounded query.',
    job: 'Job',
    progress: 'Progress',
    message: 'Message',
    noJobs: 'No local sync jobs yet.',
    passed: 'passed',
  }
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
  const [quality, setQuality] = useState<{ passed: number; total: number } | null>(null)
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
  const [selectedIndicators, setSelectedIndicators] = useIndicatorSelection('data-workbench-market')

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
      setQuality({ passed, total: reports.length })
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

  if (error) return <div className="panel"><h2>{copy.title}</h2><p className="error">{error}</p></div>
  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
        <select value={profile} onChange={(event) => chooseProfile(event.target.value as DataProfile)}>
          <option value="demo">{copy.demoProfile}</option>
          <option value="runtime">{copy.runtimeProfile}</option>
        </select>
      </div>
      <div className="metric-grid">
        {Object.entries(status?.datasets ?? {}).map(([kind, dataset]) => <div className="metric" key={kind}><span>{kind}</span><strong>{dataset.status}</strong><small>{(dataset.bytes / 1024 / 1024).toFixed(2)} MB</small></div>)}
        <div className="metric">
          <span>{copy.coverage}</span>
          <strong>{manifest?.symbol_count ?? 0} {copy.symbols}</strong>
          <small>{manifest?.sample_start ?? '-'} → {manifest?.cutoff_date ?? '-'}</small>
        </div>
      </div>
      <div className="sync-toolbar">
        <span className={`status-pill ${health?.rq.configured ? 'ready' : 'neutral'}`}>
          <Database size={13} /> RQ {health?.rq.status ?? copy.loading}
        </span>
        <button type="button" onClick={preview} disabled={busy} title={copy.previewTitle}>
          <RefreshCw size={14} /> {copy.preview}
        </button>
        <button type="button" onClick={startSync} disabled={busy || !plan || !health?.rq.configured} title={copy.syncTitle}>
          <Play size={14} /> {copy.sync}
        </button>
        <button type="button" onClick={validateRuntime} disabled={busy || catalog?.ready === 0} title={copy.validateTitle}>
          <ShieldCheck size={14} /> {copy.validate}
        </button>
        {quality && <span className="status-pill ready">{quality.passed}/{quality.total} {copy.passed}</span>}
      </div>
      {!health?.rq.configured && (
        <p className="data-guidance">
          {copy.configuredRequired} {health?.rq.missing.join(', ') || copy.rqEnvironment} {copy.beforeSync} {copy.demoFallback}
        </p>
      )}
      {plan && (
        <div className="detail-strip">
          <span>{plan.symbol_count} {copy.symbols} · {plan.steps.length} {copy.datasets} · {plan.estimated_batches} {copy.batches}</span>
          <strong>{plan.requested_start} → {plan.requested_end}</strong>
        </div>
      )}
      <div className="workbench-tabs" role="tablist" aria-label={copy.tabsLabel}>
        <button type="button" role="tab" aria-selected={tab === 'market'} onClick={() => setTab('market')}>{copy.marketTab}</button>
        <button type="button" role="tab" aria-selected={tab === 'catalog'} onClick={() => setTab('catalog')}>{copy.catalogTab}</button>
        <button type="button" role="tab" aria-selected={tab === 'preview'} onClick={() => setTab('preview')}>{copy.previewTab}</button>
        <button type="button" role="tab" aria-selected={tab === 'jobs'} onClick={() => setTab('jobs')}>{copy.jobsTab}</button>
      </div>

      {tab === 'market' && (
        <div className="workbench-body market-kline-workbench">
          <div className="market-kline-toolbar">
            <SymbolCombobox
              symbols={marketSymbols}
              value={marketSymbol}
              onChange={setSelectedSymbol}
              ariaLabel={copy.marketSymbol}
            />
            <select aria-label={copy.klineRange} value={marketRange} onChange={(event) => setMarketRange(event.target.value as MarketRange)}>
              <option value="3m">{copy.threeMonths}</option>
              <option value="6m">{copy.sixMonths}</option>
              <option value="1y">{copy.oneYear}</option>
              <option value="all">{copy.allHistory}</option>
            </select>
            <IndicatorMenu selected={selectedIndicators} onChange={setSelectedIndicators} />
            <span className="status-pill neutral"><CalendarDays size={13} /> {copy.dailyAdjusted}</span>
          </div>
          {marketError ? <div className="workbench-message error">{marketError}</div> : null}
          {!marketError && marketBusy && marketRows.length === 0 ? <div className="analytics-empty">{copy.loadingBars}</div> : null}
          {!marketError && !marketBusy && marketRows.length === 0 ? <div className="analytics-empty">{copy.noBars}</div> : null}
          {marketRows.length > 0 ? (
            <>
              <div className="analytics-kpi-grid market-kline-kpis">
                <div className="analytics-kpi"><span><BarChart3 size={12} /> {copy.lastClose}</span><strong>{latestBar?.close.toFixed(2) ?? '—'}</strong></div>
                <div className="analytics-kpi"><span>{copy.dailyChange}</span><strong className={dailyChange !== null && dailyChange < 0 ? 'gate-fail-text' : 'gate-pass-text'}>{dailyChange === null ? '—' : `${(dailyChange * 100).toFixed(2)}%`}</strong></div>
                <div className="analytics-kpi"><span>{copy.rangeChange}</span><strong className={rangeChange !== null && rangeChange < 0 ? 'gate-fail-text' : 'gate-pass-text'}>{rangeChange === null ? '—' : `${(rangeChange * 100).toFixed(2)}%`}</strong></div>
                <div className="analytics-kpi"><span>{copy.latestVolume}</span><strong>{compactNumber(latestBar?.volume, language === 'zh' ? 'zh-CN' : 'en-US')}</strong></div>
                <div className="analytics-kpi"><span>{copy.latestDate}</span><strong>{latestBar?.date ?? '—'}</strong></div>
              </div>
              <div className="market-kline-frame">
                <CandlestickChart rows={marketRows} selectedIndicators={selectedIndicators} />
              </div>
            </>
          ) : null}
        </div>
      )}

      {tab === 'catalog' && (
        <div className="table runtime-table">
          <div className="table-row table-head"><span>{copy.runtimeDataset}</span><span>{copy.status}</span><span>{copy.rows}</span><span>{copy.coverage}</span></div>
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
            <select aria-label={copy.runtimeDataset} value={datasetId} onChange={(event) => chooseDataset(event.target.value)}>
              {(catalog?.datasets ?? []).map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.label}</option>)}
            </select>
            <input aria-label={copy.symbolsInput} placeholder={copy.symbolsPlaceholder} value={symbolFilter} onChange={(event) => setSymbolFilter(event.target.value)} />
            <input aria-label={copy.startDate} type="date" value={startFilter} onChange={(event) => setStartFilter(event.target.value)} />
            <input aria-label={copy.endDate} type="date" value={endFilter} onChange={(event) => setEndFilter(event.target.value)} />
            <input aria-label={copy.columns} placeholder={copy.columnsPlaceholder} value={columnFilter} onChange={(event) => setColumnFilter(event.target.value)} />
            <input aria-label={copy.rowLimit} type="number" min={1} max={1000} value={rowLimit} onChange={(event) => setRowLimit(Math.max(1, Math.min(1000, Number(event.target.value) || 1)))} />
            <button className="primary-command" type="button" onClick={queryDataset} disabled={busy || !datasetId}>
              <Search size={14} /> {copy.query}
            </button>
          </div>
          {dataPreview ? (
            <>
              <div className="detail-strip">
                <span>{dataPreview.returned_rows.toLocaleString()} {copy.ofMatchedRows} {dataPreview.matched_rows.toLocaleString()} {copy.matchedRowsSuffix}</span>
                <strong>{dataPreview.dataset}{dataPreview.truncated ? ` · ${copy.boundedPreview}` : ''}</strong>
                <CSVExportButton
                  data={dataPreview.rows}
                  filename={`alphalab-${dataPreview.dataset}-preview`}
                  label={copy.exportCsv}
                />
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
          ) : <div className="analytics-empty">{copy.chooseDataset}</div>}
        </div>
      )}

      {tab === 'jobs' && (
        <div className="table runtime-jobs">
          <div className="table-row table-head"><span>{copy.job}</span><span>{copy.status}</span><span>{copy.progress}</span><span>{copy.message}</span></div>
          {jobs.length === 0 && <div className="table-row"><span>{copy.noJobs}</span></div>}
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
