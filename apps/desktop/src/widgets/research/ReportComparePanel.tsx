import { useEffect, useMemo, useState } from "react"
import { ArrowLeftRight, Info } from "lucide-react"

import { SafeMarkdown } from "@/components/shared/SafeMarkdown"
import { useLanguage } from "@/contexts/LanguageContext"
import { api } from "@/lib/api"
import { reportEvidence, reportReferenceKey, reportReferenceLabel, reportReferenceSignature, type ReportEvidenceValidation } from "@/workspace/reportEvidence"
import type { AgentResearchResult } from "@/workspace/researchResults"

export function ReportComparePanel({ reports, initialLeft, initialRight }: { reports: AgentResearchResult[]; initialLeft?: string; initialRight?: string }) {
  const zh = useLanguage().language === "zh"
  const [leftId, setLeftId] = useState(initialLeft ?? reports[0]?.id)
  const [rightId, setRightId] = useState(initialRight ?? reports[1]?.id ?? reports[0]?.id)
  const [validations, setValidations] = useState<Record<string, ReportEvidenceValidation>>({})
  const [validationState, setValidationState] = useState<"loading" | "ready" | "failed">("loading")
  const left = reports.find(item => item.id === leftId)
  const right = reports.find(item => item.id === rightId)
  useEffect(() => {
    if (!left || !right) return
    let active = true
    setValidationState("loading")
    void Promise.all([left, right].map(async result => {
      const evidence = reportEvidence(result)
      if (!evidence.projectId || !evidence.references.length) return [result.id, null] as const
      const validation = await api.post<ReportEvidenceValidation>("/conexus/reports/validate-references", {
        project_id: evidence.projectId,
        references: evidence.references,
      }).catch(() => null)
      return [result.id, validation, validation === null && evidence.references.length > 0] as const
    })).then(values => {
      if (!active) return
      const next: Record<string, ReportEvidenceValidation> = {}
      values.forEach(([id, value]) => { if (value) next[id] = value })
      setValidations(next)
      setValidationState(values.some(([, , failed]) => failed) ? "failed" : "ready")
    })
    return () => { active = false }
  }, [left, right])
  useEffect(() => {
    if (!reports.length) return
    const nextLeft = reports.some(item => item.id === leftId) ? leftId : reports[0].id
    const nextRight = reports.some(item => item.id === rightId && item.id !== nextLeft) ? rightId : reports.find(item => item.id !== nextLeft)?.id
    setLeftId(nextLeft)
    if (nextRight) setRightId(nextRight)
  }, [reports, leftId, rightId])

  const comparability = useMemo(() => {
    if (!left || !right) return ""
    const leftProject = reportEvidence(left).projectId
    const rightProject = reportEvidence(right).projectId
    if (leftProject !== rightProject) return zh ? "项目不同；结果不具直接可比性。" : "Different projects; results are not directly comparable."
    if (validationState === "loading") return zh ? "正在验证双方固定引用…" : "Validating pinned references…"
    if (validationState === "failed") return zh ? "引用验证失败；不能直接比较结论。" : "Reference validation failed; conclusions are not directly comparable."
    const leftReferences = validations[left.id]?.references ?? []
    const rightReferences = validations[right.id]?.references ?? []
    if (!leftReferences.length || !rightReferences.length) return zh ? "一方或双方没有已验证的结构化证据；不能直接比较结论。" : "Verified structured evidence is missing on one or both sides; conclusions are not directly comparable."
    if ([...leftReferences, ...rightReferences].some(reference => reference.status !== "verified")) {
      return zh ? "存在版本不匹配、缺失或不可用引用；请先核对证据。" : "Some references conflict, are missing, or are unavailable. Check the evidence first."
    }
    if (reportReferenceSignature(leftReferences, "code") !== reportReferenceSignature(rightReferences, "code")) {
      return zh ? "引用的源码版本不同，请分别解读结果。" : "Referenced source versions differ. Interpret results separately."
    }
    if (reportReferenceSignature(leftReferences, "sample") !== reportReferenceSignature(rightReferences, "sample")) {
      return zh ? "样本股票池或日期不同，请分别解读结果。" : "Universe or sample dates differ. Interpret results separately."
    }
    if (reportReferenceSignature(leftReferences, "settings") !== reportReferenceSignature(rightReferences, "settings")) {
      return zh ? "回测成本或执行设置不同，请分别解读结果。" : "Costs or execution settings differ. Interpret results separately."
    }
    const manifests = (refs: typeof leftReferences) => Array.from(new Set(refs
      .filter(ref => ref.kind === "data_manifest" && ref.status === "verified").map(ref => ref.id))).sort()
    const leftManifests = manifests(leftReferences)
    const rightManifests = manifests(rightReferences)
    if (!leftManifests.length || !rightManifests.length) {
      return zh ? "一方或双方缺少已验证的数据清单。" : "One or both reports lack a verified data manifest."
    }
    if (JSON.stringify(leftManifests) !== JSON.stringify(rightManifests)) {
      return zh ? "数据快照不同，请分别解读结果。" : "Data snapshots differ. Interpret results separately."
    }
    return zh ? "已核对双方的代码版本、样本、设置和数据快照。" : "Recorded code versions, samples, settings, and snapshots match."
  }, [left, right, validations, validationState, zh])

  function downloadComparison() {
    const payload = {
      left: { report: left!, validation: validations[left!.id] ?? null },
      right: { report: right!, validation: validations[right!.id] ?? null },
      comparability,
      validation_state: validationState,
      self_contained_reproduction: false,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = "report-comparison.json"
    anchor.click()
    URL.revokeObjectURL(url)
  }
  if (!left || !right || left.id === right.id) return <div className="analytics-empty">{zh ? "至少需要两份不同的报告才能比较。" : "Two different reports are required for comparison."}</div>
  return <section className="report-compare-panel" aria-labelledby="report-compare-title">
    <div className="report-compare-controls">
      <label>{zh ? "原报告" : "Original"}<select value={leftId} onChange={event => setLeftId(event.target.value)}>{reports.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      <ArrowLeftRight size={16} />
      <label>{zh ? "目标报告" : "Target"}<select value={rightId} onChange={event => setRightId(event.target.value)}>{reports.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      <button type="button" className="report-evidence-open" onClick={downloadComparison}>{zh ? "导出比较" : "Export comparison"}</button>
    </div>
    <div className="report-compare-note"><Info size={14} /><span>{comparability}</span></div>
    <div className="report-compare-grid">
      {[left, right].map((result, index) => <article key={`${result.id}:${index}`}>
        <header><span>{index === 0 ? (zh ? "原报告" : "Original") : (zh ? "目标报告" : "Target")}</span><strong>{result.title}</strong><code>{result.id}</code></header>
        <div className="report-compare-references">{(validations[result.id]?.references ?? reportEvidence(result).references).map(reference => <span key={reportReferenceKey(reference)}>{reportReferenceLabel(reference, zh)}{reference.details?.sample?.universe ? ` · ${reference.details.sample.universe}` : ""}{reference.details?.sample?.start_date ? ` · ${reference.details.sample.start_date} → ${reference.details.sample.end_date}` : ""}{reference.details?.strategy_revision ? ` · 策略 v${reference.details.strategy_revision} / 验证 v${reference.details.validation_revision}` : ""}{reference.details?.summary?.mean_rank_ic != null ? ` · IC ${reference.details.summary.mean_rank_ic}` : ""}{reference.status ? ` · ${reference.status}` : ""}</span>)}</div>
        <div className="report-compare-document"><SafeMarkdown>{result.markdown}</SafeMarkdown></div>
      </article>)}
    </div>
  </section>
}
