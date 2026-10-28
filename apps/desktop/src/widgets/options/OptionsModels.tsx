import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { optionApi, number, percent, type OptionCalibration, type SurfacePrice } from "@/lib/options"
import { useOptionsI18n } from "@/lib/useOptionsI18n"

export function OptionsModels({ symbol, day, field, rate, model, initialStrike, canApply, onApply }: {
  symbol: string; day: string; field: string; rate?: number; model: string; initialStrike: number; canApply: boolean; onApply: (value: OptionCalibration) => void
}) {
  const { language, t } = useOptionsI18n()
  const L = (zh: string, en: string) => language === "zh" ? zh : en
  const [strike, setStrike] = useState(String(initialStrike))
  const [days, setDays] = useState("60")
  const [kind, setKind] = useState("C")
  const [price, setPrice] = useState<{key: string; value: SurfacePrice} | null>(null)
  const [calibration, setCalibration] = useState<OptionCalibration | null>(null)
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")
  const context = { underlying: symbol, day, price_field: field, ...(rate === undefined ? {} : { rate }) }
  const request = { ...context, model, strike: Number(strike), maturity_days: Number(days), option_type: kind }
  const key = JSON.stringify(request)
  const currentPrice = price?.key === key ? price.value : null
  async function action(name: string, work: () => Promise<void>) {
    setBusy(name);setError("")
    try { await work() } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy("") }
  }
  const calibrationWarnings: Record<string,string> = {
    calibration_not_converged: L("校准未收敛", "Calibration did not converge"),
    calibration_holdout_error: L("保留执行价误差超过阈值", "Held-out strike error exceeds the threshold"),
    calibration_weak_identification: L("跳跃参数识别较弱", "Jump parameters are weakly identified"),
  }
  return <section className="space-y-4" data-testid="option-model-tools">
    <div className="space-y-3 rounded border border-border p-3"><h3 className="text-sm font-medium">{L("曲面定价 · 共享观测范围内", "Surface valuation · shared observed domain")}</h3><div className="flex flex-wrap items-end gap-3">
      <label className="text-xs">{L("行权价", "Strike")}<Input aria-label={L("模型行权价", "Model strike")} className="mt-1" style={{width:"8rem"}} type="number" step=".01" value={strike} onChange={e=>setStrike(e.target.value)} /></label>
      <label className="text-xs">{L("剩余日历天", "Remaining calendar days")}<Input aria-label={L("模型剩余日历天", "Model remaining days")} className="mt-1" style={{width:"8rem"}} type="number" value={days} onChange={e=>setDays(e.target.value)} /></label>
      <select aria-label={L("模型期权方向", "Model option type")} className="h-8 rounded border bg-background px-2 text-xs" value={kind} onChange={e=>setKind(e.target.value)}><option value="C">{L("认购", "Call")}</option><option value="P">{L("认沽", "Put")}</option></select>
      <Button size="sm" disabled={!!busy} onClick={()=>void action("price",async()=>setPrice({key,value:await optionApi.surfacePrice(request)}))}>{L("计算曲面理论价", "Calculate surface value")}</Button>
    </div>{currentPrice && <p role="status" className="text-sm">{model.toUpperCase()} · IV {percent(currentPrice.iv)} · {L("理论价（每单位）", "Value per unit")} {number(currentPrice.price,6)} · {L("远期", "Forward")} {number(currentPrice.forward,4)}</p>}
    <p className="text-[11px] text-muted-foreground">{L("拒绝超出共享执行价和期限范围的查询。SSVI 使用单调 ATM 总方差与静态套利充分约束，拟合残差仍需查看。", "Queries beyond shared strikes or maturities are rejected. SSVI uses monotone ATM total variance and sufficient static-arbitrage constraints; inspect fitting residuals.")}</p></div>
    <div className="space-y-3 rounded border border-border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium">{L("Merton 跳跃模型校准与检验", "Merton jump calibration and validation")}</h3><Button size="sm" variant="outline" disabled={!!busy} onClick={()=>void action("calibrate",async()=>setCalibration(await optionApi.calibrate(context)))}>{busy === "calibrate" ? L("正在校准…", "Calibrating…") : L("校准当日期权", "Calibrate dated options")}</Button></div>
      <p className="text-[11px] text-muted-foreground">{L("每四个执行价保留一个作检验，不参与拟合。校准依据当前日期与价格口径，仅能套用于匹配的单 ETF 组合。日线报价、参数识别和模型误差均可能影响结果。", "Every fourth strike is held out and never fitted. Calibration is pinned to date and price basis and applies to a matching single-ETF basket. Daily references, parameter identification and model error limit inference.")}</p>
      {calibration && <div className="space-y-3 text-xs"><div className="grid grid-cols-2 gap-2 lg:grid-cols-4">{[[L("扩散波动率", "Diffusion volatility"),percent(calibration.parameters.diffusion_volatility)],[L("年跳跃次数", "Annual jump intensity"),number(calibration.parameters.jump_intensity,3)],[L("对数跳跃均值", "Log-jump mean"),number(calibration.parameters.jump_mean,4)],[L("跳跃波动率", "Jump volatility"),number(calibration.parameters.jump_volatility,4)]].map(([name,value])=><div className="rounded border border-border p-2" key={name}>{name}<strong className="mt-1 block font-mono">{value}</strong></div>)}</div>
      <p>{L("训练 / 保留报价", "Training / held-out quotes")}: {calibration.training_quotes} / {calibration.holdout_quotes} · {L("保留集 Vega 标准化 RMSE", "Holdout vega-scaled RMSE")}: {percent(calibration.holdout_vega_scaled_rmse)} · {L("识别条件数", "Identification condition number")}: {number(calibration.jacobian_condition,0)}</p>
      {calibration.warnings.map(w=><p className="text-amber-700" key={w}>{calibrationWarnings[w] ?? w}</p>)}
      <Button size="sm" disabled={!calibration.usable || !canApply} onClick={()=>onApply(calibration)}>{L("套用于组合压力测试", "Apply to portfolio stress")}</Button>{!canApply && <p className="text-muted-foreground">{L("先在期权链加入同一 ETF 的合约。", "First add contracts from this ETF to the basket.")}</p>}
      <details><summary className="cursor-pointer">{L("参数敏感性（不是置信区间）", "Parameter sensitivity (not confidence intervals)")}</summary><div className="mt-2 overflow-auto"><table className="w-full whitespace-nowrap text-right text-xs"><thead><tr>{[L("参数", "Parameter"),L("倍数", "Scale"),L("参考合约价格变化", "Reference option price change"),L("平均绝对价格变化", "Mean absolute price change")].map(v=><th className="p-2" key={v}>{v}</th>)}</tr></thead><tbody>{calibration.sensitivity?.map(row=><tr key={`${row.parameter}:${row.scale}`}><td>{({diffusion_volatility:L("扩散波动率","Diffusion volatility"),jump_intensity:L("年跳跃次数","Jump intensity"),jump_mean:L("对数跳跃均值","Log-jump mean"),jump_volatility:L("跳跃波动率","Jump volatility")} as Record<string,string>)[row.parameter]}</td><td>{row.scale}</td><td>{number(row.price_change,6)}</td><td>{number(row.mean_absolute_price_change,6)}</td></tr>)}</tbody></table></div></details>
            <details><summary className="cursor-pointer">{L("校准报价与模型误差", "Calibration quotes and pricing errors")}</summary><div className="mt-2 max-h-64 overflow-auto"><table className="w-full whitespace-nowrap text-right text-xs"><thead><tr>{[L("合约", "Contract"),L("到期", "Expiry"),L("市场价", "Market"),L("模型价", "Model"),L("用途", "Use")].map(v=><th className="p-2" key={v}>{v}</th>)}</tr></thead><tbody>{calibration.quotes.map(q=><tr key={q.contract}><td>{language === "zh" ? q.name_zh : q.name_en}<span className="block text-[10px] text-muted-foreground">{q.contract}</span></td><td>{q.expiry}</td><td>{number(q.market_price,5)}</td><td>{number(q.model_price,5)}</td><td>{q.holdout ? L("保留检验", "Held out") : L("训练", "Training")}</td></tr>)}</tbody></table></div><p className="mt-2 break-all text-muted-foreground">{L("校准版本", "Calibration version")}: {calibration.id}</p></details></div>}
    </div>
    {error && <p role="alert" className="rounded border border-red-300 p-3 text-xs text-red-700">{t(error)}</p>}
  </section>
}
