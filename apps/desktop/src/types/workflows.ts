export type WorkflowStatus = "queued" | "running" | "success" | "failed"
export type WorkflowParamType = "text" | "integer" | "number" | "boolean" | "select" | "symbols"

export interface WorkflowParamOption {
  value: string
  label: string
}

export interface WorkflowParamSpec {
  name: string
  label: string
  type: WorkflowParamType
  default: unknown
  required: boolean
  min: number | null
  max: number | null
  options: WorkflowParamOption[]
  description: string
}

export interface WorkflowDefinition {
  id: string
  title: string
  title_en: string
  description: string
  description_en: string
  category: string
  research_only: boolean
  params: WorkflowParamSpec[]
}

export interface WorkflowDefinitionListResponse {
  workflows: WorkflowDefinition[]
  running: string[]
}

export interface WorkflowRunRecord {
  id: string
  workflow_id: string
  status: WorkflowStatus
  requested_at: string | null
  started_at: string | null
  finished_at: string | null
  params: Record<string, unknown>
  summary: Record<string, unknown>
  artifact_paths: Record<string, unknown>
  warnings: string[]
  error: string | null
}

export interface WorkflowRunListResponse {
  runs: WorkflowRunRecord[]
}

export type WorkflowRealtimeEvent =
  | { event: "started"; run_id: string; workflow_id: string; status: WorkflowStatus }
  | { event: "progress"; run_id: string; workflow_id: string; status: WorkflowStatus; message?: string }
  | { event: "complete"; run_id: string; workflow_id: string; status: WorkflowStatus; summary?: Record<string, unknown>; warnings?: string[] }
  | { event: "failed"; run_id: string; workflow_id: string; status: WorkflowStatus; error?: string }
