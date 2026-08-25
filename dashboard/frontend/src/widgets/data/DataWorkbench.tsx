import { useCallback, useEffect, useMemo, useState } from 'react'
import { BarChart3, CalendarDays, ChevronDown, Database, ListFilter, Play, Plus, RefreshCw, Save, Search, ShieldCheck, Star, X } from 'lucide-react'
import { CandlestickChart } from '../../components/charts'
import { CSVExportButton } from '../../components/shared/CSVExportButton'
import { IndicatorMenu } from '../../components/shared/IndicatorMenu'
import { SymbolCombobox } from '../../components/shared/SymbolCombobox'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card'
import { Checkbox } from '../../components/ui/checkbox'
import { Input } from '../../components/ui/input'
import { Label } from '../../components/ui/label'
import {
  api,
  apiGet,
  apiPost,
  type DataSyncHealth,
  type MarketBar,
  type MarketInstrument,
  type PipelineProjectDetail,
  type ProviderStatus,
  type RuntimeCatalog,
  type SyncJob,
  type SyncPlan,
} from '../../lib/api'
import { useWorkspaceRefresh } from '../../hooks/useWorkspaceRefresh'
import { useIndicatorSelection } from '../../hooks/useIndicatorSelection'
import { useLanguage } from '../../contexts/LanguageContext'
import { useDataProfile } from '../../lib/data-profile'
import { useWorkspace } from '../../contexts/WorkspaceContext'
import { RiskDataWorkspace } from './RiskDataWorkspace'

type DataWorkbenchTab = 'catalog' | 'risk' | 'preview' | 'jobs'
type MarketRange = '3m' | '6m' | '1y' | 'all'
type ResearchScopeMode = 'all' | 'custom'
const WATCHLIST_STORAGE_KEY = 'alphalab.data-watchlist.v1'

interface ResearchScope {
  symbols: string[]
  minPrice: number
  minHistoryDays: number
  minAverageAmount: number
  maxStaleDays: number
  requirePositiveVolume: boolean
}

const DEFAULT_RESEARCH_SCOPE: ResearchScope = {
  symbols: [],
  minPrice: 0,
  minHistoryDays: 60,
  minAverageAmount: 0,
  maxStaleDays: 7,
  requirePositiveVolume: true,
}

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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function finiteNumber(value: unknown, fallback: number): number {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function researchScopeFrom(settings: Record<string, unknown>): ResearchScope {
  const universe = asRecord(settings.universe)
  return {
    symbols: Array.isArray(universe.symbols)
      ? [...new Set(universe.symbols.map((value) => String(value).trim().toUpperCase()).filter(Boolean))]
      : [],
    minPrice: Math.max(0, finiteNumber(universe.min_price, DEFAULT_RESEARCH_SCOPE.minPrice)),
    minHistoryDays: Math.max(2, Math.round(finiteNumber(universe.min_history_days, DEFAULT_RESEARCH_SCOPE.minHistoryDays))),
    minAverageAmount: Math.max(0, finiteNumber(universe.min_average_amount, DEFAULT_RESEARCH_SCOPE.minAverageAmount)),
    maxStaleDays: Math.max(0, Math.round(finiteNumber(universe.max_stale_days, DEFAULT_RESEARCH_SCOPE.maxStaleDays))),
    requirePositiveVolume: universe.require_positive_volume !== false,
  }
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
    researchScope: '研究范围',
    researchScopeDescription: '这里定义进入因子研究和信号排名的初始候选；偏好与排序仍由因子模型决定。',
    project: '项目',
    allSecurities: '全部可用证券',
    allSecuritiesHint: '使用当前数据源中的全部证券',
    customSecurities: '自定义范围',
    customSecuritiesHint: '只研究明确加入的证券',
    initialCandidates: '初始候选',
    candidatesUnit: '只',
    addCurrent: '加入当前证券',
    addFromWatchlist: '导入自选股',
    removeFromScope: '移出研究范围',
    customScopeEmpty: '请从当前证券或自选股中加入至少一只证券。',
    eligibilityRules: '自动准入筛选',
    eligibilityRulesHint: '在每个研究截面先排除数据不足或不可交易标的，不参与因子加权。',
    minHistoryDays: '最少历史天数',
    minPrice: '最低价格',
    minAverageAmount: '最低平均成交额',
    maxStaleDays: '行情最长停滞天数',
    positiveVolume: '要求当日成交量大于 0',
    saveScope: '保存研究范围',
    scopeSaved: '研究范围已保存，后续因子研究、信号与回测将共同使用。',
    scopeReadOnly: '当前项目只读，复制为可编辑项目后才能修改研究范围。',
    noProject: '请先选择一个可编辑研究项目。',
    ready: '可用',
    coverage: '覆盖范围',
    symbols: '只证券',
    loading: '加载中',
    reloadChart: '重新加载',
    reloadChartTitle: '重新读取当前证券行情',
    dataManagement: '高级数据管理',
    dataManagementDescription: '管理本地研究数据库、风险数据、质量检查和原始数据查询。',
    runtimeReadiness: '本地数据集',
    checkUpdates: '检查更新',
    checkUpdatesTitle: '检查本地 RQ 研究数据的更新范围',
    startUpdate: '开始更新',
    startUpdateTitle: '下载并更新已确认的本地研究数据',
    validate: '数据质量检查',
    validateTitle: '检查所有本地研究数据集',
    configuredRequired: '请先配置',
    rqClientMissing: '未安装 RQData 客户端，请运行 pip install -e ".[rq]"。',
    rqCredentialsMissing: '请先配置本地 RQ 凭据',
    rqEnvironment: '本地 RQ 环境',
    beforeSync: '后再更新本地研究数据。',
    demoFallback: '这不影响演示数据，也不影响查看已经保存在本地的行情。',
    datasets: '个数据集',
    batches: '个批次',
    updatePlan: '更新计划',
    updateScope: '将更新',
    allAShares: '全部 A 股',
    resolveOnStart: '证券数量将在任务启动时从 RQ 证券主数据解析',
    batchesPending: '批次数待解析',
    tabsLabel: '高级数据管理视图',
    catalogTab: '数据目录',
    riskTab: '风险数据',
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
    researchScope: 'Research scope',
    researchScopeDescription: 'Define the initial candidates for factor research and signal ranking; factors still own preferences and ordering.',
    project: 'Project',
    allSecurities: 'All available securities',
    allSecuritiesHint: 'Use every security in the current data source',
    customSecurities: 'Custom scope',
    customSecuritiesHint: 'Research only explicitly included securities',
    initialCandidates: 'Initial candidates',
    candidatesUnit: '',
    addCurrent: 'Add current symbol',
    addFromWatchlist: 'Import watchlist',
    removeFromScope: 'Remove from research scope',
    customScopeEmpty: 'Add at least one symbol from the current security or watchlist.',
    eligibilityRules: 'Automatic eligibility filters',
    eligibilityRulesHint: 'Exclude insufficient or untradable data before each cross-section; these rules are not factor weights.',
    minHistoryDays: 'Minimum history days',
    minPrice: 'Minimum price',
    minAverageAmount: 'Minimum average amount',
    maxStaleDays: 'Maximum stale days',
    positiveVolume: 'Require positive daily volume',
    saveScope: 'Save research scope',
    scopeSaved: 'Research scope saved for factor research, signals, and backtests.',
    scopeReadOnly: 'This project is read-only. Clone it before changing the research scope.',
    noProject: 'Select an editable research project first.',
    ready: 'Ready',
    coverage: 'Coverage',
    symbols: 'symbols',
    loading: 'loading',
    reloadChart: 'Reload',
    reloadChartTitle: 'Reload market data for the current symbol',
    dataManagement: 'Advanced data management',
    dataManagementDescription: 'Manage the local research database, risk data, quality checks, and raw data queries.',
    runtimeReadiness: 'Local datasets',
    checkUpdates: 'Check for updates',
    checkUpdatesTitle: 'Check the update scope for local RQ research data',
    startUpdate: 'Start update',
    startUpdateTitle: 'Download and update the confirmed local research data',
    validate: 'Check data quality',
    validateTitle: 'Check all local research datasets',
    configuredRequired: 'Configure',
    rqClientMissing: 'The RQData client is not installed. Run pip install -e ".[rq]".',
    rqCredentialsMissing: 'Configure local RQ credentials',
    rqEnvironment: 'the local RQ environment',
    beforeSync: 'before updating local research data.',
    demoFallback: 'This does not affect demo data or market data already saved locally.',
    datasets: 'datasets',
    batches: 'batches',
    updatePlan: 'Update plan',
    updateScope: 'Will update',
    allAShares: 'all A-shares',
    resolveOnStart: 'The symbol count will be resolved from RQ instruments when the job starts',
    batchesPending: 'batch count pending',
    tabsLabel: 'Advanced data management view',
    catalogTab: 'Catalog',
    riskTab: 'Risk data',
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
  const {
    selectedDataset,
    selectedStrategy,
    selectedSymbol,
    setSelectedDataset,
    setSelectedStrategyRevision,
    setSelectedSymbol,
  } = useWorkspace()
  const [status, setStatus] = useState<ProviderStatus | null>(null)
  const [error, setError] = useState('')
  const [scopeError, setScopeError] = useState('')
  const [scopeMessage, setScopeMessage] = useState('')
  const [scopeBusy, setScopeBusy] = useState(false)
  const [scopeDirty, setScopeDirty] = useState(false)
  const [project, setProject] = useState<PipelineProjectDetail | null>(null)
  const [scopeMode, setScopeMode] = useState<ResearchScopeMode>('all')
  const [researchScope, setResearchScope] = useState<ResearchScope>(DEFAULT_RESEARCH_SCOPE)
  const [health, setHealth] = useState<DataSyncHealth | null>(null)
  const [catalog, setCatalog] = useState<RuntimeCatalog | null>(null)
  const [jobs, setJobs] = useState<SyncJob[]>([])
  const [plan, setPlan] = useState<SyncPlan | null>(null)
  const [profile] = useDataProfile()
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
    let active = true
    setScopeError('')
    setScopeMessage('')
    if (!selectedStrategy) {
      setProject(null)
      setResearchScope(DEFAULT_RESEARCH_SCOPE)
      setScopeMode('all')
      setScopeDirty(false)
      return () => { active = false }
    }
    api.get<PipelineProjectDetail>(`/pipeline/projects/${selectedStrategy}`)
      .then((next) => {
        if (!active) return
        const nextScope = researchScopeFrom(next.settings)
        setProject(next)
        setResearchScope(nextScope)
        setScopeMode(nextScope.symbols.length ? 'custom' : 'all')
        setScopeDirty(false)
        setSelectedStrategyRevision(next.revision)
      })
      .catch((reason: Error) => {
        if (active) setScopeError(reason.message)
      })
    return () => { active = false }
  }, [selectedStrategy, setSelectedStrategyRevision])

  useEffect(() => {
    const handleProjectUpdated = (event: Event) => {
      const next = (event as CustomEvent<PipelineProjectDetail>).detail
      if (!next || next.id !== selectedStrategy) return
      setProject(next)
      setSelectedStrategyRevision(next.revision)
      if (scopeDirty) return
      const nextScope = researchScopeFrom(next.settings)
      setResearchScope(nextScope)
      setScopeMode(nextScope.symbols.length ? 'custom' : 'all')
    }
    window.addEventListener('alphalab:projectUpdated', handleProjectUpdated)
    return () => window.removeEventListener('alphalab:projectUpdated', handleProjectUpdated)
  }, [scopeDirty, selectedStrategy, setSelectedStrategyRevision])

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

  function changeScopeMode(mode: ResearchScopeMode) {
    if (mode === scopeMode) return
    setScopeMode(mode)
    setScopeDirty(true)
    setScopeMessage('')
    setScopeError('')
  }

  function updateResearchScope(patch: Partial<ResearchScope>) {
    setResearchScope((current) => ({ ...current, ...patch }))
    setScopeDirty(true)
    setScopeMessage('')
    setScopeError('')
  }

  function addScopeSymbols(symbols: string[]) {
    const available = new Set(marketSymbols)
    const additions = symbols
      .map((symbol) => symbol.trim().toUpperCase())
      .filter((symbol) => symbol && available.has(symbol))
    if (!additions.length) return
    setScopeMode('custom')
    updateResearchScope({ symbols: [...new Set([...researchScope.symbols, ...additions])] })
  }

  function removeScopeSymbol(symbol: string) {
    updateResearchScope({ symbols: researchScope.symbols.filter((item) => item !== symbol) })
  }

  async function saveResearchScope() {
    if (!project?.editable || (scopeMode === 'custom' && !researchScope.symbols.length)) return
    setScopeBusy(true)
    setScopeError('')
    setScopeMessage('')
    try {
      // Reload before saving so changes made in the factor, signal, or backtest
      // workbench are not overwritten by a stale project snapshot.
      const latest = await api.get<PipelineProjectDetail>(`/pipeline/projects/${project.id}`)
      const saved = await api.put<PipelineProjectDetail>(`/pipeline/projects/${project.id}`, {
        name: latest.name,
        description: latest.description,
        components: latest.components,
        settings: {
          ...latest.settings,
          universe: {
            ...asRecord(latest.settings.universe),
            pool: 'all',
            symbols: scopeMode === 'custom' ? researchScope.symbols : [],
            min_price: researchScope.minPrice,
            min_history_days: researchScope.minHistoryDays,
            min_average_amount: researchScope.minAverageAmount,
            max_stale_days: researchScope.maxStaleDays,
            require_positive_volume: researchScope.requirePositiveVolume,
          },
        },
      })
      const savedScope = researchScopeFrom(saved.settings)
      setProject(saved)
      setResearchScope(savedScope)
      setScopeMode(savedScope.symbols.length ? 'custom' : 'all')
      setScopeDirty(false)
      setScopeMessage(copy.scopeSaved)
      setSelectedStrategyRevision(saved.revision)
      window.dispatchEvent(new CustomEvent('alphalab:projectUpdated', { detail: saved }))
    } catch (reason) {
      setScopeError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setScopeBusy(false)
    }
  }

  const initialCandidateCount = scopeMode === 'custom'
    ? researchScope.symbols.length
    : marketSymbols.length || profileStatus?.symbol_count || 0
  const customScopeInvalid = scopeMode === 'custom' && researchScope.symbols.length === 0

  if (error) return <div className="panel"><p className="error">{error}</p></div>
  return (
    <div className="panel">
      <Card className="mx-2 mt-2 rounded-lg shadow-none">
        <CardHeader className="flex-row items-start justify-between gap-4 space-y-0 p-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="flex items-center gap-2 text-sm"><ListFilter size={15} /> {copy.researchScope}</CardTitle>
              {project ? <Badge variant="outline" className="max-w-64 truncate font-normal">{copy.project}: {project.name}</Badge> : null}
              <Badge variant="secondary" className="font-normal">{copy.initialCandidates}: {initialCandidateCount.toLocaleString()} {copy.candidatesUnit}</Badge>
            </div>
            <CardDescription className="mt-1 text-xs">{copy.researchScopeDescription}</CardDescription>
          </div>
          <Button
            size="sm"
            type="button"
            onClick={() => void saveResearchScope()}
            disabled={!project?.editable || !scopeDirty || customScopeInvalid}
            isLoading={scopeBusy}
          >
            <Save /> {copy.saveScope}
          </Button>
        </CardHeader>
        <CardContent className="grid gap-3 p-3 pt-0">
          {!project ? <div className={`workbench-message${scopeError ? ' error' : ''}`}>{scopeError || copy.noProject}</div> : (
            <>
              <div className="grid gap-2 md:grid-cols-2">
                <Button
                  className="h-auto justify-start whitespace-normal px-3 py-2 text-left"
                  variant={scopeMode === 'all' ? 'default' : 'outline'}
                  type="button"
                  onClick={() => changeScopeMode('all')}
                  disabled={!project.editable}
                >
                  <span><strong className="block text-xs">{copy.allSecurities}</strong><small className="block opacity-75">{copy.allSecuritiesHint}</small></span>
                </Button>
                <Button
                  className="h-auto justify-start whitespace-normal px-3 py-2 text-left"
                  variant={scopeMode === 'custom' ? 'default' : 'outline'}
                  type="button"
                  onClick={() => changeScopeMode('custom')}
                  disabled={!project.editable}
                >
                  <span><strong className="block text-xs">{copy.customSecurities}</strong><small className="block opacity-75">{copy.customSecuritiesHint}</small></span>
                </Button>
              </div>

              {scopeMode === 'custom' ? (
                <div className="grid gap-2 rounded-md border border-border bg-muted/20 p-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="outline" type="button" onClick={() => addScopeSymbols([marketSymbol])} disabled={!project.editable || !marketSymbol || researchScope.symbols.includes(marketSymbol)}>
                      <Plus /> {copy.addCurrent}{marketSymbol ? ` · ${marketSymbol}` : ''}
                    </Button>
                    <Button size="sm" variant="outline" type="button" onClick={() => addScopeSymbols(watchlist)} disabled={!project.editable || !watchlist.some((symbol) => !researchScope.symbols.includes(symbol))}>
                      <Star /> {copy.addFromWatchlist}
                    </Button>
                  </div>
                  {researchScope.symbols.length ? (
                    <div className="flex max-h-24 flex-wrap gap-1.5 overflow-auto">
                      {researchScope.symbols.map((symbol) => (
                        <Badge key={symbol} variant="outline" className="gap-1 pr-1 font-mono font-normal">
                          {symbol}
                          <button className="rounded p-0.5 hover:bg-destructive/10 hover:text-destructive" type="button" onClick={() => removeScopeSymbol(symbol)} disabled={!project.editable} aria-label={`${copy.removeFromScope}: ${symbol}`} title={copy.removeFromScope}>
                            <X size={11} />
                          </button>
                        </Badge>
                      ))}
                    </div>
                  ) : <p className="m-0 text-xs text-destructive">{copy.customScopeEmpty}</p>}
                </div>
              ) : null}

              <details className="rounded-md border border-border bg-background">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2">
                  <span className="min-w-0"><strong className="block text-xs text-foreground">{copy.eligibilityRules}</strong><small className="block text-[10px] text-muted-foreground">{copy.eligibilityRulesHint}</small></span>
                  <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
                </summary>
                <div className="grid gap-3 border-t border-border p-3 sm:grid-cols-2 lg:grid-cols-4">
                  <Label className="grid gap-1 normal-case tracking-normal">
                    <span>{copy.minHistoryDays}</span>
                    <Input type="number" min={2} step={1} value={researchScope.minHistoryDays} disabled={!project.editable} onChange={(event) => updateResearchScope({ minHistoryDays: Math.max(2, Math.round(Number(event.target.value) || 2)) })} />
                  </Label>
                  <Label className="grid gap-1 normal-case tracking-normal">
                    <span>{copy.minPrice}</span>
                    <Input type="number" min={0} step="0.01" value={researchScope.minPrice} disabled={!project.editable} onChange={(event) => updateResearchScope({ minPrice: Math.max(0, Number(event.target.value) || 0) })} />
                  </Label>
                  <Label className="grid gap-1 normal-case tracking-normal">
                    <span>{copy.minAverageAmount}</span>
                    <Input type="number" min={0} step={10000} value={researchScope.minAverageAmount} disabled={!project.editable} onChange={(event) => updateResearchScope({ minAverageAmount: Math.max(0, Number(event.target.value) || 0) })} />
                  </Label>
                  <Label className="grid gap-1 normal-case tracking-normal">
                    <span>{copy.maxStaleDays}</span>
                    <Input type="number" min={0} step={1} value={researchScope.maxStaleDays} disabled={!project.editable} onChange={(event) => updateResearchScope({ maxStaleDays: Math.max(0, Math.round(Number(event.target.value) || 0)) })} />
                  </Label>
                  <Label className="flex items-center gap-2 normal-case tracking-normal sm:col-span-2 lg:col-span-4">
                    <Checkbox checked={researchScope.requirePositiveVolume} disabled={!project.editable} onCheckedChange={(checked) => updateResearchScope({ requirePositiveVolume: checked === true })} />
                    <span>{copy.positiveVolume}</span>
                  </Label>
                </div>
              </details>
              {!project.editable ? <div className="workbench-message">{copy.scopeReadOnly}</div> : null}
              {scopeError ? <div className="workbench-message error">{scopeError}</div> : null}
              {scopeMessage ? <div className="workbench-message research-message">{scopeMessage}</div> : null}
            </>
          )}
        </CardContent>
      </Card>
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
            <span className={`status-pill ${health?.rq.ready ? 'ready' : 'neutral'}`}>
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
          {health && !health.rq.ready && (
            <p className="data-guidance">
              {!health.rq.installed
                ? copy.rqClientMissing
                : `${copy.rqCredentialsMissing}: ${health.rq.missing.join(', ') || copy.rqEnvironment} ${copy.beforeSync}`}{' '}
              {copy.demoFallback}
            </p>
          )}
          {plan && (
            <div className="sync-plan-card">
              <div>
                <strong>{copy.updatePlan}</strong>
                <span>
                  {copy.updateScope} {plan.scope === 'all_a_shares' ? copy.allAShares : `${plan.symbol_count ?? 0} ${copy.symbols}`}
                  {' · '}{plan.steps.length} {copy.datasets}
                  {' · '}{plan.estimated_batches === null ? copy.batchesPending : `${plan.estimated_batches} ${copy.batches}`}
                </span>
                {!plan.symbols_resolved ? <small>{copy.resolveOnStart}</small> : null}
                <small>{plan.requested_start} → {plan.requested_end}</small>
              </div>
              <button className="primary-command" type="button" onClick={startUpdate} disabled={busy || !health?.rq.ready} title={copy.startUpdateTitle}>
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
            <button type="button" role="tab" aria-selected={tab === 'risk'} onClick={() => setTab('risk')}>{copy.riskTab}</button>
            <button type="button" role="tab" aria-selected={tab === 'preview'} onClick={() => setTab('preview')}>{copy.previewTab}</button>
            <button type="button" role="tab" aria-selected={tab === 'jobs'} onClick={() => setTab('jobs')}>{copy.jobsTab}</button>
          </div>

          {tab === 'catalog' && (
            <div className="table runtime-table">
              <div className="table-row table-head"><span>{copy.runtimeDataset}</span><span>{copy.status}</span><span>{copy.rows}</span><span>{copy.coverage}</span></div>
              {(catalog?.datasets ?? []).map((dataset) => (
                <button className={`table-row ${dataset.id === datasetId ? 'active' : ''}`} type="button" key={dataset.id} onClick={() => chooseDataset(dataset.id)}>
                  <span>{dataset.label}<small>{dataset.id}{dataset.provider_api?.length ? ` · ${dataset.provider_api.join(' + ')}` : ''}</small></span>
                  <span>{dataset.status}</span>
                  <span>{dataset.rows.toLocaleString()}</span>
                  <span>{dataset.date_start ?? '-'} → {dataset.date_end ?? '-'}</span>
                </button>
              ))}
            </div>
          )}

          {tab === 'risk' && <RiskDataWorkspace profile={profile} />}

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
