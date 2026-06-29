export type DataSyncActionId =
  | "check"
  | "daily_sync"
  | "backfill"
  | "rebuild_panel"
  | "build_factor_cache"
  | "check_repair_queue"
  | "copy_command"

export type DataSyncStatus = "ok" | "warning" | "blocked" | "error" | "unknown" | string

export interface DataSyncAction {
  id: DataSyncActionId
  label: string
  supported: boolean
  dry_run_default: boolean
  disabled_reason?: string | null
  required_fields?: string[]
  optional_fields?: string[]
  parameter_hint?: string | null
}

export interface DataSyncSourceDefinition {
  id: string
  label: string
  group: string
  source_quality: string
  strict_pit: boolean
  dependencies: string[]
  notes: string[]
  actions: DataSyncAction[]
}

export interface DataSyncJob {
  id: string
  source_id: string
  action: DataSyncActionId | string
  status: string
  dry_run: boolean
  command?: string | null
  started_at?: string | null
  ended_at?: string | null
  progress?: Record<string, unknown>
  warnings?: string[]
  result?: DataSyncJobResult | null
  error?: string | null
  message?: string | null
}

export interface DataSyncRepairNextAction {
  source_id?: string | null
  action?: string | null
  label?: string | null
  mode?: string | null
  request_params?: DataSyncRepairRequestParams | null
}

export interface DataSyncRepairRequestParams {
  symbols?: string | null
  start_date?: string | null
  end_date?: string | null
  as_of_date?: string | null
  strategy_id?: string | null
  strategy_path?: string | null
  factor_sources?: string[] | string | null
  allow_stale_days?: string | number | null
  min_coverage?: string | number | null
}

export interface DataSyncRepairFactorDetail {
  name: string
  coverage?: number | null
  coverage_basis?: string | null
  coverage_date?: string | null
  as_of_non_null?: number | null
  as_of_symbols?: number | null
  history_coverage?: number | null
  history_non_null?: number | null
  history_rows?: number | null
  history_symbols?: number | null
  non_null?: number | null
  rows?: number | null
  symbols?: number | null
  start_date?: string | null
  end_date?: string | null
  latest_common_date?: string | null
  latest_factor_date?: string | null
  reasons?: string[]
}

export interface DataSyncRepairStepResult {
  id?: string | null
  source_id?: string | null
  source_label?: string | null
  target_source_id?: string | null
  target_action?: string | null
  strategy_id?: string | null
  strategy_path?: string | null
  blocking_reasons?: string[]
  reason?: string | null
  factors?: string[]
  factor_details?: DataSyncRepairFactorDetail[]
  factor_count?: number | null
  command?: string | null
  dry_run?: boolean
  can_run_from_dashboard?: boolean
  disabled_reason?: string | null
  note?: string | null
  request_params?: DataSyncRepairRequestParams | null
  next_action?: DataSyncRepairNextAction | null
  return_code?: number | null
  status?: string | null
  output_tail?: string[]
}

export interface DataSyncJobResult {
  status?: string | null
  command?: string | null
  commands?: string[]
  step_results?: DataSyncRepairStepResult[]
  finding_steps?: DataSyncRepairStepResult[]
  error_steps?: DataSyncRepairStepResult[]
  findings?: number
  output_tail?: string[]
}

export interface DataSyncRepairStep {
  id: string
  priority: number
  source_id: string
  source_label: string
  target_source_id?: string | null
  target_action?: string | null
  strategy_id: string
  strategy_path?: string | null
  blocking_reasons: string[]
  reason: string
  factors: string[]
  factor_details?: DataSyncRepairFactorDetail[]
  factor_count: number
  command: string
  dry_run: boolean
  can_run_from_dashboard: boolean
  disabled_reason?: string | null
  note?: string | null
}

export interface DataSyncRepairPlan {
  generated_at: string
  as_of_date?: string | null
  blocked_strategies: number
  gated_strategies?: number
  ready_strategies: number
  total_strategies: number
  source_summary?: Record<string, unknown>
  steps: DataSyncRepairStep[]
  warnings: string[]
}

export interface DataSyncSourceCard {
  id: string
  source_id: string
  label: string
  group: string
  source_quality: string
  strict_pit: boolean
  dependencies: string[]
  notes: string[]
  actions: DataSyncAction[]
  status: DataSyncStatus
  latest_date?: string | null
  expected_date?: string | null
  stale_days?: number | null
  coverage?: number | null
  covered_symbols?: number | null
  total_symbols?: number | null
  row_count?: number | null
  warnings: string[]
  details?: Record<string, unknown>
  repair_plan?: DataSyncRepairPlan | null
  last_job?: DataSyncJob | null
}

export interface DataSyncHealth {
  generated_at: string
  cached?: boolean
  cache_age_seconds?: number
  degraded?: boolean
  stale?: boolean
  refreshing?: boolean
  health_mode?: "registry" | string
  refresh_error?: {
    message?: string | null
    at?: string | null
  }
  diagnostics?: {
    mode?: string
    total_ms?: number
    stages?: Array<{
      name: string
      duration_ms: number
      cached?: boolean
      cache_age_seconds?: number
      cache_ttl_seconds?: number
    }>
    slowest_stage?: {
      name: string
      duration_ms: number
    } | null
  }
  summary: {
    sources: number
    ok: number
    warning: number
    blocked: number
    latest_market_date?: string | null
    latest_altdata_date?: string | null
    strategy_blocked?: number | null
    strategy_gated?: number | null
  }
  safe_sync_policy: {
    max_symbols_per_chunk?: number
    request_sleep_min_seconds?: number
    request_sleep_max_seconds?: number
    chunk_pause_seconds?: number
    remote_workers?: string[]
    startup_auto_sync?: boolean
    notes?: string[]
  }
  warnings: string[]
  sources: DataSyncSourceCard[]
  jobs: DataSyncJob[]
}

export interface DataSyncSourcesResponse {
  generated_at: string
  sources: DataSyncSourceDefinition[]
}

export interface DataSyncJobRequest {
  source_id: string
  action: DataSyncActionId | string
  dry_run: boolean
  start_date?: string
  end_date?: string
  symbols?: string
  as_of_date?: string
  strategy_id?: string
  strategy_path?: string
  factor_sources?: string[] | string
  allow_stale_days?: string | number
  min_coverage?: string | number
}
