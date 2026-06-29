// Mirrors the Python DecisionCard JSON from alphalab.research.daily_brief.

export type SignalType = "accumulate" | "reduce" | "watch" | "avoid"
export type Confidence = "low" | "medium" | "high"
export type SynthesisMode = "llm" | "deterministic"

export interface NewsItem {
  title: string
  url: string
  source: string
  published_at: string | null
  summary: string
  sentiment: number | null
}

export interface CoreConclusion {
  signal_type: SignalType
  confidence: Confidence
  operation_advice: string
  time_sensitivity: string
}

export interface DataPerspective {
  trend_status: string
  price_position: string
  volume_analysis: string
  chip_structure: string
  factor_scores: Record<string, number>
}

export interface Intelligence {
  latest_news: NewsItem[]
  risk_alerts: string[]
  positive_catalysts: string[]
  earnings_outlook: string | null
}

export interface SniperPoints {
  ideal_buy: number | null
  secondary_buy: number | null
  stop_loss: number | null
  take_profit: number | null
}

export interface BattlePlan {
  sniper_points: SniperPoints
  action_checklist: string[]
}

export interface DecisionCard {
  symbol: string
  asof_date: string
  name: string | null
  core_conclusion: CoreConclusion
  data_perspective: DataPerspective
  intelligence: Intelligence
  battle_plan: BattlePlan
  sentiment_score: number
  synthesis: SynthesisMode
  guardrail: string
  evidence_ids: string[]
}

export interface DailyBriefLatest {
  asof_date: string | null
  cards: DecisionCard[]
  count: number
}

export interface DailyBriefRunResponse {
  asof_date: string
  run_id: string
  universe_label: string
  warnings: string[]
  created_at: string
  cards: DecisionCard[]
}

/** Realtime envelope payloads broadcast on the "daily_brief" topic. */
export type DailyBriefEvent =
  | { event: "card"; card: DecisionCard }
  | { event: "complete"; run_id: string; asof_date: string; count: number; warnings: string[] }
