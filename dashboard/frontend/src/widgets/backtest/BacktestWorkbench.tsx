import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import { Code2, Database, History, Play, RefreshCw, Save, ShieldCheck } from "lucide-react"

import { CumulativeReturnsChart, DrawdownChart } from "@/components/charts"
import { PythonEditor } from "@/components/python"
import { SdkDocumentation } from "@/components/shared/SdkDocumentation"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useStrategySdk } from "@/contexts/StrategySdkContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { useConfirm } from "@/hooks/useConfirm"
import {
  api,
  type BacktestAnalysis,
  type BacktestAttribution,
  type BacktestJob,
  type BacktestRecord,
  type BacktestRobustness,
  type BacktestSignalDiagnostics,
  type BacktestValidation,
} from "@/lib/api"
import { Widget } from "@/widgets/Widget"

interface ProfileFields { start_date: string; end_date: string }
interface ValidationParameter {
  name: string
  annotation: string | null
  default: unknown
  editable: boolean
  minimum: number | null
  maximum: number | null
  step: number | null
}
interface ValidationEntrypoint {
  id: string
  label: string | null
  parameters: ValidationParameter[]
  line: number
}
interface ValidationWorkspace {
  project_id: string
  source: string
  source_sha256: string
  current_revision: number
  editable: boolean
  inspection: { entrypoints: ValidationEntrypoint[] }
}
type ValidationTab = "performance" | "signals" | "attribution" | "validation" | "robustness" | "history"

const PARAMETER_LABELS: Record<string, string> = {
  periods_per_year: "年化周期",
  risk_free_rate: "无风险年利率",
  minimum_observations: "最少回归样本",
  minimum_signal_periods: "最少信号证据期",
  minimum_mean_ic: "最低平均 Rank IC",
  minimum_coverage: "最低评分覆盖率",
  minimum_execution_fidelity: "最低执行保真度",
  confidence_95: "VaR 置信度 95%",
  confidence_99: "VaR 置信度 99%",
  newey_west_lags: "Newey-West 滞后阶数",
}

function parameterKey(entrypointId: string, parameter: string) {
  return `${entrypointId}.${parameter}`
}

function parameterValues(workspace: ValidationWorkspace) {
  return Object.fromEntries(workspace.inspection.entrypoints.flatMap((entrypoint) =>
    entrypoint.parameters.map((parameter) => [parameterKey(entrypoint.id, parameter.name), parameter.default]),
  ))
}

function percent(value: unknown, digits = 2) {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : "—"
}

function number(value: unknown, digits = 2) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—"
}

function statusLabel(status: BacktestRobustness["status"] | undefined) {
  return ({ research_candidate: "研究候选", watch: "需要观察", weak: "证据较弱", invalid: "无效" } as const)[status ?? "invalid"]
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return <div className="analytics-kpi"><span>{label}</span><strong>{value}</strong></div>
}

export function ValidationWorkbenchWidget() {
  const sdk = useStrategySdk()
  const confirm = useConfirm()
  const { selectedBacktest, setSelectedBacktest } = useWorkspace()
  const project = sdk.project
  const [tab, setTab] = useState<ValidationTab>("performance")
  const [validation, setValidation] = useState<ValidationWorkspace | null>(null)
  const [source, setSource] = useState("")
  const [validationParameters, setValidationParameters] = useState<Record<string, unknown>>({})
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [profileBounds, setProfileBounds] = useState<ProfileFields | null>(null)
  const [configPaneWidth, setConfigPaneWidth] = useState(34)
  const [jobs, setJobs] = useState<BacktestJob[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [runs, setRuns] = useState<BacktestRecord[]>([])
  const [analysis, setAnalysis] = useState<BacktestAnalysis | null>(null)
  const [signals, setSignals] = useState<BacktestSignalDiagnostics | null>(null)
  const [attribution, setAttribution] = useState<BacktestAttribution | null>(null)
  const [robustness, setRobustness] = useState<BacktestRobustness | null>(null)
  const [frozenValidation, setFrozenValidation] = useState<BacktestValidation | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadingResult, setLoadingResult] = useState(false)
  const [error, setError] = useState("")
  const authoringSplit = useRef<HTMLDivElement>(null)
  const resultsSection = useRef<HTMLElement>(null)
  const projectId = project?.id
  const projectProfile = project?.profile
  const selectedBacktestRef = useRef(selectedBacktest)

  useEffect(() => { selectedBacktestRef.current = selectedBacktest }, [selectedBacktest])

  useEffect(() => {
    let current = true
    setValidation(null); setSource(""); setValidationParameters({})
    setAnalysis(null); setSignals(null); setAttribution(null); setRobustness(null); setFrozenValidation(null)
    setRuns([]); setJobs([]); setActiveJobId(null); setError("")
    setProfileBounds(null); setStartDate(""); setEndDate("")
    if (!projectId || !projectProfile) return
    void Promise.all([
      api.get<ValidationWorkspace>(`/validation/projects/${projectId}`),
      api.get<ProfileFields>(`/strategy/fields?profile=${projectProfile}`),
      api.get<BacktestRecord[]>("/backtests?limit=50"),
      api.get<BacktestJob[]>("/backtests/jobs?limit=20"),
    ]).then(([validationWorkspace, profile, runRows, jobRows]) => {
      if (!current) return
      const projectRuns = runRows.filter((item) => item.strategy_id === projectId)
      setValidation(validationWorkspace)
      setSource(validationWorkspace.source)
      setValidationParameters(parameterValues(validationWorkspace))
      setProfileBounds(profile)
      setStartDate(profile.start_date); setEndDate(profile.end_date)
      setRuns(projectRuns); setJobs(jobRows)
      const active = jobRows.find((item) => item.request?.project_id === projectId && (item.status === "queued" || item.status === "running"))
      setActiveJobId(active?.id ?? null)
      if (!projectRuns.some((item) => item.id === selectedBacktestRef.current)) {
        setSelectedBacktest(projectRuns[0]?.id ?? null)
      }
    }).catch((reason: Error) => { if (current) setError(reason.message) })
    return () => { current = false }
  }, [projectId, projectProfile, setSelectedBacktest])

  useEffect(() => {
    if (!activeJobId) return
    let current = true
    const timer = window.setInterval(() => {
      void api.get<BacktestJob>(`/backtests/jobs/${activeJobId}`).then(async (job) => {
        if (!current) return
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])
        if (job.status === "succeeded" && job.result_id) {
          const rows = await api.get<BacktestRecord[]>("/backtests?limit=50")
          if (!current) return
          setRuns(rows.filter((item) => item.strategy_id === projectId))
          setActiveJobId(null)
          setSelectedBacktest(job.result_id)
          setTab("performance")
          window.setTimeout(() => resultsSection.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0)
        } else if (job.status === "failed" || job.status === "interrupted") {
          setActiveJobId(null)
          setError(job.error_summary || job.message || "回测未完成")
        }
      }).catch((reason: Error) => { if (current) { setActiveJobId(null); setError(reason.message) } })
    }, 1500)
    return () => { current = false; window.clearInterval(timer) }
  }, [activeJobId, projectId, setSelectedBacktest])

  const currentRunId = runs.find((run) => run.id === selectedBacktest && run.strategy_id === projectId)?.id
  useEffect(() => {
    setAnalysis(null); setSignals(null); setAttribution(null); setRobustness(null); setFrozenValidation(null)
    if (!currentRunId) {
      setLoadingResult(false)
      return
    }
    let current = true
    setLoadingResult(true); setError("")
    void Promise.allSettled([
      api.get<BacktestAnalysis>(`/backtests/${currentRunId}/analysis`),
      api.get<BacktestSignalDiagnostics>(`/backtests/${currentRunId}/signals`),
      api.get<BacktestAttribution>(`/backtests/${currentRunId}/attribution`),
      api.get<BacktestRobustness>(`/backtests/${currentRunId}/robustness`),
      api.get<BacktestValidation>(`/backtests/${currentRunId}/validation`),
    ]).then(([analysisResult, signalResult, attributionResult, robustnessResult, validationResult]) => {
      if (!current) return
      if (analysisResult.status === "rejected") {
        setError(analysisResult.reason instanceof Error ? analysisResult.reason.message : String(analysisResult.reason))
        setAnalysis(null)
      } else setAnalysis(analysisResult.value)
      setSignals(signalResult.status === "fulfilled" ? signalResult.value : null)
      setAttribution(attributionResult.status === "fulfilled" ? attributionResult.value : null)
      setRobustness(robustnessResult.status === "fulfilled" ? robustnessResult.value : null)
      setFrozenValidation(validationResult.status === "fulfilled" ? validationResult.value : null)
    }).finally(() => { if (current) setLoadingResult(false) })
    return () => { current = false }
  }, [currentRunId])

  const localDirty = Boolean(validation && source !== validation.source)
  const visualEdits = useMemo(() => validation?.inspection.entrypoints.flatMap((entrypoint) =>
    entrypoint.parameters.filter((parameter) => parameter.editable).flatMap((parameter) => {
      const key = parameterKey(entrypoint.id, parameter.name)
      return Object.is(validationParameters[key], parameter.default) ? [] : [{
        entrypoint_id: entrypoint.id,
        parameter: parameter.name,
        value: validationParameters[key],
      }]
    })) ?? [], [validation, validationParameters])
  const visualDirty = visualEdits.length > 0
  const hasUnsavedChanges = Boolean(project?.dirty || localDirty || visualDirty)
  const equityData = useMemo(() => analysis?.dates.map((date, index) => ({
    date,
    strategy: analysis.equity_curve[index] ?? 1,
    benchmark: analysis.benchmark_equity_curve[index] ?? 1,
    excess: analysis.excess_equity_curve[index] ?? 1,
  })) ?? [], [analysis])
  const drawdownData = useMemo(() => analysis?.dates.map((date, index) => ({
    date,
    strategy: analysis.drawdown[index] ?? 0,
    benchmark: analysis.benchmark_drawdown[index] ?? 0,
  })) ?? [], [analysis])
  const activeJob = jobs.find((item) => item.id === activeJobId)
  const invalidRange = Boolean(startDate && endDate && startDate > endDate)
  const workspaceReady = !sdk.loading && validation?.project_id === projectId && Boolean(startDate && endDate)

  async function selectProject(nextId: string) {
    if (nextId === projectId || busy || sdk.loading) return
    if ((localDirty || visualDirty) && !await confirm({
      title: "切换回测策略",
      description: "当前验证代码或参数尚未保存，切换后将丢弃这些修改。",
      confirmText: "放弃修改并切换",
      tone: "danger",
    })) return
    setBusy(true); setError("")
    try { await sdk.openProject(nextId) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  function beginResize(event: ReactPointerEvent<HTMLButtonElement>) {
    const container = authoringSplit.current
    if (!container) return
    event.preventDefault()
    const move = (pointer: PointerEvent) => {
      const bounds = container.getBoundingClientRect()
      const width = ((pointer.clientX - bounds.left) / bounds.width) * 100
      setConfigPaneWidth(Math.min(56, Math.max(24, width)))
    }
    const stop = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", stop)
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", stop)
  }

  async function runBacktest() {
    if (!project || !workspaceReady || hasUnsavedChanges || invalidRange) return
    if (!await confirm({
      title: "运行完整事件回测",
      description: `运行当前已保存的“${project.name}”。将调用受信任的本机 Python；它不是安全沙箱。`,
      confirmText: "运行回测",
    })) return
    setBusy(true); setError("")
    try {
      const job = await api.post<BacktestJob>("/backtests/jobs", {
        project_id: project.id, revision: project.current_revision, profile: project.profile,
        start_date: startDate, end_date: endDate, confirm_python_execution: true,
      })
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])
      setActiveJobId(job.id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function saveValidationSource(nextSource = source) {
    if (!project || !validation?.editable || nextSource === validation.source) return
    setBusy(true); setError("")
    try {
      const updated = await api.put<ValidationWorkspace>(`/validation/projects/${project.id}`, {
        source: nextSource,
        expected_source_sha256: validation.source_sha256,
        confirm_write: true,
      })
      setValidation(updated); setSource(updated.source)
      setValidationParameters(parameterValues(updated))
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function applyValidationParameters() {
    if (!project || !validation?.editable || !visualEdits.length || localDirty) return
    setBusy(true); setError("")
    try {
      const updated = await api.post<ValidationWorkspace>(
        `/validation/projects/${project.id}/parameters`,
        {
          edits: visualEdits,
          expected_source_sha256: validation.source_sha256,
          confirm_write: true,
        },
      )
      setValidation(updated); setSource(updated.source)
      setValidationParameters(parameterValues(updated))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function migrateDefaultValidation() {
    if (!project || !validation?.editable || localDirty || visualDirty) return
    if (!await confirm({
      title: "迁移到最新验证模板",
      description: "这会用当前官方收益、归因、尾部风险、研究质量标准和研究证据模板保存新的验证代码；历史回测不会重算。",
      confirmText: "创建新 revision",
      tone: "danger",
    })) return
    setBusy(true); setError("")
    try {
      const updated = await api.post<ValidationWorkspace>(`/validation/projects/${project.id}/migrate-default`, {
        expected_source_sha256: validation.source_sha256,
        confirm_write: true,
      })
      setValidation(updated); setSource(updated.source)
      setValidationParameters(parameterValues(updated))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project) return <Widget title="验证与回测" loading={sdk.loading} error={sdk.error}><span /></Widget>
  return (
    <Widget headerless className="validation-workbench-widget">
      <section className="backtest-authoring">
        <div
          ref={authoringSplit}
          className="backtest-authoring-split"
          style={{ gridTemplateColumns: `${configPaneWidth}% 0.4rem minmax(0, 1fr)` }}
        >
          <section className="backtest-config-pane">
            <header className="backtest-authoring-pane-header"><strong>回测配置</strong></header>
            <div className="backtest-config-pane-body">
              <div className="backtest-setup-card">
                <label className="space-y-2 text-xs">
                  <span className="block font-medium">回测策略</span>
                  <select aria-label="回测策略" className="w-full" value={project.id} disabled={busy || sdk.loading} onChange={(event) => void selectProject(event.target.value)}>
                    {sdk.projects.map((item) => <option key={item.id} value={item.id}>{item.name}{item.built_in ? "（系统模板）" : ""}</option>)}
                  </select>
                </label>
                <p className="text-xs text-muted-foreground">使用所选项目已保存的策略和验证代码，历史回测随项目切换。</p>
              </div>
              <div className="backtest-setup-card">
                <div className="backtest-setup-card-heading"><strong>回测样本</strong></div>
                <div className="backtest-run-controls">
                  <label><span>开始日期</span><input type="date" min={profileBounds?.start_date} max={profileBounds?.end_date} value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
                  <label><span>结束日期</span><input type="date" min={profileBounds?.start_date} max={profileBounds?.end_date} value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
                </div>
              </div>
              {validation?.inspection.entrypoints.map((entrypoint) => entrypoint.parameters.some((parameter) => parameter.editable) ? (
                <div className="backtest-setup-card" key={entrypoint.id}>
                  <div className="backtest-setup-card-heading"><strong>{entrypoint.label || entrypoint.id}</strong></div>
                  <div className="validation-parameter-grid">
                    {entrypoint.parameters.filter((parameter) => parameter.editable).map((parameter) => {
                      const key = parameterKey(entrypoint.id, parameter.name)
                      const value = validationParameters[key]
                      return <label key={key} className={Object.is(value, parameter.default) ? "" : "changed"}>
                        <span>{PARAMETER_LABELS[parameter.name] || parameter.name}</span>
                        {typeof parameter.default === "boolean" ? <select
                          value={String(value)}
                          disabled={!validation.editable || localDirty}
                          onChange={(event) => setValidationParameters((current) => ({ ...current, [key]: event.target.value === "true" }))}
                        ><option value="true">是</option><option value="false">否</option></select> : <input
                          type={typeof parameter.default === "number" ? "number" : "text"}
                          min={parameter.minimum ?? undefined}
                          max={parameter.maximum ?? undefined}
                          step={parameter.step ?? (parameter.annotation === "int" ? 1 : "any")}
                          value={String(value ?? "")}
                          disabled={!validation.editable || localDirty}
                          onChange={(event) => setValidationParameters((current) => ({
                            ...current,
                            [key]: typeof parameter.default === "number" ? Number(event.target.value) : event.target.value,
                          }))}
                        />}
                      </label>
                    })}
                  </div>
                </div>
              ) : null)}
              <div className="validation-parameter-actions">
                <Button disabled={!visualDirty || busy || localDirty || !validation?.editable} onClick={() => void applyValidationParameters()}>应用参数</Button>
              </div>
            </div>
            <footer className="backtest-run-footer">
              {invalidRange || hasUnsavedChanges ? <span className="warning">{invalidRange ? "开始日期必须早于结束日期" : project.dirty ? "请先保存策略修改" : visualDirty ? "请先应用验证参数" : "请先保存右侧 Python"}</span> : <span />}
              <div className="backtest-run-actions"><button className="primary-command" type="button" disabled={busy || !workspaceReady || Boolean(activeJobId) || hasUnsavedChanges || invalidRange} onClick={() => void runBacktest()}><Play />{activeJobId ? "回测运行中" : "运行回测"}</button></div>
            </footer>
          </section>

          <button
            type="button"
            className="backtest-split-divider"
            role="separator"
            aria-orientation="vertical"
            aria-label="调整回测配置与 Python 面板宽度"
            aria-valuemin={24}
            aria-valuemax={56}
            aria-valuenow={Math.round(configPaneWidth)}
            onPointerDown={beginResize}
            onKeyDown={(event) => {
              if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
              event.preventDefault()
              setConfigPaneWidth((current) => Math.min(56, Math.max(24, current + (event.key === "ArrowLeft" ? -2 : 2))))
            }}
          />

          <section className="backtest-python-pane">
            <header className="backtest-authoring-pane-header"><div><Code2 size={15} /><strong>验证 Python</strong></div><SdkDocumentation topic="validation" /></header>
            <div className="backtest-python-pane-body">
              {validation ? <PythonEditor
                className="backtest-python-editor"
                kind="validation"
                documentId={`${project.id}.validation`}
                value={source}
                version={validation.source_sha256}
                disabled={!validation.editable}
                height="100%"
                fields={[]}
                factors={[]}
                parameters={validation.inspection.entrypoints.flatMap((entrypoint) => entrypoint.parameters)}
                onChange={setSource}
                onSave={(nextSource) => saveValidationSource(nextSource)}
              /> : <div className="python-editor-loading">正在加载 validation.py…</div>}
            </div>
            <footer className="backtest-python-actions"><Button variant="outline" disabled={!validation?.editable || busy || localDirty || visualDirty} onClick={() => void migrateDefaultValidation()}>迁移官方模板</Button><Button disabled={!validation?.editable || busy || !localDirty || visualDirty} onClick={() => void saveValidationSource()}><Save />保存</Button></footer>
          </section>
        </div>
      </section>

      {activeJob ? <div className="workbench-message"><RefreshCw className="spin" />{activeJob.message || (activeJob.status === "queued" ? "已进入回测队列" : "逐交易日运行事件引擎…")}</div> : null}
      {error ? <div className="workbench-message error">{error}</div> : null}

      <section ref={resultsSection} className="backtest-results-section">
      <section className="backtest-result-identity">
        <div className="backtest-result-title"><strong>回测结果</strong><span>{analysis ? `${analysis.start_date} — ${analysis.end_date}` : "选择历史 Run 或运行一次回测"}</span></div>
        {analysis?.strategy_snapshot ? <div className="backtest-snapshot-summary"><Database size={13} /><span>运行时策略 · {analysis.strategy_snapshot.factors.length} 个因子</span></div> : null}
        <select aria-label="选择历史回测" value={selectedBacktest ?? ""} onChange={(event) => setSelectedBacktest(event.target.value || null)}><option value="">选择历史 Run</option>{runs.map((run) => <option key={run.id} value={run.id}>{run.start_date}–{run.end_date} · {percent(run.total_return)} · {run.run_at.slice(0, 10)}</option>)}</select>
      </section>

      <div className="workbench-tabs" role="tablist" aria-label="验证结果视图">
        {([
          ["performance", "收益与回撤"], ["signals", "信号诊断"], ["attribution", "Alpha/Beta 归因"],
          ["validation", "风险与证据"], ["robustness", "稳健性"], ["history", "历史 Run"],
        ] as Array<[ValidationTab, string]>).map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>)}
      </div>

      {loadingResult ? <div className="analytics-empty">正在读取回测结果…</div> : null}
      {!loadingResult && tab === "performance" ? <div className="workbench-body">
        {analysis ? <>
          <div className="analytics-kpi-grid workbench-kpis">
            <MetricCard label="总收益" value={percent(analysis.metrics.total_return)} /><MetricCard label="年化收益" value={percent(analysis.metrics.annual_return)} />
            <MetricCard label="年化波动" value={percent(analysis.metrics.annual_vol)} /><MetricCard label="Sharpe" value={number(analysis.metrics.sharpe)} />
            <MetricCard label="最大回撤" value={percent(analysis.metrics.max_drawdown)} /><MetricCard label="平均换手" value={percent(analysis.average_turnover)} />
          </div>
          <div className="detail-strip"><span>{analysis.dates.length} 个交易日</span><span>{analysis.executions.length} 条执行记录</span><span>基准覆盖 {percent(analysis.benchmark_coverage)}</span><span className="font-mono">Run {analysis.id.slice(0, 12)}</span></div>
          <CumulativeReturnsChart data={equityData} series={[{ key: "strategy", name: "策略净值" }, { key: "benchmark", name: "等权基准" }, { key: "excess", name: "超额净值" }]} height={270} />
          <DrawdownChart data={drawdownData} height={190} />
        </> : <div className="analytics-empty">尚无可显示的回测结果。</div>}
      </div> : null}

      {!loadingResult && tab === "signals" ? <div className="workbench-body">
        {signals ? <>
          {signals.warning ? <div className="workbench-message warning">{signals.warning}</div> : null}
          <div className="analytics-kpi-grid workbench-kpis"><MetricCard label="平均 Rank IC" value={number(signals.summary.mean_ic)} /><MetricCard label="IC 为正比例" value={percent(signals.summary.positive_ic_ratio)} /><MetricCard label="评分覆盖率" value={percent(signals.summary.average_coverage)} /><MetricCard label="选股集合换手" value={percent(signals.summary.average_selection_turnover)} /><MetricCard label="有效检验期" value={`${signals.evidence_periods}/${signals.periods}`} /></div>
          <div className="analytics-table-wrap"><table className="analytics-table compact"><thead><tr><th>信号日</th><th>下一截面</th><th>评分覆盖</th><th>入选</th><th>Rank IC</th><th>集合换手</th></tr></thead><tbody>{signals.rows.map((row) => <tr key={row.signal_date}><td>{row.signal_date}</td><td>{row.horizon_end_date || "—"}</td><td>{percent(row.coverage)}（{row.scored_count}/{row.universe_count}）</td><td>{row.selected_count}</td><td>{number(row.ic)}{row.ic_observations ? `（n=${row.ic_observations}）` : ""}</td><td>{percent(row.selection_turnover)}</td></tr>)}</tbody></table></div>
        </> : <div className="analytics-empty">该 Run 没有可用的信号诊断快照。</div>}
      </div> : null}

      {!loadingResult && tab === "attribution" ? <div className="workbench-body">
        {attribution ? <>
          {attribution.warnings.map((warning) => <div key={warning} className="workbench-message warning">{warning}</div>)}
          <div className="analytics-kpi-grid workbench-kpis"><MetricCard label="CAPM 年化 Alpha" value={percent(attribution.capm.alpha_annualized)} /><MetricCard label="市场 Beta" value={number(attribution.capm.betas.MKT)} /><MetricCard label="R²" value={percent(attribution.capm.r_squared)} /><MetricCard label="回归样本" value={String(attribution.observations)} /></div>
          <div className="analytics-table-wrap"><table className="analytics-table compact"><thead><tr><th>因子</th><th>估计值</th><th>标准误</th><th>t 值</th></tr></thead><tbody>{Object.entries(attribution.multi_factor.estimates).map(([name, estimate]) => <tr key={name}><td><strong>{name}</strong></td><td>{number(estimate.estimate, 4)}</td><td>{number(estimate.standard_error, 4)}</td><td>{number(estimate.t_stat)}</td></tr>)}</tbody></table></div>
        </> : <div className="analytics-empty">该 Run 没有可用的归因数据。</div>}
      </div> : null}

      {!loadingResult && tab === "validation" ? <div className="workbench-body">
        {frozenValidation ? <FrozenValidationPanels value={frozenValidation} /> : <div className="analytics-empty">该 Run 没有冻结的验证输出。</div>}
      </div> : null}

      {!loadingResult && tab === "robustness" ? <div className="workbench-body">
        {robustness ? <>
          <div className="backtest-verdict-summary"><div><span>研究结论</span><strong className={`research-status ${robustness.status}`}><ShieldCheck />{statusLabel(robustness.status)}</strong></div><div><span>策略年化</span><strong>{percent(robustness.metrics.strategy.annual_return)}</strong></div><div><span>超额年化</span><strong>{percent(robustness.metrics.excess.annual_return)}</strong></div><div><span>验证期超额</span><strong>{percent(robustness.validation.validation.excess?.annual_return)}</strong></div><div><span>校正后 p 值</span><strong>{number(robustness.statistical.adjusted_p_value, 3)}</strong></div></div>
          <p className="mb-3 text-xs text-muted-foreground">{robustness.disclaimer}</p>
          <div className="analytics-table-wrap"><table className="analytics-table compact"><thead><tr><th>完整性检查</th><th>状态</th><th>说明</th></tr></thead><tbody>{robustness.checks.map((check) => <tr key={check.name}><td>{check.name}</td><td><Badge variant={check.passed ? "secondary" : "destructive"}>{check.passed ? "通过" : "未通过"}</Badge></td><td>{check.detail}</td></tr>)}</tbody></table></div>
        </> : <div className="analytics-empty">该 Run 无法形成稳健性结论，通常是基准数据覆盖不足。</div>}
      </div> : null}

      {tab === "history" ? <div className="workbench-body">
        <div className="backtest-section-heading"><div><strong><History size={15} /> 历史 Run</strong><span>每条记录都保留当时的策略代码和运行输入。</span></div><Button size="sm" variant="outline" onClick={() => void api.get<BacktestRecord[]>("/backtests?limit=50").then((rows) => setRuns(rows.filter((item) => item.strategy_id === project.id)))}><RefreshCw />刷新</Button></div>
        <div className="editor-list">{runs.map((run) => <button key={run.id} type="button" className={selectedBacktest === run.id ? "active" : ""} onClick={() => { setSelectedBacktest(run.id); setTab("performance") }}><strong>{run.start_date} → {run.end_date}</strong><small>{run.run_at.slice(0, 16).replace("T", " ")} · {run.profile}</small><em>{percent(run.total_return)} · Sharpe {number(run.sharpe)}</em></button>)}</div>
        {!runs.length ? <div className="analytics-empty">当前项目尚无历史回测。</div> : null}
      </div> : null}
      </section>
    </Widget>
  )
}

function FrozenValidationPanels({ value }: { value: BacktestValidation }) {
  const risk = value.outputs.risk as Record<string, unknown> | undefined
  const assessment = value.outputs.research_quality as Record<string, unknown> | undefined
  const quality = (value.outputs.research_evidence ?? (assessment?.status ? assessment : undefined)) as Record<string, unknown> | undefined
  const portfolio = risk?.portfolio as Record<string, unknown> | undefined
  const checks = quality?.checks as Record<string, boolean> | undefined
  const additional = value.available_analyses.filter((name) => !["performance", "alpha_beta", "risk", "research_quality", "research_evidence"].includes(name))
  return <>
    {value.warnings.map((warning) => <div key={warning} className="workbench-message warning">{warning}</div>)}
    {typeof assessment?.passed === "boolean" ? <section className="mb-4">
      <div className="backtest-section-heading"><div><strong>研究质量标准</strong><span>按本次回测保存的项目阈值评价实际成交。</span></div><Badge variant={assessment.passed ? "secondary" : "destructive"}>{assessment.passed ? "通过" : "未通过"}</Badge></div>
      {(assessment.reasons as string[] | undefined)?.map((reason) => <div key={reason} className="workbench-message error">{reason}</div>)}
      {(assessment.warnings as string[] | undefined)?.map((warning) => <div key={warning} className="workbench-message warning">{warning}</div>)}
    </section> : null}
    {risk ? <section className="mb-4">
      <div className="backtest-section-heading"><div><strong>冻结尾部风险</strong><span>由该 Run 当时固定的 validation.py 计算。</span></div><Badge variant={risk.status === "sufficient" ? "secondary" : "outline"}>{String(risk.status)}</Badge></div>
      {(risk.warnings as string[] | undefined)?.map((warning) => <div key={warning} className="workbench-message warning">{warning}</div>)}
      <div className="analytics-kpi-grid workbench-kpis"><MetricCard label="VaR 95%" value={percent(risk.var_95)} /><MetricCard label="CVaR 95%" value={percent(risk.cvar_95)} /><MetricCard label="VaR 99%" value={percent(risk.var_99)} /><MetricCard label="最大回撤持续期" value={typeof risk.max_drawdown_duration_periods === "number" ? `${risk.max_drawdown_duration_periods} 期` : "—"} /><MetricCard label="最大权重" value={percent(portfolio?.max_weight)} /><MetricCard label="有效持仓数" value={number(portfolio?.effective_positions)} /></div>
    </section> : null}
    {quality ? <section>
      <div className="backtest-section-heading"><div><strong>研究证据质量</strong><span>综合信号、数据和实际执行保真度。</span></div><Badge variant={quality.status === "pass" ? "secondary" : quality.status === "fail" ? "destructive" : "outline"}>{String(quality.status)}</Badge></div>
      {(quality.warnings as string[] | undefined)?.map((warning) => <div key={warning} className="workbench-message warning">{warning}</div>)}
      <div className="analytics-kpi-grid workbench-kpis"><MetricCard label="平均 Rank IC" value={number(quality.mean_rank_ic)} /><MetricCard label="Newey-West t" value={number(quality.newey_west_t_stat)} /><MetricCard label="评分覆盖率" value={percent(quality.average_coverage)} /><MetricCard label="执行保真度" value={percent(quality.execution_fidelity)} /><MetricCard label="有效证据期" value={`${String(quality.evidence_periods ?? 0)}/${String(quality.signal_periods ?? 0)}`} /></div>
      {checks ? <div className="analytics-table-wrap"><table className="analytics-table compact"><thead><tr><th>证据检查</th><th>状态</th></tr></thead><tbody>{Object.entries(checks).map(([name, passed]) => <tr key={name}><td>{name}</td><td><Badge variant={passed ? "secondary" : "destructive"}>{passed ? "通过" : "未通过"}</Badge></td></tr>)}</tbody></table></div> : null}
    </section> : null}
    {additional.map((name) => <section className="mt-4" key={name}>
      <div className="backtest-section-heading"><div><strong>{name}</strong><span>项目自定义冻结分析</span></div></div>
      <pre className="overflow-auto rounded-md border bg-muted/30 p-3 text-xs">{JSON.stringify(value.outputs[name], null, 2)}</pre>
    </section>)}
    {!risk && !quality && !assessment ? <div className="analytics-empty">该历史回测尚未包含风险或研究证据分析；已有分析：{value.available_analyses.join("、") || "无"}。</div> : null}
  </>
}
