import { createContext, useContext, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useConfirm } from "@/hooks/useConfirm"
import { useOptionsDraft } from "@/hooks/useOptionsDraft"
import { useOptionsI18n } from "@/lib/useOptionsI18n"
import { optionApi, type BasketRisk, type OptionCalibration, type OptionRow, type SyncPlan } from "@/lib/options"

function dateAgo(days: number): string { return new Date(Date.now()-days*86400000).toLocaleDateString("sv-SE") }
export function useOptionsController() {

    const { t, language } = useOptionsI18n();
    const confirm = useConfirm();
    const queryClient = useQueryClient();
    const [surfaceModel, setSurfaceModel] = useOptionsDraft("surfaceModel");
    const [sampling, setSampling] = useOptionsDraft("sampling");
    const [importanceTilt, setImportanceTilt] = useOptionsDraft("importanceTilt");
    const [calibration, setCalibration] = useState<OptionCalibration | null>(null);
    const [useCalibration, setUseCalibration] = useState(false);
    const [calibrationRevision, setCalibrationRevision] = useState<unknown>(null);
    const [symbol, setSymbol] = useOptionsDraft("symbol");
    const [selectedDay, setSelectedDay] = useOptionsDraft("selectedDay");
    const [field, setField] = useOptionsDraft("field");
    const [manualRate, setManualRate] = useOptionsDraft("manualRate");
    const [rate, setRate] = useOptionsDraft("rate");
    const [tab, setTab] = useOptionsDraft("tab");
    const [selectedContract, setSelectedContract] = useState("");
    const [expiry, setExpiry] = useOptionsDraft("expiry");
    const [legs, setLegs] = useOptionsDraft("legs");
    const [paths, setPaths] = useOptionsDraft("paths");
    const [daysForward, setDaysForward] = useOptionsDraft("daysForward");
    const [correlation, setCorrelation] = useOptionsDraft("correlation");
    const [intensity, setIntensity] = useOptionsDraft("intensity");
    const [jumpMean, setJumpMean] = useOptionsDraft("jumpMean");
    const [jumpVol, setJumpVol] = useOptionsDraft("jumpVol");
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
    const universe = useQuery({ queryKey: ["option-universe"], queryFn: optionApi.universe,
        refetchInterval: (query) => query.state.data?.jobs.some(j => ["running", "queued"].includes(j.status)) ? 5000 : false });
    const listing = universe.data?.underlyings.find(item => item.symbol === symbol);
    const day = listing?.dates.includes(selectedDay) ? selectedDay : listing?.latest ?? "";
    const rateValue = manualRate ? Number(rate) / 100 : undefined;
    const revision = universe.data?.revision;
    const analysis = useQuery({ queryKey: ["option-analysis", symbol, day, field, rateValue, revision],
        queryFn: () => optionApi.analysis(symbol, day, field, rateValue), enabled: Boolean(day) && !["data", "research"].includes(tab), staleTime: 15000 });
    const history = useQuery({ queryKey: ["option-history", symbol, day, field, rateValue, revision], queryFn: () => optionApi.history(symbol, day, field, rateValue), enabled: tab === "volatility" && Boolean(day) });
    const data = analysis.data;
    const surface = surfaceModel === "ssvi" ? data?.surface.ssvi ?? data?.surface : data?.surface;
    const fits = surface?.fits ?? [];
    const selectedFit = fits.find(fit => fit.expiry === expiry) ?? fits[0];
    const expiryDates = [...new Set(data?.contracts.map(row => row.expiry) ?? [])];


    const activeJob = universe.data?.jobs.find(j => ["running", "queued"].includes(j.status));
    const lastJob = universe.data?.jobs[0];
    const basketNames = useQuery({ queryKey: ["option-basket-names", day, revision, [...new Set(legs.map(l => l.underlying))].sort().join(",")], queryFn: async () => {
      const chains = await Promise.all([...new Set(legs.map(l => l.underlying))].map(s => optionApi.names(s, day)));
      return new Map(chains.flatMap(chain => chain.contracts.map(row => [`${row.underlying}:${row.order_book_id}`, row] as const)));
    }, enabled: tab === "portfolio" && !!day && legs.length > 0 });
    function activateCalibration(value: OptionCalibration) {
      setCalibration(value); setCalibrationRevision(revision); setIntensity(value.parameters.jump_intensity); setJumpMean(value.parameters.jump_mean); setJumpVol(value.parameters.jump_volatility); setUseCalibration(true);
    }
    const calibrationActive = useCalibration && calibrationRevision === revision && calibration?.usable && calibration.date === day && calibration.price_field === field && calibration.manual_rate === (rateValue ?? null) && legs.every(l => l.underlying === calibration.underlying);
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

return { t, language, queryClient, tab, setTab, surfaceModel, setSurfaceModel, sampling, setSampling,
importanceTilt,setImportanceTilt,calibration,calibrationRevision,useCalibration,setUseCalibration,symbol,setSymbol,selectedDay,setSelectedDay,field,setField,
manualRate,setManualRate,rate,setRate,expiry,setExpiry,legs,setLegs,paths,setPaths,daysForward,setDaysForward,
correlation,setCorrelation,intensity,setIntensity,jumpMean,setJumpMean,jumpVol,setJumpVol,risk,setRisk,busy,error,
syncStart,setSyncStart,syncEnd,setSyncEnd,allSymbols,setAllSymbols,plan,universe,listing,day,rateValue,revision,analysis,
history,data,surface,fits,selectedFit,expiryDates,activeJob,lastJob,basketNames,activateCalibration,calibrationActive,
request,requestKey,currentRisk,action,addLeg,sync,selectedContract,setSelectedContract };
}
export type OptionsController = ReturnType<typeof useOptionsController>
export const OptionsContext = createContext<OptionsController | null>(null)
export function useOptionsWorkspace(){ const value=useContext(OptionsContext);if(!value)throw new Error("Options workspace is unavailable");return value }
