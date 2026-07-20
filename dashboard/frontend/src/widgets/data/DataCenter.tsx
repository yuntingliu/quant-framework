import { useCallback, useEffect, useState } from 'react'
import { Database, Play, RefreshCw, ShieldCheck } from 'lucide-react'
import {
  apiGet,
  apiPost,
  type DataManifest,
  type DataSyncHealth,
  type ProviderStatus,
  type RuntimeCatalog,
  type SyncJob,
  type SyncPlan,
} from '../../lib/api'
import { useWorkspaceRefresh } from '../../hooks/useWorkspaceRefresh'
import { useDataProfile, type DataProfile } from '../../lib/data-profile'

export function DataCenterWidget() {
  const refreshRevision = useWorkspaceRefresh()
  const [status, setStatus] = useState<ProviderStatus | null>(null)
  const [error, setError] = useState('')
  const [manifest, setManifest] = useState<DataManifest | null>(null)
  const [health, setHealth] = useState<DataSyncHealth | null>(null)
  const [catalog, setCatalog] = useState<RuntimeCatalog | null>(null)
  const [jobs, setJobs] = useState<SyncJob[]>([])
  const [plan, setPlan] = useState<SyncPlan | null>(null)
  const [profile, chooseProfile] = useDataProfile()
  const [busy, setBusy] = useState(false)
  const [quality, setQuality] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [providerStatus, dataManifest, syncHealth, runtimeCatalog, syncJobs] = await Promise.all([
        apiGet<ProviderStatus>('/data/providers'),
        apiGet<DataManifest>('/data/manifest'),
        apiGet<DataSyncHealth>('/data-sync/health'),
        apiGet<RuntimeCatalog>('/data-sync/catalog'),
        apiGet<SyncJob[]>('/data-sync/jobs?limit=8'),
      ])
      setStatus(providerStatus)
      setManifest(dataManifest)
      setHealth(syncHealth)
      setCatalog(runtimeCatalog)
      setJobs(syncJobs)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => { void refresh() }, 5000)
    return () => window.clearInterval(timer)
  }, [refresh, refreshRevision])

  async function preview() {
    setBusy(true)
    setError('')
    try {
      setPlan(await apiPost<SyncPlan>('/data-sync/plan', {
        source: 'rq',
        datasets: ['instruments', 'bars', 'fundamentals'],
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function startSync() {
    if (!plan) return
    setBusy(true)
    setError('')
    try {
      await apiPost<SyncJob>('/data-sync/jobs', {
        source: 'rq',
        datasets: ['instruments', 'bars', 'fundamentals'],
        start: plan.requested_start,
        end: plan.requested_end,
      })
      setPlan(null)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function validateRuntime() {
    setBusy(true)
    setError('')
    try {
      const reports = await apiPost<Array<{ status: string }>>('/data-sync/validate', {})
      const passed = reports.filter((item) => item.status === 'passed').length
      setQuality(`${passed}/${reports.length} passed`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (error) return <div className="panel"><h2>Data Providers</h2><p className="error">{error}</p></div>
  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Data Center</h2>
          <p>Bundled demo data stays immutable. RQ syncs write only to local runtime storage.</p>
        </div>
        <select value={profile} onChange={(event) => chooseProfile(event.target.value as DataProfile)}>
          <option value="demo">Demo profile</option>
          <option value="runtime">Local RQ profile</option>
        </select>
      </div>
      <div className="metric-grid">
        {Object.entries(status?.datasets ?? {}).map(([kind, dataset]) => <div className="metric" key={kind}><span>{kind}</span><strong>{dataset.status}</strong><small>{(dataset.bytes / 1024 / 1024).toFixed(2)} MB</small></div>)}
        <div className="metric">
          <span>coverage</span>
          <strong>{manifest?.symbol_count ?? 0} symbols</strong>
          <small>{manifest?.sample_start ?? '-'} → {manifest?.cutoff_date ?? '-'}</small>
        </div>
      </div>
      <div className="sync-toolbar">
        <span className={`status-pill ${health?.rq.configured ? 'ready' : 'neutral'}`}>
          <Database size={13} /> RQ {health?.rq.status ?? 'loading'}
        </span>
        <button type="button" onClick={preview} disabled={busy} title="Preview local RQ synchronization">
          <RefreshCw size={14} /> Preview
        </button>
        <button type="button" onClick={startSync} disabled={busy || !plan || !health?.rq.configured} title="Start the planned local synchronization">
          <Play size={14} /> Sync
        </button>
        <button type="button" onClick={validateRuntime} disabled={busy || catalog?.ready === 0} title="Validate runtime datasets">
          <ShieldCheck size={14} /> Validate
        </button>
        {quality && <span className="status-pill ready">{quality}</span>}
      </div>
      {!health?.rq.configured && (
        <p className="data-guidance">
          Configure {health?.rq.missing.join(', ') || 'the local RQ environment'} before syncing.
          The workstation continues to use the demo profile.
        </p>
      )}
      {plan && (
        <div className="detail-strip">
          <span>{plan.symbol_count} symbols · {plan.steps.length} datasets · {plan.estimated_batches} batches</span>
          <strong>{plan.requested_start} → {plan.requested_end}</strong>
        </div>
      )}
      <div className="table runtime-table">
        <div className="table-row table-head"><span>Runtime dataset</span><span>Status</span><span>Rows</span><span>Coverage</span></div>
        {(catalog?.datasets ?? []).map((dataset) => (
          <div className="table-row" key={dataset.id}>
            <span>{dataset.label}<small>{dataset.id}</small></span>
            <span>{dataset.status}</span>
            <span>{dataset.rows.toLocaleString()}</span>
            <span>{dataset.date_start ?? '-'} → {dataset.date_end ?? '-'}</span>
          </div>
        ))}
      </div>
      <div className="table runtime-jobs">
        <div className="table-row table-head"><span>Job</span><span>Status</span><span>Progress</span><span>Message</span></div>
        {jobs.length === 0 && <div className="table-row"><span>No local sync jobs yet.</span></div>}
        {jobs.map((job) => (
          <div className="table-row" key={job.id}>
            <span>{job.id.slice(0, 8)}<small>{job.created_at}</small></span>
            <span>{job.status}</span>
            <span>{job.progress}/{job.total}</span>
            <span>{job.error || job.message || '-'}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
