import { AlertTriangle, CheckCircle2, ShieldCheck } from 'lucide-react'

const checks = [
  ['Max position weight', '0.10 default cap'],
  ['Cost model', '20 bps template assumption'],
  ['Data freshness', 'validated against the bundled manifest'],
  ['Live orders', 'disabled in barebone core'],
]

export function RiskConsoleWidget() {
  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Risk Console</h2>
          <p>Generic guardrails for strategy development before any execution adapter is installed.</p>
        </div>
        <ShieldCheck size={18} />
      </div>
      <div className="risk-list">
        {checks.map(([name, detail], index) => (
          <div className="risk-row" key={name}>
            {index < 2 ? <CheckCircle2 size={16} className="ok" /> : <AlertTriangle size={16} className="warn" />}
            <strong>{name}</strong>
            <span>{detail}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
