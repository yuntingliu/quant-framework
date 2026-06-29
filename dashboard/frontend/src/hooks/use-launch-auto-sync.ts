/**
 * One-shot launch auto-sync.
 *
 * On first app mount this probes RQ availability and data freshness, and
 * — only when RQ is BOTH available AND stale — triggers an incremental sync.
 * It then polls `/api/data/status` until the launched jobs finish, surfacing
 * progress for a non-blocking banner.
 *
 * QMT daily sync is only auto-started by a conservative one-shot post-close
 * gate. During trading hours QMT freshness comes from live ticks; full-market
 * daily parquet sync remains manual. External broker adapters are on-demand. The launch probe runs
 * at most once per app session (module flag + sessionStorage), survives React
 * StrictMode/HMR double-mounts, and never blocks render.
 */
import { useCallback, useEffect, useState } from "react"
import { api } from "@/lib/api"

type Phase = "idle" | "probing" | "syncing" | "stale" | "done"

interface SyncJob {
  id: string
  source: string
  status: string
  message?: string
  total?: number | null
  completed?: number | null
}

interface DataStatus {
  qmt?: {
    latest?: string | null
    expected_latest?: string | null
    needs_update?: boolean
    raw_needs_update?: boolean
    provider_pending?: boolean
    daily_sync_running?: boolean
    auto_sync_ready?: boolean
    freshness_note?: string | null
    last_sync_time?: string | null
    live_connected?: boolean
    live_tick_at?: string | null
    live_tick_age_seconds?: number | null
    live_tick_symbol?: string | null
    intraday_mode?: string | null
  }
  rq?: {
    needs_update?: boolean
    market_state?: Record<string, { available?: boolean; snapshot_only?: boolean } | undefined>
  }
  jobs?: SyncJob[]
}

interface RqConnection {
  rq_connected?: boolean
}

export interface LaunchAutoSyncState {
  phase: Phase
  jobs: SyncJob[]
  qmt?: DataStatus["qmt"] | null
  dismissed: boolean
  dismiss: () => void
  startQmtSync: () => void
}

const SESSION_KEY = "alphalab-launch-sync-started"
const QMT_DAILY_SESSION_PREFIX = "alphalab-qmt-daily-sync-"
const POLL_INTERVAL_MS = 3_000
const MAX_POLL_MS = 90_000 // absolute cap so phase can't get stuck on "syncing"
let LAUNCH_SYNC_STARTED = false

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

function qmtNeedsAttention(status: DataStatus | null): boolean {
  return Boolean(status?.qmt?.needs_update || status?.qmt?.provider_pending)
}

function alreadyStarted(): boolean {
  if (LAUNCH_SYNC_STARTED) return true
  try {
    if (sessionStorage.getItem(SESSION_KEY) === "1") return true
  } catch {
    /* ignore */
  }
  return false
}

function markStarted(): void {
  LAUNCH_SYNC_STARTED = true
  try {
    sessionStorage.setItem(SESSION_KEY, "1")
  } catch {
    /* ignore */
  }
}

function qmtDailySessionKey(status: DataStatus | null): string | null {
  const expected = status?.qmt?.expected_latest
  return expected ? `${QMT_DAILY_SESSION_PREFIX}${expected}` : null
}

function qmtDailyAutoStarted(status: DataStatus | null): boolean {
  const key = qmtDailySessionKey(status)
  if (!key) return true
  try {
    return sessionStorage.getItem(key) === "1"
  } catch {
    return true
  }
}

function markQmtDailyAutoStarted(status: DataStatus | null): void {
  const key = qmtDailySessionKey(status)
  if (!key) return
  try {
    sessionStorage.setItem(key, "1")
  } catch {
    /* ignore */
  }
}

function shouldAutoSyncQmtDaily(status: DataStatus | null): boolean {
  const qmt = status?.qmt
  return Boolean(
    qmt?.auto_sync_ready &&
    qmt?.needs_update &&
    !qmt?.provider_pending &&
    !qmt?.daily_sync_running &&
    !qmtDailyAutoStarted(status)
  )
}

export function useLaunchAutoSync(): LaunchAutoSyncState {
  const [phase, setPhase] = useState<Phase>("idle")
  const [jobs, setJobs] = useState<SyncJob[]>([])
  const [qmt, setQmt] = useState<DataStatus["qmt"] | null>(null)
  const [dismissed, setDismissed] = useState(false)

  const finishFromStatus = useCallback((status: DataStatus | null) => {
    setQmt(status?.qmt ?? null)
    setPhase(qmtNeedsAttention(status) ? "stale" : "done")
  }, [])

  const pollSources = useCallback(async (sources: string[], isAborted: () => boolean = () => false) => {
    let emptyPolls = 0
    const deadline = Date.now() + MAX_POLL_MS
    let latest: DataStatus | null = null
    while (!isAborted() && Date.now() < deadline) {
      latest = await api.get<DataStatus>("/data/status").catch(() => null)
      if (isAborted()) return latest
      setQmt(latest?.qmt ?? null)
      const running = (latest?.jobs ?? []).filter(
        (job) => sources.includes(job.source) && job.status === "running"
      )
      setJobs(running)
      if (running.length === 0) {
        emptyPolls += 1
        if (emptyPolls >= 2) break
      } else {
        emptyPolls = 0
      }
      await sleep(POLL_INTERVAL_MS)
    }
    return latest ?? await api.get<DataStatus>("/data/status").catch(() => null)
  }, [])

  const startQmtSync = useCallback(() => {
    setDismissed(false)
    setPhase("syncing")
    void (async () => {
      const job = await api.post<SyncJob>("/data/sync/qmt", {}).catch(() => null)
      if (job) setJobs([job])
      const latest = await pollSources(["qmt"])
      finishFromStatus(latest)
    })()
  }, [finishFromStatus, pollSources])

  useEffect(() => {
    if (alreadyStarted()) return
    markStarted()

    let aborted = false

    const run = async () => {
      setPhase("probing")

      const [status, rqConn] = await Promise.all([
        api.get<DataStatus>("/data/status").catch(() => null),
        api.get<RqConnection>("/data/rq/connection").catch(() => null),
      ])
      if (aborted || !status) {
        setPhase("done")
        return
      }
      setQmt(status.qmt ?? null)

      const triggered: string[] = []
      // Sync RQ when financials are stale OR the sector-heatmap industry table is
      // missing, so a fresh machine self-heals even when financials are current.
      const industryMissing = status.rq?.market_state?.industry?.available === false
      if (rqConn?.rq_connected && (status.rq?.needs_update || industryMissing)) {
        await api.post("/data/sync/rq", {}).catch(() => null)
        triggered.push("rq")
      }

      if (shouldAutoSyncQmtDaily(status)) {
        markQmtDailyAutoStarted(status)
        await api.post("/data/sync/qmt", {}).catch(() => null)
        triggered.push("qmt")
      }

      const alreadyRunning = (status.jobs ?? [])
        .filter((job) => job.status === "running" && ["qmt", "rq"].includes(job.source))
        .map((job) => job.source)
      const sourcesToPoll = Array.from(new Set([...triggered, ...alreadyRunning]))

      if (aborted || sourcesToPoll.length === 0) {
        if (!aborted) finishFromStatus(status)
        return
      }

      setPhase("syncing")
      const latest = await pollSources(sourcesToPoll, () => aborted)
      if (!aborted) finishFromStatus(latest)
    }

    void run()
    return () => {
      aborted = true
    }
  }, [finishFromStatus, pollSources])

  return { phase, jobs, qmt, dismissed, dismiss: () => setDismissed(true), startQmtSync }
}
