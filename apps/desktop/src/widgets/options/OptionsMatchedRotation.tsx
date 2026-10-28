import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { useConfirm } from "@/hooks/useConfirm"
import { useOptionsI18n } from "@/lib/useOptionsI18n"
import { researchApi, type StrategyComparison } from "@/lib/optionsResearch"
import { percent } from "@/lib/options"

export function OptionsMatchedRotation({suiteId,horizon,active,onSelect}:{suiteId:string;horizon:number;active:boolean;onSelect:(id:string)=>void}){
  const {language,t}=useOptionsI18n()
  const L=(zh:string,en:string)=>language==="zh"?zh:en
  const confirm=useConfirm(),queryClient=useQueryClient()
  const [busy,setBusy]=useState(false),[error,setError]=useState("")
  const key=["option-matched-rotation",suiteId,horizon]
  const query=useQuery({queryKey:key,queryFn:()=>researchApi.rotation(suiteId,horizon),enabled:active,staleTime:30000,
    refetchInterval:q=>q.state.data?.jobs?.some(j=>["queued","running"].includes(j.status))?5000:false})
  const value=query.data
  const completed=value?.jobs?.filter(j=>j.status==="succeeded").map(j=>j.backtest_id).join(",")??""
  useEffect(()=>{if(completed)void queryClient.invalidateQueries({queryKey:["option-strategies"]})},[completed,queryClient])
  async function run(){
    if(!await confirm({title:L("运行同口径 ETF 轮动对照", "Run matched ETF rotation backtests"),description:L("将保存两份可查看的 SDK 策略并执行本地 Python 回测：控制模型与期权增强模型。两者均每周选前3只、等权、次日开盘成交，使用标准引擎的费用与可交易性约束，不访问供应商。", "Saves two reviewable SDK strategies and executes local Python backtests: controls-only versus option-augmented forecasts. Both select the top three weekly, allocate equally and fill at the next open under canonical costs and tradability checks, without vendor access."),confirmText:L("保存并运行对照", "Save and run comparison")}))return
    setBusy(true);setError("")
    try{queryClient.setQueryData(key,await researchApi.runRotation(suiteId,horizon));await query.refetch()}
    catch(reason){setError(reason instanceof Error?reason.message:String(reason))}finally{setBusy(false)}
  }
  function summary(comparison:StrategyComparison,title:string){
    const controlId=value?.jobs?.find(j=>j.role==="controls")?.backtest_id
    const modelId=value?.jobs?.find(j=>j.role==="model")?.backtest_id
    const control=comparison.rows.find(r=>r.backtest_id===controlId),model=comparison.rows.find(r=>r.backtest_id===modelId)
    const relative=control&&model&&control.total_return> -1?(1+model.total_return)/(1+control.total_return)-1:null
    return <div className="space-y-2 rounded border border-border p-3"><h4 className="text-xs font-semibold">{title} · {comparison.period?.start} → {comparison.period?.end}</h4><div className="grid grid-cols-3 gap-2 text-xs">{[[L("控制模型净收益", "Controls-only net return"),control?.total_return],[L("期权模型净收益", "Option-model net return"),model?.total_return],[L("相对控制模型财富增幅", "Relative wealth vs controls"),relative]].map(([name,v])=><div key={String(name)}><div className="text-muted-foreground">{String(name)}</div><strong className="mt-1 block font-mono text-base">{percent(v as number|null|undefined)}</strong></div>)}</div><p className="text-[11px] text-muted-foreground">{L("共同有效日期", "Shared valid dates")}: {comparison.period?.sessions} · {L("只有预测分数不同；盈利不等于期权指标增量有效。", "Only the forecast score differs; profit does not establish incremental option-signal value.")}</p>{comparison.rows.some(r=>r.execution_reliable===false||r.research_valid===false||r.execution_warnings?.some(w=>w.startsWith("LOW_EXECUTION_FIDELITY")))&&<p className="text-xs text-amber-700 dark:text-amber-400">{L("存在成交／目标跟踪警告，请在规则与成交详情中核验；收益使用实际成交。", "Review the reported execution warnings in the rules and fills; returns use actual fills.")}</p>}</div>
  }
  return <section className="space-y-3 rounded-lg border border-border p-4" data-testid="matched-etf-rotation">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">{L("预测是否带来轮动增益？控制模型对照", "Do forecasts improve rotation? Matched control comparison")}</h3><Button size="sm" variant="outline" disabled={busy||value?.jobs?.some(j=>["queued","running"].includes(j.status))} onClick={()=>{if(value?.pair&&!value.jobs?.some(j=>["failed","interrupted","unavailable"].includes(j.status)))void query.refetch();else void run()}}>{value?.pair?(value.jobs?.some(j=>["failed","interrupted","unavailable"].includes(j.status))?L("恢复失败回测", "Resume failed backtests"):L("刷新对照", "Refresh comparison")):L("创建并运行轮动对照", "Create and run matched rotations")}</Button></div>
    <p className="text-xs text-muted-foreground">{L("共同完整样本、相同训练与固定保留期，控制模型仅含 RV20、20日动量和 ETF 截距；增强模型加入所列期权指标。两者使用同一标准回测引擎、周末收盘信号、前3只等权、次日开盘成交与相同费用规则。", "Both models share complete observations, training and the reserved period. Controls use RV20, 20-day momentum and ETF intercepts; the augmented model adds the listed option signals. Both use the canonical engine, weekly final-close signals, equal top-three weights, next-open fills and identical cost rules.")}</p>
    {query.isFetching&&!value&&<p role="status" className="text-xs">{L("正在读取轮动对照…", "Loading matched rotation results…")}</p>}
    {(error||query.error)&&<p role="alert" className="text-xs text-red-700">{t(error||query.error?.message||"")}</p>}
    {value?.pair===null&&<p className="rounded border border-dashed p-3 text-xs">{L("当前期限尚未运行同口径轮动回测；预测统计结果不能代替交易结果。", "No matched rotation backtests for this horizon yet; forecast statistics do not substitute for trading results.")}</p>}
    {value?.jobs?.map(j=><div key={j.role} className="flex flex-wrap items-center gap-2 text-xs"><strong>{j.role==="controls"?L("控制模型", "Controls-only"):L("期权增强模型", "Option-augmented")}</strong><span>{({queued:L("已排队", "Queued"),running:L("回测中", "Running"),succeeded:L("已完成", "Completed"),failed:L("失败", "Failed"),interrupted:L("已中断", "Interrupted")} as Record<string,string>)[j.status]??j.status}</span>{j.error_summary&&<span role="alert" className="text-red-700">{t(j.error_summary)}</span>}{j.backtest_id&&<button className="text-primary underline" onClick={()=>onSelect(j.backtest_id!)}>{L("查看规则与成交", "Inspect rules and fills")}</button>}</div>)}
    {value?.comparison&&summary(value.comparison,L("全部样本外区间", "Full OOS interval"))}
    {value?.holdout_comparison&&<>{summary(value.holdout_comparison,L("固定保留期净值片段", "Frozen-holdout NAV segment"))}<p className="text-[11px] text-muted-foreground">{L("保留期片段延续期初已有持仓，未重新注入现金或重新选模型；属于历史固定切分验证。", "The reserved segment retains entering holdings, without fresh capital or model reselection; it is fixed-split historical validation.")}</p></>}
  </section>
}
