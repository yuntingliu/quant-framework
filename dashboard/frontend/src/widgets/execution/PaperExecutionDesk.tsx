import { useEffect, useState } from 'react'
import { RefreshCw, WalletCards } from 'lucide-react'
import { apiGet, apiPost, type PaperOrder } from '../../lib/api'

export function PaperExecutionDeskWidget() {
  const [symbols, setSymbols] = useState<string[]>([])
  const [orders, setOrders] = useState<PaperOrder[]>([])
  const [symbol, setSymbol] = useState('')
  const [action, setAction] = useState('buy')
  const [quantity, setQuantity] = useState('100')
  const [error, setError] = useState('')

  async function refresh() {
    try {
      const [symbolPayload, orderRows] = await Promise.all([
        apiGet<{ symbols: string[] }>('/data/market/symbols'),
        apiGet<PaperOrder[]>('/paper/orders'),
      ])
      setSymbols(symbolPayload.symbols)
      setSymbol((current) => current || symbolPayload.symbols[0] || '')
      setOrders(orderRows)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => { void refresh() }, [])

  async function submit() {
    setError('')
    try {
      await apiPost<PaperOrder>('/paper/orders', { symbol, action, quantity: Number(quantity) })
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Paper Execution Desk</h2>
          <p>Local simulated fills at the bundled historical cutoff. Real orders are unavailable.</p>
        </div>
        <span className="status-pill neutral"><WalletCards size={13} /> paper only</span>
      </div>
      <div className="form-row paper-controls">
        <select value={symbol} onChange={(event) => setSymbol(event.target.value)}>{symbols.map((item) => <option key={item} value={item}>{item}</option>)}</select>
        <select value={action} onChange={(event) => setAction(event.target.value)}><option value="buy">Buy</option><option value="sell">Sell</option></select>
        <input type="number" min="1" step="100" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
        <button type="button" onClick={submit}>Submit</button>
        <button type="button" className="icon-action" onClick={() => void refresh()} title="Refresh orders"><RefreshCw size={14} /></button>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="table"><div className="table-row table-head paper-table-row"><span>Symbol</span><span>Side</span><span>Quantity</span><span>Fill</span><span>Status</span></div>{orders.map((order) => <div className="table-row paper-table-row" key={order.id}><span>{order.symbol}</span><span>{order.action}</span><span>{order.quantity}</span><span>{typeof order.fill_price === 'number' ? order.fill_price.toFixed(2) : '-'}</span><span>{order.status}</span></div>)}</div>
    </div>
  )
}
