export interface PositionAnalysisSummary {
  closed_count: number
  open_count: number
  win_count: number
  loss_count: number
  win_rate: number
  realized_pnl: number
  commission: number
  net_realized_pnl: number
  options_net_realized_pnl: number
  stocks_net_realized_pnl: number
  profit_factor: number
  avg_win: number
  avg_loss: number
  avg_holding_days: number
  best_symbol: string
  best_pnl: number
  worst_symbol: string
  worst_pnl: number
}

export interface PositionRoundTrip {
  symbol: string
  underlying: string
  asset_category: string
  currency: string
  is_option: boolean
  put_call: string
  strike: number
  expiry: string
  multiplier: number
  status: "closed" | "open" | string
  first_trade_date: string
  last_trade_date: string
  holding_days: number
  trades: number
  buy_quantity: number
  sell_quantity: number
  net_quantity: number
  avg_buy_price: number
  avg_sell_price: number
  realized_pnl: number
  commission: number
  net_realized_pnl: number
}

export interface MonthlyPnlPoint {
  month: string
  realized_pnl: number
  commission: number
  net_realized_pnl: number
  cumulative_net_pnl: number
  trades: number
}

export interface UnderlyingContribution {
  underlying: string
  net_realized_pnl: number
  commission: number
  positions: number
  option_positions: number
}

export interface ExecutionAssetRow {
  asset_category: string
  fills: number
  notional: number
  commission: number
  commission_bps: number
  avg_slip_pct: number | null
  median_slip_pct: number | null
  slip_value: number | null
}

export interface OrderDispersionRow {
  order_id: string
  date: string
  symbol: string
  side: "buy" | "sell" | string
  fills: number
  quantity: number
  vwap: number
  price_min: number
  price_max: number
  range_pct: number
  is_option: boolean
}

export interface SlippageTradeRow {
  date: string
  time: string
  symbol: string
  side: "buy" | "sell" | string
  quantity: number
  trade_price: number
  close_price: number
  slip_pct: number
  slip_value: number
  is_option: boolean
}

export interface ExecutionQuality {
  notional_traded: number
  total_commission: number
  commission_bps: number
  fills: number
  orders: number
  by_asset: ExecutionAssetRow[]
  order_dispersion: OrderDispersionRow[]
  worst_slippage_trades: SlippageTradeRow[]
  close_mark_coverage: number
  data_notes: string[]
}

export interface PositionAnalysisResponse {
  source: string
  configured: boolean
  generated_at: string
  filters: { start?: string | null; end?: string | null; underlying?: string | null }
  summary: PositionAnalysisSummary
  positions: PositionRoundTrip[]
  monthly: MonthlyPnlPoint[]
  by_underlying: UnderlyingContribution[]
  execution: ExecutionQuality
  coverage: {
    trade_rows?: number
    date_range?: Array<string | null>
    provider?: {
      configured?: boolean
      missing_config?: string[]
      latest_fetch_time?: string | null
    }
  }
  warnings: string[]
}
