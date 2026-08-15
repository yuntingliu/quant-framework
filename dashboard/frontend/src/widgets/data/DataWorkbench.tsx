import { useCallback, useEffect, useMemo, useState } from 'react'
import { BarChart3, CalendarDays, ChevronDown, Database, Play, RefreshCw, Search, ShieldCheck, Star, X } from 'lucide-react'
import { CandlestickChart } from '../../components/charts'
import { CSVExportButton } from '../../components/shared/CSVExportButton'
import { IndicatorMenu } from '../../components/shared/IndicatorMenu'
import { SymbolCombobox } from '../../components/shared/SymbolCombobox'
import {
  apiGet,
  apiPost,
  type DataSyncHealth,
  type MarketBar,
  type MarketInstrument,
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

type DataWorkbenchTab = 'catalog' | 'preview' | 'jobs'
type MarketRange = '3m' | '6m' | '1y' | 'all'
const WATCHLIST_STORAGE_KEY = 'alphalab.data-watchlist.v1'

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

interface QualityReport {
  dataset: string
  status: string
  rows: number
  files: number
  issues: Array<{
    code: string
    message: string
    severity: string
  }>
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

function loadWatchlist(): string[] {
  try {
    const stored = JSON.parse(window.localStorage.getItem(WATCHLIST_STORAGE_KEY) ?? '[]')
    if (!Array.isArray(stored)) return []
    return [...new Set(
      stored
        .filter((item): item is string => typeof item === 'string')
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean),
    )]
  } catch {
    return []
  }
}

export function DataWorkbenchWidget() {
  const { language } = useLanguage()
  const copy = language === 'zh' ? {
    title: '数据工作台',
    description: '选择证券，直接查看行情、成交量和技术指标。',
    demoProfile: '演示数据',
    runtimeProfile: '本地 RQ 数据',
    source: '数据源',
    updatedThrough: '数据更新至',
    availableSymbols: '可选证券',
    ready: '可用',
    coverage: '覆盖范围',
    symbols: '只证券',
    loading: '加载中',
    reloadChart: '重新加载',
    reloadChartTitle: '重新读取当前证券行情',
    dataManagement: '高级数据管理',
    dataManagementDescription: '管理本地研究数据库、质量检查和原始数据查询。',
    runtimeReadiness: '本地数据集',
    checkUpdates: '检查更新',
    checkUpdatesTitle: '检查本地 RQ 研究数据的更新范围',
    startUpdate: '开始更新',
    startUpdateTitle: '下载并更新已确认的本地研究数据',
    validate: '数据质量检查',
    validateTitle: '检查所有本地研究数据集',
    configuredRequired: '请先配置',
    rqEnvironment: '本地 RQ 环境',
    beforeSync: '后再更新本地研究数据。',
    demoFallback: '这不影响演示数据，也不影响查看已经保存在本地的行情。',
    datasets: '个数据集',
    batches: '个批次',
    updatePlan: '更新计划',
    updateScope: '将更新',
    tabsLabel: '高级数据管理视图',
    catalogTab: '数据目录',
    previewTab: '数据查询',
    jobsTab: '更新任务',
    marketSymbol: '股票代码或名称',
    addWatchlist: '加入自选',
    removeWatchlist: '移出自选',
    watchlist: '自选股',
    watchlistLocal: '保存在当前浏览器',
    watchlistEmpty: '暂无自选股。搜索股票后点击“加入自选”。',
    unavailableSymbol: '当前数据源不可用',
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
    qualityPassed: '质量检查通过',
    qualityFailed: '质量检查未通过',
    noQualityIssues: '未发现问题',
    rowsUnit: '行',
  } : {
    title: 'Data Workbench',
    description: 'Choose a symbol and inspect its market data, volume, and indicators.',
    demoProfile: 'Demo profile',
    runtimeProfile: 'Local RQ profile',
    source: 'Data source',
    updatedThrough: 'Updated through',
    availableSymbols: 'Available symbols',
    ready: 'Ready',
    coverage: 'Coverage',
    symbols: 'symbols',
    loading: 'loading',
    reloadChart: 'Reload',
    reloadChartTitle: 'Reload market data for the current symbol',
    dataManagement: 'Advanced data management',
    dataManagementDescription: 'Manage the local research database, quality checks, and raw data queries.',
    runtimeReadiness: 'Local datasets',
    checkUpdates: 'Check for updates',
    checkUpdatesTitle: 'Check the update scope for local RQ research data',
    startUpdate: 'Start update',
    startUpdateTitle: 'Download and update the confirmed local research data',
    validate: 'Check data quality',
    validateTitle: 'Check all local research datasets',
    configuredRequired: 'Configure',
    rqEnvironment: 'the local RQ environment',
    beforeSync: 'before updating local research data.',
    demoFallback: 'This does not affect demo data or market data already saved locally.',
    datasets: 'datasets',
    batches: 'batches',
    updatePlan: 'Update plan',
    updateScope: 'Will update',
    tabsLabel: 'Advanced data management view',
    catalogTab: 'Catalog',
    previewTab: 'Data query',
    jobsTab: 'Update jobs',
    marketSymbol: 'Stock symbol or name',
    addWatchlist: 'Add to watchlist',
    removeWatchlist: 'Remove from watchlist',
    watchlist: 'Watchlist',
    watchlistLocal: 'Saved in this browser',
    watchlistEmpty: 'No stocks yet. Search for a stock, then add it to the watchlist.',
    unavailableSymbol: 'Unavailable in this data source',
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
    qualityPassed: 'Quality check passed',
    qualityFailed: 'Quality check failed',
    noQualityIssues: 'No issues found',
    rowsUnit: 'rows',
  }
  const refreshRevision = useWorkspaceRefresh()
  const { selectedDataset, setSelectedDataset, selectedSymbol, setSelectedSymbol } = useWorkspace()
  const [status, setStatus] = useState<ProviderStatus | null>(null)
  const [error, setError] = useState('')
  const [health, setHealth] = useState<DataSyncHealth | null>(null)
  const [catalog, setCatalog] = useState<RuntimeCatalog | null>(null)
  const [jobs, setJobs] = useState<SyncJob[]>([])
  const [plan, setPlan] = useState<SyncPlan | null>(null)
  const [profile, chooseProfile] = useDataProfile()
  const [busy, setBusy] = useState(false)
  const [qualityReports, setQualityReports] = useState<QualityReport[] | null>(null)
  const [tab, setTab] = useState<DataWorkbenchTab>('catalog')
  const [datasetId, setDatasetId] = useState('')
  const [symbolFilter, setSymbolFilter] = useState('')
  const [startFilter, setStartFilter] = useState('')
  const [endFilter, setEndFilter] = useState('')
  const [columnFilter, setColumnFilter] = useState('')
  const [rowLimit, setRowLimit] = useState(50)
  const [dataPreview, setDataPreview] = useState<DataPreview | null>(null)
  const [marketInstruments, setMarketInstruments] = useState<MarketInstrument[]>([])
  const [marketRows, setMarketRows] = useState<MarketBar[]>([])
  const [marketRange, setMarketRange] = useState<MarketRange>('1y')
  const [marketBusy, setMarketBusy] = useState(false)
  const [marketError, setMarketError] = useState('')
  const [marketReloadRevision, setMarketReloadRevision] = useState(0)
  const [watchlist, setWatchlist] = useState<string[]>(loadWatchlist)
  const [selectedIndicators, setSelectedIndicators] = useIndicatorSelection('data-workbench-market')

  const refresh = useCallback(async () => {
    try {
      const [providerStatus, syncHealth, runtimeCatalog, syncJobs] = await Promise.all([
        apiGet<ProviderStatus>('/data/providers'),
        apiGet<DataSyncHealth>('/data-sync/health'),
        apiGet<RuntimeCatalog>('/data-sync/catalog'),
        apiGet<SyncJob[]>('/data-sync/jobs?limit=8'),
      ])
      setStatus(providerStatus)
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
    setMarketInstruments([])
    setMarketRows([])
    setMarketError('')
    apiGet<{ symbols: string[]; instruments?: MarketInstrument[] }>(`/data/market/symbols?profile=${profile}`)
      .then((payload) => {
        if (!active) return
        const instruments = payload.instruments?.length
          ? payload.instruments
          : payload.symbols.map((symbol) => ({ symbol, name: null }))
        setMarketInstruments(instruments)
      })
      .catch((err: Error) => {
        if (active) setMarketError(err.message)
      })
    return () => { active = false }
  }, [profile, refreshRevision])

  useEffect(() => {
    try {
      window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(watchlist))
    } catch {
      // The workbench remains usable when browser storage is disabled.
    }
  }, [watchlist])

  const marketSymbols = useMemo(
    () => marketInstruments.map((item) => item.symbol),
    [marketInstruments],
  )
  const marketInstrumentBySymbol = useMemo(
    () => new Map(marketInstruments.map((item) => [item.symbol, item])),
    [marketInstruments],
  )
  const marketSymbol = selectedSymbol && marketSymbols.includes(selectedSymbol)
    ? selectedSymbol
    : marketSymbols[0] ?? ''
  const marketSymbolIsWatched = watchlist.includes(marketSymbol)
  const profileStatus = status?.profiles[profile]
  const marketEnd = profileStatus?.latest_date ?? null

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
  }, [marketEnd, marketRange, marketReloadRevision, marketSymbol, profile, refreshRevision])

  async function checkUpdates() {
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

  async function startUpdate() {
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
      setTab('jobs')
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
      const reports = await apiPost<QualityReport[]>('/data-sync/validate', {})
      setQualityReports(reports)
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
  const qualityPassed = qualityReports?.filter((item) => item.status === 'passed').length ?? 0
  const latestJob = jobs[0]
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

  function datasetLabel(id: string): string {
    return catalog?.datasets.find((item) => item.id === id)?.label ?? id
  }

  function toggleWatchlist(symbol: string) {
    if (!symbol) return
    setWatchlist((current) => current.includes(symbol)
      ? current.filter((item) => item !== symbol)
      : [...current, symbol])
  }

  if (error) return <div className="panel"><h2>{copy.title}</h2><p className="error">{error}</p></div>
  return (
    <div className="panel">
      <div className="panel-heading data-workbench-heading">
        <div>
          <h2>{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
        <div className="data-source-summary">
          <label>
            <span>{copy.source}</span>
            <select value={profile} onChange={(event) => chooseProfile(event.target.value as DataProfile)}>
              <option value="demo">{copy.demoProfile}</option>
              <option value="runtime">{copy.runtimeProfile}</option>
            </select>
          </label>
          <span className={`status-pill ${profileStatus?.status === 'ready' ? 'ready' : 'neutral'}`}>
            {profileStatus?.status ?? copy.loading}
          </span>
          <span className="status-pill neutral">
            <CalendarDays size={13} /> {copy.updatedThrough}: {profileStatus?.latest_date ?? '—'}
          </span>
          <span className="status-pill">
            {copy.availableSymbols}: {(profileStatus?.symbol_count ?? 0).toLocaleString()}
          </span>
        </div>
      </div>
      <div className="workbench-body market-kline-workbench">
        <div className="market-kline-toolbar">
          <SymbolCombobox
            symbols={marketInstruments}
            value={marketSymbol}
            onChange={setSelectedSymbol}
            ariaLabel={copy.marketSymbol}
          />
          <button
            className={`watchlist-toggle ${marketSymbolIsWatched ? 'active' : ''}`}
            type="button"
            aria-pressed={marketSymbolIsWatched}
            onClick={() => toggleWatchlist(marketSymbol)}
            disabled={!marketSymbol}
            title={marketSymbolIsWatched ? copy.removeWatchlist : copy.addWatchlist}
          >
            <Star size={14} fill={marketSymbolIsWatched ? 'currentColor' : 'none'} />
            {marketSymbolIsWatched ? copy.removeWatchlist : copy.addWatchlist}
          </button>
          <select aria-label={copy.klineRange} value={marketRange} onChange={(event) => setMarketRange(event.target.value as MarketRange)}>
            <option value="3m">{copy.threeMonths}</option>
            <option value="6m">{copy.sixMonths}</option>
            <option value="1y">{copy.oneYear}</option>
            <option value="all">{copy.allHistory}</option>
          </select>
          <IndicatorMenu selected={selectedIndicators} onChange={setSelectedIndicators} />
          <button
            className="market-refresh-button"
            type="button"
            onClick={() => setMarketReloadRevision((value) => value + 1)}
            disabled={marketBusy || !marketSymbol}
            title={copy.reloadChartTitle}
          >
            <RefreshCw size={14} /> {copy.reloadChart}
          </button>
          <span className="status-pill neutral"><CalendarDays size={13} /> {copy.dailyAdjusted}</span>
        </div>
        <section className="data-watchlist" aria-label={copy.watchlist}>
          <div className="data-watchlist-heading">
            <strong><Star size={13} /> {copy.watchlist}</strong>
            <span>{watchlist.length} · {copy.watchlistLocal}</span>
          </div>
          {watchlist.length ? (
            <div className="data-watchlist-list">
              {watchlist.map((symbol) => {
                const instrument = marketInstrumentBySymbol.get(symbol)
                return (
                  <div className={`data-watchlist-item ${symbol === marketSymbol ? 'active' : ''}`} key={symbol}>
                    <button
                      className="data-watchlist-select"
                      type="button"
                      disabled={!instrument}
                      onClick={() => setSelectedSymbol(symbol)}
                      title={instrument ? `${symbol}${instrument.name ? ` ${instrument.name}` : ''}` : copy.unavailableSymbol}
                    >
                      <strong>{symbol}</strong>
                      <span>{instrument?.name || (!instrument ? copy.unavailableSymbol : '—')}</span>
                    </button>
                    <button
                      className="data-watchlist-remove"
                      type="button"
                      aria-label={`${copy.removeWatchlist}: ${symbol}`}
                      onClick={() => toggleWatchlist(symbol)}
                      title={copy.removeWatchlist}
                    >
                      <X size={13} />
                    </button>
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="data-watchlist-empty">{copy.watchlistEmpty}</p>
          )}
        </section>
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

      <details className="data-management">
        <summary>
          <Database size={17} />
          <span className="data-management-title">
            <strong>{copy.dataManagement}</strong>
            <small>{copy.dataManagementDescription}</small>
          </span>
          <span className="data-management-status">
            {copy.runtimeReadiness}: {catalog?.ready ?? 0}/{catalog?.configured ?? 0}
            {latestJob ? ` · ${latestJob.status}` : ''}
          </span>
          <ChevronDown className="data-management-chevron" size={17} />
        </summary>

        <div className="data-management-body">
          <div className="sync-toolbar">
            <span className={`status-pill ${health?.rq.configured ? 'ready' : 'neutral'}`}>
              <Database size={13} /> RQ {health?.rq.status ?? copy.loading}
            </span>
            <button type="button" onClick={checkUpdates} disabled={busy} title={copy.checkUpdatesTitle}>
              <RefreshCw size={14} /> {copy.checkUpdates}
            </button>
            <button type="button" onClick={validateRuntime} disabled={busy || catalog?.ready === 0} title={copy.validateTitle}>
              <ShieldCheck size={14} /> {copy.validate}
            </button>
            {qualityReports && (
              <span className={`status-pill ${qualityPassed === qualityReports.length ? 'ready' : 'neutral'}`}>
                {qualityPassed}/{qualityReports.length} {copy.passed}
              </span>
            )}
          </div>
          {!health?.rq.configured && (
            <p className="data-guidance">
              {copy.configuredRequired} {health?.rq.missing.join(', ') || copy.rqEnvironment} {copy.beforeSync} {copy.demoFallback}
            </p>
          )}
          {plan && (
            <div className="sync-plan-card">
              <div>
                <strong>{copy.updatePlan}</strong>
                <span>{copy.updateScope} {plan.symbol_count} {copy.symbols} · {plan.steps.length} {copy.datasets} · {plan.estimated_batches} {copy.batches}</span>
                <small>{plan.requested_start} → {plan.requested_end}</small>
              </div>
              <button className="primary-command" type="button" onClick={startUpdate} disabled={busy || !health?.rq.configured} title={copy.startUpdateTitle}>
                <Play size={14} /> {copy.startUpdate}
              </button>
            </div>
          )}
          {qualityReports && (
            <div className="quality-report-list">
              {qualityReports.map((report) => (
                <div className="quality-report-row" key={report.dataset}>
                  <span className={report.status === 'passed' ? 'gate-pass-text' : 'gate-fail-text'}>
                    {report.status === 'passed' ? copy.qualityPassed : copy.qualityFailed}
                  </span>
                  <strong>{datasetLabel(report.dataset)}</strong>
                  <small>{report.rows.toLocaleString()} {copy.rowsUnit}</small>
                  <div>
                    {report.issues.length === 0
                      ? copy.noQualityIssues
                      : report.issues.map((issue) => <span key={`${report.dataset}-${issue.code}`}>{issue.message}</span>)}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="workbench-tabs" role="tablist" aria-label={copy.tabsLabel}>
            <button type="button" role="tab" aria-selected={tab === 'catalog'} onClick={() => setTab('catalog')}>{copy.catalogTab}</button>
            <button type="button" role="tab" aria-selected={tab === 'preview'} onClick={() => setTab('preview')}>{copy.previewTab}</button>
            <button type="button" role="tab" aria-selected={tab === 'jobs'} onClick={() => setTab('jobs')}>{copy.jobsTab}</button>
          </div>

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
      </details>
    </div>
  )
}
