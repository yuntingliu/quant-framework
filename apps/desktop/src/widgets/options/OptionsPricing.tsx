import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { number, percent } from "@/lib/options"
import { OptionsModels } from "./OptionsModels"
import { useOptionsWorkspace } from "./OptionsContext"
import { Metric } from "./OptionsPrimitives"

interface PriceResult { bs_price:number; iv?:number; greeks:Record<string,number>|null; mc?:{price:number;standard_error:number;ci95:number[]|null} }
function ContractPricing(){
  const {language,t,data,selectedContract,setSelectedContract,day,field,rateValue}=useOptionsWorkspace()
  const L=(zh:string,en:string)=>language==="zh"?zh:en
  const eligible=data?.contracts.filter(r=>r.status==="eligible")??[]
  const row=eligible.find(r=>r.order_book_id===selectedContract)??eligible[0]
  const context=JSON.stringify([data?.underlying,day,field,rateValue,row?.order_book_id])
  const [volatility,setVolatility]=useState({context:"",value:""})
  const [includeMc,setIncludeMc]=useState(false)
  const [quantity,setQuantity]=useState("1")
  const [result,setResult]=useState<{key:string;data:PriceResult}|null>(null)
  const [busy,setBusy]=useState(false),[error,setError]=useState("")
  const vol=volatility.context===context?volatility.value:String(Number(((row?.iv??.2)*100).toFixed(4)))
  const request=row?{spot:row.spot,strike:row.strike,maturity:row.maturity,rate:row.rate,
    volatility:Number(vol)/100,dividend_yield:row.effective_dividend_yield??0,option_type:row.option_type,
    market_price:row.market_price,include_mc:includeMc,paths:20000,seed:42}:null
  const key=JSON.stringify([context,request])
  const value=result?.key===key?result.data:null
  async function calculate(){
    if(!request)return
    setBusy(true);setError("")
    try{setResult({key,data:await api.post<PriceResult>("/options/price",request)})}
    catch(reason){setError(reason instanceof Error?reason.message:String(reason))}finally{setBusy(false)}
  }
  return <section className="space-y-3 rounded-lg border border-border p-4" data-testid="option-bs-pricing">
    <h2 className="text-sm font-semibold">{L("Black–Scholes · 单合约定价", "Black–Scholes · contract valuation")}</h2>
    <p className="text-[11px] text-muted-foreground">{L("使用所选日期的真实合约条款与隐含持有成本。可以调整定价波动率；理论价格不是可成交报价。", "Uses dated contract terms and implied carry. Override valuation volatility as needed; theoretical values are not executable quotes.")}</p>
    {!row?<p className="text-xs">{L("所选日期没有可用于定价的合约。", "No eligible contracts for this valuation date.")}</p>:<>
      <label className="block text-xs">{L("定价合约", "Valuation contract")}<select aria-label={L("定价合约", "Valuation contract")} className="mt-1 block h-9 w-full rounded border border-input bg-background px-2 text-xs" value={row.order_book_id} onChange={e=>setSelectedContract(e.target.value)}>{eligible.map(r=><option key={r.order_book_id} value={r.order_book_id}>{language==="zh"?r.contract_name_zh:r.contract_name_en} · {r.order_book_id}</option>)}</select></label>
      <div className="flex flex-wrap items-end gap-3"><label className="text-xs">{L("定价波动率 %", "Valuation volatility %")}<Input aria-label={L("定价波动率 %", "Valuation volatility %")} type="number" step=".1" className="mt-1 w-32" value={vol} onChange={e=>setVolatility({context,value:e.target.value})}/></label><label className="text-xs">{L("估值张数", "Valuation quantity")}<Input aria-label={L("估值张数", "Valuation quantity")} type="number" step="1" className="mt-1 w-28" value={quantity} onChange={e=>setQuantity(e.target.value)}/></label><label className="flex h-9 items-center gap-2 text-xs"><input type="checkbox" checked={includeMc} onChange={e=>setIncludeMc(e.target.checked)}/>{L("标准 Monte Carlo 对照 · 20,000 路径", "Standard Monte Carlo check · 20,000 paths")}</label><Button size="sm" disabled={busy||!Number.isFinite(Number(vol))||Number(vol)<=0} onClick={()=>void calculate()}>{busy?L("正在计算…", "Calculating…"):L("计算合约理论价", "Calculate contract value")}</Button></div>
      <p className="text-[11px] text-muted-foreground">{L("现价", "Spot")} {number(row.spot,4)} · {L("行权价", "Strike")} {number(row.strike,4)} · {L("剩余日历天", "Calendar days")} {number(row.maturity*365,0)} · {L("年化利率", "Annual rate")} {percent(row.rate)} · {L("隐含持有成本 q", "Implied carry q")} {percent(row.effective_dividend_yield)} · {L("每张乘数", "Contract multiplier")} {number(row.multiplier,0)}</p>
      {value&&<><div className="grid grid-cols-2 gap-2 xl:grid-cols-4"><Metric label={L("理论价 · CNY／每单位", "Model price · CNY per unit")} value={number(value.bs_price,6)}/><Metric label={L("理论价值 · CNY／每张", "Model value · CNY per contract")} value={number(value.bs_price*row.multiplier,2)}/><Metric label={L("所选张数理论总值 · CNY", "Model value for quantity · CNY")} value={number(Number.isFinite(Number(quantity))?value.bs_price*row.multiplier*Number(quantity):null,2)}/><Metric label={L("市场参考价 · CNY／每单位", "Market reference · CNY per unit")} value={number(row.market_price,6)}/></div>
      {value.greeks&&<details className="rounded border border-border p-3"><summary className="cursor-pointer text-xs">{L("Greeks 与计量单位", "Greeks and units")}</summary><table className="mt-2 w-full text-right text-xs"><thead><tr><th className="p-2">{L("敏感度", "Sensitivity")}</th><th>{L("每单位", "Per unit")}</th><th>{L("每张", "Per contract")}</th></tr></thead><tbody>{["delta","gamma","vega_1pct","theta_day"].map(g=><tr key={g}><td className="p-2">{g==="vega_1pct"?"Vega / 1pp":g==="theta_day"?L("Theta / 日", "Theta / day"):g}</td><td>{number(value.greeks?.[g],6)}</td><td>{number((value.greeks?.[g]??0)*row.multiplier,4)}</td></tr>)}</tbody></table></details>}
      {value.mc&&<p role="status" className="text-xs">{L("标准 MC 每单位贴现价值", "Standard MC discounted value per unit")}: {number(value.mc.price,6)} · {L("标准误", "Standard error")}: {number(value.mc.standard_error,6)} · {L("95% 抽样区间", "95% sampling interval")}: {value.mc.ci95?.map(v=>number(v,6)).join(" – ")??L("样本不足", "Insufficient samples")}</p>}</>}
      {result&&!value&&<p className="text-xs text-amber-700">{L("合约或参数已变化，请重新计算。", "Contract or parameters changed; recalculate the value.")}</p>}
    </>}
    {error&&<p role="alert" className="text-xs text-red-700">{t(error)}</p>}
  </section>
}
export function OptionsPricing(){
  const {language,symbol,day,field,rateValue,revision,surfaceModel,setSurfaceModel,data,legs,activateCalibration,setTab}=useOptionsWorkspace()
  const L=(zh:string,en:string)=>language==="zh"?zh:en
  return <div className="space-y-4" data-testid="options-pricing"><ContractPricing key={`${symbol}:${day}:${field}:${rateValue}:${revision}`}/>
    <div className="flex items-center gap-2 text-xs"><label>{L("曲面定价模型", "Surface valuation model")}<select aria-label={L("曲面定价模型", "Surface valuation model")} className="ml-2 h-8 rounded border bg-background px-2" value={surfaceModel} onChange={e=>setSurfaceModel(e.target.value as "ssvi"|"svi")}><option value="ssvi">SSVI</option><option value="svi">SVI</option></select></label></div>
    <OptionsModels key={`${symbol}:${day}:${field}:${rateValue}:${revision}`} symbol={symbol} day={day} field={field} rate={rateValue} model={surfaceModel} initialStrike={data?.contracts[0]?.spot??3} canApply={legs.length>0&&legs.every(l=>l.underlying===symbol)} onApply={value=>{activateCalibration(value);setTab("portfolio")}}/>
  </div>
}
