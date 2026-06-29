export const FACTOR_COLORS = {
  MKT: '#2962FF',
  SMB: '#00C853',
  HML: '#FF6D00',
} as const

export const CHART_COLORS = [
  '#2962FF', '#00C853', '#FF6D00', '#AB47BC', '#26A69A',
  '#EC407A', '#7E57C2', '#5C6BC0', '#00897B', '#F4511E',
] as const

export const ALL_FACTORS = ['MKT', 'SMB', 'HML'] as const
export type FactorName = (typeof ALL_FACTORS)[number]

export const PAGE_ROUTES = {
  market: '/',
  factors: '/factors',
  backtest: '/backtest',
  optimizer: '/optimizer',
  risk: '/risk',
  timing: '/timing',
  execution: '/execution',
} as const

export const INDEX_OPTIONS = [
  { value: '000300', label: 'CSI 300', labelCN: '沪深300', etf: '510300.SH' },
  { value: '000905', label: 'CSI 500', labelCN: '中证500', etf: '510500.SH' },
  { value: '000852', label: 'CSI 1000', labelCN: '中证1000', etf: '159845.SZ' },
  { value: '000016', label: 'SSE 50', labelCN: '上证50', etf: '510050.SH' },
] as const

/** React Query staleTime values (ms). */
export const STALE_TIME = {
  /** 1 min — read-only endpoints (market overview, factor stats) */
  SHORT: 60_000,
  /** 2 min — expensive endpoints (backtest, optimizer, risk) */
  LONG: 120_000,
} as const

export const TIMING_METHOD_OPTIONS: { value: string; label: string }[] = [
  { value: 'combined', label: 'Combined (Vol x TSMom)' },
  { value: 'vol_only', label: 'Vol-Timing Only' },
  { value: 'tsmom_only', label: 'TSMom Only' },
]

/** Trading safety constants. */
export const TRADING = {
  CONFIRM_STRING: "I-UNDERSTAND-REAL-ORDERS",
  MIN_ORDER_AMOUNT: 1000,        // ¥ minimum per order
  VOLUME_STEP: 100,              // A-share lot size
  PRICE_DEVIATION_WARN: 0.03,    // warn if price > 3% from market
  MAX_POSITION_PCT: 0.10,        // 10% of portfolio per stock
} as const

/** API timeout values (ms). */
export const API_TIMEOUTS = {
  SHORT: 5_000,
  LONG: 30_000,
  BACKTEST: 120_000,
} as const

export const PAGE_LABELS: Record<string, string> = {
  '/': '市场概览',
  '/factors': '因子分析',
  '/backtest': '策略回测',
  '/optimizer': '组合优化',
  '/risk': '风险分析',
  '/timing': '指数择时',
  '/execution': '执行监控',
}
