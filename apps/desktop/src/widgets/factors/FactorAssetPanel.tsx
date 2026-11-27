import { useCallback, useEffect, useRef, useState } from "react"
import { Check, Copy, Database, FileClock, GitBranch, Info, RefreshCw, ShieldCheck } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useConfirm } from "@/hooks/useConfirm"
import { api } from "@/lib/api"

export interface FactorAsset {
  id: string
  project_id: string
  path: string
  factor_id: string
  current_revision_id: number | null
  package_revision: number | null
  status: "candidate" | "observation" | "adopted" | "retired"
  status_revision: number
  copied_from_asset_id: string | null
  copied_from_revision_id: number | null
}
export interface FactorEvidence {
  id: string
  factor_revision_id: number
  factor_unit_sha256: string
  sample: { universe?: string; start_date?: string; end_date?: string }
  assumptions: Record<string, unknown>
  request?: { parameters?: Record<string, unknown> }
  data_manifest: { status: string; reason?: string }
  result: { status: string; summary?: { mean_rank_ic?: number | null; observations?: number } }
  created_at: string
}
interface AssetDetail {
  asset: FactorAsset
  transitions: Array<{ id: string; from_status: string; to_status: string; reason: string; origin: string; created_at: string }>
  usage: Array<{ strategy_id: string; name: string; assembly_scope: boolean; usage: string }>
  data_requirements: Record<string, unknown> | null
  data_requirements_scope: string | null
}
interface Props {
  projectId: string
  factorId: string
  assets: FactorAsset[]
  editable: boolean
  blocked: boolean
  evidenceVersion: number
  reloadAssets: () => Promise<void>
  onCopied: (factorId: string) => Promise<void>
}

const statusLabels = { candidate: "候选", observation: "观察", adopted: "采用", retired: "退役" } as const
const nextStatus = {
  candidate: ["observation", "retired"],
  observation: ["candidate", "adopted", "retired"],
  adopted: ["observation", "retired"],
  retired: ["candidate"],
} as const

export function FactorAssetPanel({ projectId, factorId, assets, editable, blocked, evidenceVersion, reloadAssets, onCopied }: Props) {
  const confirm = useConfirm()
  const asset = assets.find(item => item.factor_id === factorId && item.current_revision_id !== null)
  const [detail, setDetail] = useState<AssetDetail | null>(null)
  const [evidence, setEvidence] = useState<FactorEvidence[]>([])
  const [selectedEvidence, setSelectedEvidence] = useState<string[]>([])
  const [reason, setReason] = useState("")
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [focusedEvidence, setFocusedEvidence] = useState<FactorEvidence | null>(null)
  const loadSequence = useRef(0)
  const transitionRequest = useRef<{ key: string; id: string } | null>(null)
  const displayAsset = (asset && detail?.asset.id === asset.id ? detail.asset : asset) as FactorAsset

  const reload = useCallback(async () => {
    const sequence = ++loadSequence.current
    if (!asset) {
      setDetail(null)
      setEvidence([])
      return
    }
    setLoading(true)
    setError("")
    try {
      const [nextDetail, page] = await Promise.all([
        api.get<AssetDetail>(`/strategy/projects/${projectId}/factor-assets/${asset.id}`),
        api.get<{ items: FactorEvidence[] }>(`/strategy/projects/${projectId}/factor-assets/${asset.id}/evidence?limit=20`),
      ])
      if (sequence !== loadSequence.current) return
      setDetail(nextDetail)
      setEvidence(page.items)
      setSelectedEvidence(page.items.filter(item => item.factor_revision_id === nextDetail.asset.current_revision_id).map(item => item.id))
    } catch (reason) {
      if (sequence === loadSequence.current) setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (sequence === loadSequence.current) setLoading(false)
    }
  }, [asset, projectId])

  useEffect(() => {
    loadSequence.current += 1
    setDetail(null)
    setEvidence([])
    setError("")
    setFocusedEvidence(null)
    void reload()
  }, [reload, evidenceVersion, asset?.id])
  useEffect(() => {
    const stored = sessionStorage.getItem("alphalab:factor-evidence-intent")
    if (!stored) return
    const detail = JSON.parse(stored) as { projectId: string; evidenceId: string; factorPath?: string }
    if (detail.projectId !== projectId || !detail.evidenceId || !asset) return
    if (detail.factorPath && detail.factorPath !== asset.path) return
    void api.get<FactorEvidence>(`/strategy/projects/${projectId}/factor-evidence/${detail.evidenceId}`)
      .then(value => {
        setFocusedEvidence(value)
        sessionStorage.removeItem("alphalab:factor-evidence-intent")
      })
      .catch((reason: Error) => setError(reason.message))
  }, [projectId, asset])

  async function copyAsset() {
    if (!asset || blocked) return
    const base = `${factorId}_copy`
    const used = new Set(assets.map(item => item.factor_id))
    let candidate = base
    let suffix = 2
    while (used.has(candidate)) candidate = `${base}_${suffix++}`
    if (!await confirm({
      title: `复制因子 ${factorId}`,
      description: "创建同名显示标签的新资产；状态和研究证据不会复制。",
      confirmText: "创建独立副本",
    })) return
    setBusy(true)
    setError("")
    try {
      const project = await api.get<{ draft_source_sha256: string }>(`/strategy/projects/${projectId}`)
      await api.post(`/strategy/projects/${projectId}/factors/${factorId}/copy`, {
        new_factor_id: candidate,
        expected_source_sha256: project.draft_source_sha256,
        confirm_write: true,
        confirm_python_execution: true,
      })
      await onCopied(candidate)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function transition(toStatus: FactorAsset["status"]) {
    if (!displayAsset || blocked) return
    if (["observation", "adopted"].includes(toStatus) && !selectedEvidence.length) {
      setError("观察或采用前，请选择当前因子修订的研究证据。")
      return
    }
    if (!reason.trim()) {
      setError("请填写这次研究决策的原因。")
      return
    }
    if (!await confirm({
      title: `将 ${factorId} 标记为${statusLabels[toStatus]}`,
      description: "状态只记录研究决策，不改变策略，也不代表投资有效。",
      body: <div className="space-y-1"><p>{reason}</p><p>关联 {selectedEvidence.length} 项当前版本证据。</p></div>,
      confirmText: "保存研究状态",
    })) return
    setBusy(true)
    setError("")
    try {
      const requestKey = JSON.stringify([displayAsset.id, toStatus, reason.trim(), selectedEvidence])
      if (transitionRequest.current?.key !== requestKey) {
        transitionRequest.current = { key: requestKey, id: crypto.randomUUID() }
      }
      await api.post(`/strategy/projects/${projectId}/factor-assets/${displayAsset.id}/transitions`, {
        to_status: toStatus,
        reason: reason.trim(),
        expected_status_revision: displayAsset.status_revision,
        evidence_ids: ["observation", "adopted"].includes(toStatus) ? selectedEvidence : [],
        request_id: transitionRequest.current.id,
        confirm_write: true,
      })
      transitionRequest.current = null
      setReason("")
      await Promise.all([reloadAssets(), reload()])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  if (!asset) return <section className="factor-asset-panel"><div className="analytics-empty">当前因子没有可用的资产身份。</div></section>
  const currentEvidence = evidence.filter(item => item.factor_revision_id === displayAsset.current_revision_id)
  return <section className="factor-asset-panel" aria-labelledby="factor-asset-title" aria-busy={loading || busy}>
    <header className="factor-asset-heading">
      <div>
        <span className="factor-asset-eyebrow"><ShieldCheck size={13} />研究资产</span>
        <h3 id="factor-asset-title">{factorId}<Badge variant="outline">{statusLabels[displayAsset.status]}</Badge></h3>
        <p>因子修订 r{displayAsset.current_revision_id} · 代码包 v{displayAsset.package_revision ?? "—"}</p>
      </div>
      <div className="factor-asset-actions">
        <Button size="sm" variant="outline" disabled={loading || busy} onClick={() => void reload()}><RefreshCw />刷新</Button>
        <Button size="sm" variant="outline" disabled={!editable || blocked || busy} onClick={() => void copyAsset()}><Copy />复制资产</Button>
      </div>
    </header>
    {error ? <div className="workbench-message error" role="alert">{error}</div> : null}
    <div className="factor-asset-grid">
      <section>
        <header className="factor-asset-section-title"><GitBranch size={14} /><strong>身份与使用关系</strong></header>
        <dl className="factor-asset-meta">
          <div><dt>稳定资产 ID</dt><dd><code>{displayAsset.id}</code></dd></div>
          <div><dt>复制来源</dt><dd>{displayAsset.copied_from_asset_id ? <code>{displayAsset.copied_from_asset_id} · r{displayAsset.copied_from_revision_id}</code> : "无记录"}</dd></div>
          <div><dt>数据要求</dt><dd>{detail?.data_requirements_scope === "strategy_package" ? "当前策略包范围" : "不可用"}</dd></div>
        </dl>
        <div className="factor-asset-usage">{detail?.usage.map(item => <div key={item.strategy_id}><span>{item.name}<code>{item.strategy_id}</code></span><Badge variant="outline">{item.usage === "signal_direct" ? "信号直接使用" : item.usage === "signal_static_reference" ? "代码静态引用" : item.usage === "assembly_scope" ? "组装范围" : "动态/潜在"}</Badge></div>)}</div>
        <p className="factor-asset-hint">组装范围不等于信号实际使用；动态调用保守标为未知或潜在。</p>
      </section>
      <section>
        <header className="factor-asset-section-title"><Database size={14} /><strong>当前版本证据</strong><Badge variant="outline">{currentEvidence.length}</Badge></header>
        {currentEvidence.length ? <div className="factor-evidence-list">{currentEvidence.map(item => <label key={item.id}>
          <input type="checkbox" checked={selectedEvidence.includes(item.id)} onChange={event => setSelectedEvidence(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} />
          <span><strong>{item.result.status} · Rank IC {item.result.summary?.mean_rank_ic ?? "—"}</strong><small>{item.sample.start_date} → {item.sample.end_date} · {item.sample.universe}</small><code>r{item.factor_revision_id} · {item.id.slice(0, 8)}</code></span>
        </label>)}</div> : <div className="analytics-empty">当前修订尚未评估。旧版本证据不会自动转移。</div>}
        <p className="factor-asset-hint"><Info size={12} />数据清单不可用：#15 尚未实现；不会用当前数据冒充历史快照。</p>
        <details className="factor-evidence-history"><summary><FileClock size={13} />最近历史证据（{evidence.length}）</summary><div>{evidence.filter(item => item.factor_revision_id !== displayAsset.current_revision_id).map(item => <p key={item.id}><code>r{item.factor_revision_id}</code> {item.result.status} · Rank IC {item.result.summary?.mean_rank_ic ?? "—"} · {item.sample.start_date} → {item.sample.end_date} · {String(item.assumptions.forward_return ?? "假设未记录")}</p>)}</div></details>
      </section>
    </div>
    <section className="factor-status-editor">
      <header className="factor-asset-section-title"><Check size={14} /><strong>研究决策</strong><small>需要原因；观察/采用需要当前版本证据</small></header>
      <div className="factor-status-controls">
        <input aria-label="状态转换原因" value={reason} onChange={event => setReason(event.target.value)} placeholder="记录样本、假设和判断依据" disabled={!editable || blocked || busy || loading} />
        <div>{nextStatus[displayAsset.status].map(status => <Button key={status} size="sm" variant="outline" disabled={!editable || blocked || busy || loading} onClick={() => void transition(status)}>标记为{statusLabels[status]}</Button>)}</div>
      </div>
    </section>
    {focusedEvidence ? <section className="factor-focused-evidence" aria-label="选定历史证据">
      <header><strong>选定证据 r{focusedEvidence.factor_revision_id}</strong><button type="button" onClick={() => setFocusedEvidence(null)}>关闭</button></header>
      <p>{focusedEvidence.sample.start_date} → {focusedEvidence.sample.end_date} · {focusedEvidence.sample.universe}</p>
      <p>参数：{JSON.stringify(focusedEvidence.request?.parameters ?? {})} · 假设：{JSON.stringify(focusedEvidence.assumptions)}</p>
      <p>状态：{focusedEvidence.result.status} · Rank IC {focusedEvidence.result.summary?.mean_rank_ic ?? "—"}</p>
    </section> : null}
    {detail?.transitions.length ? <details className="factor-status-history"><summary>状态历史（{detail.transitions.length}）</summary><ol>{detail.transitions.map(item => <li key={item.id}><span>{statusLabels[item.from_status as FactorAsset["status"]]} → {statusLabels[item.to_status as FactorAsset["status"]]}</span><p>{item.reason}</p><small>{item.origin === "system_definition_reset" ? "定义变化自动重置" : "手动研究决策"} · {item.created_at}</small></li>)}</ol></details> : null}
  </section>
}
