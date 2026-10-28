import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useConfirm } from "@/hooks/useConfirm"
import { useOptionsI18n } from "@/lib/useOptionsI18n"
import { researchApi, type ProjectSpec, type ResearchResult } from "@/lib/optionsResearch"
import { number, percent } from "@/lib/options"
import { useStrategySdk } from "@/contexts/StrategySdkContext"

const researchWarnings: Record<string, [string, string]> = {
  fixed_etf_universe_and_correlated_assets: ["固定 ETF 池且多个标的跟踪相同指数，横截面并非独立样本。", "Fixed ETF universe; shared indices make cross-sectional assets dependent."],
  daily_price_and_finite_strike_literature_adaptations: ["日线价格、有限执行价和中国 ETF 属于文献迁移研究，非精确复现。", "Daily prices, finite strikes and Chinese ETFs are literature adaptations, not exact replications."],
  historical_vendor_revisions_not_bitemporal: ["历史供应商修订可能存在；按评估日截断不等于拥有原始历史发布版本。", "Vendor history may contain revisions; date truncation does not recreate original publication vintages."],
  multiple_testing_not_corrected: ["重复尝试不同指标后，本报告未对多重检验作校正。", "Repeated feature experiments are not corrected for multiple testing."],
  group_returns_overlap_and_are_not_a_tradeable_nav: ["分组收益区间重叠，不是可交易净值；交易成本须由标准回测计算。", "Group returns overlap and are not tradable NAV; use the standard backtest for costs."],
  short_out_of_sample_history: ["样本外时长较短，统计证据不足。", "Short out-of-sample history limits statistical evidence."],
  large_ic_requires_leakage_and_shared_index_exposure_review: ["IC 较高：需要复核时间对齐、共同指数暴露和小横截面效应。", "Large IC: review timing, shared-index exposure and small cross-sectional effects."],
  large_oos_r2_requires_alignment_review: ["样本外 R² 较高：需要进一步复核标签和时点对齐。", "Large OOS R²: further review return labels and information timing."],
}

function exportFile(name: string, value: unknown, markdown = false) {
  const url = URL.createObjectURL(new Blob([markdown ? String(value) : JSON.stringify(value, null, 2)], { type: markdown ? "text/markdown;charset=utf-8" : "application/json" }))
  const link = document.createElement("a"); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url)
}

export function OptionsResearchLab({ active, symbol, endDay, symbols, revision, jobActive, onRefresh }: {
  active: boolean; symbol: string; endDay: string; symbols: string[]; revision?: string; jobActive: boolean; onRefresh: () => Promise<unknown>
}) {
  const { language, t } = useOptionsI18n()
  const L = (zh: string, en: string) => language === "zh" ? zh : en
  const confirm = useConfirm()
  const sdk = useStrategySdk()
  const [start, setStart] = useState("2023-01-03")
  const [end, setEnd] = useState(endDay)
  const [chosen, setChosen] = useState(["option_smirk", "option_term_slope", "option_vrp_hf_30"])
  const [weights, setWeights] = useState<Record<string, number>>({ option_smirk: -1, option_term_slope: 1, option_vrp_hf_30: 1 })
  const [horizon, setHorizon] = useState(5)
  const [minTrain, setMinTrain] = useState(120)
  const [refit, setRefit] = useState(20)
  const [ridge, setRidge] = useState(1)
  const [embargo, setEmbargo] = useState(1)
  const [controls, setControls] = useState(true)
  const [allEtfs, setAllEtfs] = useState(true)
  const [historyField, setHistoryField] = useState("option_smirk")
  const [source, setSource] = useState<{ key: string; text: string } | null>(null)
  const [result, setResult] = useState<ResearchResult | null>(null)
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")
  const [projectName, setProjectName] = useState("ETF Option Research")
  const [topN, setTopN] = useState(3)
  const [frequency, setFrequency] = useState("weekly")
  const [projectPlan, setProjectPlan] = useState<{ key: string; spec: ProjectSpec; body: Record<string, unknown> } | null>(null)
  const [created, setCreated] = useState<string | null>(null)
  const catalog = useQuery({ queryKey: ["option-research-catalog", revision], queryFn: researchApi.catalog, enabled: active })
  const history = useQuery({ queryKey: ["option-feature-history", symbol, start, end, revision], queryFn: () => researchApi.features(symbol, start, end), enabled: active && catalog.data?.data.status === "ready" })
  const scope = { start, end, underlyings: allEtfs ? symbols : [symbol], confirm_python_execution: true }
  const sourceKey = JSON.stringify(scope)
  const request = { ...scope, fields: chosen, horizon, min_train: minTrain, refit_every: refit, ridge, embargo, controls }
  const runBody = { ...request }; delete (runBody as Partial<typeof request>).confirm_python_execution
  const selectedWeights = Object.fromEntries(chosen.map(field => [field, weights[field] ?? 1]))
  const baseProject = { name: projectName, start, end, underlyings: scope.underlyings, top_n: topN, frequency, weights: selectedWeights }
  const planKey = JSON.stringify([baseProject, result?.id])
  const label = (field: string) => { const spec = catalog.data?.features.find(s => s.field === field); return spec ? spec[language] : field }
  async function action(name: string, work: () => Promise<void>) {
    setBusy(name); setError("")
    try { await work() } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy("") }
  }
  function toggle(field: string) { setChosen(current => current.includes(field) ? current.filter(v => v !== field) : current.length < 8 ? [...current, field] : current) }
  async function previewProject(forecast: boolean) {
    const body = { ...baseProject, project_id: `option-${Date.now().toString(36)}`, ...(forecast ? { research_id: result?.id } : {}) }
    await action("project", async () => setProjectPlan({ key: planKey, body, spec: await researchApi.projectPlan(body) }))
  }
  async function createProject() {
    if (!projectPlan || projectPlan.key !== planKey) return
    if (!await confirm({ title: L("创建期权研究项目", "Create option research project"), description: L("保存下方可查看的因子和策略源码，并运行 SDK 输出探针。后续交易回测使用现有引擎。", "Save the visible factor and strategy source below and run SDK output probes. Trading backtests use the existing engine."), confirmText: L("创建项目", "Create project") })) return
    await action("project", async () => {
      const response = await researchApi.createProject({ ...projectPlan.body, confirm_save: true, confirm_python_execution: true })
      setCreated(response.project.id)
      await sdk.refresh()
    })
  }
  const disabled = Boolean(busy) || jobActive
  return <section className="space-y-4" data-testid="option-research">
    <div className="rounded border border-border p-3 text-xs"><strong>{L("文献 → 历史因子 → 样本外证据 → SDK 策略", "Literature → dated factors → OOS evidence → SDK strategy")}</strong><p className="mt-2 text-muted-foreground">{L("曲面形状是待检验的预测变量。RR25、文献 smirk、风险中性偏度和 VRP 分别定义；迁移研究不预设预测有效。", "Surface shape is a predictor hypothesis. RR25, literature smirk, Q skewness and VRP have distinct definitions; predictive value must be tested locally.")}</p></div>
    {(error || catalog.error) && <div role="alert" className="rounded border border-red-300 p-3 text-sm text-red-700">{t(error || catalog.error?.message || "")}</div>}
    <div className="flex flex-wrap items-end gap-3 rounded border border-border p-3">
      <label className="text-xs">{L("研究开始", "Research start")}<Input aria-label={L("研究开始", "Research start")} style={{ width: "10rem" }} className="mt-1" type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
      <label className="text-xs">{L("研究结束", "Research end")}<Input aria-label={L("研究结束", "Research end")} style={{ width: "10rem" }} className="mt-1" type="date" value={end} max={endDay} onChange={e => setEnd(e.target.value)} /></label>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={allEtfs} onChange={e => setAllEtfs(e.target.checked)} />{L("全部 ETF", "All ETFs")}</label>
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => void action("plan", async () => setSource({ key: sourceKey, text: (await researchApi.featurePlan(scope)).source }))}>{L("预览因子配方", "Preview feature recipe")}</Button>
      <Button size="sm" disabled={disabled || source?.key !== sourceKey} onClick={() => void action("build", async () => { if (await confirm({ title: L("构建历史因子", "Build dated features"), description: L("执行下方本地配方，按源数据指纹增量重建；不会发起供应商下载。", "Execute the local recipe below, rebuilding changed source fingerprints incrementally without vendor downloads."), confirmText: L("开始构建", "Build features") })) { await researchApi.build(scope); await onRefresh() } })}>{L("构建 / 更新因子", "Build / update features")}</Button>
      <div className="ml-auto text-xs text-muted-foreground">{L("已保存因子", "Saved feature rows")}: {number(catalog.data?.data.rows, 0)}</div>
    </div>
    {source?.key === sourceKey && <details><summary className="cursor-pointer text-xs">{L("完整本地 Python 配方", "Complete local Python recipe")}</summary><pre className="mt-2 max-h-60 overflow-auto rounded bg-muted p-3 text-[11px]">{source.text}</pre></details>}
    <details className="rounded border border-border p-3"><summary className="cursor-pointer text-sm font-medium">{L("文献、定义与复现边界", "Literature, definitions and replication boundaries")}</summary><div className="mt-3 space-y-3">{catalog.data?.papers.map(paper => <div key={paper.id} className="border-t border-border pt-3 text-xs"><a href={paper.url} target="_blank" rel="noreferrer" className="font-medium text-primary underline">{paper.authors} · {paper.title}</a><p className="mt-2 text-muted-foreground">{paper[language]}</p></div>)}</div></details>
    <details className="rounded border border-border p-3"><summary className="cursor-pointer text-sm font-medium">{L("展开指标与权重设置（最多8个）", "Expand feature and weight settings (up to 8)")}</summary><p className="mt-1 text-[11px] text-muted-foreground">{L("权重仅用于创建原始指标组合；预测模型系数在每个训练窗口独立估计。权重方向不按测试结果自动翻转。", "Weights apply to raw-feature strategy creation. Forecast coefficients are estimated within each training window; test results never flip strategy directions automatically.")}</p><div className="mt-3 grid gap-2 md:grid-cols-2">{catalog.data?.features.map(spec => { const count = catalog.data.coverage.find(c => c.field === spec.field); return <div className="rounded border border-border p-2 text-xs" key={spec.field}><label className="flex items-center gap-2"><input type="checkbox" checked={chosen.includes(spec.field)} disabled={!chosen.includes(spec.field) && chosen.length >= 8} onChange={() => toggle(spec.field)} /><span className="flex-1">{spec[language]}</span><span className="text-muted-foreground">{count?.valid ?? 0}/{count?.total ?? 0}</span>{chosen.includes(spec.field) && <Input aria-label={`${spec[language]} ${L("策略权重", "strategy weight")}`} style={{ width: "4rem", flexShrink: 0 }} type="number" step=".5" value={weights[spec.field] ?? 1} onChange={e => setWeights(current => ({ ...current, [spec.field]: Number(e.target.value) }))} />}</label><div className="mt-1 break-words font-mono text-[10px] text-muted-foreground">{spec.formula}</div></div> })}</div></details>
    <div className="rounded border border-border p-3"><div className="mb-2 flex items-center justify-between text-sm font-medium">{L("历史因子 · 当前 ETF", "Feature history · selected ETF")}<select className="max-w-[60%] rounded border bg-background p-1 text-xs" aria-label={L("历史指标", "History feature")} value={historyField} onChange={e => setHistoryField(e.target.value)}>{catalog.data?.features.map(f => <option key={f.field} value={f.field}>{f[language]}</option>)}</select></div>{history.error && <p className="text-xs text-amber-700">{t(history.error.message)}</p>}<ResponsiveContainer width="100%" height={210}><LineChart data={history.data?.points ?? []}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" tickFormatter={v => String(v).slice(2)} minTickGap={50} /><YAxis tickFormatter={v => number(Number(v), 3)} /><Tooltip /><Line dataKey={historyField} name={label(historyField)} stroke="#7c3aed" dot={false} isAnimationActive={false} connectNulls={false} /></LineChart></ResponsiveContainer></div>
    <div className="grid grid-cols-2 gap-3 rounded border border-border p-3 md:grid-cols-4">
      <label className="text-xs">{L("收益期限（交易日）", "Return horizon (sessions)")}<select className="mt-1 block h-8 w-full rounded border bg-background px-2" value={horizon} onChange={e => setHorizon(Number(e.target.value))}>{[1,5,20].map(v => <option key={v}>{v}</option>)}</select></label>
      {[{ zh: "最少训练交易日", en: "Minimum training sessions", value: minTrain, set: setMinTrain }, { zh: "重估间隔", en: "Refit interval", value: refit, set: setRefit }, { zh: "岭回归强度", en: "Ridge penalty", value: ridge, set: setRidge }, { zh: "隔离交易日", en: "Embargo sessions", value: embargo, set: setEmbargo }].map(item => <label className="text-xs" key={item.en}>{L(item.zh,item.en)}<Input aria-label={L(item.zh,item.en)} type="number" className="mt-1" value={item.value} onChange={e => item.set(Number(e.target.value))} /></label>)}
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={controls} onChange={e => setControls(e.target.checked)} />{L("控制 RV20 与20日动量", "Control RV20 and 20d momentum")}</label>
      <Button size="sm" disabled={disabled || !chosen.length || catalog.data?.data.status !== "ready"} onClick={() => void action("run", async () => { setResult(await researchApi.run(runBody)); window.dispatchEvent(new Event("alphalab:refreshData")) })}>{busy === "run" ? L("计算中…", "Computing…") : L("运行样本外检验", "Run out-of-sample test")}</Button>
      <select className="h-8 rounded border bg-background px-2 text-xs" aria-label={L("历史研究结果", "Saved experiments")} value="" onChange={e => { if(e.target.value) void action("load", async () => setResult(await researchApi.load(e.target.value))) }}><option value="">{L("载入已保存研究", "Load saved experiment")}</option>{catalog.data?.runs.map(run => <option key={run.id} value={run.id}>{run.config.horizon}d · {run.scope.start} → {run.scope.end} · {run.id.slice(0,6)} · {run.config.fields.join(", ")}</option>)}</select>
    </div>
    <p className="text-[11px] text-muted-foreground">{L("t日收盘指标 → t+1开盘至t+h收盘的复权收益。训练仅使用标签已在测试窗口前结束的样本；未知尾部收益保留为未评分预测。", "Close-t predictors → adjusted next-open to t+h close returns. Training uses only labels completed before the test block; unknown tail returns remain unscored forecasts.")}</p>
    {result && <div className="space-y-3" data-testid="option-research-result">
      <p className="text-xs text-muted-foreground">{L("当前报告", "Displayed experiment")}: {result.scope.start} → {result.scope.end} · {result.config.horizon}d · {result.config.fields.map(label).join(" / ")}</p>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">{[
        [L("样本外 Rank IC", "OOS rank IC"), number(result.rank_ic.mean, 4)], [L("IC 的 HAC t值", "IC HAC t-stat"), number(result.rank_ic.hac_t, 2)],
        [L("相对控制模型的样本外 R²", "OOS R² vs controls"), percent(result.oos_r2_vs_controls)], [L("已评分 / 未知收益", "Scored / unknown returns"), `${result.coverage.scored_rows} / ${result.coverage.unscored_rows}`],
      ].map(([title,value]) => <div className="rounded border border-border p-3" key={title}><div className="text-xs text-muted-foreground">{title}</div><div className="mt-2 font-mono text-lg">{value}</div></div>)}</div>
      <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => exportFile(`option-research-${result.id}.json`, result)}>{L("导出完整 JSON", "Export full JSON")}</Button><Button size="sm" variant="outline" onClick={() => exportFile(`option-research-${result.id}.md`, `# ${L("期权收益预测研究", "Option return prediction research")}\n\n${L("样本外 Rank IC", "OOS rank IC")}: ${number(result.rank_ic.mean,4)}\n\n${L("样本外 R²", "OOS R²")}: ${percent(result.oos_r2_vs_controls)}\n\n${result.scope.start} → ${result.scope.end} · ${result.config.horizon}d\n\n${result.scope.underlyings.join(", ")}\n\n${result.config.fields.map(label).join(" / ")}\n\n${L("训练交易日 / 重估间隔 / 岭强度 / 隔离日", "Training sessions / refit / ridge / embargo")}: ${result.config.min_train} / ${result.config.refit_every} / ${result.config.ridge} / ${result.config.embargo}\n\n${L("t日收盘指标；复权次日开盘至t+h收盘收益；未知尾部收益保留为未评分预测。", result.label_convention)}\n\n${L("训练系数使用日期聚类 Newey-West 推断；样本外 IC 使用期限滞后 HAC。岭回归的测试预测不作选择后显著性声明。", result.inference)}\n\n${result.warnings.map(w=>`- ${researchWarnings[w]?.[language==="zh"?0:1] ?? w}`).join("\n")}\n\n${catalog.data?.papers.map(p=>`[${p.authors}](${p.url})`).join("\n\n")}`,true)}>{L("导出研究说明", "Export research report")}</Button></div>
      <div className="rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800">{result.warnings.map(w => <p className="mb-1" key={w}>{researchWarnings[w]?.[language === "zh" ? 0 : 1] ?? w}</p>)}</div>
      <div className="rounded border border-border p-3"><h3 className="text-sm font-medium">{L("样本外每日横截面 Rank IC", "Daily out-of-sample cross-sectional rank IC")}</h3><ResponsiveContainer width="100%" height={220}><LineChart data={result.daily_ic}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" minTickGap={50} /><YAxis domain={[-1,1]} /><Tooltip /><Line dataKey="rank_ic" stroke="#0d9488" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>
      <div className="overflow-auto rounded border border-border p-3"><h3 className="mb-2 text-sm font-medium">{L("指标分组收益（每个标签区间，非净值）", "Predictor group returns (per label horizon, not NAV)")}</h3><table className="w-full whitespace-nowrap text-right text-xs"><thead><tr>{[L("指标", "Feature"),L("低组", "Low"),L("中组", "Middle"),L("高组", "High"),L("高减低", "High–low"),"HAC t"].map(v=><th key={v} className="p-2">{v}</th>)}</tr></thead><tbody>{result.groups.map(row=><tr key={row.field}><td className="p-2">{label(row.field)}</td><td>{percent(row.low)}</td><td>{percent(row.middle)}</td><td>{percent(row.high)}</td><td>{percent(row.high_minus_low.mean)}</td><td>{number(row.high_minus_low.hac_t)}</td></tr>)}</tbody></table></div>
      <details className="rounded border border-border p-3"><summary className="cursor-pointer text-sm">{L("训练系数与日期聚类 HAC 推断（非样本外显著性）", "Training coefficients and date-clustered HAC inference (not OOS significance)")}</summary>{result.training_inference.map(row => <p className="mt-2 text-xs" key={row.field}>{label(row.field)} · β/SD {number(row.coefficient_per_train_sd,4)} · HAC t {number(row.hac_t)}</p>)}</details>
      <details className="rounded border border-border p-3"><summary className="cursor-pointer text-sm">{L("训练截止审计与最新预测", "Training-cutoff audit and latest forecasts")}</summary><div className="mt-3 overflow-auto"><table className="w-full whitespace-nowrap text-right text-xs"><thead><tr>{[L("测试开始", "Test start"),L("测试结束", "Test end"),L("训练标签截止", "Training label cutoff"),L("训练交易日", "Training sessions")].map(v=><th className="p-2" key={v}>{v}</th>)}</tr></thead><tbody>{result.folds.slice(-8).map(f=><tr key={f.test_start}><td>{f.test_start}</td><td>{f.test_end}</td><td>{f.training_label_end}</td><td>{f.training_sessions}</td></tr>)}</tbody></table></div><div className="mt-3 overflow-auto"><table className="w-full whitespace-nowrap text-right text-xs"><thead><tr>{[L("日期", "Date"),"ETF",L("预测收益", "Predicted return"),L("实际收益", "Actual return"),L("训练标签截止", "Training label cutoff")].map(v=><th className="p-2" key={v}>{v}</th>)}</tr></thead><tbody>{result.predictions.slice(-18).map(row=><tr key={`${row.date}-${row.symbol}`}><td>{row.date}</td><td>{row.symbol}</td><td>{percent(row.prediction)}</td><td>{percent(row.target_return)}</td><td>{row.training_label_end}</td></tr>)}</tbody></table></div></details>
    </div>}
    <div className="space-y-3 rounded border border-border p-3"><h3 className="text-sm font-medium">{L("接入因子与策略工作台", "Use in Factor and Strategy workbenches")}</h3><div className="flex flex-wrap items-end gap-3"><label className="text-xs">{L("新项目名称", "New project name")}<Input className="mt-1" value={projectName} onChange={e=>setProjectName(e.target.value)} /></label><label className="text-xs">Top N<Input aria-label="Top N" style={{width:"5rem"}} className="mt-1" type="number" value={topN} onChange={e=>setTopN(Number(e.target.value))} /></label><select aria-label={L("调仓频率", "Rebalance frequency")} className="h-8 rounded border bg-background px-2 text-xs" value={frequency} onChange={e=>setFrequency(e.target.value)}><option value="weekly">{L("周末", "Weekly close")}</option><option value="monthly">{L("月末", "Monthly close")}</option></select><Button size="sm" variant="outline" disabled={disabled || !chosen.length} onClick={()=>void previewProject(false)}>{L("预览指标组合策略", "Preview feature strategy")}</Button><Button size="sm" variant="outline" disabled={disabled || !result} onClick={()=>void previewProject(true)}>{L("预览样本外预测策略", "Preview OOS forecast strategy")}</Button></div>
      {projectPlan?.key === planKey && <><details open><summary className="cursor-pointer text-xs">{L("将保存的策略函数与因子源码", "Strategy functions and factor source to save")}</summary><pre className="mt-2 max-h-80 overflow-auto rounded bg-muted p-3 text-[11px]">{projectPlan.spec.function_replacements.map(f=>f.function_source).join("\n")}{"\n"}{projectPlan.spec.factors.map(f=>`${f.factor_id}:\n${f.body}`).join("\n\n")}</pre></details><Button size="sm" disabled={disabled} onClick={()=>void createProject()}>{L("创建可编辑 SDK 项目", "Create editable SDK project")}</Button></>}
      {created && <div role="status" className="flex flex-wrap items-center gap-2 text-xs">{L("项目已创建", "Project created")}: {created}<Button size="sm" variant="outline" onClick={()=>void action("open",async()=>{ await sdk.openProject(created);window.dispatchEvent(new CustomEvent("alphalab:switchMode", { detail: "factor" })) })}>{L("打开因子工作台", "Open Factor workbench")}</Button><Button size="sm" variant="outline" onClick={()=>void action("open",async()=>{ await sdk.openProject(created);window.dispatchEvent(new CustomEvent("alphalab:switchMode", { detail: "validation" })) })}>{L("打开验证与回测", "Open Validation & Backtest")}</Button></div>}
      <p className="text-[11px] text-muted-foreground">{L("项目中的 ETF 持仓、下一开盘成交、费用、停牌与涨跌停仍由现有引擎处理。期权预测字段按实验版本隔离，冻结运行不会改读另一个实验。", "ETF holdings, next-open fills, costs, suspensions and price limits stay in the existing engine. Forecast fields are experiment-scoped, so frozen runs do not read another experiment.")}</p>
    </div>
    {busy && <p role="status" className="text-xs text-muted-foreground">{L("正在处理…", "Working…")}</p>}
    <p className="text-[10px] text-muted-foreground">{t("日线 · 真实数据")}</p>
  </section>
}
