import { useLayoutEffect, useRef } from "react"
import { Activity, ListTree, ChartNoAxesCombined, Calculator, Layers3, FlaskConical, Database, RefreshCw, Loader2, SlidersHorizontal } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Widget } from "@/widgets/Widget"
import { number } from "@/lib/options"
import type { OptionSection } from "@/lib/optionsWorkspace"
import { OptionsContext, useOptionsController, useOptionsWorkspace } from "./OptionsContext"
import { OptionsChain } from "./OptionsChain"
import { OptionsVolatility } from "./OptionsVolatility"
import { OptionsPortfolio } from "./OptionsPortfolio"
import { OptionsPricing } from "./OptionsPricing"
import { OptionsResearch } from "./OptionsResearch"
import { OptionsDataManagement } from "./OptionsDataManagement"

const sections=[
  {id:"chain",label:"期权链",icon:ListTree}, {id:"volatility",label:"波动率研究",icon:ChartNoAxesCombined},
  {id:"models",label:"定价与模型",icon:Calculator}, {id:"portfolio",label:"组合与风险",icon:Layers3},
  {id:"research",label:"策略与实证",icon:FlaskConical}, {id:"data",label:"数据管理",icon:Database},
] as const
const selectClass="h-8 max-w-full rounded-md border border-input bg-background px-2 text-xs"

function OptionsHeader(){
  const {t,language,tab,setTab,symbol,setSymbol,day,setSelectedDay,setExpiry,field,setField,listing,universe,
    manualRate,setManualRate,rate,setRate,data,legs,queryClient}=useOptionsWorkspace()
  const L=(zh:string,en:string)=>language==="zh"?zh:en
  const invalid=data?.status_counts.source_quote_invalid??0
  return <header className="shrink-0 space-y-3 border-b border-border bg-card px-4 py-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><Activity className="h-5 w-5 text-primary"/><h1 className="text-base font-semibold">{t("ETF 期权研究")}</h1><span className="rounded border border-border px-2 py-0.5 text-[10px] text-muted-foreground">{t("日线 · 真实数据")}</span></div><div className="flex items-center gap-2"><Button size="sm" variant={tab==="portfolio"?"secondary":"ghost"} onClick={()=>setTab("portfolio")}>{t("组合草稿")} ({legs.length})</Button><Button size="sm" variant="ghost" aria-label={t("刷新期权数据")} onClick={()=>void queryClient.invalidateQueries({predicate:q=>typeof q.queryKey[0]==="string"&&q.queryKey[0].startsWith("option-")})}><RefreshCw className={`h-3.5 w-3.5 ${universe.isFetching?"animate-spin":""}`}/>{t("刷新")}</Button></div></div>
    <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
      <label className="text-[11px] text-muted-foreground">{t("标的 ETF")}<select aria-label={t("标的 ETF")} className={`${selectClass} mt-1 block min-w-44 text-foreground`} value={symbol} onChange={e=>{setSymbol(e.target.value);setSelectedDay("");setExpiry("")}}>{universe.data?.underlyings.map(item=><option key={item.symbol} value={item.symbol}>{t(item.name)} · {item.symbol.slice(0,6)}</option>)}</select></label>
      <label className="text-[11px] text-muted-foreground">{t("分析日期")}<select aria-label={t("分析日期")} className={`${selectClass} mt-1 block text-foreground`} value={day} onChange={e=>{setSelectedDay(e.target.value);setExpiry("")}}>{listing?.dates.slice().reverse().map(d=><option key={d}>{d}</option>)}</select></label>
      <label className="text-[11px] text-muted-foreground">{t("价格口径")}<select aria-label={t("价格口径")} className={`${selectClass} mt-1 block text-foreground`} value={field} onChange={e=>setField(e.target.value as "close"|"settlement")}><option value="close">{t("收盘价")}</option><option value="settlement">{t("结算价")}</option></select></label>
      <details className="relative text-xs"><summary className="flex h-8 cursor-pointer list-none items-center gap-1 rounded-md border border-border px-2"><SlidersHorizontal className="h-3.5 w-3.5"/>{t("估值设置")}</summary><div className="absolute left-0 top-10 z-40 w-64 space-y-3 rounded-lg border border-border bg-popover p-3 shadow-lg"><label className="block">{t("贴现曲线")}<select aria-label={t("贴现曲线")} className={`${selectClass} mt-1 block w-full`} value={manualRate?"manual":"market"} onChange={e=>setManualRate(e.target.value==="manual")}><option value="market">{t("同期中债国债曲线")}</option><option value="manual">{t("手动年化利率")}</option></select></label>{manualRate&&<label className="block">{t("利率 %")}<input aria-label={t("手动年化利率")} type="number" step=".1" className={`${selectClass} mt-1 w-full`} value={rate} onChange={e=>setRate(e.target.value)}/></label>}<p className="text-[10px] text-muted-foreground">{t("按到期日插值贴现")}</p></div></details>
      <div className="ml-auto flex flex-col items-end text-[11px] text-muted-foreground"><span>{L("数据截至", "Data through")} {listing?.latest??"—"}</span><button className="mt-1 text-primary hover:underline" onClick={()=>setTab("data")}>{L("覆盖与质量", "Coverage & quality")} · {number(listing?.sessions,0)} {L("交易日", "sessions")}{invalid>0?` · ${L("本日隔离", "quarantined today")} ${invalid}`:""}</button></div>
    </div>
  </header>
}

function OptionsContents(){
  const value=useOptionsWorkspace()
  const mainRef=useRef<HTMLElement>(null)
  const {t,tab,setTab,legs,language,universe,analysis,error,day,data,symbol,revision,activeJob}=value
  const L=(zh:string,en:string)=>language==="zh"?zh:en
  useLayoutEffect(()=>{mainRef.current?.scrollTo({top:0,left:0})},[tab])
  return <div className="flex h-full min-h-0 flex-col" data-testid="options-workbench">
    <OptionsHeader/>
    <div className="flex min-h-0 flex-1 flex-col xl:flex-row">
      <nav aria-label={t("期权研究工作区")} className="flex shrink-0 gap-1 overflow-x-auto border-b border-border bg-muted/20 p-2 xl:w-40 xl:flex-col xl:border-b-0 xl:border-r">{sections.map(({id,label,icon:Icon})=><button key={id} type="button" aria-current={tab===id?"page":undefined} className={`flex shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-left text-xs transition-colors ${tab===id?"bg-primary/10 font-semibold text-primary":"text-muted-foreground hover:bg-muted hover:text-foreground"}`} onClick={()=>setTab(id as OptionSection)}><Icon className="h-4 w-4 shrink-0"/><span className="xl:whitespace-normal">{t(label)}</span>{id==="portfolio"&&legs.length>0&&<span className="rounded bg-primary/10 px-1 font-mono text-[10px]">{legs.length}</span>}</button>)}</nav>
      <main ref={mainRef} className="min-h-0 min-w-0 flex-1 space-y-4 overflow-auto p-4" aria-label={t(sections.find(s=>s.id===tab)?.label??"期权链")}>
        {(error||universe.error||(analysis.error&&!["data","research"].includes(tab)))&&<p role="alert" className="rounded-lg border border-red-300 bg-red-500/5 p-3 text-xs text-red-700 dark:text-red-300">{t(error||universe.error?.message||analysis.error?.message||"")}</p>}
        {universe.isPending&&<p role="status" className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin"/>{L("正在读取历史覆盖…", "Loading historical coverage…")}</p>}
        {!["data","research"].includes(tab)&&analysis.isFetching&&<p role="status" className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin"/>{t("正在计算期权链与波动率…")}</p>}
        {!day&&!universe.isPending&&tab!=="data"&&<div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground"><p>{t("当前标的尚无数据。打开“历史同步”选择日期范围后运行。")}</p><Button className="mt-3" size="sm" onClick={()=>setTab("data")}>{t("数据管理")}</Button></div>}
        {tab==="chain"&&data&&<OptionsChain/>}
        {tab==="volatility"&&data&&<OptionsVolatility/>}
        {data&&<div className={tab==="models"?"":"hidden"}><OptionsPricing/></div>}
        {tab==="portfolio"&&data&&<><div><h2 className="text-sm font-semibold">{t("组合与风险")}</h2><p className="mt-1 text-[11px] text-muted-foreground">{L("组合草稿自动保存在本机。以下组合市值、Greeks 和情景盈亏均按张数及当日乘数汇总。", "Drafts are saved on this device. Portfolio values, Greeks and scenario P&L aggregate quantities and dated multipliers.")}</p></div><OptionsPortfolio/></>}
        <div className={tab==="research"?"":"hidden"}><OptionsResearch active={tab==="research"} symbol={symbol} endDay={day} symbols={universe.data?.underlyings.map(v=>v.symbol)??[]} revision={revision} jobActive={Boolean(activeJob)} onRefresh={()=>universe.refetch()}/></div>
        <div className={tab==="data"?"":"hidden"}><OptionsDataManagement active={tab==="data"}/></div>
      </main>
    </div>
  </div>
}
export function OptionsWorkbench(){const controller=useOptionsController();return <Widget headerless className="overflow-hidden"><OptionsContext.Provider value={controller}><OptionsContents/></OptionsContext.Provider></Widget>}
