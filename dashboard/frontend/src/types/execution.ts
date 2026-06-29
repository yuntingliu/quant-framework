/** Current signal with ETF mapping. */
export interface CurrentSignal {
  date: string
  index_code: string
  index_name: string
  etf_code: string
  signals: Record<string, number>
  raw_position: number
  position: number
  realized_vol: number
}

/** A single execution log entry. */
export interface ExecutionLogEntry {
  timestamp: string
  signal_date: string
  index: string
  etf: string
  target_weight: number
  action: string
  delta_shares: number
  price: number
  trade_value: number
  order_id: string | null
  order_status: string | null
}

/** Execution log response. */
export interface ExecutionLog {
  entries: ExecutionLogEntry[]
  total: number
}

/** Dry-run execution preview. */
export interface ExecutionPreview {
  preview_text: string
  action: string
  target_weight: number
  current_weight: number
  delta_shares: number
  trade_value: number
  etf_code: string
  index_code: string
}
