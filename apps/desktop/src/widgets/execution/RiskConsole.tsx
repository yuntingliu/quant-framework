import { useCallback, useEffect, useState } from "react"
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldCheck } from "lucide-react"

import {
  api,
  type PaperRebalancePreview,
} from "../../lib/api"
import { useDataProfile } from "../../lib/data-profile"

export function RiskConsoleWidget() {
  const [profile] = useDataProfile()
  const [strategies, setStrategies] = useState<Array<{ id: string; name: string }>>([])
  const [strategyId, setStrategyId] = useState("sdk-v1-default")
  const [preview, setPreview] = useState<PaperRebalancePreview | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.get<Array<{ id: string; name: string }>>("/strategy/projects")
      .then((items) => {
        setStrategies(items)
        setStrategyId((current) => (
          items.some((item) => item.id === current)
            ? current
            : items[0]?.id ?? ""
        ))
      })
      .catch((loadError: Error) => setError(loadError.message))
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      setPreview(await api.post<PaperRebalancePreview>("/paper/rebalance/preview", {
        strategy_id: strategyId,
        profile,
        account_id: "paper",
      }))
    } catch (loadError) {
      setPreview(null)
      setError(loadError instanceof Error ? loadError.message : String(loadError))
    } finally {
      setLoading(false)
    }
  }, [profile, strategyId])

  useEffect(() => {
    if (strategyId) void refresh()
  }, [refresh, strategyId])

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Risk Console</h2>
          <p>Live checks against the selected signal and current paper account.</p>
        </div>
        <ShieldCheck size={18} />
      </div>
      <div className="risk-toolbar">
        <select value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>
          {strategies.map((strategy) => (
            <option key={strategy.id} value={strategy.id}>{strategy.name}</option>
          ))}
        </select>
        <span className={`research-status ${preview?.allowed ? "research_candidate" : "invalid"}`}>
          {preview?.risk_status ?? profile}
        </span>
        <button type="button" className="icon-command" title="Refresh risk checks" onClick={refresh} disabled={loading}>
          <RefreshCw aria-hidden="true" />
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="risk-list">
        {(preview?.checks ?? []).map((check) => (
          <div className="risk-row" key={check.name}>
            {check.passed
              ? <CheckCircle2 size={16} className="ok" />
              : <AlertTriangle size={16} className="warn" />}
            <strong>{check.name.replace(/_/g, " ")}</strong>
            <span>{check.detail}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
