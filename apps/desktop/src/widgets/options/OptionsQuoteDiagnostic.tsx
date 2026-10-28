import { Button } from "@/components/ui/button"
import { number, type OptionRow } from "@/lib/options"
import { useOptionsWorkspace } from "./OptionsContext"

export function OptionsQuoteDiagnostic({row}:{row:OptionRow}) {
  const {language,field,setField}=useOptionsWorkspace()
  const L=(zh:string,en:string)=>language==="zh"?zh:en
  const d=row.iv_diagnostic
  if(row.status!=="invalid_iv"||!d)return null
  const labels:Record<string,string>={
    below_model_lower:L("参考价低于模型下界，IV 无解", "Reference below model lower bound; no IV solution"),
    at_or_above_model_upper:L("参考价达到或超过模型上界，IV 无解", "Reference at or above model upper bound; no IV solution"),
    at_zero_volatility_boundary:L("参考价处于零波动率边界，未报告 IV", "Reference at zero-volatility boundary; IV withheld"),
    solver_limit:L("IV 超出求解范围", "IV outside solver range"),
  }
  return <div className="mt-3 space-y-3 rounded border border-amber-300 bg-amber-500/5 p-3" data-testid="option-quote-diagnostic">
    <h4 className="text-sm font-medium">{labels[d.code]??L("IV 诊断", "IV diagnostic")}</h4>
    <dl className="grid grid-cols-2 gap-3 text-xs lg:grid-cols-4">{[
      [L("当前参考价／单位", "Selected reference / unit"),row.market_price],
      [L("模型下界／单位", "Model lower bound / unit"),d.lower_bound],
      [L("模型上界／单位", "Model upper bound / unit"),d.upper_bound],
      [L("参考价减下界／每张 CNY", "Reference minus lower bound / contract CNY"),d.distance_from_lower*row.multiplier],
    ].map(([label,value])=><div key={String(label)}><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 font-mono">{number(Number(value),6)}</dd></div>)}</dl>
    <p className="text-xs">{d.carry_mode==="put_call_forward"?L("边界使用同到期认购认沽配对拟合的远期及贴现曲线。日线成交不同步、参考价口径或远期拟合误差都可能造成不一致；这条提示本身不能证明源报价错误或存在可成交套利。", "Bounds use the expiry's fitted put/call forward and discount curve. Asynchronous daily trades, reference conventions or forward fitting can cause inconsistencies. This diagnostic alone establishes neither a source error nor executable arbitrage."):L("边界基于指定利率和持有成本，诊断只适用于当前输入。", "Bounds use the supplied rate and carry and apply only to these inputs.")}</p>
    <p className="text-[11px] text-muted-foreground">{L("保留所选参考价，不截断、不强行填入 IV；该口径下不进入 IV、曲面或组合估值。", "Selected references are retained without clipping or fabricated IV; this basis is excluded from IV, surfaces and basket valuation.")}</p>
    <div className="flex flex-wrap items-center gap-3 text-xs"><span>{L("收盘参考价", "Close reference")}: {number(row.reference_prices?.close,4)} · {L("结算参考价", "Settlement reference")}: {number(row.reference_prices?.settlement,4)}</span><Button size="sm" variant="outline" onClick={()=>setField(field==="close"?"settlement":"close")}>{field==="close"?L("用结算价重新核验", "Recheck using settlement"):L("用收盘价重新核验", "Recheck using close")}</Button></div>
  </div>
}
