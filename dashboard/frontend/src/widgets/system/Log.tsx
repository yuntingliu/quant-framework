import { useEffect, useState } from 'react'
import { apiGet } from '../../lib/api'

interface LogRow {
  level: string
  message: string
}

export function LogWidget() {
  const [rows, setRows] = useState<LogRow[]>([])

  useEffect(() => {
    apiGet<LogRow[]>('/system/logs').then(setRows).catch((err: Error) => {
      setRows([{ level: 'error', message: err.message }])
    })
    const cleanupStdout = window.api?.onBackendStdout?.((message) => {
      setRows((current) => [...current.slice(-100), { level: 'stdout', message }])
    })
    const cleanupStderr = window.api?.onBackendStderr?.((message) => {
      setRows((current) => [...current.slice(-100), { level: 'stderr', message }])
    })
    return () => {
      cleanupStdout?.()
      cleanupStderr?.()
    }
  }, [])

  return (
    <div className="panel">
      <h2>System Log</h2>
      <pre className="log">{rows.map((row) => `[${row.level}] ${row.message}`).join('\n')}</pre>
    </div>
  )
}

