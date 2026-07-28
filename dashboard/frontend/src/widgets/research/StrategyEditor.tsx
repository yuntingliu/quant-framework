import { useEffect, useRef, useState } from "react"
import { CheckCircle2, Copy, Download, FileCode2, Save, Trash2, Upload } from "lucide-react"

import {
  api,
  type StrategyTemplate,
  type StrategyTemplateDetail,
} from "../../lib/api"

export function StrategyEditorWidget() {
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [strategyId, setStrategyId] = useState("")
  const [detail, setDetail] = useState<StrategyTemplateDetail | null>(null)
  const [yaml, setYaml] = useState("")
  const [cloneId, setCloneId] = useState("")
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const importInput = useRef<HTMLInputElement>(null)

  async function loadStrategies(preferred?: string) {
    const items = await api.get<StrategyTemplate[]>("/strategies")
    setStrategies(items)
    const next = preferred && items.some((item) => item.id === preferred)
      ? preferred
      : strategyId && items.some((item) => item.id === strategyId)
        ? strategyId
        : items[0]?.id ?? ""
    setStrategyId(next)
  }

  useEffect(() => {
    api.get<StrategyTemplate[]>("/strategies")
      .then((items) => {
        setStrategies(items)
        setStrategyId(items[0]?.id ?? "")
      })
      .catch((loadError: Error) => setError(loadError.message))
  }, [])

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
    if (!detail?.editable || !window.confirm(`Delete local strategy ${detail.id}?`)) return
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

  async function importYaml(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setError("")
    try {
      const imported = await api.post<StrategyTemplateDetail>("/strategies/import", {
        yaml: await file.text(),
        overwrite: false,
      })
      setMessage("Local strategy imported")
      await loadStrategies(imported.id)
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : String(importError))
    } finally {
      setBusy(false)
      if (importInput.current) importInput.current.value = ""
    }
  }

  async function exportYaml() {
    if (!detail?.editable) return
    setBusy(true)
    setError("")
    try {
      const content = await api.getText(`/strategies/${detail.id}/export`)
      const url = URL.createObjectURL(new Blob([content], { type: "application/yaml" }))
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = `${detail.id}.yaml`
      anchor.click()
      URL.revokeObjectURL(url)
      setMessage("Local strategy exported")
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : String(exportError))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Strategy Editor</h2>
          <p>Built-in templates remain immutable; local copies stay under runtime data.</p>
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
              onClick={() => setStrategyId(strategy.id)}
            >
              <strong>{strategy.name}</strong>
              <span>
                {strategy.factors.length} factors · {strategy.built_in ? "template" : "local"}
              </span>
            </button>
          ))}
        </aside>
        <section className="editor-main">
          <div className="detail-strip editor-detail-strip">
            <span>{detail?.description ?? "Select a strategy"}</span>
            <strong>{detail?.built_in ? "read only" : "editable"}</strong>
          </div>
          <textarea
            className="code-view code-editor"
            aria-label="Strategy YAML"
            value={yaml}
            readOnly={!detail?.editable}
            spellCheck={false}
            onChange={(event) => setYaml(event.target.value)}
          />
          <div className="editor-actions">
            <input
              ref={importInput}
              type="file"
              accept=".yaml,.yml,application/yaml,text/yaml"
              hidden
              onChange={(event) => void importYaml(event.target.files?.[0])}
            />
            <button
              type="button"
              className="icon-command"
              title="Import local strategy"
              onClick={() => importInput.current?.click()}
              disabled={busy}
            >
              <Upload aria-hidden="true" />
            </button>
            <button
              type="button"
              className="icon-command"
              title="Export local strategy"
              onClick={exportYaml}
              disabled={busy || !detail?.editable}
            >
              <Download aria-hidden="true" />
            </button>
            <button type="button" className="icon-text-command" onClick={validate} disabled={busy || !yaml}>
              <CheckCircle2 aria-hidden="true" />
              Validate
            </button>
            <button type="button" className="icon-text-command primary" onClick={save} disabled={busy || !detail?.editable}>
              <Save aria-hidden="true" />
              Save
            </button>
            <input
              aria-label="Local strategy id"
              value={cloneId}
              onChange={(event) => setCloneId(event.target.value)}
            />
            <button type="button" className="icon-command" title="Clone strategy" onClick={clone} disabled={busy || !strategyId || !cloneId}>
              <Copy aria-hidden="true" />
            </button>
            <button type="button" className="icon-command danger" title="Delete local strategy" onClick={remove} disabled={busy || !detail?.editable}>
              <Trash2 aria-hidden="true" />
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}
