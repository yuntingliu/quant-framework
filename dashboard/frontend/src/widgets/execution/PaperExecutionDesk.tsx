import { useCallback, useEffect, useState } from "react"
import { Eye, RefreshCw, WalletCards, Zap } from "lucide-react"

import {
  api,
  type PaperAccountPayload,
  type PaperOrder,
  type PaperRebalancePreview,
} from "../../lib/api"
import { useDataProfile, type DataProfile } from "../../lib/data-profile"
import { SymbolCombobox } from "../../components/shared/SymbolCombobox"

function money(value: number | "" | undefined): string {
  return typeof value === "number"
    ? value.toLocaleString("en-US", { maximumFractionDigits: 0 })
    : "—"
}

export function PaperExecutionDeskWidget() {
  const [profile, setProfile] = useDataProfile()
  const [strategies, setStrategies] = useState<Array<{ id: string; name: string }>>([])
  const [strategyId, setStrategyId] = useState("sdk-v1-default")
  const [symbols, setSymbols] = useState<string[]>([])
  const [orders, setOrders] = useState<PaperOrder[]>([])
  const [account, setAccount] = useState<PaperAccountPayload | null>(null)
  const [preview, setPreview] = useState<PaperRebalancePreview | null>(null)
  const [symbol, setSymbol] = useState("")
  const [action, setAction] = useState("buy")
  const [quantity, setQuantity] = useState("100")
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")

  const refresh = useCallback(async () => {
    setError("")
    const [templates, symbolPayload, orderRows, accountPayload] = await Promise.all([
      api.get<Array<{ id: string; name: string }>>("/strategy/projects"),
      api.get<{ symbols: string[] }>(`/data/market/symbols?profile=${profile}`),
      api.get<PaperOrder[]>("/paper/orders"),
      api.get<PaperAccountPayload>(`/paper/account?profile=${profile}`),
    ])
    setStrategies(templates)
    setStrategyId((current) => (
      templates.some((item) => item.id === current)
        ? current
        : templates[0]?.id ?? ""
    ))
    setSymbols(symbolPayload.symbols)
    setSymbol((current) => current || symbolPayload.symbols[0] || "")
    setOrders(orderRows)
    setAccount(accountPayload)
  }, [profile])

  useEffect(() => {
    setPreview(null)
    refresh().catch((loadError: Error) => setError(loadError.message))
  }, [refresh])

  async function createPreview() {
    setBusy(true)
    setError("")
    setMessage("")
    try {
      const value = await api.post<PaperRebalancePreview>("/paper/rebalance/preview", {
        strategy_id: strategyId,
        profile,
        account_id: "paper",
      })
      setPreview(value)
      setMessage(value.allowed ? "Rebalance ready for confirmation" : "Risk checks blocked rebalance")
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : String(previewError))
    } finally {
      setBusy(false)
    }
  }

  async function executePreview() {
    if (!preview?.allowed || !window.confirm(`Execute ${preview.orders.length} paper orders?`)) return
    setBusy(true)
    setError("")
    try {
      await api.post("/paper/rebalance/execute", {
        signal_id: preview.signal_id,
        profile,
        account_id: "paper",
        confirm: true,
      })
      setMessage("Paper rebalance filled")
      setPreview(null)
      await refresh()
    } catch (executeError) {
      setError(executeError instanceof Error ? executeError.message : String(executeError))
    } finally {
      setBusy(false)
    }
  }

  async function submitManual() {
    setBusy(true)
    setError("")
    try {
      await api.post<PaperOrder>("/paper/orders", {
        symbol,
        action,
        quantity: Number(quantity),
        profile,
        account_id: "paper",
      })
      setMessage("Manual paper order filled")
      await refresh()
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : String(submitError))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Paper Portfolio</h2>
          <p>Local cash, positions, fills and signal-driven rebalance simulation.</p>
        </div>
        <span className="status-pill neutral"><WalletCards size={13} /> paper only</span>
      </div>
      {account && (
        <div className="analytics-kpi-grid paper-account-grid">
          <div className="analytics-kpi"><span>Equity</span><strong>¥{money(account.account.equity)}</strong></div>
          <div className="analytics-kpi"><span>Cash</span><strong>¥{money(account.account.cash)}</strong></div>
          <div className="analytics-kpi"><span>Market value</span><strong>¥{money(account.account.market_value)}</strong></div>
          <div className="analytics-kpi"><span>Total return</span><strong>{(account.account.total_return * 100).toFixed(2)}%</strong></div>
          <div className="analytics-kpi"><span>Positions</span><strong>{account.account.positions_count}</strong></div>
          <div className="analytics-kpi"><span>Price date</span><strong>{account.price_date}</strong></div>
        </div>
      )}
      <div className="paper-rebalance-controls">
        <select value={profile} onChange={(event) => setProfile(event.target.value as DataProfile)}>
          <option value="demo">Demo</option>
          <option value="runtime">Local RQ</option>
        </select>
        <select value={strategyId} onChange={(event) => {
          setStrategyId(event.target.value)
          setPreview(null)
        }}>
          {strategies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <button type="button" className="icon-text-command" onClick={createPreview} disabled={busy}>
          <Eye aria-hidden="true" />
          Preview
        </button>
        <button type="button" className="icon-text-command primary" onClick={executePreview} disabled={busy || !preview?.allowed}>
          <Zap aria-hidden="true" />
          Confirm paper rebalance
        </button>
        <button type="button" className="icon-command" onClick={() => refresh().catch((loadError: Error) => setError(loadError.message))} title="Refresh paper portfolio">
          <RefreshCw aria-hidden="true" />
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {message && <p className="success-message">{message}</p>}
      {preview && (
        <div className="paper-preview">
          <div className="detail-strip">
            <span>{preview.orders.length} orders · turnover {(preview.turnover * 100).toFixed(1)}%</span>
            <strong className={preview.allowed ? "gate-pass-text" : "gate-fail-text"}>
              {preview.risk_status}
            </strong>
          </div>
          <div className="risk-list compact-risk-list">
            {preview.checks.map((check) => (
              <div className="risk-row" key={check.name}>
                <span className={check.passed ? "gate-pass" : "gate-fail"}>{check.passed ? "PASS" : "FAIL"}</span>
                <strong>{check.name.replace(/_/g, " ")}</strong>
                <span>{check.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="paper-sections">
        <section>
          <h3>Positions</h3>
          <div className="analytics-table-wrap">
            <table className="analytics-table compact">
              <thead><tr><th>Symbol</th><th>Quantity</th><th>Average</th><th>Last</th><th>Value</th><th>Unrealized</th></tr></thead>
              <tbody>
                {(account?.positions ?? []).map((position) => (
                  <tr key={position.symbol}>
                    <td><strong>{position.symbol}</strong></td>
                    <td>{money(position.quantity)}</td>
                    <td>{money(position.avg_cost)}</td>
                    <td>{money(position.market_price)}</td>
                    <td>{money(position.market_value)}</td>
                    <td>{money(position.unrealized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section>
          <h3>Manual paper order</h3>
          <div className="form-row paper-controls">
            <SymbolCombobox symbols={symbols} value={symbol} onChange={setSymbol} ariaLabel="Paper symbol" />
            <select value={action} onChange={(event) => setAction(event.target.value)}>
              <option value="buy">Buy</option><option value="sell">Sell</option>
            </select>
            <input type="number" min="100" step="100" value={quantity} onChange={(event) => setQuantity(event.target.value)} aria-label="Paper quantity" />
            <button type="button" onClick={submitManual} disabled={busy}>Submit</button>
          </div>
          <div className="table">
            <div className="table-row table-head paper-table-row"><span>Symbol</span><span>Side</span><span>Quantity</span><span>Fill</span><span>Status</span></div>
            {orders.slice(0, 12).map((order) => (
              <div className="table-row paper-table-row" key={order.id}>
                <span>{order.symbol}</span><span>{order.action}</span><span>{order.quantity}</span>
                <span>{typeof order.fill_price === "number" ? order.fill_price.toFixed(2) : "—"}</span><span>{order.status}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
