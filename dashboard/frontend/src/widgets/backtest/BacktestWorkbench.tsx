import { useEffect, useMemo, useState } from "react"
import { Code2, Database, History, Play, RefreshCw, Save, ShieldCheck } from "lucide-react"

import { CumulativeReturnsChart, DrawdownChart } from "@/components/charts"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { useStrategySdk, type SourcePackage } from "@/contexts/StrategySdkContext"
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
} from "@/lib/api"
import { Widget } from "@/widgets/Widget"

interface ProfileFields { start_date: string; end_date: string }
type ValidationTab = "performance" | "signals" | "attribution" | "robustness" | "source" | "history"

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
  const [source, setSource] = useState("")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [revisions, setRevisions] = useState<SourcePackage[]>([])
  const [revision, setRevision] = useState<number | null>(null)
  const [jobs, setJobs] = useState<BacktestJob[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [runs, setRuns] = useState<BacktestRecord[]>([])
  const [analysis, setAnalysis] = useState<BacktestAnalysis | null>(null)
  const [signals, setSignals] = useState<BacktestSignalDiagnostics | null>(null)
  const [attribution, setAttribution] = useState<BacktestAttribution | null>(null)
  const [robustness, setRobustness] = useState<BacktestRobustness | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadingResult, setLoadingResult] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    setSource(project?.draft_source ?? "")
    setRevision(project?.current_revision ?? null)
    setAnalysis(null); setSignals(null); setAttribution(null); setRobustness(null)
    if (!project) return
    void Promise.all([
      api.get<ProfileFields>(`/strategy/fields?profile=${project.profile}`),
      api.get<SourcePackage[]>(`/strategy/projects/${project.id}/revisions`),
      api.get<BacktestRecord[]>("/backtests?limit=50"),
      api.get<BacktestJob[]>("/backtests/jobs?limit=20"),
    ]).then(([profile, revisionRows, runRows, jobRows]) => {
      const projectRuns = runRows.filter((item) => item.strategy_id === project.id)
      setStartDate(profile.start_date); setEndDate(profile.end_date)
      setRevisions(revisionRows); setRuns(projectRuns); setJobs(jobRows)
      const active = jobRows.find((item) => item.request?.project_id === project.id && (item.status === "queued" || item.status === "running"))
      setActiveJobId(active?.id ?? null)
      if (!selectedBacktest || !projectRuns.some((item) => item.id === selectedBacktest)) {
        setSelectedBacktest(projectRuns[0]?.id ?? null)
      }
    }).catch((reason: Error) => setError(reason.message))
  }, [project?.id, project?.profile, project?.current_revision, project?.draft_source_sha256])

  useEffect(() => {
    if (!activeJobId) return
    const timer = window.setInterval(() => {
      void api.get<BacktestJob>(`/backtests/jobs/${activeJobId}`).then((job) => {
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])
        if (job.status === "succeeded" && job.result_id) {
          setActiveJobId(null)
          setSelectedBacktest(job.result_id)
          setTab("performance")
          void api.get<BacktestRecord[]>("/backtests?limit=50").then((rows) => {
            setRuns(rows.filter((item) => item.strategy_id === project?.id))
          })
        } else if (job.status === "failed" || job.status === "interrupted") {
          setActiveJobId(null)
          setError(job.error || job.message || "回测未完成")
        }
      }).catch((reason: Error) => { setActiveJobId(null); setError(reason.message) })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [activeJobId, project?.id, setSelectedBacktest])

  useEffect(() => {
    if (!selectedBacktest) {
      setAnalysis(null); setSignals(null); setAttribution(null); setRobustness(null)
      return
    }
    let current = true
    setLoadingResult(true); setError("")
    void Promise.allSettled([
      api.get<BacktestAnalysis>(`/backtests/${selectedBacktest}/analysis`),
      api.get<BacktestSignalDiagnostics>(`/backtests/${selectedBacktest}/signals`),
      api.get<BacktestAttribution>(`/backtests/${selectedBacktest}/attribution`),
      api.get<BacktestRobustness>(`/backtests/${selectedBacktest}/robustness`),
    ]).then(([analysisResult, signalResult, attributionResult, robustnessResult]) => {
      if (!current) return
      if (analysisResult.status === "rejected") {
        setError(analysisResult.reason instanceof Error ? analysisResult.reason.message : String(analysisResult.reason))
        setAnalysis(null)
      } else setAnalysis(analysisResult.value)
      setSignals(signalResult.status === "fulfilled" ? signalResult.value : null)
      setAttribution(attributionResult.status === "fulfilled" ? attributionResult.value : null)
      setRobustness(robustnessResult.status === "fulfilled" ? robustnessResult.value : null)
    }).finally(() => { if (current) setLoadingResult(false) })
    return () => { current = false }
  }, [selectedBacktest])

  const selectedPackage = revisions.find((item) => item.revision === revision)
  const localDirty = Boolean(project && source !== project.draft_source)
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

  async function runBacktest() {
    if (!project || !revision || !selectedPackage || invalidRange) return
    if (!await confirm({
      title: "运行完整事件回测",
      description: `运行 ${project.id}@${revision}（${selectedPackage.source_sha256.slice(0, 16)}）。将调用受信任的本机 Python；它不是安全沙箱。`,
      confirmText: "运行回测",
    })) return
    setBusy(true); setError("")
    try {
      const job = await api.post<BacktestJob>("/backtests/jobs", {
        project_id: project.id, revision, profile: project.profile,
        start_date: startDate, end_date: endDate, confirm_python_execution: true,
      })
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])
      setActiveJobId(job.id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function saveAsDraft() {
    if (!project?.editable || !localDirty) return
    setBusy(true); setError("")
    try { await sdk.updateDraft(source) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  if (!project) return <Widget title="验证与回测" loading={sdk.loading} error={sdk.error}><span /></Widget>
  return (
    <Widget headerless>
      <section className="backtest-run-setup">
        <div className="backtest-section-heading">
          <div><strong>完整策略回测</strong><span>选择不可变 revision 和样本区间；调度、持仓与成交参数来自同一份 Python。</span></div>
          <small>{project.profile === "runtime" ? "本地 RQ" : "Demo"} · Strategy SDK v1</small>
        </div>
        <div className="backtest-setup-grid">
          <div className="backtest-setup-card">
            <div className="backtest-setup-card-heading"><strong>策略与源码</strong><span>当前草稿不会覆盖历史 Run</span></div>
            <div className="backtest-run-controls">
              <div className="pipeline-pinned-component"><span>策略项目</span><strong>{project.name}</strong></div>
              <label><span>冻结修订</span><select value={revision ?? ""} onChange={(event) => setRevision(Number(event.target.value))}>{revisions.map((item) => <option key={item.revision} value={item.revision}>r{item.revision} · {item.source_sha256.slice(0, 10)}</option>)}</select></label>
              <div className="pipeline-pinned-component readonly"><span>源码哈希</span><strong>{selectedPackage?.source_sha256.slice(0, 12) ?? "—"}</strong></div>
            </div>
          </div>
          <div className="backtest-setup-card">
            <div className="backtest-setup-card-heading"><strong>回测样本</strong><span>交易日和可交易性由所选数据 profile 提供</span></div>
            <div className="backtest-run-controls">
              <label><span>开始日期</span><input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
              <label><span>结束日期</span><input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
              <div className="pipeline-pinned-component readonly"><span>执行环境</span><strong>本机 Python</strong></div>
            </div>
          </div>
        </div>
        <div className="backtest-run-footer">
          <span className={invalidRange || project.dirty || localDirty ? "warning" : ""}>{invalidRange ? "开始日期必须早于结束日期" : localDirty ? "屏幕中的 Python 尚未保存；本次仍运行所选冻结 revision" : project.dirty ? "项目有未冻结草稿；本次仍运行所选冻结 revision" : "所有结果都固定源码哈希、数据范围和执行审计"}</span>
          <div className="backtest-run-actions"><button className="primary-command" type="button" disabled={busy || Boolean(activeJobId) || !selectedPackage || invalidRange} onClick={() => void runBacktest()}><Play />{activeJobId ? "回测运行中" : "运行冻结源码"}</button></div>
        </div>
      </section>

      {activeJob ? <div className="workbench-message"><RefreshCw className="spin" />{activeJob.message || (activeJob.status === "queued" ? "已进入回测队列" : "逐交易日运行事件引擎…")}</div> : null}
      {error ? <div className="workbench-message error">{error}</div> : null}

      <section className="backtest-result-identity">
        <div className="backtest-result-title"><strong>当前结果</strong><span>{analysis ? `${analysis.strategy_id} · ${analysis.start_date} — ${analysis.end_date}` : "选择历史 Run 或运行一次回测"}</span></div>
        {analysis?.strategy_snapshot ? <div className="backtest-snapshot-summary"><Database size={13} /><span>StrategySourcePackage · {(analysis.strategy_snapshot as BacktestAnalysis["strategy_snapshot"] & { revision?: number }).revision ?? "—"} · {analysis.strategy_snapshot.factors.length} 个因子</span></div> : null}
        <select aria-label="选择历史回测" value={selectedBacktest ?? ""} onChange={(event) => setSelectedBacktest(event.target.value || null)}><option value="">选择历史 Run</option>{runs.map((run) => <option key={run.id} value={run.id}>{run.start_date}–{run.end_date} · {percent(run.total_return)} · {run.run_at.slice(0, 10)}</option>)}</select>
      </section>

      <div className="workbench-tabs" role="tablist" aria-label="验证结果视图">
        {([
          ["performance", "收益与回撤"], ["signals", "信号诊断"], ["attribution", "Alpha/Beta 归因"],
          ["robustness", "稳健性"], ["source", "回测页 Python"], ["history", "历史 Run"],
        ] as Array<[ValidationTab, string]>).map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>)}
      </div>

      {loadingResult ? <div className="analytics-empty">正在读取冻结结果…</div> : null}
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
        </> : <div className="analytics-empty">尚无可显示的冻结回测结果。</div>}
      </div> : null}

      {!loadingResult && tab === "signals" ? <div className="workbench-body">
        {signals ? <>
          {signals.warning ? <div className="workbench-message warning">{signals.warning}</div> : null}
          <div className="analytics-kpi-grid workbench-kpis"><MetricCard label="平均 Rank IC" value={number(signals.summary.mean_ic)} /><MetricCard label="IC 为正比例" value={percent(signals.summary.positive_ic_ratio)} /><MetricCard label="评分覆盖率" value={percent(signals.summary.average_coverage)} /><MetricCard label="选股集合换手" value={percent(signals.summary.average_selection_turnover)} /><MetricCard label="有效检验期" value={`${signals.evidence_periods}/${signals.periods}`} /></div>
          <div className="analytics-table-wrap"><table className="analytics-table compact"><thead><tr><th>信号日</th><th>评分覆盖</th><th>Rank IC</th><th>头尾收益差</th><th>集合换手</th></tr></thead><tbody>{signals.rows.map((row) => <tr key={row.signal_date}><td>{row.signal_date}</td><td>{percent(row.coverage)}</td><td>{number(row.ic)}</td><td>{percent(row.quantile_spread)}</td><td>{percent(row.selection_turnover)}</td></tr>)}</tbody></table></div>
        </> : <div className="analytics-empty">该 Run 没有可用的信号诊断快照。</div>}
      </div> : null}

      {!loadingResult && tab === "attribution" ? <div className="workbench-body">
        {attribution ? <>
          {attribution.warnings.map((warning) => <div key={warning} className="workbench-message warning">{warning}</div>)}
          <div className="analytics-kpi-grid workbench-kpis"><MetricCard label="CAPM 年化 Alpha" value={percent(attribution.capm.alpha_annualized)} /><MetricCard label="市场 Beta" value={number(attribution.capm.betas.MKT)} /><MetricCard label="R²" value={percent(attribution.capm.r_squared)} /><MetricCard label="回归样本" value={String(attribution.observations)} /></div>
          <div className="analytics-table-wrap"><table className="analytics-table compact"><thead><tr><th>因子</th><th>估计值</th><th>标准误</th><th>t 值</th></tr></thead><tbody>{Object.entries(attribution.multi_factor.estimates).map(([name, estimate]) => <tr key={name}><td><strong>{name}</strong></td><td>{number(estimate.estimate, 4)}</td><td>{number(estimate.standard_error, 4)}</td><td>{number(estimate.t_stat)}</td></tr>)}</tbody></table></div>
        </> : <div className="analytics-empty">该 Run 没有冻结的归因数据。</div>}
      </div> : null}

      {!loadingResult && tab === "robustness" ? <div className="workbench-body">
        {robustness ? <>
          <div className="backtest-verdict-summary"><div><span>研究结论</span><strong className={`research-status ${robustness.status}`}><ShieldCheck />{statusLabel(robustness.status)}</strong></div><div><span>策略年化</span><strong>{percent(robustness.metrics.strategy.annual_return)}</strong></div><div><span>超额年化</span><strong>{percent(robustness.metrics.excess.annual_return)}</strong></div><div><span>验证期超额</span><strong>{percent(robustness.validation.validation.excess?.annual_return)}</strong></div><div><span>校正后 p 值</span><strong>{number(robustness.statistical.adjusted_p_value, 3)}</strong></div></div>
          <p className="mb-3 text-xs text-muted-foreground">{robustness.disclaimer}</p>
          <div className="analytics-table-wrap"><table className="analytics-table compact"><thead><tr><th>完整性检查</th><th>状态</th><th>说明</th></tr></thead><tbody>{robustness.checks.map((check) => <tr key={check.name}><td>{check.name}</td><td><Badge variant={check.passed ? "secondary" : "destructive"}>{check.passed ? "通过" : "未通过"}</Badge></td><td>{check.detail}</td></tr>)}</tbody></table></div>
        </> : <div className="analytics-empty">该 Run 无法形成稳健性结论，通常是基准数据覆盖不足。</div>}
      </div> : null}

      {tab === "source" ? <div className="workbench-body">
        <div className="backtest-section-heading"><div><strong><Code2 size={15} /> 回测页高级 Python</strong><span>修改的是项目同一份 draft；历史 Run 与已选 revision 始终保持不变。</span></div><Badge variant={localDirty ? "destructive" : project.dirty ? "outline" : "secondary"}>{localDirty ? "尚未保存" : project.dirty ? "草稿未冻结" : "与冻结版本一致"}</Badge></div>
        <Textarea className="pipeline-code-editor min-h-[620px] resize-y font-mono text-xs leading-5" spellCheck={false} value={source} disabled={!project.editable} onChange={(event) => setSource(event.target.value)} />
        <div className="mt-3 flex justify-end"><Button disabled={!project.editable || busy || !localDirty} onClick={() => void saveAsDraft()}><Save />保存到同一草稿</Button></div>
      </div> : null}

      {tab === "history" ? <div className="workbench-body">
        <div className="backtest-section-heading"><div><strong><History size={15} /> 历史 Run</strong><span>每条记录都绑定独立源码哈希和运行输入。</span></div><Button size="sm" variant="outline" onClick={() => void api.get<BacktestRecord[]>("/backtests?limit=50").then((rows) => setRuns(rows.filter((item) => item.strategy_id === project.id)))}><RefreshCw />刷新</Button></div>
        <div className="editor-list">{runs.map((run) => <button key={run.id} type="button" className={selectedBacktest === run.id ? "active" : ""} onClick={() => { setSelectedBacktest(run.id); setTab("performance") }}><strong>{run.start_date} → {run.end_date}</strong><small>{run.run_at.slice(0, 16).replace("T", " ")} · {run.profile}</small><em>{percent(run.total_return)} · Sharpe {number(run.sharpe)}</em></button>)}</div>
        {!runs.length ? <div className="analytics-empty">当前项目尚无历史回测。</div> : null}
      </div> : null}
    </Widget>
  )
}
