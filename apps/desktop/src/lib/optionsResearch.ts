import { api } from "./api"
import type { StrategyProject } from "@/contexts/StrategySdkContext"

export interface ResearchFeature { field: string; zh: string; en: string; unit: string; formula: string; paper: string; direction: string }
export interface Paper { id: string; title: string; authors: string; url: string; zh: string; en: string; classification: string }
export interface ResearchResult {
  version?: string; feature_formula_version?: string;
  independent_holdout?: { start: string; end: string; training_label_end: string; scored_rows: number; sessions: number; rank_ic: {mean: number | null; hac_t: number | null}; oos_r2_vs_controls: number | null } | null
  holdout_ablations?: Array<{field: string; rank_ic: {mean: number | null; hac_t: number | null}; oos_r2_vs_controls: number | null; scored_rows: number}>
  id: string; prediction_field: string; config: { fields: string[]; horizon: number; min_train: number; refit_every: number; ridge: number; embargo: number; controls: string[] }
  coverage: { input_rows: number; complete_feature_rows: number; prediction_rows: number; scored_rows: number; unscored_rows: number; oos_sessions: number; symbols: string[] }
  rank_ic: { mean: number | null; hac_t: number | null; observations: number }; oos_r2_vs_controls: number | null
  folds: Array<{ test_start: string; test_end: string; training_label_end: string; training_sessions: number; training_rows: number; test_rows: number }>
  daily_ic: Array<{ date: string; rank_ic: number | null; assets: number }>
  groups: Array<{ field: string; low: number; middle: number; high: number; high_minus_low: { mean: number; hac_t: number | null } }>
  training_inference: Array<{ field: string; coefficient_per_train_sd: number; hac_t: number | null }>
  predictions: Array<{ date: string; symbol: string; prediction: number; target_return: number | null; training_label_end: string }>
  warnings: string[]; label_convention: string; inference: string; trading: string
  scope: { start: string; end: string; underlyings: string[] }
}
export type ResearchSummary = Pick<ResearchResult, "id" | "scope" | "config" | "coverage" | "rank_ic" | "oos_r2_vs_controls">
export interface ResearchCatalog { features: ResearchFeature[]; papers: Paper[]; data: { status: string; date_start?: string; date_end?: string; rows: number }; coverage: Array<{ field: string; valid: number; total: number }>; runs: ResearchSummary[] }
export interface ProjectSpec { project_id: string; function_replacements: Array<{ entrypoint_id: string; function_source: string }>; factors: Array<{ factor_id: string; body: string }>; [key: string]: unknown }
export interface OptionStrategy {
  backtest_id: string; project_id: string; strategy_id: string; project_exists?: boolean; name_zh: string; name_en: string; family: string
  rule: { universe_verified: boolean; weights: Record<string, number>; normalization: string; top_n: number | null; frequency: string; symbols: string[]; execution: string; selection: string; schedule_day: string; decision_time: string; allocation: string; max_weight: number | null }
  period: { start_date: string; end_date: string }; metrics: { total_return: number; annual_return: number; sharpe: number; max_drawdown: number; annual_vol: number; n_periods: number }
  research_valid?: boolean | null; research_invalid_reasons?: string[]; warnings: string[]; execution_reliable: boolean | null; research_id: string | null; revision: number; source_sha256: string; benchmark_symbols: string[]
}
export interface OptionStrategyResult extends OptionStrategy {
  curve: Array<{ date: string; nav: number; benchmark_nav: number | null; drawdown: number }>
  annual: Array<{ year: string; start: string; end: string; return: number; sessions: number }>
  positions: Array<{ date: string; symbol: string; weight: number; name_zh: string; name_en: string }>; positions_date: string | null
  common_period: { start: string | null; end: string | null; sessions: number; total_sessions: number; strategy_return: number | null; benchmark_return: number | null; relative_return: number | null }
  trading: { rebalances: number; successful_trades: number; mean_turnover: number | null; sum_period_cost_rates: number }
  recent_executions: Array<{ decision_date: string; entry_date: string; turnover: number; total_cost: number; successful_trade_count: number }>
  forecast: ResearchSummary | null
}
export interface StrategyComparison {
  period: {start: string; end: string; sessions: number} | null
  rows: Array<{backtest_id: string; name_zh: string; name_en: string; total_return: number; execution_reliable?:boolean|null;execution_warnings?:string[];research_valid?:boolean|null;pool_relative_return: number; index_relative_return: number; market_proxy_attribution: {alpha_annualized: number | null; betas: Record<string,number>; estimates: Record<string,{t_stat: number}>}; attribution_warnings: string[]; index_exposures: Array<{name_zh: string; name_en: string; mean_weight: number}>}>
  benchmarks?: {pool_equal_weight: number; index_equal_weight: number}
  curve: Array<Record<string,string | number>>
}
export interface ControlledStudy {
  id:string;created_at:string;scope:{start:string;end:string;underlyings:string[]}
  config:{fields:string[];min_train:number;refit_every:number;ridge:number;embargo:number;holdout_start:string|null}
  models:number;tests:number;common_rows:number;input_rows:number
  feature_coverage:Array<{field:string;valid:number;total:number}>
  rows:Array<{variant:string;fields:string[];horizon:number;run_id:string;rank_ic:ResearchResult["rank_ic"];
    oos_r2_vs_controls:number|null;coverage:ResearchResult["coverage"];independent_holdout:ResearchResult["independent_holdout"];
    loss_test:{mean:number|null;hac_t:number|null;p_bonferroni:number|null};verdict:string}>
}
export interface RotationPairResult {
  pair:{suite_id:string;horizon:number;start:string;end:string;rule:{top_n:number;frequency:string;execution:string}}|null
  jobs?:Array<{role:"model"|"controls";status:string;backtest_id?:string|null;error_summary?:string|null}>
  comparison?:StrategyComparison|null;holdout_comparison?:StrategyComparison|null
}
export const researchApi = {
  rotation:(id:string,horizon:number)=>api.get<RotationPairResult>(`/options/research/suites/${id}/rotation?horizon=${horizon}`,{timeoutMs:120000}),
  runRotation:(id:string,horizon:number)=>api.post<RotationPairResult>(`/options/research/suites/${id}/rotation`,{horizon,confirm_python_execution:true},{timeoutMs:120000}),
  suites:()=>api.get<{suites:Array<Pick<ControlledStudy,"id"|"scope"|"config"|"created_at"|"models"|"tests">>}>("/options/research/suites"),
  suite:(id:string)=>api.get<ControlledStudy>(`/options/research/suites/${id}`),
  runSuite:(body:unknown)=>api.post<ControlledStudy>("/options/research/suites/run",body,{timeoutMs:300000}),
  comparison: (ids: string[]) => api.get<StrategyComparison>(`/options/research/comparison?${new URLSearchParams({ids:ids.join(",")})}`,{timeoutMs:120000}),
  strategies: () => api.get<{ strategies: OptionStrategy[] }>("/options/research/strategies"),
  strategy: (id: string) => api.get<OptionStrategyResult>(`/options/research/strategies/${id}`),
  catalog: () => api.get<ResearchCatalog>("/options/research/catalog", { timeoutMs: 60000 }),
  features: (symbol: string, start: string, end: string) => api.get<{ points: Array<Record<string, number | string | null>> }>(`/options/features?${new URLSearchParams({ underlying: symbol, start, end })}`),
  featurePlan: (body: unknown) => api.post<{ source: string }>("/options/features/plan", body),
  build: (body: unknown) => api.post<{ id: string; status: string }>("/options/features/jobs", body),
  run: (body: unknown) => api.post<ResearchResult>("/options/research/run", body),
  load: (id: string) => api.get<ResearchResult>(`/options/research/runs/${id}`),
  projectPlan: (body: unknown) => api.post<ProjectSpec>("/options/research/projects/plan", body),
  createProject: (body: unknown) => api.post<{ project: StrategyProject }>("/options/research/projects", body),
}
