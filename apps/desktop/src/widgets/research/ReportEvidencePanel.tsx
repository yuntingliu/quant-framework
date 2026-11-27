import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, CheckCircle2, Database, FileCode2, Link2, ShieldCheck } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { useLanguage } from "@/contexts/LanguageContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { api } from "@/lib/api"
import { reportEvidence, reportReferenceKey, reportReferenceLabel, type ReportEvidenceValidation } from "@/workspace/reportEvidence"
import type { AgentResearchResult } from "@/workspace/researchResults"

export function ReportEvidencePanel({ result }: { result: AgentResearchResult }) {
  const zh = useLanguage().language === "zh"
  const { setActiveMode, setSelectedBacktest, setSelectedStrategy } = useWorkspace()
  const evidence = useMemo(() => reportEvidence(result), [result])
  const referencesKey = JSON.stringify(evidence.references)
  const [validation, setValidation] = useState<ReportEvidenceValidation | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    let active = true
    setValidation(null)
    setError("")
    if (!evidence.projectId || !evidence.references.length) {
      setLoading(false)
      return
    }
    setLoading(true)
    void api.post<ReportEvidenceValidation>("/conexus/reports/validate-references", {
      project_id: evidence.projectId,
      references: evidence.references,
    }).then(value => {
      if (active) setValidation(value)
    }).catch((reason: Error) => {
      if (active) setError(reason.message)
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [result.id, evidence.projectId, referencesKey, evidence.references])

  const references = validation?.references ?? evidence.references
  const openReference = (kind: string, revision?: number, id?: string, factorPath?: string) => {
    if (!evidence.projectId) return
    setSelectedStrategy(evidence.projectId)
    if (kind === "backtest") {
      if (id) setSelectedBacktest(id)
      setActiveMode("validation")
      return
    }
    if (kind === "factor_evidence") {
      setActiveMode("factor")
      sessionStorage.setItem("alphalab:factor-evidence-intent", JSON.stringify({
        projectId: evidence.projectId, evidenceId: id, factorPath,
      }))
      return
    }
    const sourceKind = kind === "strategy_package" ? "strategy" : kind === "validation_package" ? "validation" : "recipe"
    setActiveMode(sourceKind === "strategy" ? "strategy" : sourceKind === "validation" ? "validation" : "data")
    window.dispatchEvent(new CustomEvent("alphalab:openSourceRevision", { detail: { projectId: evidence.projectId, kind: sourceKind } }))
    sessionStorage.setItem("alphalab:source-revision-intent", JSON.stringify({
      projectId: evidence.projectId, kind: sourceKind, revision,
    }))
  }
  return <section className="report-evidence-panel" aria-labelledby="report-evidence-title" aria-busy={loading}>
    <header className="report-evidence-heading">
      <div>
        <span className="report-evidence-eyebrow"><ShieldCheck size={13} />{zh ? "服务器验证" : "Server verified"}</span>
        <h2 id="report-evidence-title">{zh ? "报告证据" : "Report evidence"}</h2>
        <p>{evidence.projectId ?? (zh ? "未关联项目" : "No linked project")} · Document {result.id}</p>
      </div>
      <Badge variant="outline">{validation?.status === "verified" ? (zh ? "已验证" : "Verified") : (zh ? "需核对" : "Review")}</Badge>
    </header>
    {error ? <div className="workbench-message error" role="alert">{error}</div> : null}
    {loading ? <p className="report-evidence-status" role="status">{zh ? "正在验证固定引用…" : "Validating pinned references…"}</p> : null}
    {!evidence.references.length ? <div className="report-evidence-empty"><Link2 size={18} /><strong>{zh ? "未关联结构化证据" : "No structured evidence links"}</strong><p>{zh ? "正文仍保留在 Conexus Document；不会根据正文猜测代码或数据版本。" : "The document remains in Conexus. Code and data versions are never inferred from prose."}</p></div> : null}
    <div className="report-evidence-list">{references.map(reference => {
      const status = reference.status ?? "not_linked"
      return <article key={reportReferenceKey(reference)}>
        <div className="report-evidence-icon">{status === "verified" ? <CheckCircle2 size={15} /> : status === "unavailable" ? <Database size={15} /> : <AlertTriangle size={15} />}</div>
        <div><strong>{reportReferenceLabel(reference, zh)}</strong><p><code>{reference.kind}{reference.id ? ` · ${reference.id}` : ""}</code></p>
          {reference.kind === "data_manifest" && reference.status === "verified" && reference.details?.datasets && <p>
            {reference.details.datasets.join(" · ")} · {reference.details.partition_count} {zh ? "个数据分区" : "data partitions"}
          </p>}
          {reference.issues?.map(issue => <p key={issue.kind}>{zh
            ? `回测冻结的${issue.kind === "strategy_package" ? "策略" : "验证"}版本为 ${issue.actual_revision ? `v${issue.actual_revision}` : "未知"}，请核对报告的版本引用。`
            : issue.message}</p>)}</div>
        <Badge variant="outline">{status === "verified" ? (zh ? "已验证" : "Verified") : status === "mismatch" ? (zh ? "版本不匹配" : "Version mismatch") : status === "missing" ? (zh ? "缺失" : "Missing") : status === "unavailable" ? (zh ? "不可用" : "Unavailable") : (zh ? "未关联" : "Not linked")}</Badge>
        {["strategy_package", "validation_package", "factor_evidence", "recipe_revision", "backtest"].includes(reference.kind) ? <button type="button" className="report-evidence-open" onClick={() => openReference(reference.kind, reference.revision, reference.id, reference.factor_path)}>{zh ? "打开" : "Open"}</button> : null}
      </article>
    })}</div>
    <footer className="report-evidence-footer"><FileCode2 size={13} />{zh ? "引用指向固定版本；查看不会重新运行研究。数据快照未接入时，本报告不是自包含复现包。" : "References point to pinned versions and never trigger research. Without data snapshots, this is not a self-contained reproduction package."}</footer>
  </section>
}
