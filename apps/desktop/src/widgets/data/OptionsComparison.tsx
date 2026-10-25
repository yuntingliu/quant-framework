import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { researchApi } from "@/lib/optionsResearch"
import { number, percent } from "@/lib/options"
import { useOptionsI18n } from "@/lib/useOptionsI18n"

export function OptionsComparison({ids,revision,active}:{ids:string[];revision?:string;active:boolean}) {
  const {language,t}=useOptionsI18n()
  const L=(zh:string,en:string)=>language==="zh"?zh:en
  const [open,setOpen]=useState(false)
  const comparison=useQuery({queryKey:["option-comparison",ids.join(","),revision],queryFn:()=>researchApi.comparison(ids),enabled:active&&open&&ids.length>0})
  const result=comparison.data
  const colors=["#0d9488","#dc2626","#2563eb","#d97706","#7c3aed","#475569"]
  return <details className="rounded border border-border p-3" onToggle={e=>setOpen(e.currentTarget.open)} data-testid="option-common-comparison"><summary className="cursor-pointer text-sm font-medium">{L("同日期策略比较、基准与指数暴露", "Common-date comparison, benchmarks and index exposures")}</summary>
    {comparison.isFetching&&<p role="status" className="mt-3 text-xs">{L("正在对齐真实回测日期并计算基准…", "Aligning saved run dates and computing benchmarks…")}</p>}
    {comparison.error&&<p role="alert" className="mt-3 text-xs text-red-600">{t(comparison.error.message)}</p>}
    {result?.period&&<div className="mt-3 space-y-3"><p className="text-xs text-muted-foreground">{result.period.start} → {result.period.end} · {result.period.sessions} {L("个共同有效交易日", "shared valid sessions")}</p><p className="text-xs">{L("ETF 池等权基准", "ETF-pool equal-weight benchmark")}: {percent(result.benchmarks?.pool_equal_weight)} · {L("每指数一只 ETF 等权基准", "One ETF per index, equal-weight benchmark")}: {percent(result.benchmarks?.index_equal_weight)}</p>
      <div className="overflow-auto"><table className="w-full whitespace-nowrap text-right text-xs [&_td]:p-2 [&_th]:p-2 [&_tbody_tr]:border-t [&_tbody_tr]:border-border"><thead><tr>{[L("策略", "Strategy"),L("区间净收益", "Period net return"),L("相对 ETF 池", "Relative to ETF pool"),L("相对指数基准", "Relative to index benchmark"),L("市场代理 Beta", "Market-proxy beta"),L("年化截距", "Annualized intercept"),L("截距 HAC t", "Intercept HAC t")].map(v=><th key={v}>{v}</th>)}</tr></thead><tbody>{result.rows.map(r=><tr key={r.backtest_id}><td>{language==="zh"?r.name_zh:r.name_en}</td><td>{percent(r.total_return)}</td><td>{percent(r.pool_relative_return)}</td><td>{percent(r.index_relative_return)}</td><td>{number(r.market_proxy_attribution.betas.MKT)}</td><td>{percent(r.market_proxy_attribution.alpha_annualized)}</td><td>{number(r.market_proxy_attribution.estimates?.alpha?.t_stat)}</td></tr>)}</tbody></table></div>
      <ResponsiveContainer width="100%" height={250}><LineChart data={result.curve}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="date" minTickGap={60} tick={{fontSize:10}}/><YAxis domain={["auto","auto"]} tick={{fontSize:10}}/><Tooltip formatter={v=>number(Number(v),4)}/><Legend/>{result.rows.map((r,i)=><Line key={r.backtest_id} dataKey={r.backtest_id} name={language==="zh"?r.name_zh:r.name_en} stroke={colors[i%colors.length]} dot={false} isAnimationActive={false}/>)}<Line dataKey="pool_equal_weight" name={L("ETF 池基准", "ETF-pool benchmark")} stroke="#334155" strokeDasharray="4 3" dot={false}/><Line dataKey="index_equal_weight" name={L("指数等权基准", "Index-balanced benchmark")} stroke="#a855f7" strokeDasharray="4 3" dot={false}/></LineChart></ResponsiveContainer>
      <details><summary className="cursor-pointer text-xs">{L("历史平均指数仓位", "Historical average index weights")}</summary>{result.rows.map(r=><p className="mt-2 text-xs" key={r.backtest_id}><strong>{language==="zh"?r.name_zh:r.name_en}</strong> · {r.index_exposures.map(e=>`${language==="zh"?e.name_zh:e.name_en} ${percent(e.mean_weight)}`).join(" / ")}</p>)}</details>
      <p className="text-[11px] text-muted-foreground">{L("仅比较共同有效日期。基准复用标准计算，采用收盘至收盘收益并标记在区间结束日，基准未扣费用、策略已扣费。月度 CAPM 使用 ETF 池市场代理和零无风险利率，截距为描述性归因；不能作为已证明超额收益的依据。", "Only shared valid dates are compared. Benchmarks use the canonical close-to-close helper, labeled at period end and are gross of costs; strategies are net. Monthly CAPM uses an ETF-pool market proxy and zero risk-free rate. Its intercept is descriptive attribution, not proof of alpha.")}</p>
    </div>}
  </details>
}
