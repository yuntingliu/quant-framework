import { useEffect, useRef, useState } from 'react'
import { apiGet, apiPost, type BacktestRecord, type BacktestRunResult, type DataManifest, type StrategyTemplate } from '../../lib/api'
import { useWorkspace } from '../../contexts/WorkspaceContext'
import { useWorkspaceRefresh } from '../../hooks/useWorkspaceRefresh'

export function BacktestRunnerWidget() {
  const refreshRevision = useWorkspaceRefresh()
  const workspace = useWorkspace()
  const initialWorkspaceRef = useRef({
    selectedStrategy: workspace.selectedStrategy,
    selectedBacktest: workspace.selectedBacktest,
    selectedDate: workspace.selectedDate,
  })
  initialWorkspaceRef.current = {
    selectedStrategy: workspace.selectedStrategy,
    selectedBacktest: workspace.selectedBacktest,
    selectedDate: workspace.selectedDate,
  }
  const [strategies, setStrategies] = useState<StrategyTemplate[]>([])
  const [strategyId, setStrategyId] = useState('momentum')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BacktestRunResult | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      apiGet<StrategyTemplate[]>('/strategies'),
      apiGet<DataManifest>('/data/manifest'),
      apiGet<BacktestRecord[]>('/backtests?limit=1'),
    ]).then(async ([items, manifest, records]) => {
      const initialWorkspace = initialWorkspaceRef.current
      setStrategies(items)
      setStartDate(manifest.sample_start)
      setEndDate(initialWorkspace.selectedDate ?? manifest.cutoff_date)
      const initialStrategy = initialWorkspace.selectedStrategy && items.some((item) => item.id === initialWorkspace.selectedStrategy)
        ? initialWorkspace.selectedStrategy
        : items[0]?.id
      if (initialStrategy) setStrategyId(initialStrategy)
      const initialBacktest = initialWorkspace.selectedBacktest ?? records[0]?.id
      if (initialBacktest) setResult(await apiGet<BacktestRunResult>(`/backtests/${initialBacktest}`))
    }).catch((err: Error) => setError(err.message))
  }, [refreshRevision])

  useEffect(() => {
    if (workspace.selectedStrategy && strategies.some((item) => item.id === workspace.selectedStrategy)) {
      setStrategyId(workspace.selectedStrategy)
    }
  }, [strategies, workspace.selectedStrategy])

  useEffect(() => {
    if (workspace.selectedDate) setEndDate(workspace.selectedDate)
  }, [workspace.selectedDate])

  useEffect(() => {
    if (!workspace.selectedBacktest) return
    apiGet<BacktestRunResult>(`/backtests/${workspace.selectedBacktest}`)
      .then(setResult)
      .catch((err: Error) => setError(err.message))
  }, [workspace.selectedBacktest])

  async function run() {
    setRunning(true)
    setError('')
    setResult(null)
    try {
      const response = await apiPost<BacktestRunResult>('/backtests/run', { strategy_id: strategyId, start_date: startDate, end_date: endDate })
      setResult(response)
      workspace.setSelectedStrategy(strategyId)
      workspace.setSelectedBacktest(response.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="panel">
      <h2>Backtest Runner</h2>
      <div className="form-row">
        <select value={strategyId} onChange={(event) => {
          setStrategyId(event.target.value)
          workspace.setSelectedStrategy(event.target.value)
        }}>
          {strategies.map((strategy) => <option key={strategy.id} value={strategy.id}>{strategy.name}</option>)}
        </select>
        <input value={startDate} onChange={(event) => setStartDate(event.target.value)} />
        <input value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        <button type="button" onClick={run} disabled={running || !startDate || !endDate}>{running ? 'Running' : 'Run'}</button>
      </div>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="metric-grid">
          {Object.entries(result.metrics ?? {}).map(([key, value]) => (
            <div className="metric" key={key}>
              <span>{key}</span>
              <strong>{Number(value).toFixed(4)}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
