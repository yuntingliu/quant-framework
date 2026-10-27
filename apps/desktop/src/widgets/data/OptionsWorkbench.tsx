import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Download, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { CartesianGrid, ComposedChart, Legend, Line, LineChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useConfirm } from "@/hooks/useConfirm";
import { api } from "@/lib/api";
import { optionApi, number, percent, type BasketLeg, type BasketRisk, type DatasetQuality, type OptionRow, type SyncPlan, type OptionCalibration } from "@/lib/options";
import { Widget } from "@/widgets/Widget";
import { useOptionsI18n } from "@/lib/useOptionsI18n";
import { OptionsModels } from "./OptionsModels";
import { OptionsResearch } from "./OptionsResearch";
const tabs = ["期权链", "波动率", "组合压力", "数据质量", "文献与预测"] as const;
type Tab = typeof tabs[number];
const selectClass = "h-8 rounded border border-input bg-background px-2 text-xs";
const statusLabels: Record<string, string> = { eligible: "可分析", source_quote_invalid: "源报价异常（隔离）", invalid_iv: "价格边界异常", illiquid: "无成交/持仓不足", forward_unavailable: "远期配对不足", expired_or_expiry_day: "到期日", zero_price: "零价格" };
const datasetLabels: Record<string, string> = { snapshots: "期权合约日快照", rates: "国债收益率曲线", underlyings: "ETF 复权历史", features: "历史期权因子", intraday: "五分钟ETF行情", realized: "五分钟已实现方差" };
const metricLabels: Record<string, string> = { validated_rows: "已校验记录", distinct_dates: "交易日", zero_volume: "零成交参考价", zero_volume_close_outside_range: "无成交收盘/区间差异", zero_volume_open_outside_range: "无成交开盘/区间差异", audited_reference_opens: "日内证据核验的异常开盘价", quarantined_quotes: "隔离的源报价", adjusted_contracts: "调整后合约", reconstructed_closes: "日内证据恢复收盘价", missing_indicators: "指标缺失单元格", invalid_observations: "计算失败记录", start_date: "最早样本", end_date: "最新样本" };
function dateAgo(days: number): string {
    return new Date(Date.now() - days * 86400000).toLocaleDateString("sv-SE");
}
function download(name: string, value: unknown) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    URL.revokeObjectURL(url);
}
function Metric({ label, value, detail }: {
    label: string;
    value: string;
    detail?: string;
}) {
    const { t } = useOptionsI18n();
    return <div className="rounded-md border border-border bg-card p-3"><div className="text-xs text-muted-foreground">{t(label)}</div><div className="mt-1 font-mono text-lg font-semibold tabular-nums">{value}</div>{detail && <div className="mt-1 text-[11px] text-muted-foreground">{t(detail)}</div>}</div>;
}
export function OptionsWorkbench() {
    const { t, language } = useOptionsI18n();
    const confirm = useConfirm();
    const queryClient = useQueryClient();
    const [surfaceModel, setSurfaceModel] = useState("ssvi");
    const [sampling, setSampling] = useState("standard");
    const [importanceTilt, setImportanceTilt] = useState(-2);
    const [calibration, setCalibration] = useState<OptionCalibration | null>(null);
    const [useCalibration, setUseCalibration] = useState(false);
    const [symbol, setSymbol] = useState("510050.XSHG");
    const [selectedDay, setSelectedDay] = useState("");
    const [field, setField] = useState("close");
    const [manualRate, setManualRate] = useState(false);
    const [rate, setRate] = useState("1.2");
    const [tab, setTab] = useState<Tab>("期权链");
    const [expiry, setExpiry] = useState("");
    const [kind, setKind] = useState("all");
    const [legs, setLegs] = useState<BasketLeg[]>([]);
    const [paths, setPaths] = useState(100000);
    const [daysForward, setDaysForward] = useState(0);
    const [correlation, setCorrelation] = useState(.8);
    const [intensity, setIntensity] = useState(1);
    const [jumpMean, setJumpMean] = useState(-.15);
    const [jumpVol, setJumpVol] = useState(.15);
    const [risk, setRisk] = useState<{
        key: string;
        value: BasketRisk;
    } | null>(null);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const [syncStart, setSyncStart] = useState(dateAgo(92));
    const [syncEnd, setSyncEnd] = useState(dateAgo(1));
    const [allSymbols, setAllSymbols] = useState(true);
    const [plan, setPlan] = useState<SyncPlan | null>(null);
    const [showSync, setShowSync] = useState(false);
    const universe = useQuery({ queryKey: ["option-universe"], queryFn: optionApi.universe,
        refetchInterval: (query) => query.state.data?.jobs.some(j => ["running", "queued"].includes(j.status)) ? 5000 : false });
    const listing = universe.data?.underlyings.find(item => item.symbol === symbol);
    const day = listing?.dates.includes(selectedDay) ? selectedDay : listing?.latest ?? "";
    const rateValue = manualRate ? Number(rate) / 100 : undefined;
    const revision = universe.data?.revision;
    const analysis = useQuery({ queryKey: ["option-analysis", symbol, day, field, rateValue, revision],
        queryFn: () => optionApi.analysis(symbol, day, field, rateValue), enabled: Boolean(day), staleTime: 15000 });
    const quality = useQuery({ queryKey: ["option-quality", revision], queryFn: optionApi.quality, enabled: tab === "数据质量" });
    const history = useQuery({ queryKey: ["option-history", symbol, day, field, rateValue, revision], queryFn: () => optionApi.history(symbol, day, field, rateValue), enabled: tab === "波动率" && Boolean(day) });
    const data = analysis.data;
    const surface = surfaceModel === "ssvi" ? data?.surface.ssvi ?? data?.surface : data?.surface;
    const fits = surface?.fits ?? [];
    const selectedFit = fits.find(fit => fit.expiry === expiry) ?? fits[0];
    const expiryDates = [...new Set(data?.contracts.map(row => row.expiry) ?? [])];
    const rows = (data?.contracts ?? []).filter(row => (!expiry || row.expiry === expiry) && (kind === "all" || row.option_type === kind))
        .sort((a, b) => a.expiry.localeCompare(b.expiry) || a.strike - b.strike || a.option_type.localeCompare(b.option_type));

    const activeJob = universe.data?.jobs.find(j => ["running", "queued"].includes(j.status));
    const lastJob = universe.data?.jobs[0];
    const basketNames = useQuery({ queryKey: ["option-basket-names", day, revision, [...new Set(legs.map(l => l.underlying))].sort().join(",")], queryFn: async () => {
      const chains = await Promise.all([...new Set(legs.map(l => l.underlying))].map(s => optionApi.names(s, day)));
      return new Map(chains.flatMap(chain => chain.contracts.map(row => [`${row.underlying}:${row.order_book_id}`, row] as const)));
    }, enabled: tab === "组合压力" && !!day && legs.length > 0 });
    function activateCalibration(value: OptionCalibration) {
      setCalibration(value); setIntensity(value.parameters.jump_intensity); setJumpMean(value.parameters.jump_mean); setJumpVol(value.parameters.jump_volatility); setUseCalibration(true);
    }
    const calibrationActive = useCalibration && calibration?.usable && calibration.date === day && calibration.price_field === field && calibration.manual_rate === (rateValue ?? null) && legs.every(l => l.underlying === calibration.underlying);
    const request = { day, price_field: field, sampling, importance_tilt: importanceTilt, ...(calibrationActive ? { calibration_id: calibration!.id } : {}), legs: legs.map(({ underlying, contract, quantity }) => ({ underlying, contract, quantity })),
        ...(manualRate ? { rate: rateValue } : {}), paths, days_forward: daysForward, correlation,
        jump_intensity: intensity, jump_mean: jumpMean, jump_volatility: jumpVol, include_mc: true };
    const requestKey = JSON.stringify([request, revision]);
    const currentRisk = risk?.key === requestKey ? risk.value : null;
    async function action(name: string, callback: () => Promise<void>) {
        setBusy(name);
        setError("");
        try {
            await callback();
        }
        catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        }
        finally {
            setBusy("");
        }
    }
    function addLeg(row: OptionRow) {
        if (legs.length >= 20 && !legs.some(item => item.underlying === symbol && item.contract === row.order_book_id)) {
            setError(t("组合最多支持 20 个合约"));
            return;
        }
        setLegs(current => {
            const index = current.findIndex(item => item.underlying === symbol && item.contract === row.order_book_id);
            if (index >= 0)
                return current.map((item, i) => i === index ? { ...item, quantity: item.quantity + 1 } : item);
            if (current.length >= 20)
                return current;
            return [...current, { underlying: symbol, contract: row.order_book_id, quantity: 1,
                    label: row.contract_name_zh, label_zh: row.contract_name_zh, label_en: row.contract_name_en }];
        });
    }
    async function sync(execute: boolean) {
        const underlyings = allSymbols ? universe.data?.underlyings.map(r => r.symbol) ?? [symbol] : [symbol];
        const body = { underlyings, start: syncStart, end: syncEnd, overlap: 3, confirm_python_execution: true };
        if (execute && !await confirm({ title: t("同步 ETF 期权数据"), description: t(`将执行可查看的内置数据配方，同步 ${underlyings.length} 个 ETF，${syncStart} 至 ${syncEnd}；已有完整日期会增量复用。`), confirmText: t("开始同步") }))
            return;
        await action("sync", async () => {
            if (execute) {
                await optionApi.sync(body);
                await universe.refetch();
            }
            else
                setPlan(await optionApi.plan(body));
        });
    }
    return <Widget headerless bodyClassName="overflow-auto">
    <div className="space-y-4 p-1" data-testid="options-workbench">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><div className="flex items-center gap-2 font-semibold"><Activity className="h-4 w-4 text-primary"/>{t("ETF 期权研究")}<Badge variant="outline">{t("日线 · 真实数据")}</Badge></div>
          <p className="mt-1 text-xs text-muted-foreground">{t("期权链、隐含远期、波动率与组合全量重估")}</p></div>
        <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => { void universe.refetch(); void analysis.refetch(); void quality.refetch(); void queryClient.invalidateQueries({ predicate: q => typeof q.queryKey[0] === "string" && q.queryKey[0].startsWith("option-") }); }}><RefreshCw className="h-3.5 w-3.5"/>{t("刷新")}</Button><Button size="sm" onClick={() => setShowSync(!showSync)}>{t("历史同步")}</Button></div>
      </div>
      <div className="flex flex-wrap items-end gap-3 rounded border border-border p-3">
        <label className="space-y-1 text-xs"><span className="text-muted-foreground">{t("标的 ETF")}</span><select aria-label={t("标的 ETF")} className={`${selectClass} block min-w-44`} value={symbol} onChange={e => { setSymbol(e.target.value); setExpiry(""); }}>{universe.data?.underlyings.map(item => <option key={item.symbol} value={item.symbol}>{t(item.name)} · {item.symbol.slice(0, 6)}</option>)}</select></label>
        <label className="space-y-1 text-xs"><span className="text-muted-foreground">{t("分析日期")}</span><select aria-label={t("分析日期")} className={`${selectClass} block`} value={day} onChange={e => setSelectedDay(e.target.value)}>{listing?.dates.slice().reverse().map(d => <option key={d}>{d}</option>)}</select></label>
        <label className="space-y-1 text-xs"><span className="text-muted-foreground">{t("价格口径")}</span><select className={`${selectClass} block`} value={field} onChange={e => setField(e.target.value)}><option value="close">{t("收盘价")}</option><option value="settlement">{t("结算价")}</option></select></label>
        <label className="space-y-1 text-xs"><span className="text-muted-foreground">{t("贴现曲线")}</span><select aria-label={t("贴现曲线")} className={`${selectClass} block`} value={manualRate ? "manual" : "market"} onChange={e => setManualRate(e.target.value === "manual")}><option value="market">{t("同期中债国债曲线")}</option><option value="manual">{t("手动年化利率")}</option></select></label>
        {manualRate && <label className="text-xs">{t("利率 %")}<Input aria-label={t("手动年化利率")} className="mt-1 h-8 w-24" style={{ width: "6rem" }} type="number" step=".1" value={rate} onChange={e => setRate(e.target.value)}/></label>}
        <div className="ml-auto text-right text-xs text-muted-foreground"><div>{number(listing?.sessions, 0)}{t("个已同步交易日")}</div><div>{number(universe.data?.total_rows, 0)}{t("条合约日记录")}</div></div>
      </div>
      {showSync && <section className="space-y-3 rounded border border-primary/30 bg-primary/5 p-3">
        <div className="flex flex-wrap items-end gap-3"><label className="text-xs">{t("开始日期")}<Input className="mt-1 h-8 w-40" style={{ width: "10rem" }} type="date" value={syncStart} onChange={e => setSyncStart(e.target.value)}/></label><label className="text-xs">{t("结束日期")}<Input className="mt-1 h-8 w-40" style={{ width: "10rem" }} type="date" value={syncEnd} max={dateAgo(1)} onChange={e => setSyncEnd(e.target.value)}/></label>
          <label className="flex h-8 items-center gap-2 text-xs"><input type="checkbox" checked={allSymbols} onChange={e => setAllSymbols(e.target.checked)}/>{t("全部 9 个 ETF 标的")}</label>
          <Button size="sm" variant="outline" disabled={Boolean(busy)} onClick={() => void sync(false)}>{t("预览配方")}</Button><Button size="sm" disabled={Boolean(busy) || Boolean(activeJob)} onClick={() => void sync(true)}>{busy === "sync" && <Loader2 className="animate-spin"/>}{t("开始增量同步")}</Button></div>
        <p className="text-xs text-muted-foreground">{t("分段批量获取、逐日历史合约发现；同步真实利率与 ETF 复权价格。失败后可重跑续传，最近三个交易日重拉修订。")}</p>
        <p className="text-xs text-muted-foreground">{t("ETF 复权行情会统一刷新已保存历史，避免分红拼接跳变；历史指标只重算输入已变化的日期。")}</p>
        {plan && <details><summary className="cursor-pointer text-xs">{t("查看将执行的完整 Python 配方（")}{plan.planned.length}{t("类数据）")}</summary><pre className="mt-2 max-h-64 overflow-auto rounded bg-muted p-3 text-[11px]">{plan.source}</pre></details>}
      </section>}
      {activeJob && <div role="status" className="flex items-center gap-2 rounded bg-primary/5 p-2 text-xs"><Loader2 className="h-3.5 w-3.5 animate-spin"/>{t("数据任务执行中：下载、增量入库和质量校验；完成后显示结果。")}<Button className="ml-auto" size="sm" variant="ghost" onClick={() => void action("cancel", async () => { await api.post(`/data-sync/jobs/${activeJob.id}/cancel`, {}); await universe.refetch(); })}>{t("取消任务")}</Button></div>}
      {!activeJob && lastJob && <div role="status" className={`rounded border p-2 text-xs ${lastJob.status === "succeeded" ? "border-border text-muted-foreground" : "border-amber-300 bg-amber-50 text-amber-800"}`}>{t("最近数据任务：")}{({ succeeded: t("完成"), failed: t("失败，可重跑续传"), cancelled: t("已取消"), interrupted: t("已中断，可重跑续传") } as Record<string, string>)[lastJob.status] ?? lastJob.status}{t("· 进度")}{lastJob.progress}{lastJob.total > 0 ? ` / ${lastJob.total}` : ""}{lastJob.status !== "succeeded" && lastJob.message ? ` · ${lastJob.message}` : ""}</div>}
      {(error || analysis.error || universe.error) && <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">{t(error || analysis.error?.message || universe.error?.message || "")}</div>}
      {analysis.isFetching && <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin"/>{t("正在计算期权链与波动率…")}</div>}
      {!day && !universe.isLoading && <div className="rounded border border-dashed p-8 text-center text-sm text-muted-foreground">{t("当前标的尚无数据。打开“历史同步”选择日期范围后运行。")}</div>}
      {data && <>
        {tab !== "文献与预测" && <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
          <Metric label={t("标的价格")} value={number(data.contracts[0]?.spot, 4)} detail={t(`${data.date} · ${field === "close" ? t("收盘") : t("结算")}`)}/>
          <Metric label={t("可用 IV / 合约数")} value={`${data.eligible} / ${data.total}`} detail={t("异常报价独立标记")}/>
          <Metric label={t("约 30 天 ATM IV")} value={percent(data.constant_maturity?.atm_iv_30)} detail={t("远期平值 · 总方差插值")}/>
          <Metric label={t("20 日已实现波动率")} value={percent(data.realized_volatility.rv20)} detail={t("ETF 复权对数收益 · 年化")}/>
          <Metric label={t("持仓量 PCR")} value={number(data.activity.oi_pcr)} detail={t(`成交量 PCR ${number(data.activity.volume_pcr)}`)}/>
          <Metric label={t("贴现数据日期")} value={data.discount_curve.date ?? "手动"} detail={data.discount_curve.stale_days ? t(`沿用前 ${data.discount_curve.stale_days} 日曲线`) : t("按到期日插值贴现")}/>
        </div>}
        <div className="flex gap-1 border-b border-border">{tabs.map(item => <button key={item} type="button" className={`border-b-2 px-4 py-2 text-sm ${tab === item ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`} onClick={() => setTab(item)}>{t(item)}{item === "组合压力" && legs.length > 0 ? ` (${legs.length})` : ""}</button>)}</div>

        {tab === "期权链" && <section className="space-y-3">
          <div className="flex flex-wrap items-center gap-2"><select aria-label={t("到期月份")} className={selectClass} value={expiry} onChange={e => setExpiry(e.target.value)}><option value="">{t("全部到期日")}</option>{expiryDates.map(d => <option key={d}>{d}</option>)}</select><select className={selectClass} value={kind} onChange={e => setKind(e.target.value)}><option value="all">{t("认购 + 认沽")}</option><option value="C">{t("认购")}</option><option value="P">{t("认沽")}</option></select><span className="text-xs text-muted-foreground">{t("按当日乘数计算；+ 加入组合，张数正买负卖")}</span><Button className="ml-auto" size="sm" variant="ghost" onClick={() => download(`option-chain-${symbol}-${day}.json`, data)}><Download className="h-3.5 w-3.5"/>{t("导出")}</Button></div>
          <div className="max-h-[520px] overflow-auto rounded border border-border"><table className="w-full whitespace-nowrap text-right text-xs"><thead className="sticky top-0 bg-muted"><tr>{[t("合约名称 / 代码"), t("到期"), t("方向"), t("行权价"), t("价格"), "IV", "Delta", "Gamma", "Vega/1%", t("Theta/日"), t("成交量"), t("持仓量"), t("乘数"), t("状态"), t("组合")].map(title => <th key={title} className="px-2 py-2 font-medium">{t(title)}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row.order_book_id} className={`border-t border-border hover:bg-muted/60 ${row.status !== "eligible" ? "text-muted-foreground" : ""}`}><td className="px-2 py-2 text-left"><div className="font-medium">{language === "zh" ? row.contract_name_zh : row.contract_name_en}</div><div className="mt-1 font-mono text-[10px] text-muted-foreground">{row.order_book_id}</div></td><td className="px-2">{row.expiry}</td><td className={row.option_type === "C" ? "text-rose-600" : "text-emerald-600"}>{row.option_type === "C" ? t("购") : t("沽")}</td><td className="px-2 font-mono">{number(row.strike, 4)}</td><td className="px-2 font-mono">{row.market_price.toFixed(4)}</td><td className="px-2 font-mono">{percent(row.iv)}</td><td className="px-2">{number(row.delta, 3)}</td><td className="px-2">{number(row.gamma, 3)}</td><td className="px-2">{number(row.vega_1pct, 4)}</td><td className="px-2">{number(row.theta_day, 5)}</td><td className="px-2">{number(row.volume, 0)}</td><td className="px-2">{number(row.open_interest, 0)}</td><td className="px-2">{number(row.multiplier, 0)}</td><td className="px-2" title={row.reason ? t(row.reason) : undefined}>{t(statusLabels[row.status] ?? row.status)}</td><td className="px-2"><Button aria-label={t(`加入合约 ${row.order_book_id}`)} variant="ghost" size="sm" disabled={row.status !== "eligible"} onClick={() => addLeg(row)}><Plus className="h-3 w-3"/></Button></td></tr>)}</tbody></table></div>
          <p className="text-[11px] text-muted-foreground">{t("日线收盘价可能不同步；IV 采用认购认沽平价隐含远期。贴现曲线为国债收益率的零息近似，Greeks 固定当前有效持有成本。")}</p>
        </section>}

        {tab === "波动率" && <section className="space-y-4">
          <label className="flex items-center gap-2 text-xs">{t("曲面模型")}<select aria-label={t("曲面模型")} className={selectClass} value={surfaceModel} onChange={e => setSurfaceModel(e.target.value)}><option value="ssvi">{t("SSVI · 静态约束")}</option><option value="svi">{t("SVI · 诊断拟合")}</option></select></label>
          <div className="grid gap-4 xl:grid-cols-2"><div className="rounded border border-border p-3"><div className="mb-2 flex items-center justify-between text-sm font-medium">{t("波动率微笑")}<select className={selectClass} value={selectedFit?.expiry ?? ""} onChange={e => setExpiry(e.target.value)}>{fits.map(fit => <option key={fit.expiry}>{fit.expiry}</option>)}</select></div>{selectedFit ? <><ResponsiveContainer width="100%" height={250}><ComposedChart><CartesianGrid strokeDasharray="3 3"/><XAxis type="number" dataKey="k" domain={["dataMin", "dataMax"]} tickFormatter={v => `${Number(v).toFixed(0)}%`}/><YAxis tickFormatter={v => `${Number(v).toFixed(0)}%`}/><Tooltip formatter={v => `${Number(v).toFixed(2)}%`} labelFormatter={v => `log(K/F) ${Number(v).toFixed(2)}%`}/><Legend wrapperStyle={{ fontSize: 11 }}/><Line data={selectedFit.smile.map(point => ({ k: point.k * 100, iv: point.iv * 100 }))} type="monotone" dataKey="iv" name={t(surfaceModel === "ssvi" ? "SSVI 约束拟合" : "SVI 拟合")} stroke="#2563eb" dot={false} strokeWidth={2} isAnimationActive={false}/><Scatter data={selectedFit.observed.map(point => ({ k: point.k * 100, iv: point.iv * 100 }))} dataKey="iv" name={t("市场 OTM IV")} fill="#d97706" isAnimationActive={false}/></ComposedChart></ResponsiveContainer><div className="text-xs text-muted-foreground">{selectedFit.observations}{t("个 OTM 执行价 · IV RMSE")}{percent(selectedFit.rmse_iv)} · RR25 {percent(selectedFit.risk_reversal_25)} · BF25 {percent(selectedFit.butterfly_25)}</div></> : <p className="p-8 text-center text-sm text-muted-foreground">{t("可用 OTM 执行价不足，未生成拟合曲线。")}</p>}</div>
            <div className="rounded border border-border p-3"><div className="mb-2 text-sm font-medium">{t("ATM 期限结构")}</div><ResponsiveContainer width="100%" height={250}><LineChart data={fits.map(fit => ({ days: Math.round(fit.maturity * 365), iv: fit.atm_iv == null ? null : fit.atm_iv * 100 }))}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="days" unit={t("天")}/><YAxis tickFormatter={v => `${Number(v).toFixed(0)}%`}/><Tooltip formatter={v => `${Number(v).toFixed(2)}%`}/><Line dataKey="iv" name="ATM IV" stroke="#7c3aed" strokeWidth={2}/></LineChart></ResponsiveContainer><div className="text-xs text-muted-foreground">{t("SVI 总方差拟合；超出观测范围不作翼部外推。").replace("SVI", surfaceModel.toUpperCase())}</div></div></div>
          <div className="overflow-auto rounded border border-border p-3"><div className="mb-3 text-sm font-medium">{t("波动率曲面 · 远期对数价内外程度")}</div><table className="w-full min-w-[700px] text-center text-[10px]"><thead><tr><th className="px-2 text-left">{t("到期日")}</th>{surface?.grid[0]?.values.map((v, i) => <th key={i}>{(v.k * 100).toFixed(1)}%</th>)}</tr></thead><tbody>{surface?.grid.map(row => <tr key={row.expiry}><td className="whitespace-nowrap px-2 py-3 text-left font-mono">{row.expiry}</td>{row.values.map((v, i) => <td key={i} className="p-1 font-mono text-slate-900" style={{ backgroundColor: `hsl(${Math.max(0, 220 - v.iv * 200)},70%,${Math.max(55, 93 - v.iv * 70)}%)` }} title={t(`IV ${percent(v.iv)}；总方差 ${v.total_variance.toFixed(5)}`)}>{(v.iv * 100).toFixed(1)}</td>)}</tr>)}</tbody></table></div>
          {surface && surface.warnings.length > 0 && <div className="rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800"><strong>{t("曲面诊断：")}{surface.usable_for_interpolation ? (surface.partial_coverage ? t("部分期限可用；定价时检查相邻期限") : t("可插值")) : t("保留诊断，不自动用于定价")}</strong>{surface.warnings.map(w => <p className="mt-1" key={w}>{t(w)}</p>)}</div>}
          <OptionsModels key={`${symbol}:${day}:${field}:${rateValue}`} symbol={symbol} day={day} field={field} rate={rateValue} model={surfaceModel} initialStrike={data.contracts[0]?.spot ?? 3} canApply={legs.length > 0 && legs.every(l => l.underlying === symbol)} onApply={value => {activateCalibration(value);setTab("组合压力");}} />
          <div className="rounded border border-border p-3"><div className="mb-2 text-sm font-medium">{t("近 40 个交易日 · 约 30 天 ATM IV")}</div>{history.isFetching && <p className="text-xs text-muted-foreground">{t("正在计算历史 IV…")}</p>}{history.error && <p className="text-xs text-amber-700">{history.error.message}</p>}<ResponsiveContainer width="100%" height={220}><LineChart data={history.data?.points.map(p => ({ date: p.date.slice(5), iv: p.atm_iv == null ? null : p.atm_iv * 100 })) ?? []}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="date"/><YAxis tickFormatter={v => `${Number(v).toFixed(0)}%`}/><Tooltip formatter={v => `${Number(v).toFixed(2)}%`}/><Line dataKey="iv" name={t("历史 ATM IV")} stroke="#0d9488" dot={false} strokeWidth={2}/></LineChart></ResponsiveContainer></div>
        </section>}

        {tab === "组合压力" && <section className="space-y-4">
          <p className="text-xs text-muted-foreground">{t("在期权链点击 + 添加合约，可跨 ETF 组合。张数正买负卖；按当日真实乘数重估。")}</p>
          {basketNames.error && <p role="alert" className="text-xs text-red-700">{t(basketNames.error.message)}</p>}
          {legs.length === 0 ? <div className="rounded border border-dashed p-8 text-center text-sm text-muted-foreground">{t("组合为空，请先从期权链添加合约。")}</div> : <>
            <div className="space-y-2 rounded border border-border p-3">{legs.map((leg, index) => <div className="flex items-center gap-3 text-xs" key={`${leg.underlying}-${leg.contract}`}><span className="min-w-0 flex-1">{basketNames.data?.get(`${leg.underlying}:${leg.contract}`)?.[language === "zh" ? "contract_name_zh" : "contract_name_en"] ?? (basketNames.isFetching ? t("正在更新合约名称…") : t("所选日期合约不可用"))} <span className="text-muted-foreground">{leg.contract}</span></span><Input aria-label={t(`合约 ${leg.contract} 张数`)} type="number" className="h-8 w-24" style={{ width: "6rem", flexShrink: 0 }} value={leg.quantity} onChange={e => setLegs(current => current.map((v, i) => i === index ? { ...v, quantity: Number(e.target.value) } : v))}/><span>{t("张")}</span><Button size="sm" variant="ghost" onClick={() => setLegs(current => current.filter((_, i) => i !== index))}><Trash2 className="h-3.5 w-3.5"/></Button></div>)}</div>
            <div className="grid grid-cols-2 gap-3 rounded border border-border p-3 md:grid-cols-4 xl:grid-cols-7">{[
                        { label: t("推进天数"), value: daysForward, set: setDaysForward, step: 1 }, { label: t("模拟路径"), value: paths, set: setPaths, step: 10000 },
                        { label: t("ETF 相关系数"), value: correlation, set: setCorrelation, step: .1 }, { label: t("年跳跃次数"), value: intensity, set: (v: number) => { setUseCalibration(false); setIntensity(v); }, step: .1 },
                        { label: t("Log 跳跃均值"), value: jumpMean, set: (v: number) => { setUseCalibration(false); setJumpMean(v); }, step: .05 }, { label: t("跳跃标准差"), value: jumpVol, set: (v: number) => { setUseCalibration(false); setJumpVol(v); }, step: .05 },
                    ].map(item => <label className="text-xs" key={item.label}>{t(item.label)}<Input type="number" className="mt-1 h-8" value={item.value} step={item.step} onChange={e => item.set(Number(e.target.value))}/></label>)}<div className="flex items-end"><Button size="sm" disabled={Boolean(busy)} onClick={() => void action("risk", async () => { const value = await optionApi.portfolio(request); setRisk({ key: requestKey, value }); })}>{busy === "risk" && <Loader2 className="animate-spin"/>}{t("全量重估")}</Button></div></div>
            <div className="flex flex-wrap items-center gap-3 text-xs"><label>{t("抽样方法")}<select aria-label={t("抽样方法")} className={selectClass} value={sampling} onChange={e => setSampling(e.target.value)}><option value="standard">{t("标准 Monte Carlo")}</option><option value="importance">{t("混合重要性抽样")}</option></select></label>{sampling === "importance" && <label>{t("尾部倾斜强度")}<Input aria-label={t("尾部倾斜强度")} type="number" step=".5" className="w-24" value={importanceTilt} onChange={e => setImportanceTilt(Number(e.target.value))}/></label>}{calibration && <label className="flex gap-2"><input type="checkbox" checked={Boolean(calibrationActive)} disabled={!calibration.usable || calibration.date !== day || calibration.price_field !== field || calibration.manual_rate !== (rateValue ?? null) || legs.some(l => l.underlying !== calibration.underlying)} onChange={e => { if (e.target.checked) activateCalibration(calibration); else setUseCalibration(false); }}/>{t("使用匹配的单 ETF 校准版本")}</label>}</div>
                        {!currentRisk && <p className="text-xs text-muted-foreground">{risk ? t("组合或参数已变化，请重新重估。") : t("点击全量重估，计算组合 Greeks、33 个压力情景及联合 Monte Carlo。")}</p>}
            {currentRisk && <><div className="grid grid-cols-2 gap-2 xl:grid-cols-5"><Metric label={t("组合市值 CNY")} value={number(currentRisk.market_value)}/><Metric label={t("现金 Delta")} value={number(currentRisk.greeks.cash_delta)}/><Metric label={t("Vega / 波动率 +1pp")} value={number(currentRisk.greeks.vega_1pct)}/><Metric label={t("Theta / 天")} value={number(currentRisk.greeks.theta_day)}/><Metric label={t("最差情景 P&L")} value={number(currentRisk.worst_pnl)}/></div>
              <div className="overflow-auto rounded border border-border p-3"><div className="mb-3 flex justify-between text-sm font-medium">{t("BS 全量重估 · 组合盈亏 CNY")}<Button size="sm" variant="ghost" onClick={() => download(`option-portfolio-${day}.json`, currentRisk)}><Download className="h-3.5 w-3.5"/>{t("导出")}</Button></div><table className="w-full text-center text-xs"><thead><tr><th className="py-2 text-left">{t("标的冲击")}</th>{[0, .1, .3].map(v => <th key={v}>IV +{(v * 100).toFixed(0)}pp</th>)}</tr></thead><tbody>{[-.4, -.3, -.2, -.1, -.05, 0, .05, .1, .2, .3, .4].map(shock => <tr key={shock}><th className="py-2 text-left font-normal">{percent(shock, 0)}</th>{[0, .1, .3].map(shift => { const cell = currentRisk.scenarios.find(s => s.spot_shock === shock && s.volatility_shift === shift); return <td key={shift} className={`border border-background p-2 font-mono ${!cell ? "" : cell.pnl < 0 ? "bg-rose-100 text-rose-800" : "bg-emerald-100 text-emerald-800"}`}>{number(cell?.pnl)}</td>; })}</tr>)}</tbody></table></div>
              {currentRisk.monte_carlo && <div className="rounded border border-border p-3"><div className="mb-3 text-sm font-medium">{t("联合 Monte Carlo · 风险中性条件情景")}</div><div className="grid grid-cols-2 gap-2 xl:grid-cols-4"><Metric label={t("跳跃模型基准价值")} value={number(currentRisk.monte_carlo.base.value)} detail={t(`标准误 ${number(currentRisk.monte_carlo.base.standard_error)} CNY`)}/><Metric label={t("跌 30% / IV +20pp 价值")} value={number(currentRisk.monte_carlo.crash.value)} detail={t(`标准误 ${number(currentRisk.monte_carlo.crash.standard_error)} CNY`)}/><Metric label={t("同模型情景 P&L")} value={number(currentRisk.monte_carlo.model_pnl)}/><Metric label={t("模拟路径")} value={number(currentRisk.monte_carlo.base.paths, 0)} detail={t("同一 ETF 各合约共用路径")}/></div><div className="mt-3 overflow-auto"><table className="w-full text-right text-xs"><thead><tr>{[t("合约"),t("有效收益次数（基准 / 压力）"),t("收益有效样本量（基准 / 压力）"),t("状态")].map(v => <th className="p-2" key={v}>{v}</th>)}</tr></thead><tbody>{currentRisk.monte_carlo.base.leg_diagnostics?.map(d => <tr key={d.contract}><td>{currentRisk.positions.find(p => p.order_book_id === d.contract)?.[language === "zh" ? "contract_name_zh" : "contract_name_en"] ?? d.contract}</td><td>{d.positive_payoffs} / {currentRisk.monte_carlo?.crash.leg_diagnostics?.find(v=>v.contract===d.contract)?.positive_payoffs ?? "—"}</td><td>{number(d.effective_payoffs,0)} / {number(currentRisk.monte_carlo?.crash.leg_diagnostics?.find(v=>v.contract===d.contract)?.effective_payoffs,0)}</td><td>{d.warning || currentRisk.monte_carlo?.crash.leg_diagnostics?.find(v=>v.contract===d.contract)?.warning ? t("抽样不足") : t("通过")}</td></tr>)}</tbody></table></div>
              <p className="mt-3 font-mono text-[11px] text-muted-foreground">{t("基准 95% 抽样区间：")}{currentRisk.monte_carlo.base.ci95?.map(v => number(v)).join(" ～ ") ?? t("稀有收益样本不足，未报告")} CNY · Seed {currentRisk.monte_carlo.base.seed}</p>{(currentRisk.monte_carlo.base.warning || currentRisk.monte_carlo.crash.warning) && <p className="mt-2 text-xs text-amber-700">{t("尾部有效样本不足：本次模拟不能据此认定极端风险为零，建议增加路径或调整抽样方法。")}</p>}<p className="mt-3 text-[11px] text-muted-foreground">{t(calibrationActive ? "使用匹配的校准扩散波动率与跳跃参数；结果是 Q 测度条件估值，抽样区间不包含模型误差。" : "每个 ETF 使用约 30 天 ATM 波动率和隐含远期曲线，相关性及系统性跳跃由上方参数指定。结果为 Q 测度下贴现到期收益，抽样区间不包含模型误差，不等同于真实世界 VaR 或保证金压力。")}</p></div>}
            </>}
          </>}
        </section>}

        <div className={tab === "文献与预测" ? "" : "hidden"}><OptionsResearch active={tab === "文献与预测"} symbol={symbol} endDay={day} symbols={universe.data?.underlyings.map(v => v.symbol) ?? []} revision={revision} jobActive={Boolean(activeJob)} onRefresh={() => universe.refetch()}/></div>

        {tab === "数据质量" && <section className="space-y-3">{quality.isFetching && <p className="text-xs text-muted-foreground">{t("正在核对分区覆盖与字段质量…")}</p>}{quality.error && <p className="text-sm text-red-700">{quality.error.message}</p>}{quality.data && (Object.entries(quality.data) as Array<[
            string,
            DatasetQuality
        ]>).map(([key, report]) => <div className="rounded border border-border p-3" key={key}><div className="flex justify-between text-sm font-medium">{t(datasetLabels[key] ?? key)}<Badge variant="outline">{report.status === "passed" ? t("通过") : t("待修复")}</Badge></div><p className="mt-2 text-xs text-muted-foreground">{number(report.rows, 0)}{t("行 ·")}{number(report.files, 0)}{t("个分区")}</p><div className="mt-2 grid gap-2 text-xs md:grid-cols-3">{Object.entries(report.metrics).filter(([name]) => ["validated_rows", "distinct_dates", "zero_volume", "zero_volume_close_outside_range", "zero_volume_open_outside_range", "audited_reference_opens", "quarantined_quotes", "adjusted_contracts", "reconstructed_closes", "missing_indicators", "invalid_observations", "start_date", "end_date"].includes(name)).map(([name, value]) => <div key={name}><span className="text-muted-foreground">{t(metricLabels[name] ?? name)}：</span>{String(value)}</div>)}</div>{report.issues.map(issue => <p className={`mt-2 text-xs ${issue.severity === "error" ? "text-red-700" : "text-amber-700"}`} key={issue.code}>{t(issue.message)}</p>)}</div>)}</section>}
      </>}
    </div>
  </Widget>;
}
