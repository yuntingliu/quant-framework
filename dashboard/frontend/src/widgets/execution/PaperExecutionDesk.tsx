import { ClipboardList, FileClock, ShieldCheck, WalletCards } from 'lucide-react'

const contracts = [
  {
    name: 'SignalExecutor',
    detail: 'Turns target weights into broker-neutral order intents.',
    icon: ClipboardList,
  },
  {
    name: 'RiskGuard',
    detail: 'Validates gross exposure, single-name caps, turnover, and blocked symbols.',
    icon: ShieldCheck,
  },
  {
    name: 'PaperTrader',
    detail: 'Simulates fills locally for development and dashboard dry-runs.',
    icon: WalletCards,
  },
  {
    name: 'OrderLogger',
    detail: 'Writes neutral order records without a live adapter dependency.',
    icon: FileClock,
  },
]

export function PaperExecutionDeskWidget() {
  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Paper Execution Desk</h2>
          <p>Execution UI remains visible, but all live broker wiring is left to external adapters.</p>
        </div>
        <span className="status-pill neutral">paper only</span>
      </div>
      <div className="lane-grid execution-grid">
        {contracts.map((item) => {
          const Icon = item.icon
          return (
            <section className="lane" key={item.name}>
              <div className="lane-title"><Icon size={15} /> {item.name}</div>
              <p>{item.detail}</p>
              <code>broker-neutral</code>
            </section>
          )
        })}
      </div>
    </div>
  )
}
