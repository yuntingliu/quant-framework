import { api } from "./api"
import type { StrategyProject } from "@/contexts/StrategySdkContext"

export interface ResearchFeature { field: string; zh: string; en: string; unit: string; formula: string; paper: string; direction: string }
export interface Paper { id: string; title: string; authors: string; url: string; zh: string; en: string; classification: string }
export interface ResearchResult {
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
export interface ResearchCatalog { features: ResearchFeature[]; papers: Paper[]; data: { status: string; date_start?: string; date_end?: string; rows: number }; coverage: Array<{ field: string; valid: number; total: number }>; runs: ResearchResult[] }
export interface ProjectSpec { project_id: string; function_replacements: Array<{ entrypoint_id: string; function_source: string }>; factors: Array<{ factor_id: string; body: string }>; [key: string]: unknown }
export const researchApi = {
  catalog: () => api.get<ResearchCatalog>("/options/research/catalog", { timeoutMs: 60000 }),
  features: (symbol: string, start: string, end: string) => api.get<{ points: Array<Record<string, number | string | null>> }>(`/options/features?${new URLSearchParams({ underlying: symbol, start, end })}`),
  featurePlan: (body: unknown) => api.post<{ source: string }>("/options/features/plan", body),
  build: (body: unknown) => api.post<{ id: string; status: string }>("/options/features/jobs", body),
  run: (body: unknown) => api.post<ResearchResult>("/options/research/run", body),
  load: (id: string) => api.get<ResearchResult>(`/options/research/runs/${id}`),
  projectPlan: (body: unknown) => api.post<ProjectSpec>("/options/research/projects/plan", body),
  createProject: (body: unknown) => api.post<{ project: StrategyProject }>("/options/research/projects", body),
}
