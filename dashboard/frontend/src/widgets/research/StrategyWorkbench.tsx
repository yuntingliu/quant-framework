import { useEffect, useState } from "react"
import { CheckCircle2, Copy, FileCode2, Save, Trash2 } from "lucide-react"

import { useLanguage } from "../../contexts/LanguageContext"
import {
  api,
  type StrategyTemplate,
  type StrategyTemplateDetail,
} from "../../lib/api"
import { useWorkspaceRefresh } from "../../hooks/useWorkspaceRefresh"
import { useWorkspace } from "../../contexts/WorkspaceContext"

export function StrategyWorkbenchWidget() {
  const { language } = useLanguage()
  const copy = language === "zh" ? {
    title: "策略工作台",
    description: "在同一处查看、克隆、校验和保存完整策略定义。",
    factors: "个因子",
    template: "模板",
    local: "本地",
    selectStrategy: "请选择策略",
    readOnly: "只读",
    editable: "可编辑",
    yaml: "策略 YAML",
    validate: "校验",
    save: "保存",
    localId: "本地策略 ID",
    clone: "克隆策略",
    remove: "删除本地策略",
    confirmRemove: "确定删除本地策略",
  } : {
    title: "Strategy Workbench",
    description: "Review, clone, validate, and save complete strategy definitions in one place.",
    factors: "factors",
    template: "template",
    local: "local",
    selectStrategy: "Select a strategy",
    readOnly: "read only",
    editable: "editable",
    yaml: "Strategy YAML",
    validate: "Validate",
    save: "Save",
    localId: "Local strategy id",
    clone: "Clone strategy",
    remove: "Delete local strategy",
    confirmRemove: "Delete local strategy",
  }
  const refreshRevision = useWorkspaceRefresh()
  const { selectedStrategy, setSelectedStrategy } = useWorkspace()
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [strategyId, setStrategyId] = useState("")
  const [detail, setDetail] = useState<StrategyTemplateDetail | null>(null)
  const [yaml, setYaml] = useState("")
  const [cloneId, setCloneId] = useState("")
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  async function loadStrategies(preferred?: string) {
    const items = await api.get<StrategyTemplate[]>("/strategies")
    setStrategies(items)
    const next = preferred && items.some((item) => item.id === preferred)
      ? preferred
      : strategyId && items.some((item) => item.id === strategyId)
        ? strategyId
        : items[0]?.id ?? ""
    setStrategyId(next)
    setSelectedStrategy(next || null)
  }

  useEffect(() => {
    api.get<StrategyTemplate[]>("/strategies")
      .then((items) => {
        setStrategies(items)
        const selected = items[0]?.id ?? ""
        setStrategyId(selected)
        setSelectedStrategy(selected || null)
      })
      .catch((loadError: Error) => setError(loadError.message))
  }, [refreshRevision, setSelectedStrategy])

  useEffect(() => {
    if (!selectedStrategy || selectedStrategy === strategyId) return
    if (strategies.some((item) => item.id === selectedStrategy)) setStrategyId(selectedStrategy)
  }, [selectedStrategy, strategies, strategyId])

  useEffect(() => {
    if (!strategyId) return
    setError("")
    api.get<StrategyTemplateDetail>(`/strategies/${strategyId}`)
      .then((item) => {
        setDetail(item)
        setYaml(item.yaml)
        setCloneId(`${item.id}_local`)
      })
      .catch((loadError: Error) => setError(loadError.message))
  }, [strategyId])

  async function validate() {
    setBusy(true)
    setError("")
    try {
      const result = await api.post<{
        valid: boolean
        warnings: string[]
        normalized_yaml: string
      }>("/strategies/validate", { yaml })
      setMessage(result.valid
        ? result.warnings.join("; ") || "YAML valid"
        : result.warnings.join("; "))
    } catch (validationError) {
      setError(validationError instanceof Error ? validationError.message : String(validationError))
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    if (!detail?.editable) return
    setBusy(true)
    setError("")
    try {
      const saved = await api.put<StrategyTemplateDetail>(`/strategies/${detail.id}`, { yaml })
      setDetail(saved)
      setYaml(saved.yaml)
      setMessage("Local strategy saved")
      await loadStrategies(saved.id)
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError))
    } finally {
      setBusy(false)
    }
  }

  async function clone() {
    setBusy(true)
    setError("")
    try {
      const saved = await api.post<StrategyTemplateDetail>(
        `/strategies/${strategyId}/clone`,
        { target_id: cloneId },
      )
      setMessage("Local strategy created")
      await loadStrategies(saved.id)
    } catch (cloneError) {
      setError(cloneError instanceof Error ? cloneError.message : String(cloneError))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!detail?.editable || !window.confirm(`${copy.confirmRemove} ${detail.id}?`)) return
    setBusy(true)
    setError("")
    try {
      await api.delete<void>(`/strategies/${detail.id}`)
      setMessage("Local strategy deleted")
      setDetail(null)
      setYaml("")
      await loadStrategies()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : String(deleteError))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
        <FileCode2 size={18} />
      </div>
      {error && <p className="error">{error}</p>}
      {message && <p className="success-message">{message}</p>}
      <div className="editor-layout">
        <aside className="editor-list">
          {strategies.map((strategy) => (
            <button
              key={strategy.id}
              type="button"
              className={strategy.id === strategyId ? "active" : ""}
              onClick={() => {
                setStrategyId(strategy.id)
                setSelectedStrategy(strategy.id)
              }}
            >
              <strong>{strategy.name}</strong>
              <span>
                {strategy.factors.length} {copy.factors} · {strategy.built_in ? copy.template : copy.local}
              </span>
            </button>
          ))}
        </aside>
        <section className="editor-main">
          <div className="detail-strip editor-detail-strip">
            <span>{detail?.description ?? copy.selectStrategy}</span>
            <strong>{detail?.built_in ? copy.readOnly : copy.editable}</strong>
          </div>
          <textarea
            className="code-view code-editor"
            aria-label={copy.yaml}
            value={yaml}
            readOnly={!detail?.editable}
            spellCheck={false}
            onChange={(event) => setYaml(event.target.value)}
          />
          <div className="editor-actions">
            <button type="button" className="icon-text-command" onClick={validate} disabled={busy || !yaml}>
              <CheckCircle2 aria-hidden="true" />
              {copy.validate}
            </button>
            <button type="button" className="icon-text-command primary" onClick={save} disabled={busy || !detail?.editable}>
              <Save aria-hidden="true" />
              {copy.save}
            </button>
            <input
              aria-label={copy.localId}
              value={cloneId}
              onChange={(event) => setCloneId(event.target.value)}
            />
            <button type="button" className="icon-command" title={copy.clone} onClick={clone} disabled={busy || !strategyId || !cloneId}>
              <Copy aria-hidden="true" />
            </button>
            <button type="button" className="icon-command danger" title={copy.remove} onClick={remove} disabled={busy || !detail?.editable}>
              <Trash2 aria-hidden="true" />
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}
