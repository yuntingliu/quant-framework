import type { AgentResearchResult } from "./researchResults"

export type ReportReferenceKind = "strategy_package" | "validation_package" | "factor_evidence" | "recipe_revision" | "backtest" | "data_manifest" | "experiment" | "trial"
export interface ReportReference {
  kind: ReportReferenceKind
  id?: string
  revision?: number
  status?: "verified" | "missing" | "unavailable" | "not_linked" | "mismatch"
  label?: string
  source_sha256?: string
  factor_status?: string
  factor_path?: string
  reason?: string
  issues?: { kind: string; actual_revision: number | null; message: string }[]
  details?: {
    datasets?: string[]
    captured_at?: string
    partition_count?: number
    backtest_id?: string
    sample?: { universe?: string; start_date?: string; end_date?: string }
    summary?: { mean_rank_ic?: number | null }
    strategy_revision?: number
    validation_revision?: number
    strategy_source_sha256?: string
    validation_source_sha256?: string
    config_yaml?: string
  }
}
export interface ReportEvidence {
  projectId?: string
  references: ReportReference[]
}
export interface ReportEvidenceValidation {
  project_id: string
  status: "verified" | "incomplete"
  references: ReportReference[]
}

const kinds: Record<ReportReferenceKind, [string, string]> = {
  strategy_package: ["策略包", "Strategy package"],
  validation_package: ["验证包", "Validation package"],
  factor_evidence: ["因子证据", "Factor evidence"],
  recipe_revision: ["配方修订", "Recipe revision"],
  backtest: ["回测", "Backtest"],
  data_manifest: ["数据清单", "Data manifest"],
  experiment: ["实验", "Experiment"],
  trial: ["试验", "Trial"],
}

export function reportEvidence(result: AgentResearchResult): ReportEvidence {
  const provenance = result.provenance ?? {}
  const references = Array.isArray(provenance.references)
    ? (provenance.references as ReportReference[]).map(item => ({ kind: item.kind, id: item.id, revision: item.revision, factor_status: item.factor_status, factor_path: item.factor_path }))
    : []
  const explicitProject = provenance.projectId ?? provenance.project_id
  return {
    projectId: typeof explicitProject === "string" ? explicitProject : undefined,
    references: references.filter(item => item && typeof item.kind === "string"),
  }
}

export function reportReferenceLabel(reference: ReportReference, zh: boolean): string {
  if (reference.label) return reference.label
  const [zhLabel, enLabel] = kinds[reference.kind] ?? [reference.kind, reference.kind]
  const suffix = reference.revision ? ` ${reference.kind === "factor_evidence" ? "r" : "v"}${reference.revision}` : reference.id ? ` ${reference.id}` : ""
  return `${zh ? zhLabel : enLabel}${suffix}`
}

export function reportReferenceKey(reference: ReportReference): string {
  return `${reference.kind}:${reference.id ?? ""}:${reference.revision ?? ""}`
}

export function reportFilterStatus(result: AgentResearchResult): "linked" | "unlinked" | "manifest_unavailable" {
  const { references } = reportEvidence(result)
  if (references.some(item => item.kind === "data_manifest" && !item.id)) return "manifest_unavailable"
  return references.length ? "linked" : "unlinked"
}

export function reportReferenceSignature(
  references: ReportReference[],
  category: "code" | "sample" | "settings",
): string {
  const values = references.flatMap(reference => {
    if (category === "sample") {
      const sample = reference.details?.sample
      return sample ? [[sample.universe, sample.start_date, sample.end_date]] : []
    }
    if (category === "settings") {
      return reference.kind === "backtest" ? [[reference.details?.config_yaml]] : []
    }
    if (reference.kind === "backtest") {
      return [
        ["run_strategy", reference.details?.strategy_revision, reference.details?.strategy_source_sha256],
        ["run_validation", reference.details?.validation_revision, reference.details?.validation_source_sha256],
      ]
    }
    return ["strategy_package", "validation_package", "factor_evidence", "recipe_revision"].includes(reference.kind)
      ? [[reference.kind, reference.revision, reference.source_sha256]] : []
  })
  return JSON.stringify([...new Set(values.map(value => JSON.stringify(value)))].sort())
}
