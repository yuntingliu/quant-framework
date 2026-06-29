import { Blocks, CheckCircle2, CircleDashed, Database, Radio, Shield } from 'lucide-react'

const slots = [
  {
    name: 'Local Parquet Market',
    layer: 'Data',
    status: 'active',
    detail: 'Built-in reader for bars.parquet and simple fixture data.',
    icon: Database,
  },
  {
    name: 'Fundamental Files',
    layer: 'Data',
    status: 'active',
    detail: 'Reads neutral fundamentals.parquet columns through the provider protocol.',
    icon: Database,
  },
  {
    name: 'External Market Vendor',
    layer: 'Data Adapter',
    status: 'slot',
    detail: 'Reserved for a plugin package that implements MarketDataProvider.',
    icon: Blocks,
  },
  {
    name: 'Realtime Feed',
    layer: 'Realtime Adapter',
    status: 'slot',
    detail: 'Reserved for streaming quotes and event channels; no bundled vendor client.',
    icon: Radio,
  },
  {
    name: 'Broker Gateway',
    layer: 'Execution Adapter',
    status: 'slot',
    detail: 'Reserved for a separate broker plugin. The barebone app keeps only paper execution contracts.',
    icon: Shield,
  },
]

export function AdapterSlotsWidget() {
  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Adapter Slots</h2>
          <p>Keep the AlphaLab integration map visible without wiring concrete vendors into the framework core.</p>
        </div>
        <span className="status-pill neutral">plugins later</span>
      </div>
      <div className="slot-list">
        {slots.map((slot) => {
          const Icon = slot.icon
          const active = slot.status === 'active'
          return (
            <section className="slot-row" key={slot.name}>
              <div className="slot-icon"><Icon size={17} /></div>
              <div className="slot-main">
                <strong>{slot.name}</strong>
                <span>{slot.layer}</span>
                <p>{slot.detail}</p>
              </div>
              <span className={`status-pill ${active ? 'ready' : 'neutral'}`}>
                {active ? <CheckCircle2 size={13} /> : <CircleDashed size={13} />}
                {active ? 'active' : 'slot'}
              </span>
            </section>
          )
        })}
      </div>
    </div>
  )
}
